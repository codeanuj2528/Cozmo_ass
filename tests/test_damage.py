"""Concealed-damage rules, evaluated against damage regions built for the test.

The previous version of this file called `detect_damage_regions("room_01", [...])` and
asserted it returned exactly two regions, the first a water stain and the second a crack.
That is a test of a hardcoded constant: the function took no image, so it returned the same
two findings for every room of every property, and this test required it to keep doing so.
A test that pins fabricated output in place is worse than no test, because it makes the
fabrication look verified.

The rule engine is worth testing on its own, so it is tested on regions constructed here,
where the input is visible in the test and the expected firing follows from it.
"""

from __future__ import annotations

from cozmo.damage.rules import RuleEngine
from cozmo.schema import (
    DamageClass,
    DamageRegion,
    ExtentKind,
    IntervalMethod,
    Measure,
)


def _region(
    damage_class: DamageClass,
    extent: float,
    surface_id: str = "room_01_s00",
    kind: ExtentKind = ExtentKind.AREA,
    unit: str = "m2",
) -> DamageRegion:
    return DamageRegion(
        damage_id=f"dmg_{damage_class.value}",
        room_id="room_01",
        surface_id=surface_id,
        damage_class=damage_class,
        extent_kind=kind,
        extent=Measure(
            value=extent,
            lo=extent * 0.8,
            hi=extent * 1.2,
            unit=unit,
            method=IntervalMethod.PRIOR,
        ),
        bbox_on_surface=(0.2, 0.3, 0.8, 0.9),
        polygon_on_surface=[(0.2, 0.3), (0.8, 0.3), (0.8, 0.9), (0.2, 0.9)],
        severity="moderate",
        classification_confidence=0.6,
        evidence_frames=[12],
    )


def test_engine_loads_rules():
    engine = RuleEngine()
    assert engine.rules, "the rule engine must ship with rules"


def test_no_damage_fires_no_rules():
    assert RuleEngine().evaluate_damage([]) == []


def test_water_damage_raises_a_concealed_flag_naming_its_rule():
    flags = RuleEngine().evaluate_damage([_region(DamageClass.WATER_STAIN, 0.45)])
    assert flags, "visible water damage should raise a concealed-damage question"
    flag = flags[0]
    assert flag.rule_id
    assert flag.rule_text and len(flag.rule_text) > 20, "the rule that fired must be readable"
    assert flag.triggered_by, "a flag must name the damage that triggered it"
    assert flag.recommended_action


def test_flags_are_traceable_to_their_damage():
    regions = [
        _region(DamageClass.WATER_STAIN, 0.45),
        _region(DamageClass.CRACK, 1.2, kind=ExtentKind.LENGTH, unit="m"),
    ]
    known = {r.damage_id for r in regions}
    for flag in RuleEngine().evaluate_damage(regions):
        assert set(flag.triggered_by) & known, f"{flag.flag_id} cites nothing in the input"


def test_confidence_is_a_probability():
    for flag in RuleEngine().evaluate_damage([_region(DamageClass.WATER_STAIN, 0.45)]):
        assert 0.0 <= flag.confidence <= 1.0



def test_flag_carries_the_values_that_fired_it():
    """The firing has to be checkable by hand, so the flag names each condition and what it read."""
    from cozmo.damage.rules import RULE_FIELDS
    from cozmo.schema import ConcealedFlag

    flag = RuleEngine().evaluate_damage([_region(DamageClass.WATER_STAIN, 0.45)])[0]
    assert flag.conditions, "a flag must say which conditions held"
    assert all(c.passed for c in flag.conditions)
    assert {c.field for c in flag.conditions} <= RULE_FIELDS
    assert ConcealedFlag.model_validate_json(flag.model_dump_json()).conditions == flag.conditions


def test_a_rule_the_engine_cannot_read_is_rejected_at_load(tmp_path):
    """A misspelt field or operator used to make its rule quietly never fire."""
    import pytest

    from cozmo.damage.rules import RuleError

    rules = tmp_path / "rules.yaml"
    head = "rules:\n  - id: R1\n    text: water stain on a wall close to the floor line\n    predicate:\n      all_of:\n"
    rules.write_text(head + "        - {field: damage_clas, op: eq, value: water_stain}\n")
    with pytest.raises(RuleError, match="damage_clas"):
        RuleEngine(rules)
    rules.write_text(head + "        - {field: damage_class, op: equals, value: water_stain}\n")
    with pytest.raises(RuleError, match="equals"):
        RuleEngine(rules)
