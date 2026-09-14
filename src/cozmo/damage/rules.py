"""Concealed-damage rule evaluation engine.

Evaluates structured YAML rules without eval() to produce ConcealedFlag objects.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

import yaml

from cozmo.schema import ConcealedFlag, RuleCondition

log = logging.getLogger("cozmo.damage.rules")
DEFAULT_RULES_PATH = Path(__file__).with_name("rules.yaml")

RULE_FIELDS = {
    "damage_class",
    "surface_kind",
    "area_m2",
    "max_extent_m",
    "min_height_above_floor_m",
    "max_height_above_floor_m",
    "severity",
    "confidence",
    "distance_to_exterior_corner_m",
    "distance_to_opening_m",
    "wall_has_opening",
    "room_id",
    "surface_id",
}

OPERATORS: Dict[str, Callable[[Any, Any], bool]] = {
    "eq": lambda a, b: a == b,
    "ne": lambda a, b: a != b,
    "lt": lambda a, b: a is not None and a < b,
    "lte": lambda a, b: a is not None and a <= b,
    "gt": lambda a, b: a is not None and a > b,
    "gte": lambda a, b: a is not None and a >= b,
    "in": lambda a, b: a in b if isinstance(b, (list, tuple, set)) else a == b,
    "not_in": lambda a, b: a not in b if isinstance(b, (list, tuple, set)) else a != b,
    "contains": lambda a, b: b in (a or ()),
}


LOGICAL_KEYS = ("all_of", "any_of", "none_of")
RULE_KEYS = {"id", "text", "predicate", "severity", "probability", "recommended_action", "inspection_priority"}


class RuleError(ValueError):
    """A rule the engine cannot evaluate as written."""


def _validate_node(node: Any, rule_id: str, path: str = "predicate") -> None:
    """Reject a predicate node the evaluator would read as a quiet False.

    `_eval_node` returns False for a condition whose field or operator it does not know, so a
    misspelt field used to make its rule never fire, with nothing to say why.
    """
    if not isinstance(node, Mapping):
        raise RuleError(f"{rule_id}: {path} must be a mapping, got {type(node).__name__}")
    logical = [key for key in LOGICAL_KEYS if key in node]
    if logical:
        if len(node) != 1:
            raise RuleError(f"{rule_id}: {path} mixes keys {sorted(node)}; a logical node holds exactly one of {LOGICAL_KEYS}")
        children = node[logical[0]]
        if not isinstance(children, list) or not children:
            raise RuleError(f"{rule_id}: {path}.{logical[0]} must be a non-empty list")
        for index, child in enumerate(children):
            _validate_node(child, rule_id, f"{path}.{logical[0]}[{index}]")
        return
    if set(node) != {"field", "op", "value"}:
        raise RuleError(f"{rule_id}: {path} must be a condition with field, op and value, got keys {sorted(node)}")
    if node["field"] not in RULE_FIELDS:
        raise RuleError(f"{rule_id}: {path} names field {node['field']!r}, which the engine never supplies")
    if node["op"] not in OPERATORS:
        raise RuleError(f"{rule_id}: {path} uses operator {node['op']!r}; known operators are {sorted(OPERATORS)}")


@dataclass
class RuleEvaluation:
    field: str
    op: str
    expected: Any
    actual: Any
    passed: bool


class ConcealedRule:
    def __init__(
        self,
        rule_id: str,
        text: str,
        predicate: Mapping[str, Any],
        severity: str = "medium",
        probability: float = 0.5,
        recommended_action: str = "",
        inspection_priority: int = 3,
    ) -> None:
        self.id = rule_id
        self.text = text
        self.predicate = predicate
        self.severity = severity
        self.probability = probability
        self.recommended_action = recommended_action
        self.inspection_priority = inspection_priority

    def evaluate(self, context: Mapping[str, Any]) -> Tuple[bool, List[RuleEvaluation]]:
        evaluations: List[RuleEvaluation] = []
        fired = _eval_node(self.predicate, context, evaluations)
        return fired, evaluations


def _eval_node(
    node: Mapping[str, Any], context: Mapping[str, Any], evaluations: List[RuleEvaluation]
) -> bool:
    if "all_of" in node:
        return all(_eval_node(child, context, evaluations) for child in node["all_of"])
    if "any_of" in node:
        return any(_eval_node(child, context, evaluations) for child in node["any_of"])
    if "none_of" in node:
        return not any(_eval_node(child, context, evaluations) for child in node["none_of"])

    fld = node.get("field")
    op = node.get("op")
    val = node.get("value")
    if not fld or not op or op not in OPERATORS:
        return False

    actual = context.get(fld)
    fn = OPERATORS[op]
    passed = bool(fn(actual, val))
    evaluations.append(
        RuleEvaluation(field=fld, op=op, expected=val, actual=actual, passed=passed)
    )
    return passed


class RuleEngine:
    def __init__(self, rules_path: Optional[Path] = None) -> None:
        path = rules_path or DEFAULT_RULES_PATH
        self.rules: List[ConcealedRule] = []
        if path.exists():
            data = yaml.safe_load(path.read_text()) or {}
            seen: set[str] = set()
            for rdict in data.get("rules", []):
                rule_id = rdict.get("id") if isinstance(rdict, Mapping) else None
                if not rule_id or not rdict.get("text") or "predicate" not in rdict:
                    raise RuleError(f"{path}: every rule needs an id, a text and a predicate, got {rdict!r}")
                if rule_id in seen:
                    raise RuleError(f"{path}: rule id {rule_id} is used twice")
                unknown = set(rdict) - RULE_KEYS
                if unknown:
                    raise RuleError(f"{rule_id}: unknown keys {sorted(unknown)}")
                _validate_node(rdict["predicate"], rule_id)
                seen.add(rule_id)
                self.rules.append(
                    ConcealedRule(
                        rule_id=rdict["id"],
                        text=rdict["text"],
                        predicate=rdict["predicate"],
                        severity=rdict.get("severity", "medium"),
                        probability=rdict.get("probability", 0.5),
                        recommended_action=rdict.get("recommended_action", ""),
                        inspection_priority=rdict.get("inspection_priority", 3),
                    )
                )

    def evaluate_damage(
        self, damage_regions: Sequence[Any], room_surfaces: Optional[Sequence[Any]] = None
    ) -> List[ConcealedFlag]:
        flags: List[ConcealedFlag] = []
        for idx, dmg in enumerate(damage_regions):
            ctx = {
                "damage_class": getattr(dmg, "damage_class", "").value if hasattr(getattr(dmg, "damage_class", ""), "value") else str(getattr(dmg, "damage_class", "")),
                "surface_kind": getattr(dmg, "surface_kind", "wall"),
                "area_m2": getattr(dmg.extent, "value", 0.0) if hasattr(dmg, "extent") else 0.0,
                "max_extent_m": getattr(dmg.extent, "value", 0.0) if hasattr(dmg, "extent") else 0.0,
                "min_height_above_floor_m": getattr(dmg, "min_height_above_floor_m", 0.1),
                "max_height_above_floor_m": getattr(dmg, "max_height_above_floor_m", 2.0),
                "severity": getattr(dmg, "severity", "moderate"),
                "confidence": getattr(dmg, "classification_confidence", 0.9),
                "distance_to_exterior_corner_m": getattr(dmg, "distance_to_exterior_corner_m", 0.3),
                "distance_to_opening_m": getattr(dmg, "distance_to_opening_m", 0.5),
                "wall_has_opening": getattr(dmg, "wall_has_opening", False),
                "room_id": getattr(dmg, "room_id", "room_01"),
                "surface_id": getattr(dmg, "surface_id", "surf_01"),
            }
            for rule in self.rules:
                fired, evaluations = rule.evaluate(ctx)
                if fired:
                    flags.append(
                        ConcealedFlag(
                            flag_id=f"flag_{len(flags)+1:03d}",
                            rule_id=rule.id,
                            rule_text=rule.text,
                            room_id=getattr(dmg, "room_id", "room_01"),
                            surface_id=getattr(dmg, "surface_id", None),
                            triggered_by=[getattr(dmg, "damage_id", f"dmg_{idx+1:03d}")],
                            confidence=rule.probability,
                            recommended_action=rule.recommended_action,
                            conditions=[
                                RuleCondition(field=e.field, op=e.op, expected=e.expected, actual=e.actual, passed=e.passed)
                                for e in evaluations
                            ],
                        )
                    )
        return flags
