"""Tests for the gate scorer.

This file replaces one that imported `cozmo.bench.score` and asserted
`report.passed_all` on a plan it had no ground truth for. That module invented its own
truth -- ceiling truth was the plan's own value times 0.996, repeatability was the constant
0.70 cm, the photo-stitch error was the constant 3.2% -- so the assertion it made was that
a scorer which cannot fail does not fail. Both the module and the assertion are gone.

What is tested instead is the property the benchmark actually depends on: with no ground
truth, every accuracy gate reports SKIP, and none of them reports PASS. A gate that passes
for lack of evidence is the one defect in a benchmark that makes every other number in it
worthless, so it is worth a test.
"""

from __future__ import annotations

from datetime import datetime, timezone

from cozmo.bench.gates import Status, score_capture
from cozmo.bench.groundtruth import GroundTruth
from cozmo.schema import (
    CalibrationReport,
    DriftReport,
    IntervalMethod,
    Measure,
    PropertyPlan,
    QualityReport,
    Room,
    Tier,
)


def _plan(**overrides) -> PropertyPlan:
    fields = dict(
        pipeline_version="0.1.0",
        capture_id="test_cap",
        tier=Tier.LIDAR,
        created_at=datetime.now(timezone.utc),
        rooms=[
            Room(
                room_id="room_01",
                label="living_room",
                polygon=[(0.0, 0.0), (4.0, 0.0), (4.0, 3.0), (0.0, 3.0)],
                walls=[],
                surfaces=[],
                openings=[],
                ceiling_height=Measure(value=2.45, lo=2.43, hi=2.47, unit="m"),
                floor_area=Measure(value=12.0, lo=11.5, hi=12.5, unit="m2"),
                perimeter=Measure(value=14.0, lo=13.5, hi=14.5, unit="m"),
                observation_quality=0.95,
            )
        ],
        adjacency=[],
        damage=[],
        concealed_flags=[],
        scope_items=[],
        drift=DriftReport(
            method="keyframe pose graph, ICP-verified loop closures",
            loop_closures_found=2,
            residual_before_m=0.08,
            residual_after_m=0.01,
            max_pose_correction_m=0.05,
            footprint_area_before_m2=12.5,
            footprint_area_after_m2=12.0,
            applied=True,
        ),
        calibration=CalibrationReport(
            method=IntervalMethod.CONFORMAL,
            nominal_coverage=0.90,
            empirical_coverage={},
            residual_quantiles={},
            fitted_on="synthetic",
        ),
        quality=QualityReport(
            tier=Tier.LIDAR,
            device_model="iPhone 15 Pro",
            frames_available=100,
            frames_used=50,
            median_depth_confidence=2.0,
            surface_coverage=0.92,
            low_light_fraction=0.0,
            specular_fraction=0.0,
            warnings=[],
        ),
        total_floor_area=Measure(value=12.0, lo=11.5, hi=12.5, unit="m2"),
        runtime_seconds=1.5,
    )
    fields.update(overrides)
    return PropertyPlan(**fields)


def test_no_ground_truth_never_passes_an_accuracy_gate():
    results = score_capture(_plan(), GroundTruth(records=[]), "test_cap")

    accuracy_gates = {
        "wall_lengths",
        "ceiling_height",
        "opening_widths",
        "footprint",
        "interval_coverage",
        "adjacency",
    }
    scored = {r.gate: r for r in results}
    assert accuracy_gates <= set(scored), "a gate disappeared from the scorer"

    for name in accuracy_gates:
        assert scored[name].status is Status.SKIP, (
            f"{name} returned {scored[name].status} with no ground truth; "
            "a gate must never pass for lack of evidence"
        )


def test_photo_folder_label_beats_lidar_room_map():
    from cozmo.bench.groundtruth import resolve_room

    truth = GroundTruth(records=[], room_map={"room_01": "hall"})
    photo_bath = Room(
        room_id="room_01",
        label="bathroom",
        polygon=[(0, 0), (1, 0), (0, 1)],
        walls=[],
        surfaces=[],
        openings=[],
        ceiling_height=None,
        floor_area=Measure(value=2.0, lo=1.0, hi=3.0, unit="m2"),
        perimeter=Measure(value=6.0, lo=5.0, hi=7.0, unit="m"),
        observation_quality=0.5,
    )
    lidar_anon = photo_bath.model_copy(update={"label": "room"})
    assert resolve_room(photo_bath, truth) == "bathroom"
    assert resolve_room(lidar_anon, truth) == "hall"


def test_drift_gate_fails_when_poses_are_used_as_is():
    """The brief makes this an automatic fail, so it is asserted rather than assumed."""
    as_is = _plan(
        drift=DriftReport(
            method="not applied: drift correction disabled by configuration",
            loop_closures_found=0,
            residual_before_m=0.0,
            residual_after_m=0.0,
            max_pose_correction_m=0.0,
            footprint_area_before_m2=12.0,
            footprint_area_after_m2=12.0,
            applied=False,
        )
    )
    scored = {r.gate: r for r in score_capture(as_is, GroundTruth(records=[]), "test_cap")}
    assert scored["drift_accountability"].status is Status.FAIL


def test_room_map_is_per_capture():
    """The same room id names different rooms in different captures.

    Room ids are assigned per reconstruction in order of area. On the benchmark flat the home
    walk's room_01 is the bedroom and the long walk's room_01 is the hall, and a single flat map
    scored the home bedroom as the hall and the long-walk bathroom as the passage.
    """
    from cozmo.bench.groundtruth import resolve_room

    truth = GroundTruth(
        records=[],
        room_map={
            "long_walk": {"room_01": "hall", "room_03": "bathroom"},
            "home_walk": {"room_01": "bedroom"},
        },
    )
    anon = Room(
        room_id="room_01",
        label="room",
        polygon=[(0, 0), (1, 0), (0, 1)],
        walls=[],
        surfaces=[],
        openings=[],
        ceiling_height=None,
        floor_area=Measure(value=2.0, lo=1.0, hi=3.0, unit="m2"),
        perimeter=Measure(value=6.0, lo=5.0, hi=7.0, unit="m"),
        observation_quality=0.5,
    )
    assert resolve_room(anon, truth, "long_walk") == "hall"
    assert resolve_room(anon, truth, "home_walk") == "bedroom"
    third = anon.model_copy(update={"room_id": "room_03"})
    assert resolve_room(third, truth, "home_walk") is None, "a room absent from its capture's map stays unnamed"
    assert resolve_room(anon, truth, "unknown_capture") is None, "a nested map is never applied to another capture"


def test_room_map_loader_ignores_annotation_keys(tmp_path):
    from cozmo.bench.groundtruth import load_ground_truth

    path = tmp_path / "room_map.json"
    path.write_text('{"_method": "from camera frames", "cap": {"room_01": "hall"}, "_evidence": {"cap": {}}}')
    truth = load_ground_truth(tmp_path / "missing.csv", path)
    assert set(truth.room_map) == {"cap"}
    assert truth.room_name("cap", "room_01") == "hall"



def test_opening_widths_pair_with_the_nearest_taped_width_when_the_counts_differ():
    from cozmo.bench.gates import _match_widths

    assert _match_widths([0.70, 0.90], [0.91]) == [(0.90, 0.91)]
    assert _match_widths([0.80, 0.90], [0.81, 0.92]) == [(0.80, 0.81), (0.90, 0.92)]
    assert _match_widths([], [0.91]) == []
