"""Tests for multi-room stitching, pose graph optimization, and drift correction."""

from __future__ import annotations

import numpy as np
import pytest

from cozmo.geometry.drift import correct_drift, propose_loops


def _synthetic_loop_trajectory(n_frames: int = 100):
    """Generate a trajectory walking out and returning to origin with drift."""
    poses = []
    half = n_frames // 2
    for i in range(n_frames):
        pose = np.eye(4)
        if i < half:
            tx = i * 0.05
            tz = 0.0
        else:
            steps_back = i - half
            tx = (half - steps_back) * 0.05
            tz = steps_back * 0.005  # Drift
        pose[:3, 3] = [tx, 1.2, tz]
        poses.append(pose)
    return np.array(poses)


def test_find_loop_closures():
    poses = _synthetic_loop_trajectory(100)
    keyframes = list(range(0, len(poses), 2))

    closures = propose_loops(poses, keyframes, radius_m=2.5, min_gap=10)
    assert isinstance(closures, list)


def test_no_loop_closure_one_way():
    poses = [np.eye(4) for _ in range(50)]
    for i, p in enumerate(poses):
        p[:3, 3] = [i * 0.1, 1.2, 0.0]
    kf_poses = np.array(poses)
    keyframes = list(range(50))

    closures = propose_loops(kf_poses, keyframes, radius_m=0.3, min_gap=15)
    assert len(closures) == 0


def _box_room(room_id: str, origin: tuple[float, float], size: tuple[float, float], area: float) -> Room:
    from cozmo.schema import Measure, Room

    x0, y0 = origin
    w, h = size
    return Room(
        room_id=room_id,
        label="room",
        polygon=[(x0, y0), (x0 + w, y0), (x0 + w, y0 + h), (x0, y0 + h)],
        walls=[],
        surfaces=[],
        openings=[],
        ceiling_height=None,
        floor_area=Measure(value=area, lo=area * 0.95, hi=area * 1.05, unit="m2"),
        perimeter=Measure(value=2 * (w + h), lo=2 * (w + h) - 0.1, hi=2 * (w + h) + 0.1, unit="m"),
        observation_quality=0.9,
    )


def test_folder_name_pairs_connect_through_hall():
    from cozmo.stitch.rooms import folder_name_pairs, stitch_property

    hall = _box_room("room_01", (0.0, 0.0), (3.0, 2.0), 6.0)
    hall = hall.model_copy(update={"label": "hall"})
    bath = _box_room("room_02", (10.0, 0.0), (2.0, 2.0), 4.0)
    bath = bath.model_copy(update={"label": "bathroom"})
    bed = _box_room("room_03", (20.0, 0.0), (4.0, 3.0), 12.0)
    bed = bed.model_copy(update={"label": "bedroom"})
    pairs = folder_name_pairs([hall, bath, bed])
    assert set(pairs) == {("room_01", "room_02"), ("room_01", "room_03")}

    placed, adjacency, warnings = stitch_property([hall, bath, bed])
    assert len(adjacency) == 2
    assert all(adj.confidence == pytest.approx(0.15) for adj in adjacency)
    assert any("folder-name" in adj.evidence for adj in adjacency)
    assert {r.room_id for r in placed} == {"room_01", "room_02", "room_03"}
    assert any("folder names" in w for w in warnings)


def test_merge_diagonal_splits_joins_fat_overlap_not_a_thin_wall():
    from shapely.geometry import box

    from cozmo.geometry.cellcomplex import _merge_diagonal_splits

    # Two halves of one L, overlapping in a fat 2.2 x 1.8 m region.
    left = box(0, 0, 4, 4)
    right = box(2, 2, 7, 6)
    merged = _merge_diagonal_splits({0: left, 1: right})
    assert len(merged) == 1

    # Two rooms that only overlap in a 3.0 x 0.12 m partition strip must stay two.
    a = box(0, 0, 3.06, 4)
    b = box(2.94, 0, 6, 4)
    stayed = _merge_diagonal_splits({0: a, 1: b})
    assert len(stayed) == 2


def test_merge_diagonal_splits_keeps_rooms_either_side_of_a_diagonal_wall():
    """Two rooms sharing a wall that runs at 40 degrees to the world axes stay two.

    Thinness used to be read from the overlap's axis-aligned bounding box, which calls a
    diagonal partition strip fat. The assignment's living room and bathroom were joined that way.
    """
    from shapely import affinity
    from shapely.geometry import box

    from cozmo.geometry.cellcomplex import _merge_diagonal_splits

    living = affinity.rotate(box(0, 0, 4.0, 3.0), 40, origin=(0, 0))
    bathroom = affinity.rotate(box(4.0, 0, 6.5, 3.0), 40, origin=(0, 0))
    assert len(_merge_diagonal_splits({0: living, 1: bathroom})) == 2

    left = affinity.rotate(box(0, 0, 4, 4), 40, origin=(0, 0))
    right = affinity.rotate(box(2, 2, 7, 6), 40, origin=(0, 0))
    assert len(_merge_diagonal_splits({0: left, 1: right})) == 1


def test_unwalked_strip_meeting_the_room_only_at_its_end_is_not_floor():
    from shapely.geometry import box

    from cozmo.geometry.cellcomplex import Face, _remove_appendage_strips

    room = Face(index=0, polygon=box(0, 0, 4, 3), evidence=0.9, track_cells=40, area=12.0, interior=True)
    spike = Face(index=1, polygon=box(4, 1.4, 6, 1.6), evidence=0.3, track_cells=0, area=0.4, interior=True)
    _remove_appendage_strips([room, spike])
    assert room.interior and not spike.interior

    left = Face(index=0, polygon=box(0, 0, 3, 3), evidence=0.9, track_cells=30, area=9.0, interior=True)
    partition = Face(index=1, polygon=box(3, 0, 3.2, 3), evidence=0.3, track_cells=0, area=0.6, interior=True)
    right = Face(index=2, polygon=box(3.2, 0, 6, 3), evidence=0.9, track_cells=20, area=8.4, interior=True)
    _remove_appendage_strips([left, partition, right])
    assert partition.interior, "a sliver with floor on both long sides is part of the room"


def test_loop_closure_moves_heading_and_position_but_not_height_or_tilt():
    """A closure whose ICP slid vertically must not lift or tip the trajectory.

    ICP between two keyframes that mostly see ceiling or blank wall is barely constrained in
    height. On the assignment's with-ceiling scan, closures like that lowered the last part of
    the walk by about 40 cm. Height and tilt are referenced to gravity and stay as measured.
    """
    from scipy.spatial.transform import Rotation

    from cozmo.geometry.drift import LoopClosure, optimise_pose_graph
    from cozmo.util.transforms import invert_pose, make_pose

    per_lap = 120
    truth, odometry = [], []
    yaw_drift, position_drift = 0.0, np.zeros(3)
    for k in range(2 * per_lap):
        s = (k % per_lap) / per_lap * 20.0
        if s < 6:
            x, z, heading = s, 0.0, 0.0
        elif s < 10:
            x, z, heading = 6.0, s - 6.0, np.pi / 2
        elif s < 16:
            x, z, heading = 16.0 - s, 4.0, np.pi
        else:
            x, z, heading = 0.0, 20.0 - s, -np.pi / 2
        pitch = np.deg2rad(60.0 * np.sin(k / 7.0))
        pose = make_pose(Rotation.from_euler("YX", [heading, pitch]).as_matrix(), np.array([x, 1.4, z]))
        if k:
            yaw_drift += 0.0009
            position_drift += np.array([0.004, 0.0, -0.003])
        truth.append(pose)
        odometry.append(make_pose(Rotation.from_euler("Y", yaw_drift).as_matrix(), position_drift) @ pose)
    truth, odometry = np.stack(truth), np.stack(odometry)

    closures = []
    for i in range(0, per_lap, 10):
        slid = truth[i + per_lap].copy()
        slid[1, 3] += 0.30
        closures.append(LoopClosure(i=i, j=i + per_lap, transform=invert_pose(truth[i]) @ slid, fitness=0.8, rmse=0.01))

    corrected, before, after = optimise_pose_graph(odometry, list(range(len(odometry))), closures)

    def seam(poses):
        return np.mean([
            np.linalg.norm((poses[c.j, [0, 2], 3] - truth[c.j, [0, 2], 3]) - (poses[c.i, [0, 2], 3] - truth[c.i, [0, 2], 3]))
            for c in closures
        ])

    assert after < before
    assert np.abs(corrected[:, 1, 3] - odometry[:, 1, 3]).max() < 1e-9, "height must stay as measured"
    assert np.abs(corrected[:, 1, :3] - odometry[:, 1, :3]).max() < 1e-9, "tilt against gravity must stay as measured"
    assert seam(odometry) > 0.5
    assert seam(corrected) < 0.02


def test_icp_that_runs_out_of_iterations_is_not_converged():
    """Twenty steps that never settle are a match still sliding, not a converged one."""
    from cozmo.geometry.icp import point_to_plane_icp
    from cozmo.util.transforms import make_pose

    rng = np.random.default_rng(3)
    planes = []
    for axis in range(3):
        points = rng.uniform(-1.0, 1.0, (400, 3))
        points[:, axis] = 0.0
        planes.append(points)
    target = np.vstack(planes)
    normals = np.repeat(np.eye(3), 400, axis=0)
    initial = make_pose(np.eye(3), np.array([0.05, -0.04, 0.03]))

    assert not point_to_plane_icp(target.copy(), target, normals, initial=initial, iterations=1).converged
    settled = point_to_plane_icp(target.copy(), target, normals, initial=initial, iterations=30)
    assert settled.converged
    assert np.abs(settled.transform[:3, 3]).max() < 0.01


def test_closure_asking_for_more_drift_than_the_walk_allows_is_rejected():
    from cozmo.geometry.drift import _plausible_drift

    assert _plausible_drift(0.05, 2.0), "a few centimetres is always within reach"
    assert _plausible_drift(0.25, 20.0), "1.25% of the distance walked is ordinary drift"
    assert not _plausible_drift(0.74, 6.5), "the single-room scan's closures asked for 11% of the path"


def test_closure_that_changes_height_or_tilt_is_rejected():
    """Odometry's height and tilt come from gravity; a closure disagreeing with them matched the wrong surfaces."""
    from scipy.spatial.transform import Rotation

    from cozmo.geometry.drift import _agrees_with_gravity
    from cozmo.util.transforms import make_pose

    recorded = make_pose(np.eye(3), np.array([1.0, 1.4, 2.0]))
    turned = make_pose(Rotation.from_euler("Y", 20.0, degrees=True).as_matrix(), np.array([1.2, 1.42, 2.1]))
    assert _agrees_with_gravity(turned, recorded), "heading and position are what closures are for"
    assert not _agrees_with_gravity(make_pose(np.eye(3), np.array([1.0, 1.99, 2.0])), recorded)
    tipped = make_pose(Rotation.from_euler("X", 8.0, degrees=True).as_matrix(), np.array([1.0, 1.4, 2.0]))
    assert not _agrees_with_gravity(tipped, recorded)
