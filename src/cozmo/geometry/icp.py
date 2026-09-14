"""Point-to-plane ICP.

Point-to-plane rather than point-to-point because indoor scenes are made of large flat
surfaces. Point-to-point asks two scans of the same wall to agree on which point is which,
which they cannot, and the residual it minimises is dominated by the sliding of one wall
across itself. Point-to-plane only penalises motion along the surface normal, which is the
only component either scan actually observed, and it converges in a handful of iterations
where point-to-point crawls.

The linearisation is the standard small-angle one. It is valid because ICP is only ever
asked to close a gap the pose graph has already brought within a few centimetres and a few
degrees; the caller is responsible for not handing it a wilder initial guess than that.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.spatial import cKDTree

from cozmo.util.transforms import make_pose


@dataclass
class IcpResult:
    transform: np.ndarray
    fitness: float
    inlier_rmse: float
    correspondences: int
    converged: bool


def point_to_plane_icp(
    source: np.ndarray,
    target: np.ndarray,
    target_normals: np.ndarray,
    initial: np.ndarray | None = None,
    max_correspondence_m: float = 0.15,
    iterations: int = 20,
    tolerance: float = 1e-5,
) -> IcpResult:
    """Align `source` onto `target`, returning the transform that maps source into target."""
    if len(source) < 20 or len(target) < 20:
        return IcpResult(np.eye(4), 0.0, np.inf, 0, False)

    tree = cKDTree(target)
    transform = np.eye(4) if initial is None else initial.copy()
    previous = np.inf
    fitness = 0.0
    rmse = np.inf
    matched = 0

    for _ in range(iterations):
        moved = source @ transform[:3, :3].T + transform[:3, 3]
        distance, index = tree.query(moved, distance_upper_bound=max_correspondence_m)
        valid = np.isfinite(distance)
        matched = int(valid.sum())
        if matched < 15:
            return IcpResult(transform, 0.0, np.inf, matched, False)

        p = moved[valid]
        q = target[index[valid]]
        n = target_normals[index[valid]]

        residual = np.einsum("ij,ij->i", p - q, n)
        # Rows of [p x n, n] are the derivative of the point-to-plane residual with
        # respect to (rotation vector, translation) at the identity.
        jac = np.hstack([np.cross(p, n), n])
        try:
            solution = np.linalg.lstsq(jac, -residual, rcond=None)[0]
        except np.linalg.LinAlgError:
            return IcpResult(transform, 0.0, np.inf, matched, False)

        omega, translation = solution[:3], solution[3:]
        angle = float(np.linalg.norm(omega))
        if angle < 1e-12:
            rotation = np.eye(3)
        else:
            axis = omega / angle
            kx = np.array([[0, -axis[2], axis[1]], [axis[2], 0, -axis[0]], [-axis[1], axis[0], 0]])
            rotation = np.eye(3) + np.sin(angle) * kx + (1 - np.cos(angle)) * (kx @ kx)
        step = make_pose(rotation, translation)
        transform = step @ transform

        rmse = float(np.sqrt((residual**2).mean()))
        fitness = matched / len(source)
        if abs(previous - rmse) < tolerance:
            return IcpResult(transform, fitness, rmse, matched, True)
        previous = rmse

    # Running out of iterations is not convergence. A match still moving after the last step is
    # usually sliding along a wall: on the assignment's floor-only scan one such match fit 99.9%
    # of its points but asked for 63 cm of drift, where a settled match three keyframes later
    # asked for 1.4 cm.
    return IcpResult(transform, fitness, rmse, matched, False)
