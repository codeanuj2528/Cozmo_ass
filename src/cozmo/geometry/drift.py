"""Accumulated pose drift: detecting it, and correcting it.

ARKit's visual-inertial odometry is locally excellent and globally not. Over a hundred
metres of walking through a flat it accumulates translation and yaw error, and the failure
mode is specific: a loop that should close does not, so the corridor is reported longer
than it is and the last room is placed a few centimetres off the first. Taking those poses
as given produces a plan that is internally consistent and externally wrong.

Correction is a pose graph over keyframes. Odometry edges hold consecutive keyframes at the
relative pose the sensor reported, and loop-closure edges hold revisited places together at
the relative pose ICP measured. Optimising the two against each other distributes the
accumulated error over the whole trajectory rather than dumping it at the seam.

Loop candidates are proposed by geometry and confirmed by ICP, in that order and never the
reverse. Two places being near each other in a drifted trajectory is weak evidence, since
the drift is exactly what makes the estimate unreliable; two places whose surfaces align
under ICP with a low residual is strong evidence. Accepting a false closure is far worse
than missing a true one, because a false closure folds the map, so the acceptance test is
deliberately strict.

The corrected poses are applied by re-fusing. Correcting the fused cloud in place would be
cheaper, but voxel reduction has already averaged points from several frames into each
voxel and kept only one frame's identity, so an in-place correction would apply one frame's
delta to another frame's contribution.

Only heading and horizontal position are corrected. Height and tilt are referenced to gravity
and do not accumulate the way heading and position do, while ICP between two keyframes that
mostly see ceiling or a blank wall is barely constrained vertically. With height free, closures
of that kind lowered the last part of the assignment's with-ceiling walk by about 40 cm and
spread its floor over 38 cm; held at what the phone measured, the floor lies within 3 cm over
87% of the scan.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import least_squares
from scipy.sparse import lil_matrix

from cozmo.geometry.icp import point_to_plane_icp
from cozmo.io.base import CaptureSource, Frame
from cozmo.schema import DriftReport
from cozmo.util.transforms import invert_pose, make_pose

LOOP_MIN_KEYFRAME_GAP = 25
LOOP_SEARCH_RADIUS_M = 2.5
LOOP_MAX_VIEW_ANGLE_RAD = np.deg2rad(60.0)
LOOP_MAX_CANDIDATES = 120
ICP_MIN_FITNESS = 0.55
ICP_MAX_RMSE_M = 0.035
KEYFRAME_POINTS = 900

# A revisit is a place reached again after going somewhere else. Two keyframes can be
# metres apart in index and centimetres apart in space simply because the operator was
# walking slowly down a corridor, and an edge between those two re-states odometry with
# ICP noise added rather than adding information. The discriminator is the ratio of path
# walked to distance closed.
LOOP_MIN_PATH_RATIO = 6.0
LOOP_MIN_PATH_M = 6.0

# Visual-inertial odometry drifts by around a percent of the distance walked. A closure asking
# for many times that is a match that slid, not drift: every closure on the assignment's
# single-room scan asked for 56-88 cm after 6-7 m of walking, while on the 90 m home walk the
# median closure asked for 0.13% of the path between the two visits.
LOOP_MAX_DRIFT_SHARE = 0.03
LOOP_MIN_ALLOWED_CORRECTION_M = 0.10
# Height and tilt are referenced to gravity, and odometry does not drift in them the way it does
# in heading and position. A settled ICP match that moves a keyframe further than this vertically,
# or tips it further than this, matched the wrong surfaces: on the assignment's scans such matches
# claimed up to 59 cm of height and 11 degrees of tilt.
LOOP_MAX_HEIGHT_DISAGREEMENT_M = 0.05
LOOP_MAX_TILT_DISAGREEMENT_RAD = float(np.deg2rad(2.0))

# Information weights. Rotation and translation residuals are in different units and a
# pose graph that adds radians to metres is weighting one arbitrarily against the other.
# Over a hundred metres of trajectory a milliradian of yaw costs more than a centimetre of
# translation, and these numbers are what say so.
ODOMETRY_SIGMA_ROT_RAD = 0.010
ODOMETRY_SIGMA_TRANS_M = 0.020
LOOP_SIGMA_ROT_RAD = 0.020
LOOP_SIGMA_TRANS_M = 0.030


@dataclass
class KeyframeCloud:
    index: int
    points: np.ndarray
    normals: np.ndarray


@dataclass
class LoopClosure:
    i: int
    j: int
    transform: np.ndarray
    fitness: float
    rmse: float


@dataclass
class DriftSolution:
    poses: np.ndarray
    report: DriftReport
    closures: list[LoopClosure] = field(default_factory=list)


def _rotation_about_up(theta: float) -> np.ndarray:
    c, s = np.cos(theta), np.sin(theta)
    return np.array([[c, 0.0, s], [0.0, 1.0, 0.0], [-s, 0.0, c]])


def _heading(rotation: np.ndarray) -> float:
    """Angle about the vertical of a rotation that is close to identity."""
    x_axis = rotation[:, 0]
    return float(np.arctan2(-x_axis[2], x_axis[0]))


def _turn_and_shift(xz: np.ndarray, theta: np.ndarray, shift: np.ndarray) -> np.ndarray:
    """Rotate rows of (x, z) about the vertical by `theta`, then add `shift`."""
    c, s = np.cos(theta), np.sin(theta)
    return np.stack(
        [c * xz[:, 0] + s * xz[:, 1] + shift[:, 0], -s * xz[:, 0] + c * xz[:, 1] + shift[:, 1]], axis=1
    )


def _wrap_angle(a: np.ndarray) -> np.ndarray:
    return (a + np.pi) % (2 * np.pi) - np.pi


def _plausible_drift(horizontal_m: float, walked_m: float) -> bool:
    """Whether odometry could have drifted this far horizontally over this much walking."""
    return horizontal_m <= max(LOOP_MIN_ALLOWED_CORRECTION_M, LOOP_MAX_DRIFT_SHARE * walked_m)


def _agrees_with_gravity(implied: np.ndarray, recorded: np.ndarray) -> bool:
    """Whether a closure leaves the keyframe's height and tilt where gravity-referenced odometry put them."""
    height = abs(float(implied[1, 3] - recorded[1, 3]))
    tilt = float(np.arccos(np.clip((implied[:3, :3] @ recorded[:3, :3].T)[1, 1], -1.0, 1.0)))
    return height <= LOOP_MAX_HEIGHT_DISAGREEMENT_M and tilt <= LOOP_MAX_TILT_DISAGREEMENT_RAD


def load_keyframe_clouds(
    source: CaptureSource,
    keyframes: list[int],
    max_points: int = KEYFRAME_POINTS,
    max_range_m: float = 4.0,
    seed: int = 0,
) -> list[KeyframeCloud]:
    """Camera-frame points and normals per keyframe, subsampled for ICP."""
    from cozmo.geometry.fusion import image_normals

    rng = np.random.default_rng(seed)
    out: list[KeyframeCloud] = []
    for position, frame in _enumerate_frames(source, keyframes):
        depth = frame.depth
        if depth is None:
            continue
        valid = (depth > 0.2) & (depth < max_range_m) & np.isfinite(depth)
        if frame.confidence is not None:
            valid &= frame.confidence >= 2
        if valid.sum() < 60:
            continue

        h, w = depth.shape
        vs, us = np.mgrid[0:h, 0:w]
        k = frame.k_depth
        z = depth.astype(np.float32)
        x = (us - k[0, 2]) * z / k[0, 0]
        y = (vs - k[1, 2]) * z / k[1, 1]
        grid = np.stack([x, y, z], axis=2).astype(np.float32)
        normals, ok = image_normals(grid, valid)
        keep = valid & ok
        if keep.sum() < 60:
            continue
        points = grid[keep]
        normal_vectors = normals[keep]
        flip = np.einsum("ij,ij->i", normal_vectors, points) > 0
        normal_vectors[flip] *= -1.0
        if len(points) > max_points:
            pick = rng.choice(len(points), size=max_points, replace=False)
            points, normal_vectors = points[pick], normal_vectors[pick]
        out.append(KeyframeCloud(index=position, points=points, normals=normal_vectors))
    return out


def _enumerate_frames(source: CaptureSource, keyframes: list[int]):
    for position, frame in zip(keyframes, source.frames(keyframes)):
        yield position, frame
    return


def propose_loops(
    poses: np.ndarray,
    keyframes: list[int],
    radius_m: float = LOOP_SEARCH_RADIUS_M,
    min_gap: int = LOOP_MIN_KEYFRAME_GAP,
    max_candidates: int = LOOP_MAX_CANDIDATES,
) -> list[tuple[int, int]]:
    """Keyframe pairs that look like genuine revisits.

    Near in space, far along the path, and facing a similar way. The path-length test is
    the one that matters: without it, a slow walk down a corridor generates a constraint
    between every pair of keyframes in it, all of which merely repeat what odometry already
    said, and the optimiser then has hundreds of noisy near-duplicate edges outvoting the
    handful of real closures.
    """
    centres = poses[keyframes][:, :3, 3]
    # Camera forward is +z in the OpenCV convention this codebase uses throughout.
    forward = poses[keyframes][:, :3, 2]
    steps = np.linalg.norm(np.diff(centres, axis=0), axis=1)
    path = np.concatenate([[0.0], np.cumsum(steps)])

    n = len(keyframes)
    scored: list[tuple[float, int, int]] = []
    for i in range(n):
        for j in range(i + min_gap, n):
            distance = float(np.linalg.norm(centres[i] - centres[j]))
            if distance > radius_m:
                continue
            walked = float(path[j] - path[i])
            if walked < LOOP_MIN_PATH_M or walked < LOOP_MIN_PATH_RATIO * max(distance, 0.05):
                continue
            angle = float(np.arccos(np.clip(forward[i] @ forward[j], -1.0, 1.0)))
            if angle > LOOP_MAX_VIEW_ANGLE_RAD:
                continue
            scored.append((distance + 0.5 * angle, i, j))
    scored.sort()
    return [(i, j) for _, i, j in scored[:max_candidates]]


find_loop_closures = propose_loops


def verify_loops(
    clouds: list[KeyframeCloud],
    poses: np.ndarray,
    keyframes: list[int],
    candidates: list[tuple[int, int]],
) -> list[LoopClosure]:
    """Confirm candidate closures with ICP, keeping only those that align well."""
    by_position = {c.index: c for c in clouds}
    closures: list[LoopClosure] = []
    positions = np.array([poses[k][[0, 2], 3] for k in keyframes])
    walked = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(positions, axis=0), axis=1))])
    for i, j in candidates:
        ci = by_position.get(keyframes[i])
        cj = by_position.get(keyframes[j])
        if ci is None or cj is None:
            continue
        # ICP runs in frame i's own coordinates, so the initial guess is what the drifted
        # odometry claims the relative pose is, and the correction it finds is the drift.
        initial = invert_pose(poses[keyframes[i]]) @ poses[keyframes[j]]
        result = point_to_plane_icp(
            cj.points.astype(np.float64),
            ci.points.astype(np.float64),
            ci.normals.astype(np.float64),
            initial=initial,
        )
        if not result.converged:
            continue
        if result.fitness < ICP_MIN_FITNESS or result.inlier_rmse > ICP_MAX_RMSE_M:
            continue
        correction = float(np.linalg.norm(result.transform[:3, 3] - initial[:3, 3]))
        # A closure that agrees exactly with odometry adds no information, and one that
        # disagrees wildly is more likely a false match than a real revisit.
        if correction < 0.004 or correction > 0.9:
            continue
        implied = poses[keyframes[i]] @ result.transform
        horizontal = float(np.linalg.norm((implied - poses[keyframes[j]])[[0, 2], 3]))
        if not _plausible_drift(horizontal, float(walked[j] - walked[i])):
            continue
        if not _agrees_with_gravity(implied, poses[keyframes[j]]):
            continue
        closures.append(LoopClosure(i=i, j=j, transform=result.transform,
                                    fitness=result.fitness, rmse=result.inlier_rmse))
    return closures


def optimise_pose_graph(
    poses: np.ndarray,
    keyframes: list[int],
    closures: list[LoopClosure],
    max_iterations: int = 40,
) -> tuple[np.ndarray, float, float]:
    """Least squares over each keyframe's heading and horizontal position.

    Returns (poses, residual before, residual after). A keyframe's correction is a turn about
    the vertical and a horizontal shift applied to the pose odometry reported, so height and
    tilt are never changed. An edge compares where the second keyframe ends up with where the
    first keyframe's correction and the edge's measurement put it, evaluated at the second
    keyframe's own position: a small turn applied far from the origin is then a turn, not a
    shift.
    """
    n = len(keyframes)
    initial = np.stack([poses[k] for k in keyframes])
    anchors = initial[:, [0, 2], 3]

    first, second, turn, target, rot_weight, trans_weight = [], [], [], [], [], []
    for i in range(n - 1):
        first.append(i)
        second.append(i + 1)
        turn.append(0.0)
        target.append(anchors[i + 1])
        rot_weight.append(1.0 / ODOMETRY_SIGMA_ROT_RAD)
        trans_weight.append(1.0 / ODOMETRY_SIGMA_TRANS_M)
    for closure in closures:
        measured = initial[closure.i] @ closure.transform
        confidence = float(np.clip(closure.fitness, 0.1, 1.0))
        first.append(closure.i)
        second.append(closure.j)
        turn.append(_heading((measured @ invert_pose(initial[closure.j]))[:3, :3]))
        target.append(measured[[0, 2], 3])
        rot_weight.append(confidence / LOOP_SIGMA_ROT_RAD)
        trans_weight.append(confidence / LOOP_SIGMA_TRANS_M)
    first_idx, second_idx = np.array(first), np.array(second)
    turns, targets = np.array(turn), np.array(target)
    rot_w, trans_w = np.array(rot_weight), np.array(trans_weight)

    def residuals(x: np.ndarray) -> np.ndarray:
        theta = x[0::3]
        shift = np.stack([x[1::3], x[2::3]], axis=1)
        moved = _turn_and_shift(anchors[second_idx], theta[second_idx], shift[second_idx])
        predicted = _turn_and_shift(targets, theta[first_idx], shift[first_idx])
        translation = (moved - predicted) * trans_w[:, None]
        rotation = _wrap_angle(theta[second_idx] - theta[first_idx] - turns) * rot_w
        # Gauge fixing: without holding the first keyframe the problem is invariant to a turn
        # and a shift of the whole walk, and the solver wanders through that null space.
        gauge = 1000.0 * x[:3]
        return np.concatenate([np.stack([rotation, translation[:, 0], translation[:, 1]], axis=1).ravel(), gauge])

    x0 = np.zeros(3 * n)
    before = float(np.sqrt((residuals(x0) ** 2).mean()))
    if not closures:
        return initial, before, before

    sparsity = lil_matrix((3 * len(first_idx) + 3, 3 * n), dtype=int)
    for e, (i, j) in enumerate(zip(first_idx, second_idx)):
        sparsity[3 * e : 3 * e + 3, 3 * i : 3 * i + 3] = 1
        sparsity[3 * e : 3 * e + 3, 3 * j : 3 * j + 3] = 1
    sparsity[-3:, :3] = 1

    # A soft L1 loss keeps a single false closure that survived ICP verification from folding
    # the whole map. Some always do survive: two bathrooms in the same flat look alike to a
    # geometric matcher.
    solution = least_squares(
        residuals, x0, jac_sparsity=sparsity, method="trf", loss="soft_l1",
        f_scale=3.0, max_nfev=max_iterations, verbose=0,
    )
    after = float(np.sqrt((solution.fun**2).mean()))
    theta = solution.x[0::3]
    shift = np.stack([solution.x[1::3], solution.x[2::3]], axis=1)
    corrected = np.stack([
        make_pose(_rotation_about_up(theta[k]), np.array([shift[k, 0], 0.0, shift[k, 1]])) @ initial[k]
        for k in range(n)
    ])
    return corrected, before, after


def correct_drift(
    source: CaptureSource, keyframes: list[int], poses: np.ndarray, seed: int = 0
) -> DriftSolution:
    """Detect and correct accumulated drift over the keyframe trajectory."""
    clouds = load_keyframe_clouds(source, keyframes, seed=seed)
    candidates = propose_loops(poses, keyframes)
    closures = verify_loops(clouds, poses, keyframes, candidates)
    corrected, before, after = optimise_pose_graph(poses, keyframes, closures)

    original = np.stack([poses[k] for k in keyframes])
    shift = np.linalg.norm(corrected[:, :3, 3] - original[:, :3, 3], axis=1)

    report = DriftReport(
        method=(
            "keyframe pose graph over heading and horizontal position, ICP-verified loop closures, gauge-fixed least squares"
            if closures
            else "pose graph built; no loop closure passed ICP verification"
        ),
        loop_closures_found=len(closures),
        residual_before_m=before,
        residual_after_m=after,
        max_pose_correction_m=float(shift.max()) if len(shift) else 0.0,
        footprint_area_before_m2=None,
        footprint_area_after_m2=0.0,
        applied=bool(closures),
    )
    return DriftSolution(poses=corrected, report=report, closures=closures)


class PoseOverride:
    """A capture source that serves corrected poses in place of the recorded ones.

    The override is keyed by frame number, not by position in the keyframe list. A reader
    is free to skip a frame whose depth map failed to load, so zipping the yielded frames
    against the requested indices would silently pair a pose with the wrong frame from the
    first skip onward, and the resulting cloud would look plausible and be wrong.
    """

    def __init__(self, source: CaptureSource, keyframes: list[int], poses: np.ndarray):
        self._source = source
        self.meta = source.meta
        if hasattr(source, "frame_indices"):
            table = source.frame_indices()
            frame_numbers = [int(table[k]) for k in keyframes]
        else:
            frame_numbers = [int(k) for k in keyframes]
        self._override = {number: poses[i] for i, number in enumerate(frame_numbers)}

    def frames(self, indices: list[int] | None = None):
        for frame in self._source.frames(indices):
            replacement = self._override.get(int(frame.index))
            if replacement is not None:
                frame = Frame(**{**frame.__dict__, "pose": replacement})
            yield frame

    def poses(self) -> np.ndarray:
        return self._source.poses()

    def load_rgb(self, frame):
        return self._source.load_rgb(frame)

    def __getattr__(self, name):
        return getattr(self._source, name)
