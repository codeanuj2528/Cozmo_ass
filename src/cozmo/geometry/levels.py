"""Floor and ceiling extraction, and the gravity refinement that has to precede it.

ARKit's gravity estimate is good but not exact. A residual tilt of one degree tips a 5 m
room by 8.7 cm end to end, which on its own would blow the 1.5 cm ceiling-height gate
before any other error source is considered. So the first thing this module does is refit
the world's up axis to the observed floor plane and rotate the cloud onto it. Everything
downstream, including the wall verticality test and the height band used for wall
evidence, assumes that correction has already been applied.

Floor and ceiling are found as modes of a weighted height histogram restricted by normal
direction, not as the minimum and maximum of the cloud. Extremes are where the stray
returns live: a single point that leaked under a door would otherwise set the floor.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.ndimage import gaussian_filter1d

from cozmo.geometry.fusion import FusedCloud
from cozmo.geometry.planes import PlaneFit, fit_plane, refit_with_inliers
from cozmo.util.transforms import UP

HORIZONTAL_NORMAL_TOLERANCE_RAD = np.deg2rad(20.0)
HISTOGRAM_BIN_M = 0.01
# A downward-facing surface less than 2.20 m above the floor it is measured from is not a
# ceiling, whatever else it is: a seven-foot door head sits at 2.13 m, and lofts, window heads
# and soffits lower still. Under the previous 1.6 m bound the long walk of the benchmark flat
# published a window bay with a 1.860 m ceiling. The cost is a real ceiling below 2.20 m,
# which abstains rather than reporting a wrong number.
MIN_CEILING_CLEARANCE_M = 2.20
MAX_CEILING_CLEARANCE_M = 4.5
# Downward-facing surfaces more than this far above the floor are overhead structure: lofts,
# door and window heads, soffits, beams, wall-cabinet undersides. Below it are the undersides
# of tables and counters, which say nothing about where the ceiling is.
OVERHEAD_FROM_M = 1.0
# The smallest patch of returns a ceiling height is read from, counted in 10 cm cells so that
# scattered points do not add up to an area. A light fitting or a fan hub is smaller.
MIN_CEILING_SUPPORT_M2 = 0.25
SUPPORT_CELL_M = 0.10


@dataclass
class LevelEstimate:
    floor: PlaneFit
    ceiling: PlaneFit | None
    floor_height: float
    ceiling_height: float | None
    height: float | None
    sigma_height: float | None
    ceiling_coverage: float
    warnings: list[str]


def _weighted_histogram(values: np.ndarray, weights: np.ndarray, bin_m: float):
    lo, hi = float(values.min()), float(values.max())
    if hi - lo < bin_m:
        hi = lo + bin_m
    bins = np.arange(lo, hi + bin_m, bin_m)
    hist, edges = np.histogram(values, bins=bins, weights=weights)
    centres = 0.5 * (edges[:-1] + edges[1:])
    return gaussian_filter1d(hist, sigma=1.5), centres


def _modes(hist: np.ndarray, centres: np.ndarray, min_fraction: float) -> list[tuple[float, float]]:
    """Local maxima above a fraction of the strongest peak, as (height, strength)."""
    if hist.size == 0:
        return []
    threshold = hist.max() * min_fraction
    out: list[tuple[float, float]] = []
    for i in range(1, len(hist) - 1):
        if hist[i] >= hist[i - 1] and hist[i] > hist[i + 1] and hist[i] >= threshold:
            out.append((float(centres[i]), float(hist[i])))
    if not out and hist.max() > 0:
        i = int(np.argmax(hist))
        out.append((float(centres[i]), float(hist[i])))
    return out


def _horizontal_mask(cloud: FusedCloud, facing: int) -> np.ndarray:
    """Points on horizontal surfaces. `facing` is +1 for upward, -1 for downward normals."""
    cos_tol = np.cos(HORIZONTAL_NORMAL_TOLERANCE_RAD)
    ny = cloud.normals[:, 1]
    return (ny * facing) > cos_tol


def _level_at(plane: PlaneFit, reference_xz: np.ndarray) -> float:
    """Height of a plane above one horizontal location."""
    n = plane.normal
    return float(-(plane.offset + n[0] * reference_xz[0] + n[2] * reference_xz[1]) / n[1])


def _support_area(points_xz: np.ndarray, cell_m: float = SUPPORT_CELL_M) -> float:
    """Plan area the returns actually cover, counted in occupied cells."""
    if len(points_xz) == 0:
        return 0.0
    cells = np.unique(np.floor(points_xz / cell_m).astype(np.int64), axis=0)
    return float(len(cells) * cell_m * cell_m)


def refine_gravity(cloud: FusedCloud) -> tuple[np.ndarray, PlaneFit, list[str]]:
    """Rotation that puts the observed floor normal on +y, plus the floor plane it used."""
    warnings: list[str] = []
    up_mask = _horizontal_mask(cloud, +1)
    if up_mask.sum() < 200:
        warnings.append("too few upward-facing points to refine gravity; using sensor gravity")
        return np.eye(3), fit_plane(cloud.points[up_mask] if up_mask.sum() >= 3 else cloud.points,
                                    cloud.weight[up_mask] if up_mask.sum() >= 3 else cloud.weight), warnings

    heights = cloud.points[up_mask, 1]
    weights = cloud.weight[up_mask]
    hist, centres = _weighted_histogram(heights, weights, HISTOGRAM_BIN_M)
    modes = _modes(hist, centres, min_fraction=0.10)
    floor_level = min(m[0] for m in modes)

    near = np.abs(heights - floor_level) < 0.06
    if near.sum() < 50:
        warnings.append("weak floor support; gravity left as sensor reported")
        return np.eye(3), fit_plane(cloud.points[up_mask][near] if near.sum() >= 3 else cloud.points[up_mask],
                                    weights[near] if near.sum() >= 3 else weights), warnings

    plane = fit_plane(cloud.points[up_mask][near], weights[near]).flip_to(UP)
    plane, inliers = refit_with_inliers(
        cloud.points[up_mask], weights, plane, threshold_m=0.04, iterations=3
    )
    plane = plane.flip_to(UP)

    tilt = float(np.arccos(np.clip(plane.normal @ UP, -1.0, 1.0)))
    if tilt > np.deg2rad(6.0):
        warnings.append(f"floor plane tilted {np.rad2deg(tilt):.1f} deg from sensor gravity; not applied")
        return np.eye(3), plane, warnings

    axis = np.cross(plane.normal, UP)
    s = float(np.linalg.norm(axis))
    if s < 1e-9:
        return np.eye(3), plane, warnings
    axis /= s
    angle = tilt
    kx = np.array([[0, -axis[2], axis[1]], [axis[2], 0, -axis[0]], [-axis[1], axis[0], 0]])
    rotation = np.eye(3) + np.sin(angle) * kx + (1 - np.cos(angle)) * (kx @ kx)
    _ = inliers
    return rotation, plane, warnings


def detect_levels(
    cloud: FusedCloud,
    footprint_area_m2: float | None = None,
    reference_xz: np.ndarray | None = None,
) -> LevelEstimate:
    """Floor and ceiling planes of an already gravity-corrected cloud.

    Both levels are read above one horizontal location, by default the centre of the
    observed floor. A plane's offset is its height where it crosses the world origin, and a
    room is often metres from there: 1.5 degrees of fitted tilt on a floor patch 6 m out
    moves its level by 16 cm, and a floor and a ceiling tilted differently gain or lose that
    much height between them. On the long walk of the benchmark flat, reading room levels at
    the origin turned a steeply tilted patch over a window ledge into a 3.04 m ceiling and
    moved a passage ceiling by 15 cm.

    `reference_xz` overrides the location. The whole-property levels in `pipeline/lidar.py`
    pass the world origin, and say why there.
    """
    warnings: list[str] = []

    up_mask = _horizontal_mask(cloud, +1)
    if up_mask.sum() < 50:
        raise ValueError("no floor surface observed")

    floor_heights = cloud.points[up_mask, 1]
    hist, centres = _weighted_histogram(floor_heights, cloud.weight[up_mask], HISTOGRAM_BIN_M)
    floor_mode = min(m[0] for m in _modes(hist, centres, min_fraction=0.08))
    near_floor = np.abs(floor_heights - floor_mode) < 0.05
    floor_points = cloud.points[up_mask][near_floor]
    floor_weights = cloud.weight[up_mask][near_floor]
    floor_plane = fit_plane(floor_points, floor_weights).flip_to(UP)
    if reference_xz is None:
        reference_xz = np.average(floor_points[:, [0, 2]], axis=0, weights=np.maximum(floor_weights, 1e-12))
    floor_level = _level_at(floor_plane, reference_xz)

    ceiling_plane: PlaneFit | None = None
    ceiling_level: float | None = None
    height: float | None = None
    sigma_height: float | None = None
    coverage = 0.0
    unmeasured = "no ceiling surface observed"

    above_floor = cloud.points[:, 1] - floor_level
    overhead = (
        _horizontal_mask(cloud, -1)
        & (above_floor > OVERHEAD_FROM_M)
        & (above_floor < MAX_CEILING_CLEARANCE_M)
    )

    if overhead.sum() >= 50:
        chist, ccentres = _weighted_histogram(above_floor[overhead], cloud.weight[overhead], HISTOGRAM_BIN_M)
        # The ceiling is the highest strong mode, and strength is judged against every
        # downward-facing surface overhead. Lower strong modes are soffits, beams, lofts and
        # door heads, which are real but are not the ceiling. Judged only against what clears
        # the height bound, a trace of returns above a loft or a window head is the strongest
        # thing left and would be published as the ceiling.
        strong = [
            m for m in _modes(chist, ccentres, min_fraction=0.20) if m[0] > MIN_CEILING_CLEARANCE_M
        ]
        if not strong:
            unmeasured = (
                f"no strong downward-facing surface more than {MIN_CEILING_CLEARANCE_M:.2f} m "
                "above the floor"
            )
        else:
            ceiling_above_floor = max(m[0] for m in strong)
            near_ceiling = overhead & (np.abs(above_floor - ceiling_above_floor) < 0.05)
            support = _support_area(cloud.points[near_ceiling][:, [0, 2]])
            if near_ceiling.sum() < 20 or support < MIN_CEILING_SUPPORT_M2:
                unmeasured = (
                    f"ceiling returns cover {support:.2f} m2, under the "
                    f"{MIN_CEILING_SUPPORT_M2:.2f} m2 a height is read from"
                )
            else:
                ceiling_plane = fit_plane(
                    cloud.points[near_ceiling], cloud.weight[near_ceiling]
                ).flip_to(-UP)
                ceiling_level = _level_at(ceiling_plane, reference_xz)

                height = float(ceiling_level - floor_level)
                # Propagate both plane offsets, plus a term for the two planes not being
                # exactly parallel evaluated over the room's own extent.
                span = float(np.ptp(cloud.points[near_ceiling][:, [0, 2]], axis=0).max())
                tilt = float(np.arccos(np.clip(abs(ceiling_plane.normal @ floor_plane.normal), -1.0, 1.0)))
                sigma_height = float(
                    np.sqrt(
                        floor_plane.sigma_offset**2
                        + ceiling_plane.sigma_offset**2
                        + (0.5 * span * np.tan(tilt)) ** 2
                    )
                )
                # Both planes are fitted to voxel-averaged points, so neither offset is known
                # better than the voxel's own quantisation, pitch / sqrt(12). Without that floor
                # a noiseless ray-traced room reported a ceiling interval of 0.2 mm and missed its
                # true height by 0.8 mm. On real captures the plane scatter is already larger.
                quantisation = np.sqrt(2.0) * cloud.voxel_m / np.sqrt(12.0)
                sigma_height = max(sigma_height, float(quantisation))
                if footprint_area_m2 and footprint_area_m2 > 0:
                    coverage = float(np.clip(support / footprint_area_m2, 0.0, 1.0))

    if ceiling_plane is None:
        warnings.append(f"{unmeasured}; ceiling height is unmeasured at this capture")
    elif coverage and coverage < 0.15:
        warnings.append(f"ceiling observed over only {coverage:.0%} of the footprint")

    return LevelEstimate(
        floor=floor_plane,
        ceiling=ceiling_plane,
        floor_height=floor_level,
        ceiling_height=ceiling_level,
        height=height,
        sigma_height=sigma_height,
        ceiling_coverage=coverage,
        warnings=warnings,
    )
