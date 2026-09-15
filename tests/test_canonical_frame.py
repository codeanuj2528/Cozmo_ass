"""The canonical frame puts the walls on the grid axes.

Every raster in the pipeline is axis-aligned, so a wall at 45 degrees to it is quantised as a
staircase. The rotation meant to prevent that doubled the wall angle instead of removing it.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from cozmo.geometry.planes import PlaneFit
from cozmo.geometry.walls import WallSegment, canonical_rotation


def _segment(angle_deg: float, length: float = 3.0) -> WallSegment:
    phi = np.deg2rad(angle_deg)
    direction = np.array([np.cos(phi), np.sin(phi)])
    normal = np.array([-direction[1], direction[0]])
    plane = PlaneFit(np.array([normal[0], 0.0, normal[1]]), 0.0, 0.001, 0.001, 100, 0.002, 100.0)
    return WallSegment(
        plane=plane,
        direction=direction,
        normal_xz=normal,
        start=np.zeros(2),
        end=direction * length,
        height_low=0.1,
        height_high=2.4,
        support_weight=1.0,
        point_indices=np.zeros(0, dtype=int),
    )


def _residual_deg(direction_xz: np.ndarray, rotation: np.ndarray) -> float:
    d = np.array([direction_xz[0], 0.0, direction_xz[1]]) @ rotation.T
    angle = np.degrees(np.arctan2(d[2], d[0])) % 90.0
    return float(min(angle, 90.0 - angle))


@pytest.mark.parametrize("angle", [0.0, 10.0, 23.0, 27.0, 44.0, 61.0, 89.0])
def test_canonical_rotation_puts_walls_on_the_axes(angle: float):
    segments = [_segment(angle), _segment(angle + 90.0, 2.0)]
    rotation = canonical_rotation(segments)
    for seg in segments:
        assert _residual_deg(seg.direction, rotation) < 0.5


def test_raytraced_room_is_drawn_on_the_axes(tmp_path: Path):
    """The ray-traced room is yawed 23 degrees; its plan must come out square to the grid."""
    from cozmo.config import PipelineConfig
    from cozmo.io import load_capture
    from cozmo.pipeline import reconstruct
    from tests.fixtures.raytrace_room import write_capture

    root = write_capture(tmp_path / "room", drop_ceiling=False)
    config = PipelineConfig(detect_damage=False, build_scope=False, max_keyframes=16)
    plan = reconstruct(load_capture(root), config=config).plan
    ring = np.asarray(plan.rooms[0].polygon)
    edges = np.diff(np.vstack([ring, ring[:1]]), axis=0)
    long_edges = edges[np.hypot(edges[:, 0], edges[:, 1]) > 0.5]
    residual = np.degrees(np.arctan2(long_edges[:, 1], long_edges[:, 0])) % 90.0
    assert np.all(np.minimum(residual, 90.0 - residual) < 1.0)
