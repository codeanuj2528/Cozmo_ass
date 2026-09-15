"""Rooms from wall barriers, doorways and interior evidence (geometry/layout.py).

Each test draws the evidence the layout reads, on a 3 cm raster, for the one situation it covers:
a door in a wall, a corridor crossing a wall line, a door beside a corner, an outline that must land
on the wall faces rather than on the raster, and a doorway measured jamb to jamb.
"""

from __future__ import annotations

import numpy as np
import pytest

from cozmo.geometry.fusion import FusedCloud
from cozmo.geometry.grid import Grid2D
from cozmo.geometry.layout import (
    Bridge,
    WallLine,
    _refine_doorway,
    is_corridor_crossing,
    line_bridges,
    raster_bridges,
    rectilinear_outline,
)

RES = 0.03


def _grid(width_m: float = 8.0, depth_m: float = 6.0) -> Grid2D:
    return Grid2D(origin=np.array([0.0, 0.0]), resolution=RES, shape=(int(depth_m / RES), int(width_m / RES)))


def _paint(mask: np.ndarray, grid: Grid2D, x0: float, z0: float, x1: float, z1: float) -> None:
    c0, c1 = int(round((x0 - grid.origin[0]) / RES)), int(round((x1 - grid.origin[0]) / RES))
    r0, r1 = int(round((z0 - grid.origin[1]) / RES)), int(round((z1 - grid.origin[1]) / RES))
    mask[min(r0, r1):max(r0, r1), min(c0, c1):max(c0, c1)] = True


def _two_rooms(gap: tuple[float, float] | None):
    grid = _grid()
    barrier = np.zeros(grid.shape, bool)
    _paint(barrier, grid, 0.5, 0.5, 7.5, 0.62)
    _paint(barrier, grid, 0.5, 5.38, 7.5, 5.5)
    _paint(barrier, grid, 0.5, 0.5, 0.62, 5.5)
    _paint(barrier, grid, 7.38, 0.5, 7.5, 5.5)
    if gap is None:
        _paint(barrier, grid, 0.5, 3.0, 7.5, 3.12)
    else:
        _paint(barrier, grid, 0.5, 3.0, gap[0], 3.12)
        _paint(barrier, grid, gap[1], 3.0, 7.5, 3.12)
    interior = np.zeros(grid.shape, bool)
    _paint(interior, grid, 0.62, 0.62, 7.38, 5.38)
    return grid, barrier, interior & ~barrier


def test_a_door_in_a_wall_is_bridged_at_its_width():
    grid, barrier, interior = _two_rooms(gap=(3.00, 3.85))
    bridges = [b for b in raster_bridges(barrier, interior, grid) if b.kind == "H"]
    assert len(bridges) == 1
    assert bridges[0].width == pytest.approx(0.85, abs=0.06)
    assert bridges[0].coord == pytest.approx(3.06, abs=0.08)


def test_a_corridor_between_two_walls_is_not_bridged_across():
    grid = _grid()
    barrier = np.zeros(grid.shape, bool)
    _paint(barrier, grid, 3.0, 0.5, 3.12, 5.5)
    _paint(barrier, grid, 4.0, 0.5, 4.12, 5.5)
    interior = np.ones(grid.shape, bool) & ~barrier
    across = [b for b in raster_bridges(barrier, interior, grid) if b.kind == "H" and b.lo < 3.2 and b.hi > 3.9]
    assert across == []


def test_a_wall_line_across_a_corridor_is_a_crossing_and_a_door_is_not():
    grid = _grid()
    crossing = np.zeros(grid.shape, bool)
    _paint(crossing, grid, 0.5, 3.0, 3.0, 3.12)
    _paint(crossing, grid, 4.0, 3.0, 7.5, 3.12)
    _paint(crossing, grid, 2.88, 0.5, 3.0, 5.5)
    _paint(crossing, grid, 4.0, 0.5, 4.12, 5.5)
    assert is_corridor_crossing(Bridge("H", 3.06, 3.0, 4.0, "test"), crossing, grid)

    _, door, _ = _two_rooms(gap=(3.0, 3.85))
    assert not is_corridor_crossing(Bridge("H", 3.06, 3.0, 3.85, "test"), door, grid)


def test_line_bridges_close_a_gap_in_a_wall_and_a_door_beside_a_corner():
    lines = [
        WallLine("H", 3.0, 0.5, 3.0, -1.0, 1.0),
        WallLine("H", 3.0, 3.85, 7.5, -1.0, 1.0),
        WallLine("H", 1.0, 0.5, 2.0, 1.0, 1.0),
        WallLine("V", 2.9, 0.0, 2.0, -1.0, 1.0),
    ]
    bridges = line_bridges(lines)
    gap = [b for b in bridges if b.kind == "H" and abs(b.coord - 3.0) < 1e-6 and abs(b.lo - 3.0) < 1e-6]
    assert len(gap) == 1 and gap[0].hi == pytest.approx(3.85)
    corner = [b for b in bridges if b.kind == "H" and abs(b.coord - 1.0) < 1e-6]
    assert len(corner) == 1
    assert (corner[0].lo, corner[0].hi) == pytest.approx((2.0, 2.9))


def test_an_outline_lands_on_the_wall_faces_not_on_the_raster():
    grid = _grid()
    mask = np.zeros(grid.shape, bool)
    # The room's floor evidence stops a cell or two short of each wall face.
    _paint(mask, grid, 1.06, 1.06, 3.93, 2.94)
    lines = [
        WallLine("V", 1.00, 0.9, 3.1, 1.0, 5.0),
        WallLine("V", 4.00, 0.9, 3.1, -1.0, 5.0),
        WallLine("H", 1.00, 0.9, 4.1, 1.0, 5.0),
        WallLine("H", 3.00, 0.9, 4.1, -1.0, 5.0),
    ]
    outline = rectilinear_outline(mask, grid, lines)
    assert outline.bounds == pytest.approx((1.00, 1.00, 4.00, 3.00), abs=1e-6)
    assert outline.area == pytest.approx(6.0, abs=1e-6)


def test_an_outline_does_not_take_the_face_of_the_wall_behind_the_room():
    grid = _grid()
    mask = np.zeros(grid.shape, bool)
    _paint(mask, grid, 1.06, 1.06, 3.93, 2.94)
    # The far face of the partition on the left points away from this room and must not be used.
    lines = [WallLine("V", 0.94, 0.9, 3.1, -1.0, 5.0)]
    outline = rectilinear_outline(mask, grid, lines)
    assert outline.bounds[0] == pytest.approx(1.05, abs=0.02)


def test_a_doorway_is_measured_jamb_to_jamb_and_its_head_is_found():
    rng = np.random.default_rng(0)
    xs, ys = np.meshgrid(np.arange(2.0, 5.0, 0.01), np.arange(0.30, 2.40, 0.02))
    xs, ys = xs.ravel(), ys.ravel()
    wall = (xs < 3.00) | (xs > 3.85) | (ys >= 2.05)
    xs, ys = xs[wall], ys[wall]
    zs = 3.0 + rng.normal(0.0, 0.004, len(xs))
    points = np.stack([xs, ys, zs], axis=1).astype(np.float32)
    n = len(points)
    normals = np.tile(np.array([0.0, 0.0, -1.0], np.float32), (n, 1))
    cloud = FusedCloud(points, normals, np.full(n, 0.01, np.float32), np.ones(n, np.float32),
                       normals.copy(), np.ones(n, np.float32), np.zeros(n, np.int32), 0.02)
    lo, hi, width, sigma, source, head = _refine_doorway(Bridge("H", 3.0, 2.97, 3.90, "test"), cloud, floor_y=0.0)
    assert width == pytest.approx(0.85, abs=0.02)
    assert "jamb" in source
    assert head == pytest.approx(2.05, abs=0.03)
