"""Tests for schema definitions, interval measurements, and contract validation.

Exercises the validators added to Measure, Room and Wall, plus round-trip
serialisation of the full PropertyPlan contract.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from cozmo.schema import (
    Adjacency,
    CalibrationReport,
    ConcealedFlag,
    DamageClass,
    DamageRegion,
    DriftReport,
    ExtentKind,
    IntervalMethod,
    Measure,
    Opening,
    OpeningType,
    Plane,
    PropertyPlan,
    QualityReport,
    Room,
    ScopeItem,
    Surface,
    SurfaceType,
    Tier,
    Wall,
)


# ---------------------------------------------------------------------------
# Measure — basic construction
# ---------------------------------------------------------------------------

def test_measure_interval_contains():
    m = Measure(value=2.45, lo=2.43, hi=2.47, unit="m", coverage=0.90, method=IntervalMethod.CONFORMAL)
    assert m.half_width == pytest.approx(0.02)
    assert m.contains(2.45)
    assert m.contains(2.44)
    assert not m.contains(2.50)


def test_measure_is_frozen():
    """Measure should be immutable once created."""
    m = Measure(value=1.0, lo=0.5, hi=1.5, unit="m")
    with pytest.raises(ValidationError):
        m.value = 2.0


# ---------------------------------------------------------------------------
# Measure — validator: lo <= value <= hi
# ---------------------------------------------------------------------------

def test_measure_rejects_value_below_lo():
    """A value below its own lower bound is nonsense."""
    with pytest.raises(ValidationError, match="value.*must lie within"):
        Measure(value=0.3, lo=0.5, hi=1.5, unit="m")


def test_measure_rejects_value_above_hi():
    """A value above its own upper bound is nonsense."""
    with pytest.raises(ValidationError, match="value.*must lie within"):
        Measure(value=2.0, lo=0.5, hi=1.5, unit="m")


def test_measure_rejects_reversed_interval():
    """lo > hi is a bug, not a measurement."""
    with pytest.raises(ValidationError, match="lo.*must not exceed hi"):
        Measure(value=1.0, lo=1.5, hi=0.5, unit="m")


def test_measure_accepts_point_interval():
    """lo == value == hi is a valid (zero-width) interval."""
    m = Measure(value=1.0, lo=1.0, hi=1.0, unit="m")
    assert m.half_width == 0.0


def test_measure_accepts_value_at_boundaries():
    """value == lo and value == hi should both be accepted."""
    m_lo = Measure(value=0.5, lo=0.5, hi=1.5, unit="m")
    m_hi = Measure(value=1.5, lo=0.5, hi=1.5, unit="m")
    assert m_lo.value == 0.5
    assert m_hi.value == 1.5


# ---------------------------------------------------------------------------
# Measure — validator: coverage
# ---------------------------------------------------------------------------

def test_measure_rejects_zero_coverage():
    """Coverage must be > 0."""
    with pytest.raises(ValidationError):
        Measure(value=1.0, lo=0.5, hi=1.5, unit="m", coverage=0.0)


def test_measure_rejects_negative_coverage():
    with pytest.raises(ValidationError):
        Measure(value=1.0, lo=0.5, hi=1.5, unit="m", coverage=-0.1)


def test_measure_rejects_coverage_above_one():
    with pytest.raises(ValidationError):
        Measure(value=1.0, lo=0.5, hi=1.5, unit="m", coverage=1.5)


def test_measure_accepts_coverage_at_one():
    m = Measure(value=1.0, lo=0.5, hi=1.5, unit="m", coverage=1.0)
    assert m.coverage == 1.0


# ---------------------------------------------------------------------------
# Measure — extra fields rejected by StrictModel
# ---------------------------------------------------------------------------

def test_measure_rejects_extra_fields():
    """StrictModel should reject unknown fields."""
    with pytest.raises(ValidationError):
        Measure(value=1.0, lo=0.5, hi=1.5, unit="m", bogus_field="surprise")


# ---------------------------------------------------------------------------
# Opening validation
# ---------------------------------------------------------------------------

def test_opening_validation():
    op = Opening(
        opening_id="op_001",
        type=OpeningType.DOOR,
        wall_id="wall_01",
        width=Measure(value=0.85, lo=0.83, hi=0.87, unit="m"),
        height=Measure(value=2.05, lo=2.03, hi=2.07, unit="m"),
        sill_height=Measure(value=0.0, lo=0.0, hi=0.0, unit="m"),
        offset_along_wall=Measure(value=1.2, lo=1.1, hi=1.3, unit="m"),
        detection_confidence=0.95,
    )
    assert op.type == OpeningType.DOOR
    assert op.detection_confidence == 0.95


def test_opening_rejects_confidence_out_of_range():
    with pytest.raises(ValidationError):
        Opening(
            opening_id="op_001",
            type=OpeningType.DOOR,
            wall_id="wall_01",
            width=Measure(value=0.85, lo=0.83, hi=0.87, unit="m"),
            height=Measure(value=2.05, lo=2.03, hi=2.07, unit="m"),
            sill_height=Measure(value=0.0, lo=0.0, hi=0.0, unit="m"),
            offset_along_wall=Measure(value=1.2, lo=1.1, hi=1.3, unit="m"),
            detection_confidence=1.5,
        )


# ---------------------------------------------------------------------------
# Wall — point_support validation
# ---------------------------------------------------------------------------

def test_wall_rejects_negative_point_support():
    """point_support must be >= 0."""
    with pytest.raises(ValidationError):
        Wall(
            wall_id="w1",
            surface_id="s1",
            start=(0.0, 0.0),
            end=(4.0, 0.0),
            length=Measure(value=4.0, lo=3.9, hi=4.1, unit="m"),
            plane=Plane(normal=(0.0, 0.0, 1.0), offset=0.0),
            point_support=-10,
        )


def test_wall_accepts_zero_point_support():
    w = Wall(
        wall_id="w1",
        surface_id="s1",
        start=(0.0, 0.0),
        end=(4.0, 0.0),
        length=Measure(value=4.0, lo=3.9, hi=4.1, unit="m"),
        plane=Plane(normal=(0.0, 0.0, 1.0), offset=0.0),
        point_support=0,
    )
    assert w.point_support == 0


# ---------------------------------------------------------------------------
# Room — polygon validation
# ---------------------------------------------------------------------------

def test_room_rejects_empty_polygon():
    """A room with no vertices cannot enclose area."""
    with pytest.raises(ValidationError, match="polygon must have >= 3"):
        Room(
            room_id="r1",
            label="test",
            polygon=[],
            walls=[],
            surfaces=[],
            openings=[],
            floor_area=Measure(value=10.0, lo=9.0, hi=11.0, unit="m2"),
            perimeter=Measure(value=14.0, lo=13.0, hi=15.0, unit="m"),
            observation_quality=0.5,
        )


def test_room_rejects_two_vertex_polygon():
    with pytest.raises(ValidationError, match="polygon must have >= 3"):
        Room(
            room_id="r1",
            label="test",
            polygon=[(0, 0), (1, 0)],
            walls=[],
            surfaces=[],
            openings=[],
            floor_area=Measure(value=10.0, lo=9.0, hi=11.0, unit="m2"),
            perimeter=Measure(value=14.0, lo=13.0, hi=15.0, unit="m"),
            observation_quality=0.5,
        )


def test_room_accepts_triangle():
    r = Room(
        room_id="r1",
        label="test",
        polygon=[(0, 0), (4, 0), (0, 3)],
        walls=[],
        surfaces=[],
        openings=[],
        floor_area=Measure(value=6.0, lo=5.0, hi=7.0, unit="m2"),
        perimeter=Measure(value=12.0, lo=11.0, hi=13.0, unit="m"),
        observation_quality=0.8,
    )
    assert len(r.polygon) == 3


# ---------------------------------------------------------------------------
# DamageRegion — severity validation
# ---------------------------------------------------------------------------

def test_damage_region_rejects_invalid_severity():
    with pytest.raises(ValidationError):
        DamageRegion(
            damage_id="d1",
            room_id="r1",
            surface_id="s1",
            damage_class=DamageClass.CRACK,
            extent_kind=ExtentKind.LENGTH,
            extent=Measure(value=0.5, lo=0.3, hi=0.7, unit="m"),
            bbox_on_surface=(0, 0, 0.5, 0.1),
            polygon_on_surface=[(0, 0), (0.5, 0), (0.5, 0.1), (0, 0.1)],
            severity="catastrophic",
            classification_confidence=0.8,
            evidence_frames=[0, 5],
        )


# ---------------------------------------------------------------------------
# PropertyPlan — round-trip serialisation
# ---------------------------------------------------------------------------

def _make_minimal_plan() -> PropertyPlan:
    """Construct a minimal valid PropertyPlan for testing."""
    room = Room(
        room_id="room_01",
        label="hall",
        polygon=[(0, 0), (4, 0), (4, 3), (0, 3)],
        walls=[],
        surfaces=[],
        openings=[],
        floor_area=Measure(value=12.0, lo=11.0, hi=13.0, unit="m2"),
        perimeter=Measure(value=14.0, lo=13.0, hi=15.0, unit="m"),
        observation_quality=0.9,
    )
    return PropertyPlan(
        pipeline_version="0.1.0",
        capture_id="test_capture",
        tier=Tier.LIDAR,
        created_at=datetime(2026, 9, 13, tzinfo=timezone.utc),
        rooms=[room],
        adjacency=[],
        damage=[],
        concealed_flags=[],
        scope_items=[],
        drift=DriftReport(
            method="pose_graph",
            loop_closures_found=0,
            residual_before_m=0.0,
            residual_after_m=0.0,
            max_pose_correction_m=0.0,
            footprint_area_after_m2=12.0,
            applied=False,
        ),
        calibration=CalibrationReport(
            method=IntervalMethod.PROPAGATED,
            nominal_coverage=0.90,
            empirical_coverage={},
            residual_quantiles={},
            fitted_on="none",
        ),
        quality=QualityReport(
            tier=Tier.LIDAR,
            device_model="test",
            frames_available=100,
            frames_used=100,
            median_depth_confidence=0.9,
            surface_coverage=0.8,
            warnings=[],
        ),
        total_floor_area=Measure(value=12.0, lo=11.0, hi=13.0, unit="m2"),
        runtime_seconds=1.0,
    )


def test_property_plan_round_trip():
    """A PropertyPlan should survive JSON serialisation and deserialisation."""
    plan = _make_minimal_plan()
    json_str = plan.model_dump_json()
    data = json.loads(json_str)
    restored = PropertyPlan.model_validate(data)
    assert restored.capture_id == plan.capture_id
    assert restored.rooms[0].room_id == "room_01"
    assert restored.total_floor_area.value == pytest.approx(12.0)


def test_property_plan_has_schema_version():
    plan = _make_minimal_plan()
    assert plan.schema_version == "1.0.0"
