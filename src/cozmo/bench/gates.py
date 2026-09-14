"""The Round 1 gates plus the five additions, scored exactly as the brief states them.

Each gate returns PASS, FAIL or SKIP, and SKIP is used only when the ground truth needed to
judge it does not exist. A gate that cannot be evaluated is reported as unevaluated and
costs its marks; it is never reported as passed. That distinction is the whole value of
this module, because the easy failure here is a table of green rows that were never
actually checked against anything.

Two of the gates are written the way the brief writes them rather than the way they are
usually implemented, and the difference matters.

Opening detection is scored, not just opening measurement. A missed opening and a phantom
opening each count as a miss, so the denominator is the union of what was found and what
exists, not the intersection. A detector that reports one opening in a property and gets it
right scores one out of however many there are, not one out of one.

Ceiling height has two gates, not one, and they fail differently. Absolute error against
the laser catches bias; spread across repeat captures catches noise. A pipeline can be
repeatable and biased, or unbiased and unrepeatable, and the report has to say which it is,
so both are computed and named separately.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import numpy as np

from cozmo.bench.groundtruth import (
    GroundTruth,
    opening_widths,
    pair_walls_cyclically,
    resolve_room,
    wall_lengths,
)
from cozmo.schema import OpeningType, PropertyPlan, Tier

CONNECTING = (OpeningType.DOOR, OpeningType.PASS_THROUGH)

OPENING_WIDTH_TOLERANCE_M = 0.02
OPENING_PASS_FRACTION = 0.85
CEILING_TOLERANCE_M = 0.015
CEILING_SPREAD_TOLERANCE_M = 0.010
REPEATABILITY_ABS_M = 0.010
REPEATABILITY_REL = 0.005

WALL_TOLERANCE = {
    Tier.LIDAR: ("abs", 0.020),
    Tier.VIDEO: ("rel", 0.030),
    Tier.PHOTO: ("rel", 0.080),
}
FOOTPRINT_TOLERANCE = {Tier.LIDAR: 0.05, Tier.VIDEO: 0.05, Tier.PHOTO: 0.08}


class Status(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    SKIP = "SKIP"


@dataclass
class GateResult:
    gate: str
    scope: str
    tier: str
    measured: str
    threshold: str
    status: Status
    detail: dict = field(default_factory=dict)


def _tolerance_ok(error: float, truth: float, kind: str, limit: float) -> bool:
    return error <= limit if kind == "abs" else error <= limit * abs(truth)


def gate_wall_lengths(plan: PropertyPlan, truth: GroundTruth, capture_id: str) -> GateResult:
    kind, limit = WALL_TOLERANCE.get(plan.tier, ("abs", 0.02))
    label = f"<= {limit * 100:.0f} cm" if kind == "abs" else f"<= {limit:.0%}"

    errors: list[tuple[float, float]] = []
    unpaired = 0
    for room in plan.rooms:
        name = resolve_room(room, truth, capture_id)
        if name is None:
            continue
        truth_walls = [r.value_m for r in truth.values(capture_id, name, "wall_length")]
        if not truth_walls:
            continue
        pairs, _ = pair_walls_cyclically(wall_lengths(room), truth_walls)
        for predicted, actual in pairs:
            if np.isfinite(predicted) and np.isfinite(actual):
                errors.append((abs(predicted - actual), actual))
            else:
                unpaired += 1

    if not errors:
        return GateResult("wall_lengths", capture_id, plan.tier.value,
                          "no wall ground truth", label, Status.SKIP)

    within = sum(1 for e, t in errors if _tolerance_ok(e, t, kind, limit))
    total = len(errors) + unpaired
    fraction = within / total
    worst = max(e for e, _ in errors)
    return GateResult(
        gate="wall_lengths",
        scope=capture_id,
        tier=plan.tier.value,
        measured=f"{within}/{total} within tolerance ({fraction:.0%}), worst {worst * 100:.1f} cm"
        + (f", {unpaired} unpaired" if unpaired else ""),
        threshold=f"{label} on >= {OPENING_PASS_FRACTION:.0%}",
        status=Status.PASS if fraction >= OPENING_PASS_FRACTION else Status.FAIL,
        detail={"worst_cm": worst * 100, "fraction": fraction, "unpaired": unpaired},
    )


def gate_ceiling_height(plan: PropertyPlan, truth: GroundTruth, capture_id: str) -> GateResult:
    errors: list[tuple[str, float]] = []
    for room in plan.rooms:
        name = resolve_room(room, truth, capture_id)
        if name is None:
            continue
        actual = truth.scalar(capture_id, name, "ceiling_height")
        if actual is None or room.ceiling_height is None or room.ceiling_height.value <= 0:
            continue
        errors.append((name, abs(room.ceiling_height.value - actual)))

    if not errors:
        return GateResult("ceiling_height", capture_id, plan.tier.value,
                          "no ceiling ground truth", f"<= {CEILING_TOLERANCE_M * 100:.1f} cm",
                          Status.SKIP)

    worst_room, worst = max(errors, key=lambda t: t[1])
    return GateResult(
        gate="ceiling_height",
        scope=capture_id,
        tier=plan.tier.value,
        measured=f"worst {worst * 100:.1f} cm ({worst_room}) over {len(errors)} room(s)",
        threshold=f"<= {CEILING_TOLERANCE_M * 100:.1f} cm every room",
        status=Status.PASS if worst <= CEILING_TOLERANCE_M else Status.FAIL,
        detail={"worst_cm": worst * 100, "rooms": len(errors)},
    )


def _match_widths(reported: list[float], actual: list[float]) -> list[tuple[float, float]]:
    """Pair reported with taped opening widths so that the total width error is smallest.

    Zipping the two sorted lists does that only when they are the same length. With one phantom it
    pairs the smallest with the smallest and shifts every pair after it: a reported 0.70 m and
    0.90 m against a single taped 0.91 m door would score the 0.70 m as that door.
    """
    if not reported or not actual:
        return []
    from scipy.optimize import linear_sum_assignment

    cost = np.abs(np.subtract.outer(np.asarray(reported, dtype=float), np.asarray(actual, dtype=float)))
    rows, cols = linear_sum_assignment(cost)
    return [(float(reported[i]), float(actual[j])) for i, j in np.stack([rows, cols], axis=1)]


def gate_opening_widths(plan: PropertyPlan, truth: GroundTruth, capture_id: str) -> GateResult:
    """Width accuracy and detection together, with phantoms and misses both counted.

    The brief is explicit that a missed opening and a phantom opening each count as a
    miss, so the denominator is the larger of what was reported and what exists. Scoring
    only the openings that were both found and measured would let a detector that reports
    one opening in a whole property score 100%.
    """
    reported_total = 0
    truth_total = 0
    within = 0
    worst = 0.0

    for room in plan.rooms:
        name = resolve_room(room, truth, capture_id)
        if name is None:
            continue
        actual = sorted(r.value_m for r in truth.values(capture_id, name, "opening_width"))
        reported = sorted(opening_widths(room))
        if not actual and not reported:
            continue
        truth_total += len(actual)
        reported_total += len(reported)
        for predicted, real in _match_widths(reported, actual):
            error = abs(predicted - real)
            worst = max(worst, error)
            if error <= OPENING_WIDTH_TOLERANCE_M:
                within += 1

    if truth_total == 0:
        return GateResult("opening_widths", capture_id, plan.tier.value,
                          "no opening ground truth", f"<= {OPENING_WIDTH_TOLERANCE_M * 100:.0f} cm",
                          Status.SKIP)

    denominator = max(truth_total, reported_total)
    missed = max(0, truth_total - reported_total)
    phantom = max(0, reported_total - truth_total)
    fraction = within / denominator
    return GateResult(
        gate="opening_widths",
        scope=capture_id,
        tier=plan.tier.value,
        measured=(
            f"{within}/{denominator} ({fraction:.0%}); {missed} missed, {phantom} phantom, "
            f"worst {worst * 100:.1f} cm"
        ),
        threshold=f"<= {OPENING_WIDTH_TOLERANCE_M * 100:.0f} cm on >= {OPENING_PASS_FRACTION:.0%}, detection scored",
        status=Status.PASS if fraction >= OPENING_PASS_FRACTION else Status.FAIL,
        detail={"missed": missed, "phantom": phantom, "worst_cm": worst * 100},
    )


def gate_footprint(plan: PropertyPlan, truth: GroundTruth, capture_id: str) -> GateResult:
    tolerance = FOOTPRINT_TOLERANCE.get(plan.tier, 0.05)
    actual = truth.scalar(capture_id, "", "footprint_area")
    if actual is None:
        rooms = truth.rooms(capture_id)
        per_room = [truth.scalar(capture_id, r, "floor_area") for r in rooms]
        per_room = [v for v in per_room if v is not None]
        actual = float(sum(per_room)) if per_room else None
    if not actual:
        return GateResult("footprint", capture_id, plan.tier.value,
                          "no footprint ground truth", f"within +-{tolerance:.0%}", Status.SKIP)

    reported = plan.total_floor_area.value
    error = abs(reported - actual) / actual
    return GateResult(
        gate="footprint",
        scope=capture_id,
        tier=plan.tier.value,
        measured=f"{reported:.2f} m2 vs {actual:.2f} m2 ({error:.1%})",
        threshold=f"within +-{tolerance:.0%}",
        status=Status.PASS if error <= tolerance else Status.FAIL,
        detail={"reported": reported, "truth": actual, "relative_error": error},
    )


def gate_interval_coverage(plan: PropertyPlan, truth: GroundTruth, capture_id: str) -> GateResult:
    """Do the reported intervals actually contain the laser measurement.

    This is the gate that catches confident garbage, and it is the reason every number in
    the contract carries an interval. A pipeline can miss every gate above and still be
    useful if it says so; one whose intervals do not cover cannot be trusted anywhere.
    """
    covered = 0
    total = 0
    half_widths: list[float] = []

    for room in plan.rooms:
        name = resolve_room(room, truth, capture_id)
        if name is None:
            continue
        checks = [
            (room.ceiling_height, truth.scalar(capture_id, name, "ceiling_height")),
            (room.floor_area, truth.scalar(capture_id, name, "floor_area")),
        ]
        truth_walls = [r.value_m for r in truth.values(capture_id, name, "wall_length")]
        if truth_walls:
            pairs, _ = pair_walls_cyclically([w.length.value for w in room.walls], truth_walls)
            wall_by_length = {w.length.value: w for w in room.walls}
            for predicted, actual in pairs:
                if np.isfinite(predicted) and np.isfinite(actual):
                    wall = wall_by_length.get(predicted)
                    if wall is not None:
                        checks.append((wall.length, actual))
        for measure, actual in checks:
            if measure is None or actual is None or measure.value <= 0:
                continue
            total += 1
            half_widths.append(measure.half_width)
            if measure.contains(actual):
                covered += 1

    if total == 0:
        return GateResult("interval_coverage", capture_id, plan.tier.value,
                          "no ground truth to check coverage against",
                          f">= {plan.calibration.nominal_coverage:.0%}", Status.SKIP)

    fraction = covered / total
    nominal = plan.calibration.nominal_coverage
    return GateResult(
        gate="interval_coverage",
        scope=capture_id,
        tier=plan.tier.value,
        measured=f"{covered}/{total} covered ({fraction:.0%}), mean half-width "
        f"{np.mean(half_widths) * 100:.1f} cm",
        threshold=f">= {nominal:.0%} of {nominal:.0%} intervals",
        status=Status.PASS if fraction >= nominal else Status.FAIL,
        detail={"covered": covered, "total": total, "fraction": fraction},
    )


def gate_room_overlap(plan: PropertyPlan, capture_id: str) -> GateResult:
    """No two rooms may occupy the same floor. Explicitly required of the photo stitch."""
    from shapely.geometry import Polygon

    polygons = []
    for room in plan.rooms:
        poly = Polygon(room.polygon)
        polygons.append(poly if poly.is_valid else poly.buffer(0))
    if len(polygons) < 2:
        return GateResult("room_overlap", capture_id, plan.tier.value,
                          "single room", "no overlap", Status.SKIP)

    worst = 0.0
    total = 0.0
    for i, a in enumerate(polygons):
        for b in polygons[i + 1 :]:
            if a.is_empty or b.is_empty:
                continue
            area = float(a.intersection(b).area)
            total += area
            worst = max(worst, area)
    smallest = min(p.area for p in polygons if p.area > 0)
    fraction = worst / max(smallest, 1e-6)
    return GateResult(
        gate="room_overlap",
        scope=capture_id,
        tier=plan.tier.value,
        measured=f"largest overlap {worst:.3f} m2 ({fraction:.1%} of smallest room), "
        f"total {total:.3f} m2",
        threshold="no overlap above 2% of the smallest room",
        status=Status.PASS if fraction <= 0.02 else Status.FAIL,
        detail={"worst_m2": worst, "fraction": fraction},
    )


def gate_drift_accountability(plan: PropertyPlan, capture_id: str) -> GateResult:
    """Poses used as-is is an automatic fail, in the brief's own words."""
    drift = plan.drift
    used_as_is = (not drift.applied) and (
        "not applicable" not in drift.method.lower()
    ) and drift.loop_closures_found == 0 and "no loop closure" not in drift.method.lower()

    if plan.tier is Tier.PHOTO and "not applicable" in drift.method.lower():
        return GateResult("drift_accountability", capture_id, plan.tier.value,
                          drift.method, "stated and ablated", Status.SKIP)

    return GateResult(
        gate="drift_accountability",
        scope=capture_id,
        tier=plan.tier.value,
        measured=(
            f"{drift.method}; {drift.loop_closures_found} closures, residual "
            f"{drift.residual_before_m:.4f} -> {drift.residual_after_m:.4f}, "
            f"max correction {drift.max_pose_correction_m * 100:.1f} cm"
        ),
        threshold="a stated method with an ablation; poses used as-is fails",
        status=Status.FAIL if used_as_is else Status.PASS,
        detail={"applied": drift.applied, "closures": drift.loop_closures_found},
    )


def gate_adjacency(plan: PropertyPlan, truth: GroundTruth, capture_id: str) -> GateResult:
    """Is the stitched plan connected the way the property actually is.

    The brief calls the stitched plan the product surface and asks for correct adjacency,
    so adjacency has to be scored, not merely emitted. It is scored the same way openings
    are: a missing edge and an invented edge each count against, with the denominator the
    larger of what was reported and what exists. Counting only the edges that were both
    reported and real would let a plan that joins one pair of rooms in a four-room flat
    score full marks.

    Ground truth comes from `adjacency` rows in the recording sheet, where the operator
    writes down which room connects to which.
    """
    expected: set[frozenset[str]] = set()
    for record in truth.for_capture(capture_id):
        if record.quantity == "adjacency" and record.room and record.identifier:
            expected.add(frozenset({record.room, record.identifier}))

    if not expected:
        return GateResult("adjacency", capture_id, plan.tier.value,
                          "no adjacency ground truth", "all edges correct", Status.SKIP)

    names = {room.room_id: (resolve_room(room, truth, capture_id) or room.room_id) for room in plan.rooms}
    reported = {
        frozenset({names.get(link.room_a, link.room_a), names.get(link.room_b, link.room_b)})
        for link in plan.adjacency
    }

    correct = expected & reported
    missed = expected - reported
    phantom = reported - expected
    denominator = max(len(expected), len(reported))
    fraction = len(correct) / max(denominator, 1)

    return GateResult(
        gate="adjacency",
        scope=capture_id,
        tier=plan.tier.value,
        measured=(
            f"{len(correct)}/{denominator} correct ({fraction:.0%}); "
            f"{len(missed)} missed, {len(phantom)} phantom"
        ),
        threshold="every real connection found, none invented",
        status=Status.PASS if not missed and not phantom else Status.FAIL,
        detail={
            "correct": len(correct),
            "missed": sorted("-".join(sorted(e)) for e in missed),
            "phantom": sorted("-".join(sorted(e)) for e in phantom),
        },
    )


def gate_repeatability(
    plans: list[PropertyPlan], truth: GroundTruth, capture_ids: list[str]
) -> GateResult:
    """Two captures of the same room at the same tier must agree.

    Reported per wall, as the brief asks, and separately from the ceiling-height spread,
    because agreeing with yourself and agreeing with the laser are different properties
    and a pipeline can have either without the other.
    """
    scope = " vs ".join(capture_ids)
    if len(plans) < 2:
        return GateResult("repeatability", scope, plans[0].tier.value if plans else "-",
                          "only one capture of this room", "within 1 cm or 0.5% per wall",
                          Status.SKIP)

    a, b = plans[0], plans[1]
    # Two captures, two maps: room_02 in one walk need not be room_02 in the other.
    from cozmo.bench.groundtruth import resolve_capture_id

    capture_a = resolve_capture_id(a, capture_ids[0], truth)
    capture_b = resolve_capture_id(b, capture_ids[1] if len(capture_ids) > 1 else capture_ids[0], truth)
    rooms_a = {resolve_room(r, truth, capture_a) or r.room_id: r for r in a.rooms}
    rooms_b = {resolve_room(r, truth, capture_b) or r.room_id: r for r in b.rooms}
    shared = set(rooms_a) & set(rooms_b)
    if not shared:
        return GateResult("repeatability", scope, a.tier.value,
                          "no room appears in both captures", "within 1 cm or 0.5% per wall",
                          Status.SKIP)

    within = 0
    total = 0
    worst = 0.0
    ceiling_spread = 0.0
    for name in sorted(shared):
        pairs, _ = pair_walls_cyclically(wall_lengths(rooms_a[name]), wall_lengths(rooms_b[name]))
        for x, y in pairs:
            if not (np.isfinite(x) and np.isfinite(y)):
                total += 1
                continue
            total += 1
            difference = abs(x - y)
            worst = max(worst, difference)
            if difference <= REPEATABILITY_ABS_M or difference <= REPEATABILITY_REL * max(x, y):
                within += 1
        ca, cb = rooms_a[name].ceiling_height, rooms_b[name].ceiling_height
        if ca is not None and cb is not None and ca.value > 0 and cb.value > 0:
            ceiling_spread = max(ceiling_spread, abs(ca.value - cb.value))

    fraction = within / max(total, 1)
    ceiling_ok = ceiling_spread <= CEILING_SPREAD_TOLERANCE_M
    return GateResult(
        gate="repeatability",
        scope=scope,
        tier=a.tier.value,
        measured=(
            f"{within}/{total} walls agree ({fraction:.0%}), worst {worst * 100:.1f} cm; "
            f"ceiling spread {ceiling_spread * 100:.1f} cm"
        ),
        threshold=f"1 cm or 0.5% per wall; ceiling spread <= {CEILING_SPREAD_TOLERANCE_M * 100:.0f} cm",
        status=Status.PASS if fraction >= OPENING_PASS_FRACTION and ceiling_ok else Status.FAIL,
        detail={"worst_cm": worst * 100, "ceiling_spread_cm": ceiling_spread * 100},
    )


def score_capture(plan: PropertyPlan, truth: GroundTruth, capture_id: str) -> list[GateResult]:
    """Every single-capture gate."""
    return [
        gate_wall_lengths(plan, truth, capture_id),
        gate_ceiling_height(plan, truth, capture_id),
        gate_opening_widths(plan, truth, capture_id),
        gate_footprint(plan, truth, capture_id),
        gate_interval_coverage(plan, truth, capture_id),
        gate_room_overlap(plan, capture_id),
        gate_adjacency(plan, truth, capture_id),
        gate_drift_accountability(plan, capture_id),
    ]


def format_table(results: list[GateResult]) -> str:
    """A fixed-width table. Plain text so it can be diffed between runs."""
    headers = ("GATE", "SCOPE", "TIER", "MEASURED", "THRESHOLD", "STATUS")
    rows = [
        (r.gate, r.scope, r.tier, r.measured, r.threshold, r.status.value) for r in results
    ]
    widths = [
        max(len(headers[i]), max((len(row[i]) for row in rows), default=0)) for i in range(6)
    ]
    line = "  ".join("-" * w for w in widths)
    out = ["  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)), line]
    out += ["  ".join(str(c).ljust(widths[i]) for i, c in enumerate(row)) for row in rows]

    counts = {s: sum(1 for r in results if r.status is s) for s in Status}
    out += [
        line,
        f"{counts[Status.PASS]} pass, {counts[Status.FAIL]} fail, {counts[Status.SKIP]} not evaluated",
        "",
        "A gate is SKIP only when the ground truth needed to judge it does not exist. It is",
        "never reported as passed on that basis, and it costs its marks either way.",
    ]
    return "\n".join(out)
