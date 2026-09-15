"""Photo- and video-tier plans scored against the LiDAR plan of the same walk (bench/tiers.py).

Each test builds the plans by hand, so the number every gate should report is known before it runs.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import numpy as np
import pytest

from cozmo.bench.gates import Status
from cozmo.bench.tiers import score_photo, score_run, score_video
from cozmo.schema import (
    Adjacency,
    CalibrationReport,
    DriftReport,
    IntervalMethod,
    Measure,
    Plane,
    PropertyPlan,
    QualityReport,
    Room,
    Tier,
    Wall,
)


def _measure(value: float, half: float, unit: str) -> Measure:
    return Measure(value=value, lo=max(value - half, 0.0), hi=value + half, unit=unit, method=IntervalMethod.PROPAGATED)


def _room(room_id: str, label: str, corners: list[tuple[float, float]], relative_half: float = 0.2) -> Room:
    ring = np.asarray(corners, float)
    walls = []
    for i in range(len(ring)):
        a, b = ring[i], ring[(i + 1) % len(ring)]
        length = float(np.linalg.norm(b - a))
        walls.append(Wall(wall_id=f"{room_id}_w{i:02d}", surface_id=f"{room_id}_s{i:02d}", start=tuple(a), end=tuple(b),
                          length=_measure(length, 0.05, "m"), plane=Plane(normal=(1.0, 0.0, 0.0), offset=0.0),
                          point_support=10))
    x, z = ring[:, 0], ring[:, 1]
    area = 0.5 * abs(float(np.dot(x, np.roll(z, -1)) - np.dot(z, np.roll(x, -1))))
    perimeter = float(sum(w.length.value for w in walls))
    return Room(room_id=room_id, label=label, polygon=[tuple(p) for p in ring], walls=walls, surfaces=[], openings=[],
                floor_area=_measure(area, relative_half * area, "m2"), perimeter=_measure(perimeter, 0.1, "m"),
                observation_quality=0.8)


def _box(x0: float, z0: float, x1: float, z1: float) -> list[tuple[float, float]]:
    return [(x0, z0), (x1, z0), (x1, z1), (x0, z1)]


def _plan(rooms: list[Room], tier: Tier, links: list[tuple[str, str]] = ()) -> PropertyPlan:
    total = float(sum(r.floor_area.value for r in rooms))
    return PropertyPlan(
        pipeline_version="test", capture_id="test", tier=tier, created_at=datetime.now(timezone.utc), rooms=rooms,
        adjacency=[Adjacency(room_a=a, room_b=b, opening_a="", opening_b=None, confidence=0.5, evidence="test") for a, b in links],
        damage=[], concealed_flags=[], scope_items=[],
        drift=DriftReport(method="none", loop_closures_found=0, residual_before_m=0.0, residual_after_m=0.0,
                          max_pose_correction_m=0.0, footprint_area_after_m2=total, applied=False),
        calibration=CalibrationReport(method=IntervalMethod.PROPAGATED, nominal_coverage=0.9, empirical_coverage={},
                                      residual_quantiles={}, fitted_on="none"),
        quality=QualityReport(tier=tier, device_model="test", frames_available=1, frames_used=1,
                              median_depth_confidence=None, surface_coverage=1.0, warnings=[]),
        total_floor_area=_measure(total, 0.15 * total, "m2"), runtime_seconds=0.0,
    )


def _reference(lidar: PropertyPlan, folders: set[str]) -> dict:
    return {
        "capture": "flat",
        "lidar_footprint_m2": lidar.total_floor_area.value,
        "photo_rooms_footprint_m2": float(sum(r.floor_area.value for r in lidar.rooms if r.room_id in folders)),
        "rooms": [
            {"room_id": r.room_id, "photo_folder": r.room_id if r.room_id in folders else None,
             "floor_area_m2": r.floor_area.value, "wall_lengths_m": [w.length.value for w in r.walls],
             "polygon": [list(p) for p in r.polygon]}
            for r in lidar.rooms
        ],
        "adjacency": [{"room_a": a.room_a, "room_b": a.room_b} for a in lidar.adjacency],
    }


def _gates(score) -> dict[str, Status]:
    return {g.gate: g.status for g in score.gates}


LIDAR = _plan([_room("room_01", "room", _box(0, 0, 4, 2.5)), _room("room_02", "room", _box(4, 0, 6, 2.5)),
               _room("room_03", "room", _box(0, 2.5, 3, 4.5))], Tier.LIDAR,
              [("room_01", "room_02"), ("room_01", "room_03")])


def test_a_photo_plan_that_agrees_with_lidar_passes_every_gate():
    reference = _reference(LIDAR, {"room_01", "room_02", "room_03"})
    # Ids are the photo tier's own; labels carry the folder names the rooms pair by.
    photo = _plan([_room("room_07", "room_01", _box(10, 10, 14.12, 12.55)), _room("room_08", "room_02", _box(20, 10, 21.94, 12.45)),
                   _room("room_09", "room_03", _box(30, 10, 33.05, 12.02))], Tier.PHOTO,
                  [("room_07", "room_08"), ("room_09", "room_07")])
    score = score_photo(photo, reference)
    assert set(_gates(score).values()) == {Status.PASS}
    footprint = next(g for g in score.gates if g.gate == "footprint vs LiDAR")
    # The three LiDAR rooms hold 10 + 5 + 6 = 21 m2.
    assert footprint.detail["relative_error"] == pytest.approx((10.506 + 4.753 + 6.161) / 21.0 - 1.0, abs=1e-3)
    assert {r.reference_room: r.plan_room for r in score.rooms} == {"room_01": "room_07", "room_02": "room_08", "room_03": "room_09"}


def test_a_missing_room_an_inflated_room_and_a_missed_connection_each_fail():
    reference = _reference(LIDAR, {"room_01", "room_02", "room_03"})
    photo = _plan([_room("room_01", "room_01", _box(0, 0, 4, 2.5)), _room("room_02", "room_02", _box(10, 0, 12.6, 3.25))],
                  Tier.PHOTO, [("room_01", "room_02")])
    gates = _gates(score_photo(photo, reference))
    assert gates["rooms reconstructed"] is Status.FAIL
    assert gates["adjacency vs LiDAR"] is Status.FAIL
    assert gates["wall_lengths vs LiDAR"] is Status.FAIL
    assert gates["footprint vs LiDAR"] is Status.FAIL


def test_a_connection_to_a_room_without_a_folder_is_not_expected_of_the_photo_plan():
    reference = _reference(LIDAR, {"room_01", "room_02"})
    photo = _plan([_room("room_01", "room_01", _box(0, 0, 4, 2.5)), _room("room_02", "room_02", _box(4, 0, 6, 2.5))],
                  Tier.PHOTO, [("room_01", "room_02")])
    score = score_photo(photo, reference)
    assert _gates(score)["adjacency vs LiDAR"] is Status.PASS
    assert score.gates[0].detail["reference_m2"] == pytest.approx(15.0)


def _edges(rooms: list[Room], extra: list[tuple[tuple[float, float], tuple[float, float]]] = ()) -> np.ndarray:
    rng = np.random.default_rng(3)
    points = []
    segments = [(tuple(w.start), tuple(w.end)) for r in rooms for w in r.walls] + list(extra)
    for (x0, z0), (x1, z1) in segments:
        n = max(int(np.hypot(x1 - x0, z1 - z0) / 0.03), 2)
        t = np.linspace(0.0, 1.0, n)
        points.append(np.stack([x0 + (x1 - x0) * t, z0 + (z1 - z0) * t], axis=1))
    return np.vstack(points) + rng.normal(0.0, 0.005, (sum(len(p) for p in points), 2))


def _moved(corners, angle_deg: float, shift, stretch: float = 1.0) -> list[tuple[float, float]]:
    ring = np.asarray(corners, float)
    ring = ring.mean(axis=0) + (ring - ring.mean(axis=0)) * stretch
    theta = np.deg2rad(angle_deg)
    rotation = np.array([[np.cos(theta), -np.sin(theta)], [np.sin(theta), np.cos(theta)]])
    return [tuple(p) for p in ring @ rotation.T + np.asarray(shift)]


def _video(stretch: float) -> tuple[PropertyPlan, np.ndarray]:
    rooms = [_room(f"room_{i + 1:02d}", "room", _moved(r.polygon, 90.4, (2.1, -0.7), stretch)) for i, r in enumerate(LIDAR.rooms)]
    stub = [((0.0, 1.2), (1.1, 1.2))]
    moved_stub = [tuple(tuple(p) for p in _moved(list(s), 90.4, (2.1, -0.7))) for s in stub]
    evidence_rooms = [_room(r.room_id, "room", _moved(r.polygon, 90.4, (2.1, -0.7))) for r in LIDAR.rooms]
    return _plan(rooms, Tier.VIDEO), _edges(evidence_rooms, moved_stub)


def test_a_video_plan_is_registered_onto_lidar_and_scored_room_by_room():
    lidar_walls = _edges(LIDAR.rooms, [((0.0, 1.2), (1.1, 1.2))])
    video, video_walls = _video(stretch=1.02)
    score = score_video(video, _reference(LIDAR, set()), LIDAR, lidar_walls, video_walls)
    gates = _gates(score)
    assert gates["rooms matched"] is Status.PASS
    assert gates["wall_lengths vs LiDAR"] is Status.PASS
    assert gates["footprint vs LiDAR"] is Status.PASS
    assert all(r.area_error == pytest.approx(0.0404, abs=2e-3) for r in score.rooms)


def test_a_video_plan_six_percent_too_long_fails_its_walls_and_footprint():
    lidar_walls = _edges(LIDAR.rooms, [((0.0, 1.2), (1.1, 1.2))])
    video, video_walls = _video(stretch=1.06)
    gates = _gates(score_video(video, _reference(LIDAR, set()), LIDAR, lidar_walls, video_walls))
    assert gates["wall_lengths vs LiDAR"] is Status.FAIL
    assert gates["footprint vs LiDAR"] is Status.FAIL


def test_a_video_plan_that_does_not_register_pairs_no_room():
    lidar_walls = _edges(LIDAR.rooms)
    video, _ = _video(stretch=1.0)
    noise = np.random.default_rng(0).uniform(-20.0, 20.0, (400, 2))
    score = score_video(video, _reference(LIDAR, set()), LIDAR, lidar_walls, noise)
    gates = _gates(score)
    assert gates["wall_lengths vs LiDAR"] is Status.SKIP
    assert "rooms matched" not in gates
    assert any("registration failed" in g.measured for g in score.gates)


def test_a_lidar_plan_is_not_scored_against_itself(tmp_path):
    (tmp_path / "plan.json").write_text(LIDAR.model_dump_json())
    (tmp_path / "reference.json").write_text(json.dumps(_reference(LIDAR, set())))
    with pytest.raises(ValueError, match="cozmo benchmark"):
        score_run(tmp_path, tmp_path / "reference.json")


def test_the_tiers_command_writes_its_scores(tmp_path):
    from typer.testing import CliRunner

    from cozmo.cli import app

    photo = _plan([_room("room_01", "room_01", _box(0, 0, 4, 2.5))], Tier.PHOTO)
    run = tmp_path / "run"
    run.mkdir()
    (run / "plan.json").write_text(photo.model_dump_json())
    (tmp_path / "reference.json").write_text(json.dumps(_reference(LIDAR, {"room_01"})))
    result = CliRunner().invoke(app, ["tiers", "--run", str(run), "--reference", str(tmp_path / "reference.json")])
    assert result.exit_code == 0, result.output
    written = json.loads((run / "tiers.json").read_text())
    assert {g["gate"]: g["status"] for g in written["gates"]}["footprint vs LiDAR"] == "PASS"
