"""Reading tape or laser ground truth, and pairing it with what the pipeline reported.

Pairing is the part that decides whether a benchmark measures anything. If a reported wall
is allowed to be compared against whichever ground-truth wall it happens to be closest to,
the benchmark cannot fail: every error shrinks to the nearest available truth and the table
reports the pipeline's precision at finding numbers rather than its accuracy at measuring
rooms.

So walls are paired cyclically. The recording sheet asks for a room's walls in order around
it, and the plan's walls come out in order around the polygon, so the only freedom is where
the two rings start and which way they run. That is one rotation and one reflection --
chosen once per room by total error, and then every wall is compared to the wall it is
actually paired with, including the ones that pair badly. A pipeline that misses a wall is
punished by the whole ring going out of step, which is the correct punishment.

Rooms are paired by an explicit mapping the operator writes down, never inferred. Inferring
which reconstructed room is the bedroom from its area is circular when area is one of the
things being scored.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from cozmo.schema import OpeningType, PropertyPlan, Room

CYCLIC_QUANTITIES = {"wall_length"}


@dataclass
class TruthRecord:
    capture_id: str
    room: str
    quantity: str
    identifier: str
    value_m: float
    tool: str
    notes: str = ""


@dataclass
class GroundTruth:
    records: list[TruthRecord] = field(default_factory=list)
    room_map: dict = field(default_factory=dict)

    @property
    def captures(self) -> set[str]:
        return {r.capture_id for r in self.records}

    def room_name(self, capture_id: str | None, room_id: str) -> str | None:
        """The taped room a reconstructed room id stands for, in one capture.

        Room ids are assigned per reconstruction, ordered by area, so `room_03` in one capture
        and `room_03` in another are unrelated rooms. A single flat map applied to both is
        therefore wrong for at least one of them, and on the benchmark flat it was wrong for
        both: the home walk's bedroom was being scored as the hall, and the long walk's
        bathroom as the passage. The map is keyed by capture. A flat map is still read, for a
        run with a single capture, and it is never applied across captures.
        """
        nested = self.room_map.get(capture_id) if capture_id is not None else None
        if isinstance(nested, dict):
            value = nested.get(room_id)
            return value if isinstance(value, str) else None
        if any(isinstance(v, dict) for v in self.room_map.values()):
            return None
        value = self.room_map.get(room_id)
        return value if isinstance(value, str) else None

    def for_capture(self, capture_id: str) -> list[TruthRecord]:
        return [r for r in self.records if r.capture_id == capture_id]

    def rooms(self, capture_id: str) -> set[str]:
        return {r.room for r in self.for_capture(capture_id) if r.room}

    def values(self, capture_id: str, room: str, quantity: str) -> list[TruthRecord]:
        return [
            r
            for r in self.records
            if r.capture_id == capture_id and r.room == room and r.quantity == quantity
        ]

    def scalar(self, capture_id: str, room: str, quantity: str) -> float | None:
        """A single value for a quantity, averaging repeated readings of the same thing.

        Ceiling height is recorded three times per room precisely so the spread of those
        readings is visible; the mean is the truth and the spread is the truth's own
        uncertainty, which is reported alongside rather than discarded.
        """
        found = self.values(capture_id, room, quantity)
        if not found:
            return None
        return float(np.mean([r.value_m for r in found]))

    def spread(self, capture_id: str, room: str, quantity: str) -> float:
        found = self.values(capture_id, room, quantity)
        if len(found) < 2:
            return 0.0
        return float(np.ptp([r.value_m for r in found]))


def load_ground_truth(
    path: Path | str | list[Path | str], room_map_path: Path | str | None = None
) -> GroundTruth:
    """Read one recording sheet or several. Comment lines and blank rows are skipped.

    Several sheets keep sources apart: the operator's tape in one file, the exact dimensions of
    the ray-traced fixtures in another, each row still naming its tool.
    """
    paths = [path] if isinstance(path, (str, Path)) else list(path)
    records: list[TruthRecord] = []
    for sheet in map(Path, paths):
        if not sheet.exists():
            continue
        with sheet.open() as handle:
            rows = [line for line in handle if line.strip() and not line.lstrip().startswith("#")]
        for row in csv.DictReader(rows):
            if not row.get("quantity"):
                continue
            try:
                value = float(row["value_m"])
            except (TypeError, ValueError):
                continue
            records.append(
                TruthRecord(
                    capture_id=(row.get("capture_id") or "").strip(),
                    room=(row.get("room") or "").strip(),
                    quantity=(row.get("quantity") or "").strip(),
                    identifier=(row.get("identifier") or "").strip(),
                    value_m=value,
                    tool=(row.get("tool") or "").strip(),
                    notes=(row.get("notes") or "").strip(),
                )
            )

    room_map: dict[str, str] = {}
    if room_map_path:
        candidate = Path(room_map_path)
        if candidate.exists():
            room_map = {
                k: v for k, v in json.loads(candidate.read_text()).items() if not k.startswith("_")
            }
    return GroundTruth(records=records, room_map=room_map)


def resolve_capture_id(plan, directory_name: str, truth: GroundTruth) -> str:
    """Which capture id in the ground truth this run corresponds to.

    A run's output directory is named for readability ("multiroom_home") while the tape
    rows are keyed by the capture the operator measured, which is the Stray folder id
    ("ae3edc814d"). Preferring the plan's own capture_id when the ground truth knows it,
    and falling back to the directory name otherwise, covers both.

    This lives here, once, because `benchmark` and `calibrate` each had their own rule and
    only one of them was right: benchmark scored the property correctly while calibrate
    reported "the ground truth and the plans share no rooms" on the same inputs, so no
    quantile could ever be fitted from measurements that were sitting right there.
    """
    if getattr(plan, "capture_id", None) in truth.captures:
        return plan.capture_id
    return directory_name


def resolve_room(plan_room: Room, truth: GroundTruth, capture_id: str | None = None) -> str | None:
    """The ground-truth room this reconstructed room corresponds to.

    From the operator's mapping, or from the room's own label when the reconstruction was
    given one. Never guessed from geometry.
    """
    # A folder name the operator wrote (hall, bedroom, …) is the room. The LiDAR
    # room_map is only for reconstructions still labelled "room". Checking the map
    # first would rename a photo-tier bathroom to hall just because both are room_01.
    if plan_room.label and plan_room.label != "room":
        return plan_room.label
    mapped = truth.room_name(capture_id, plan_room.room_id)
    if mapped:
        return mapped
    return None


def pair_walls_cyclically(
    reported: list[float], truth: list[float]
) -> tuple[list[tuple[float, float]], float]:
    """Pair two rings of wall lengths by the best rotation and direction.

    Returns the pairs and the total absolute error of the chosen alignment. When the two
    rings differ in length the shorter is padded with NaN so that a missing or invented
    wall shows up as an unpairable entry rather than quietly disappearing.
    """
    n = max(len(reported), len(truth))
    if n == 0:
        return [], 0.0
    a = list(reported) + [float("nan")] * (n - len(reported))
    b = list(truth) + [float("nan")] * (n - len(truth))

    best: tuple[float, list[tuple[float, float]]] | None = None
    for flipped in (False, True):
        candidate = list(reversed(a)) if flipped else a
        for shift in range(n):
            rotated = candidate[shift:] + candidate[:shift]
            pairs = list(zip(rotated, b))
            error = float(
                np.nansum([abs(x - y) for x, y in pairs if np.isfinite(x) and np.isfinite(y)])
            )
            unpairable = sum(1 for x, y in pairs if not (np.isfinite(x) and np.isfinite(y)))
            # An unpaired wall is penalised heavily so alignment is never improved by
            # simply leaving the awkward walls unmatched.
            score = error + 10.0 * unpairable
            if best is None or score < best[0]:
                best = (score, pairs)
    assert best is not None
    return best[1], best[0]


def wall_lengths(room: Room) -> list[float]:
    """Reported wall lengths in ring order, skipping fragments too short to be walls."""
    return [w.length.value for w in room.walls if w.length.value >= 0.30]


def opening_widths(room: Room, kinds: tuple[OpeningType, ...] | None = None) -> list[float]:
    selected = room.openings if kinds is None else [o for o in room.openings if o.type in kinds]
    return [o.width.value for o in selected]


def collect_residuals(
    plan: PropertyPlan, truth: GroundTruth, capture_id: str
) -> dict[tuple[str, str], list[tuple[float, float]]]:
    """(predicted, truth) pairs per (tier, quantity), for fitting conformal quantiles."""
    out: dict[tuple[str, str], list[tuple[float, float]]] = {}
    tier = plan.tier.value

    def add(quantity: str, predicted: float, actual: float) -> None:
        if np.isfinite(predicted) and np.isfinite(actual) and actual > 0:
            out.setdefault((tier, quantity), []).append((float(predicted), float(actual)))

    for room in plan.rooms:
        name = resolve_room(room, truth, capture_id)
        if name is None:
            continue

        actual_height = truth.scalar(capture_id, name, "ceiling_height")
        if actual_height is not None and room.ceiling_height is not None and room.ceiling_height.value > 0:
            add("ceiling_height", room.ceiling_height.value, actual_height)

        truth_walls = [r.value_m for r in truth.values(capture_id, name, "wall_length")]
        if truth_walls:
            pairs, _ = pair_walls_cyclically(wall_lengths(room), truth_walls)
            for predicted, actual in pairs:
                if np.isfinite(predicted) and np.isfinite(actual):
                    add("wall_length", predicted, actual)

        actual_area = truth.scalar(capture_id, name, "floor_area")
        if actual_area is not None:
            add("floor_area", room.floor_area.value, actual_area)

        truth_openings = sorted(
            r.value_m for r in truth.values(capture_id, name, "opening_width")
        )
        if truth_openings:
            reported = sorted(opening_widths(room))
            for predicted, actual in zip(reported, truth_openings):
                add("opening_width", predicted, actual)

    return out
