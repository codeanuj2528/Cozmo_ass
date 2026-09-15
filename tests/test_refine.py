"""Room outline corrections: stairwells, rooms seen from their doorway, walled spaces nobody saw into.

Each test builds the evidence rasters by hand, so what is being checked is the rule and not the
scan it was found on.
"""

from __future__ import annotations

import numpy as np
from shapely.geometry import Point, box

from cozmo.geometry.grid import Grid2D
from cozmo.geometry.occupancy import OccupancyMaps
from cozmo.geometry.refine import refine_rooms, trim_open_ends
from cozmo.geometry.walls import WallSegment

RES = 0.03


def _maps(width_m: float = 8.0, depth_m: float = 8.0) -> OccupancyMaps:
    grid = Grid2D(origin=np.array([0.0, 0.0]), resolution=RES, shape=(int(depth_m / RES), int(width_m / RES)))
    empty = lambda dtype=bool: np.zeros(grid.shape, dtype)  # noqa: E731
    return OccupancyMaps(
        grid=grid, free_log_odds=empty(np.float32), wall_weight=empty(np.float32),
        traversable_hits=empty(np.float32), camera_track=np.zeros((0, 2)), observed=empty(),
        floor_hits=empty(), structural_hits=empty(), ceiling_hits=empty(),
        drop_hits=empty(), deep_drop_hits=empty(), surface_hits=empty(), wall_point_hits=empty(),
    )


def _fill(mask: np.ndarray, x0: float, z0: float, x1: float, z1: float, value: bool = True) -> None:
    mask[int(round(z0 / RES)):int(round(z1 / RES)), int(round(x0 / RES)):int(round(x1 / RES))] = value


def _wall(x0: float, z0: float, x1: float, z1: float, inward: tuple[float, float]) -> WallSegment:
    start, end = np.array([x0, z0]), np.array([x1, z1])
    direction = (end - start) / np.linalg.norm(end - start)
    return WallSegment(
        plane=None, direction=direction, normal_xz=np.array(inward, float), start=start, end=end,
        height_low=0.0, height_high=2.4, support_weight=1.0, point_indices=np.zeros(0, dtype=int),
    )


def _refine(room, occ, walls=()):
    kept, notes = refine_rooms({0: room}, occ, list(walls), min_room_area_m2=1.5, min_inscribed_radius_m=0.33)
    return kept.get(0), notes[0]


def test_a_corridor_ends_where_its_walls_end():
    corridor = box(1.0, 1.0, 2.2, 7.0)
    walls = [_wall(1.0, 1.0, 1.0, 3.5, (1, 0)), _wall(2.2, 1.0, 2.2, 3.5, (-1, 0))]
    trimmed = trim_open_ends(corridor, walls)
    assert 3.4 <= trimmed.bounds[3] <= 3.65
    assert abs(trimmed.bounds[2] - 2.2) < 1e-6 and abs(trimmed.bounds[0] - 1.0) < 1e-6


def test_a_doorway_on_both_sides_does_not_cut_a_room():
    room = box(1.0, 1.0, 4.0, 6.0)
    walls = [
        _wall(1.0, 1.0, 1.0, 3.0, (1, 0)), _wall(1.0, 4.2, 1.0, 6.0, (1, 0)),
        _wall(4.0, 1.0, 4.0, 3.0, (-1, 0)), _wall(4.0, 4.2, 4.0, 6.0, (-1, 0)),
        _wall(1.0, 1.0, 4.0, 1.0, (0, 1)), _wall(1.0, 6.0, 4.0, 6.0, (0, -1)),
    ]
    assert abs(trim_open_ends(room, walls).area - room.area) < 1e-6


def test_a_stairwell_is_taken_out_of_the_hall_without_leaving_a_strip_by_the_wall():
    occ = _maps()
    _fill(occ.floor_hits, 1.0, 1.0, 5.0, 5.0)
    _fill(occ.floor_hits, 3.0, 2.0, 4.92, 4.0, False)
    _fill(occ.drop_hits, 3.0, 2.0, 4.92, 4.0)
    _fill(occ.deep_drop_hits, 3.5, 2.5, 4.5, 3.5)
    hall = box(1.0, 1.0, 5.0, 5.0)
    refined, notes = _refine(hall, occ)
    assert refined is not None
    assert not refined.contains(Point(4.0, 3.0))
    assert not refined.contains(Point(4.97, 3.0)), "an 8 cm strip between the well and the wall is not floor"
    assert 11.5 < refined.area < 12.5
    assert any("stairwell" in n for n in notes)


def test_a_shower_tray_is_not_a_stairwell():
    occ = _maps()
    _fill(occ.floor_hits, 1.0, 1.0, 4.0, 3.0)
    _fill(occ.drop_hits, 2.0, 1.5, 3.0, 2.5)
    bathroom = box(1.0, 1.0, 4.0, 3.0)
    refined, notes = _refine(bathroom, occ)
    assert refined is not None and abs(refined.area - bathroom.area) < 0.02
    assert notes == []


def test_a_walled_box_nobody_saw_into_is_removed_but_an_unseen_open_patch_is_not():
    for walled in (True, False):
        occ = _maps()
        _fill(occ.floor_hits, 0.0, 0.0, 4.0, 3.0)
        _fill(occ.floor_hits, 2.5, 0.3, 3.7, 1.7, False)
        if walled:
            _fill(occ.wall_point_hits, 2.45, 0.25, 3.75, 0.35)
            _fill(occ.wall_point_hits, 2.45, 1.65, 3.75, 1.75)
            _fill(occ.wall_point_hits, 2.45, 0.25, 2.55, 1.75)
            _fill(occ.wall_point_hits, 3.65, 0.25, 3.75, 1.75)
        room = box(0.0, 0.0, 4.0, 3.0)
        refined, notes = _refine(room, occ)
        assert refined is not None
        if walled:
            assert not refined.contains(Point(3.1, 1.0))
            assert room.area - refined.area > 1.0
        else:
            assert abs(refined.area - room.area) < 0.02 and notes == []


def test_a_slit_with_floor_in_it_is_filled_and_a_partition_is_not():
    for partition in (False, True):
        occ = _maps()
        _fill(occ.floor_hits, 0.0, 0.0, 4.0, 3.0)
        # An 8 cm slit reaching 1.5 m into the room from its top wall, as two nearly coincident
        # wall lines leave it.
        room = box(0.0, 0.0, 4.0, 3.0).difference(box(1.96, 1.5, 2.04, 3.0))
        if partition:
            _fill(occ.floor_hits, 1.93, 1.5, 2.07, 3.0, False)
            _fill(occ.wall_point_hits, 1.93, 1.5, 2.07, 3.0)
        refined, notes = _refine(room, occ)
        assert refined is not None
        if partition:
            assert abs(refined.area - room.area) < 0.01, "a partition the scan saw must stay in the outline"
        else:
            assert abs(refined.area - 12.0) < 0.02
            assert any("slit" in n for n in notes)


def test_a_filled_slit_never_overlaps_the_next_room():
    occ = _maps()
    _fill(occ.floor_hits, 0.0, 0.0, 6.0, 4.0)
    tongue = box(1.46, 2.0, 1.54, 3.0)
    room = box(0.0, 0.0, 3.0, 3.0).difference(tongue)
    # The neighbour above reaches down into the slit, so the slit is its floor and not this room's.
    neighbour = box(0.0, 3.0, 6.0, 4.0).union(tongue)
    kept, notes = refine_rooms({0: room, 1: neighbour}, occ, [], min_room_area_m2=0.5, min_inscribed_radius_m=0.2)
    assert kept[0].intersection(kept[1]).area < 1e-3, "the fill must not reach into the neighbouring room"
    assert not any("slit" in n for n in notes[0])


def test_the_refinement_runs_on_lidar_and_not_on_monocular_depth(tmp_path, monkeypatch):
    """A monocular depth map cannot show that floor is absent, so photo and video rooms stay as segmented.

    On the 1x hall photos the rules took the hall from 35.12 to 2.11 m2.
    """
    import cozmo.pipeline.lidar as lidar
    from cozmo.config import PipelineConfig
    from cozmo.io import load_capture
    from cozmo.schema import Tier
    from tests.fixtures.raytrace_room import write_capture

    calls = []
    real = lidar.refine_rooms

    def recording(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(lidar, "refine_rooms", recording)
    config = PipelineConfig(detect_damage=False, build_scope=False, max_keyframes=16)
    root = write_capture(tmp_path / "room", drop_ceiling=False)

    lidar.build_lidar_plan(load_capture(root), config)
    assert len(calls) == 1

    source = load_capture(root)
    source.meta.tier = Tier.PHOTO
    lidar.build_lidar_plan(source, config)
    assert len(calls) == 1
