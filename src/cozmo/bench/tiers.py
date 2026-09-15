"""Photo- and video-tier plans scored against the LiDAR plan of the same walk.

`scripts/make_tier_inputs.py` builds a photo-tier and a video-tier capture from a Stray Scanner export, with
a `reference.json` holding the LiDAR plan's rooms. This module scores a photo or video plan against it with
the tolerances `bench/gates.py` applies to tape: footprint within 8% (photo) or 5% (video), walls within 8%
or 3% on at least 85% of walls, no two rooms on the same floor, every connection found and none invented,
and intervals that contain the reference value.

Rooms are paired differently at the two tiers. A photo plan names each room after the folder its stills came
from, and those folders are named after the LiDAR rooms, so rooms pair by name. A video plan is one walk in a
frame of its own: it is registered onto the LiDAR plan's walls (`bench/repeat.py`) and rooms pair by overlap.
A registration that puts fewer than half the wall cells within 10 cm of a LiDAR wall pairs nothing, and the
room-level gates say so rather than scoring rooms matched by accident.

The reference is a reconstruction, not tape. A PASS says the thinner tier agrees with the LiDAR tier on the
same walk within the brief's tolerance; it says nothing about either tier against the room itself.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
from shapely.geometry import Polygon
from shapely.ops import unary_union

from cozmo.bench.gates import (
    FOOTPRINT_TOLERANCE,
    OPENING_PASS_FRACTION,
    WALL_TOLERANCE,
    GateResult,
    Status,
    gate_room_overlap,
)
from cozmo.bench.groundtruth import pair_walls_cyclically, wall_lengths
from cozmo.schema import PropertyPlan, Tier

MIN_WALL_M = 0.30
REGISTRATION_MIN_WITHIN_10CM = 0.50


@dataclass
class RoomScore:
    reference_room: str
    plan_room: str | None
    reference_area_m2: float
    plan_area_m2: float | None = None
    area_error: float | None = None
    area_interval_covers: bool | None = None
    walls_within: int = 0
    walls_total: int = 0
    wall_errors: list[float] = field(default_factory=list)


@dataclass
class TierScore:
    tier: str
    scope: str
    gates: list[GateResult]
    rooms: list[RoomScore]
    notes: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        return {
            "tier": self.tier,
            "scope": self.scope,
            "gates": [asdict(g) | {"status": g.status.value} for g in self.gates],
            "rooms": [asdict(r) for r in self.rooms],
            "notes": self.notes,
        }


def _within(error: float, truth: float, tier: Tier) -> bool:
    kind, limit = WALL_TOLERANCE[tier]
    return error <= (limit * abs(truth) if kind == "rel" else limit)


def _wall_label(tier: Tier) -> str:
    kind, limit = WALL_TOLERANCE[tier]
    return f"<= {limit:.0%}" if kind == "rel" else f"<= {limit * 100:.0f} cm"


def _footprint_gate(plan: PropertyPlan, reference_m2: float, scope: str) -> GateResult:
    tolerance = FOOTPRINT_TOLERANCE[plan.tier]
    reported = plan.total_floor_area.value
    error = (reported - reference_m2) / reference_m2
    return GateResult(
        "footprint vs LiDAR", scope, plan.tier.value,
        f"{reported:.2f} m2 vs {reference_m2:.2f} m2 ({error:+.1%})",
        f"within +-{tolerance:.0%}",
        Status.PASS if abs(error) <= tolerance else Status.FAIL,
        {"reported_m2": reported, "reference_m2": reference_m2, "relative_error": error},
    )


def _walls_gate(rooms: list[RoomScore], tier: Tier, scope: str, skip_reason: str | None = None) -> GateResult:
    total = sum(r.walls_total for r in rooms)
    if skip_reason or total == 0:
        return GateResult("wall_lengths vs LiDAR", scope, tier.value, skip_reason or "no room paired",
                          f"{_wall_label(tier)} on >= {OPENING_PASS_FRACTION:.0%}", Status.SKIP)
    within = sum(r.walls_within for r in rooms)
    fraction = within / total
    errors = [e for r in rooms for e in r.wall_errors]
    return GateResult(
        "wall_lengths vs LiDAR", scope, tier.value,
        f"{within}/{total} within ({fraction:.0%}), median error {np.median(errors):.1%}" if errors
        else f"{within}/{total} within ({fraction:.0%})",
        f"{_wall_label(tier)} on >= {OPENING_PASS_FRACTION:.0%}",
        Status.PASS if fraction >= OPENING_PASS_FRACTION else Status.FAIL,
        {"within": within, "total": total},
    )


def _adjacency_gate(expected: set[frozenset], reported: set[frozenset], tier: Tier, scope: str) -> GateResult:
    if not expected:
        return GateResult("adjacency vs LiDAR", scope, tier.value, "no connection among the scored rooms",
                          "every connection found, none invented", Status.SKIP)
    missed, phantom = expected - reported, reported - expected
    correct = len(expected & reported)
    return GateResult(
        "adjacency vs LiDAR", scope, tier.value,
        f"{correct}/{max(len(expected), len(reported))} correct; {len(missed)} missed, {len(phantom)} phantom",
        "every connection found, none invented",
        Status.PASS if not missed and not phantom else Status.FAIL,
        {"missed": sorted("-".join(sorted(p)) for p in missed), "phantom": sorted("-".join(sorted(p)) for p in phantom)},
    )


def _coverage_gate(checks: list[bool], plan: PropertyPlan, scope: str) -> GateResult:
    nominal = plan.calibration.nominal_coverage
    if not checks:
        return GateResult("interval_coverage vs LiDAR", scope, plan.tier.value, "nothing paired",
                          f">= {nominal:.0%}", Status.SKIP)
    fraction = sum(checks) / len(checks)
    return GateResult(
        "interval_coverage vs LiDAR", scope, plan.tier.value,
        f"{sum(checks)}/{len(checks)} intervals contain the LiDAR value ({fraction:.0%})",
        f">= {nominal:.0%}", Status.PASS if fraction >= nominal else Status.FAIL,
        {"covered": sum(checks), "total": len(checks)},
    )


def _score_room_walls(score: RoomScore, reported: list[float], truth: list[float], tier: Tier) -> None:
    pairs, _ = pair_walls_cyclically(reported, [w for w in truth if w >= MIN_WALL_M])
    for predicted, actual in pairs:
        score.walls_total += 1
        if np.isfinite(predicted) and np.isfinite(actual):
            error = abs(predicted - actual)
            score.wall_errors.append(error / actual)
            score.walls_within += int(_within(error, actual, tier))


def score_photo(plan: PropertyPlan, reference: dict) -> TierScore:
    """Rooms pair by folder name: the photo tier labels each room with the folder its stills were in."""
    scope = f"{reference['capture']} photo"
    by_label = {room.label: room for room in plan.rooms}
    folders = [r for r in reference["rooms"] if r.get("photo_folder")]
    rooms: list[RoomScore] = []
    coverage: list[bool] = []
    for ref in folders:
        room = by_label.get(ref["photo_folder"])
        score = RoomScore(ref["room_id"], room.room_id if room else None, ref["floor_area_m2"])
        if room is not None:
            score.plan_area_m2 = room.floor_area.value
            score.area_error = (room.floor_area.value - ref["floor_area_m2"]) / ref["floor_area_m2"]
            score.area_interval_covers = room.floor_area.contains(ref["floor_area_m2"])
            coverage.append(score.area_interval_covers)
            _score_room_walls(score, wall_lengths(room), ref["wall_lengths_m"], plan.tier)
        rooms.append(score)

    reference_m2 = float(reference["photo_rooms_footprint_m2"])
    coverage.append(plan.total_floor_area.contains(reference_m2))
    found = sum(1 for r in rooms if r.plan_room is not None)
    gates = [
        _footprint_gate(plan, reference_m2, scope),
        GateResult("rooms reconstructed", scope, plan.tier.value, f"{found}/{len(folders)} folders give a room",
                   "every folder", Status.PASS if found == len(folders) else Status.FAIL),
        _walls_gate(rooms, plan.tier, scope),
        gate_room_overlap(plan, scope),
    ]
    folder_of = {r["room_id"]: r["photo_folder"] for r in folders}
    expected = {frozenset((folder_of[a["room_a"]], folder_of[a["room_b"]]))
                for a in reference["adjacency"] if a["room_a"] in folder_of and a["room_b"] in folder_of}
    label_of = {room.room_id: room.label for room in plan.rooms}
    reported = {frozenset((label_of.get(a.room_a, a.room_a), label_of.get(a.room_b, a.room_b))) for a in plan.adjacency}
    gates.append(_adjacency_gate(expected, reported, plan.tier, scope))
    gates.append(_coverage_gate(coverage, plan, scope))
    return TierScore(plan.tier.value, scope, gates, rooms)


def score_video(plan: PropertyPlan, reference: dict, lidar_plan: PropertyPlan,
                lidar_walls_xz: np.ndarray | None, plan_walls_xz: np.ndarray | None) -> TierScore:
    """Rooms pair by overlap once the video plan is registered onto the LiDAR plan's walls."""
    from cozmo.bench.repeat import compare_plans, register_walls

    scope = f"{reference['capture']} video"
    reference_m2 = float(reference["lidar_footprint_m2"])
    gates = [_footprint_gate(plan, reference_m2, scope)]
    notes: list[str] = []
    rooms: list[RoomScore] = []
    coverage = [plan.total_floor_area.contains(reference_m2)]

    skip = None
    registration = None
    if lidar_walls_xz is None or plan_walls_xz is None or len(plan_walls_xz) < 50:
        skip = "no wall evidence to register the two plans"
    else:
        registration = register_walls(lidar_walls_xz, plan_walls_xz)
        notes.append(f"registered on walls: {registration.within_10cm:.0%} of video wall cells within 10 cm of LiDAR, "
                     f"{registration.within_5cm:.0%} within 5 cm")
        if registration.within_10cm < REGISTRATION_MIN_WITHIN_10CM:
            skip = f"registration failed: {registration.within_10cm:.0%} of wall cells within 10 cm"

    if skip is None and registration is not None:
        pairs = compare_plans(lidar_plan, plan, registration)
        lidar_rooms = {r.room_id: r for r in lidar_plan.rooms}
        video_rooms = {r.room_id: r for r in plan.rooms}
        for pair in pairs:
            score = RoomScore(pair.room_a, pair.room_b, pair.area_a)
            if pair.room_b is not None:
                room = video_rooms[pair.room_b]
                score.plan_area_m2 = room.floor_area.value
                score.area_error = (room.floor_area.value - pair.area_a) / pair.area_a
                score.area_interval_covers = room.floor_area.contains(pair.area_a)
                coverage.append(score.area_interval_covers)
            for wall in pair.walls:
                if wall.length_a < MIN_WALL_M:
                    continue
                score.walls_total += 1
                if wall.length_b is not None:
                    error = abs(wall.length_b - wall.length_a)
                    score.wall_errors.append(error / wall.length_a)
                    score.walls_within += int(_within(error, wall.length_a, plan.tier))
            rooms.append(score)
        footprint_lidar = unary_union([Polygon(r.polygon).buffer(0) for r in lidar_rooms.values()])
        footprint_video = unary_union([Polygon(registration.apply(np.asarray(r.polygon))).buffer(0) for r in video_rooms.values()])
        iou = footprint_lidar.intersection(footprint_video).area / max(footprint_lidar.union(footprint_video).area, 1e-9)
        notes.append(f"footprint intersection-over-union with LiDAR after registration: {iou:.2f}")
        matched = sum(1 for r in rooms if r.plan_room is not None)
        gates.append(GateResult("rooms matched", scope, plan.tier.value,
                                f"{matched}/{len(rooms)} LiDAR rooms overlap a video room by half",
                                "every LiDAR room", Status.PASS if matched == len(rooms) else Status.FAIL,
                                {"footprint_iou": iou}))
    gates.append(_walls_gate(rooms, plan.tier, scope, skip))
    gates.append(gate_room_overlap(plan, scope))
    gates.append(_coverage_gate(coverage, plan, scope))
    return TierScore(plan.tier.value, scope, gates, rooms, notes)


def score_run(run_dir: Path, reference_path: Path, lidar_run: Path | None = None) -> TierScore:
    from cozmo.bench.repeat import load_wall_evidence

    plan = PropertyPlan(**json.loads((Path(run_dir) / "plan.json").read_text()))
    reference = json.loads(Path(reference_path).read_text())
    if plan.tier is Tier.PHOTO:
        return score_photo(plan, reference)
    if plan.tier is Tier.VIDEO:
        if lidar_run is None:
            raise ValueError("a video plan is scored by registering it onto the LiDAR run: pass --lidar-run")
        lidar_plan = PropertyPlan(**json.loads((Path(lidar_run) / "plan.json").read_text()))
        return score_video(plan, reference, lidar_plan, load_wall_evidence(lidar_run), load_wall_evidence(run_dir))
    raise ValueError(f"{plan.tier.value} plans are scored against tape by `cozmo benchmark`, not against themselves")
