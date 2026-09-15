"""Two captures of one space, registered on their walls and compared room by room and wall by wall.

`gates.gate_repeatability` pairs rooms by the names a room map gives them, which needs camera frames
matched by hand. Two walks of the same property already share its walls, so they can be put in one
frame without anyone naming anything: the vertical returns of one capture are aligned to the other's,
first coarsely over the four quarter turns a canonical frame leaves open, then by trimmed ICP. Rooms
are paired by overlap in that frame and walls by direction and position, and the brief's test is
applied to each pair: 1 cm or 0.5% per wall, ceilings within 1 cm.

The assignment's own zips hold such a pair: `single_scan_floor_only` and `single_scan_with_ceiling`
walk the same flat at the same tier.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import cv2
import numpy as np
from scipy.ndimage import gaussian_filter
from scipy.signal import fftconvolve
from scipy.spatial import cKDTree
from shapely.geometry import Polygon
from shapely.ops import unary_union

from cozmo.bench.gates import (
    CEILING_SPREAD_TOLERANCE_M,
    OPENING_PASS_FRACTION,
    REPEATABILITY_ABS_M,
    REPEATABILITY_REL,
    GateResult,
    Status,
)
from cozmo.schema import PropertyPlan

EVIDENCE_FILENAME = "scan_walls.npz"
COARSE_RESOLUTION_M = 0.04
ICP_TRIM_M = 0.08
ROOM_MATCH_MIN_IOU = 0.50
WALL_MATCH_OFFSET_M = 0.15
WALL_MATCH_MIN_OVERLAP = 0.50


def save_wall_evidence(artifacts, out_dir: Path) -> Path | None:
    """The cells where vertical structure was measured, in the plan's own frame."""
    occupancy = getattr(artifacts, "occupancy", None)
    if occupancy is None:
        return None
    cells = np.argwhere(occupancy.wall_weight > 0).astype(float)
    xz = occupancy.grid.to_world(cells).astype(np.float32)
    path = Path(out_dir) / EVIDENCE_FILENAME
    np.savez_compressed(path, xz=xz, resolution=np.float32(occupancy.grid.resolution))
    return path


def load_wall_evidence(run_dir: Path) -> np.ndarray | None:
    path = Path(run_dir) / EVIDENCE_FILENAME
    if not path.exists():
        return None
    with np.load(path) as data:
        return np.asarray(data["xz"], dtype=np.float64)


@dataclass
class Registration:
    rotation: np.ndarray
    translation: np.ndarray
    quarter_turns: int
    coarse_score: float
    within_5cm: float
    within_10cm: float

    def apply(self, xy: np.ndarray) -> np.ndarray:
        return np.asarray(xy, float) @ self.rotation.T + self.translation

    @property
    def angle_deg(self) -> float:
        return float(np.degrees(np.arctan2(self.rotation[1, 0], self.rotation[0, 0])))


def _rotation(theta: float) -> np.ndarray:
    return np.array([[np.cos(theta), -np.sin(theta)], [np.sin(theta), np.cos(theta)]])


def _raster(points: np.ndarray, origin: np.ndarray, shape: tuple[int, int], res: float) -> np.ndarray:
    image = np.zeros(shape, np.float32)
    cells = np.round((points - origin) / res).astype(int)
    ok = (cells[:, 0] >= 0) & (cells[:, 0] < shape[1]) & (cells[:, 1] >= 0) & (cells[:, 1] < shape[0])
    image[cells[ok, 1], cells[ok, 0]] = 1.0
    return gaussian_filter(image, 1.2)


def register_walls(reference: np.ndarray, moving: np.ndarray, iterations: int = 40) -> Registration:
    """Rigid 2D transform taking `moving` wall evidence onto `reference`."""
    res = COARSE_RESOLUTION_M
    best: tuple[float, int, np.ndarray, np.ndarray] | None = None
    origin_a = reference.min(axis=0) - 1.0
    shape_a = (int((reference[:, 1].max() - origin_a[1]) / res) + 30, int((reference[:, 0].max() - origin_a[0]) / res) + 30)
    image_a = _raster(reference, origin_a, shape_a, res)
    for turns in range(4):
        rotation = _rotation(turns * np.pi / 2)
        turned = moving @ rotation.T
        origin_b = turned.min(axis=0) - 1.0
        shape_b = (int((turned[:, 1].max() - origin_b[1]) / res) + 30, int((turned[:, 0].max() - origin_b[0]) / res) + 30)
        image_b = _raster(turned, origin_b, shape_b, res)
        correlation = fftconvolve(image_a, image_b[::-1, ::-1], mode="full")
        peak = np.unravel_index(int(np.argmax(correlation)), correlation.shape)
        score = float(correlation[peak] / np.sqrt((image_a ** 2).sum() * (image_b ** 2).sum()))
        shift = np.array([peak[1] - (shape_b[1] - 1), peak[0] - (shape_b[0] - 1)], float)
        translation = origin_a + res * shift - origin_b
        if best is None or score > best[0]:
            best = (score, turns, rotation, translation)
    assert best is not None
    score, turns, rotation, translation = best
    tree = cKDTree(reference)
    for _ in range(iterations):
        moved = moving @ rotation.T + translation
        distance, index = tree.query(moved)
        keep = distance < ICP_TRIM_M
        if keep.sum() < 50:
            break
        src, dst = moved[keep], reference[index[keep]]
        cs, cd = src.mean(axis=0), dst.mean(axis=0)
        u, _, vt = np.linalg.svd((src - cs).T @ (dst - cd))
        step = vt.T @ u.T
        if np.linalg.det(step) < 0:
            vt[1] *= -1
            step = vt.T @ u.T
        rotation, translation = step @ rotation, step @ translation + (cd - step @ cs)
    distance, _ = tree.query(moving @ rotation.T + translation)
    return Registration(rotation, translation, turns, score, float((distance < 0.05).mean()), float((distance < 0.10).mean()))


@dataclass
class WallPair:
    length_a: float
    length_b: float | None
    offset_m: float | None
    agrees: bool


@dataclass
class RoomPair:
    room_a: str
    room_b: str | None
    area_a: float
    area_b: float | None
    iou: float
    ceiling_a: float | None
    ceiling_b: float | None
    walls: list[WallPair] = field(default_factory=list)


def _walls(room, transform=None) -> list[tuple[np.ndarray, np.ndarray, float]]:
    out = []
    for wall in room.walls:
        start, end = np.asarray(wall.start, float), np.asarray(wall.end, float)
        if transform is not None:
            start, end = transform(start[None, :])[0], transform(end[None, :])[0]
        out.append((start, end, float(wall.length.value)))
    return out


def _pair_walls(walls_a, walls_b) -> list[WallPair]:
    pairs: list[WallPair] = []
    used: set[int] = set()
    for start, end, length in walls_a:
        along = (end - start) / max(np.linalg.norm(end - start), 1e-9)
        normal = np.array([-along[1], along[0]])
        best: tuple[float, int] | None = None
        for j, (s2, e2, _) in enumerate(walls_b):
            if j in used:
                continue
            d2 = (e2 - s2) / max(np.linalg.norm(e2 - s2), 1e-9)
            if abs(float(along @ d2)) < np.cos(np.deg2rad(10.0)):
                continue
            offset = abs(float((0.5 * (s2 + e2) - start) @ normal))
            if offset > WALL_MATCH_OFFSET_M:
                continue
            t = sorted([float((s2 - start) @ along), float((e2 - start) @ along)])
            overlap = min(length, t[1]) - max(0.0, t[0])
            if overlap < WALL_MATCH_MIN_OVERLAP * min(length, t[1] - t[0]):
                continue
            score = overlap - offset
            if best is None or score > best[0]:
                best = (score, j)
        if best is None:
            pairs.append(WallPair(length, None, None, False))
            continue
        j = best[1]
        used.add(j)
        s2, e2, length_b = walls_b[j]
        offset = abs(float((0.5 * (s2 + e2) - start) @ normal))
        difference = abs(length - length_b)
        agrees = difference <= REPEATABILITY_ABS_M or difference <= REPEATABILITY_REL * max(length, length_b)
        pairs.append(WallPair(length, length_b, offset, agrees))
    return pairs


def compare_plans(plan_a: PropertyPlan, plan_b: PropertyPlan, registration: Registration) -> list[RoomPair]:
    shapes_a = {room.room_id: Polygon(room.polygon).buffer(0) for room in plan_a.rooms}
    shapes_b = {room.room_id: Polygon(registration.apply(np.asarray(room.polygon))).buffer(0) for room in plan_b.rooms}
    candidates = []
    for ida, sa in shapes_a.items():
        for idb, sb in shapes_b.items():
            union = sa.union(sb).area
            iou = sa.intersection(sb).area / union if union > 0 else 0.0
            if iou >= ROOM_MATCH_MIN_IOU:
                candidates.append((iou, ida, idb))
    rooms_a = {room.room_id: room for room in plan_a.rooms}
    rooms_b = {room.room_id: room for room in plan_b.rooms}
    matched: dict[str, tuple[str, float]] = {}
    taken: set[str] = set()
    for iou, ida, idb in sorted(candidates, reverse=True):
        if ida in matched or idb in taken:
            continue
        matched[ida] = (idb, iou)
        taken.add(idb)
    out: list[RoomPair] = []
    for ida, room_a in rooms_a.items():
        ceiling_a = room_a.ceiling_height.value if room_a.ceiling_height is not None else None
        if ida not in matched:
            out.append(RoomPair(ida, None, room_a.floor_area.value, None, 0.0, ceiling_a, None,
                                [WallPair(w.length.value, None, None, False) for w in room_a.walls]))
            continue
        idb, iou = matched[ida]
        room_b = rooms_b[idb]
        ceiling_b = room_b.ceiling_height.value if room_b.ceiling_height is not None else None
        walls = _pair_walls(_walls(room_a), _walls(room_b, registration.apply))
        out.append(RoomPair(ida, idb, room_a.floor_area.value, room_b.floor_area.value, iou, ceiling_a, ceiling_b, walls))
    return out


def repeatability_gate(pairs: list[RoomPair], scope: str, tier: str) -> GateResult:
    matched = [p for p in pairs if p.room_b is not None]
    if not matched:
        return GateResult("repeatability (registered)", scope, tier, "no room overlaps its counterpart by half",
                          "1 cm or 0.5% per wall; ceiling spread <= 1 cm", Status.SKIP)
    walls = [w for p in pairs for w in p.walls]
    agree = sum(w.agrees for w in walls)
    differences = [abs(w.length_a - w.length_b) for w in walls if w.length_b is not None]
    spreads = [abs(p.ceiling_a - p.ceiling_b) for p in matched if p.ceiling_a is not None and p.ceiling_b is not None]
    fraction = agree / max(len(walls), 1)
    ceiling_ok = all(s <= CEILING_SPREAD_TOLERANCE_M for s in spreads)
    measured = (
        f"{agree}/{len(walls)} walls agree ({fraction:.0%}), median length difference "
        f"{(np.median(differences) * 100 if differences else float('nan')):.1f} cm; "
        f"{len(matched)}/{len(pairs)} rooms matched; ceiling spread "
        + (f"{max(spreads) * 100:.1f} cm over {len(spreads)} rooms" if spreads else "not measurable (one capture has no ceiling)")
    )
    status = Status.PASS if fraction >= OPENING_PASS_FRACTION and ceiling_ok else Status.FAIL
    return GateResult("repeatability (registered)", scope, tier, measured,
                      "1 cm or 0.5% per wall; ceiling spread <= 1 cm", status,
                      detail={"walls_agree": agree, "walls": len(walls), "ceiling_spreads_cm": [s * 100 for s in spreads]})


def draw_registered(plan_a: PropertyPlan, plan_b: PropertyPlan, evidence_a: np.ndarray, registration: Registration,
                    out_path: Path, scale_px_per_m: float = 100.0) -> None:
    rings_b = [registration.apply(np.asarray(room.polygon)) for room in plan_b.rooms]
    everything = np.vstack([evidence_a] + [np.asarray(r.polygon) for r in plan_a.rooms] + rings_b)
    lo = everything.min(axis=0) - 0.5
    hi = everything.max(axis=0) + 0.5
    width, height = int((hi[0] - lo[0]) * scale_px_per_m), int((hi[1] - lo[1]) * scale_px_per_m)
    image = np.full((height, width, 3), 255, np.uint8)

    def px(xy: np.ndarray) -> np.ndarray:
        xy = np.asarray(xy, float)
        return np.round(np.stack([(xy[:, 0] - lo[0]) * scale_px_per_m, (hi[1] - xy[:, 1]) * scale_px_per_m], 1)).astype(np.int32)

    for x, y in px(evidence_a):
        if 0 <= y < height and 0 <= x < width:
            image[y, x] = (150, 150, 150)
    for room in plan_a.rooms:
        cv2.polylines(image, [px(room.polygon).reshape(-1, 1, 2)], True, (200, 60, 20), 3, cv2.LINE_AA)
    for ring in rings_b:
        cv2.polylines(image, [px(ring).reshape(-1, 1, 2)], True, (0, 150, 0), 2, cv2.LINE_AA)
    cv2.putText(image, f"blue: {plan_a.capture_id}   green: {plan_b.capture_id}, registered on walls "
                f"({registration.within_5cm:.0%} of wall cells within 5 cm)", (10, 24),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 2, cv2.LINE_AA)
    cv2.imwrite(str(out_path), image)


def run_repeatability(run_a: Path, run_b: Path, out_dir: Path) -> GateResult:
    plan_a = PropertyPlan(**json.loads((Path(run_a) / "plan.json").read_text()))
    plan_b = PropertyPlan(**json.loads((Path(run_b) / "plan.json").read_text()))
    evidence_a, evidence_b = load_wall_evidence(run_a), load_wall_evidence(run_b)
    scope = f"{Path(run_a).name} vs {Path(run_b).name}"
    if evidence_a is None or evidence_b is None:
        return GateResult("repeatability (registered)", scope, plan_a.tier.value,
                          f"{EVIDENCE_FILENAME} missing from a run; rerun cozmo run", "1 cm or 0.5% per wall", Status.SKIP)
    registration = register_walls(evidence_a, evidence_b)
    pairs = compare_plans(plan_a, plan_b, registration)
    gate = repeatability_gate(pairs, scope, plan_a.tier.value)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    footprint_a = unary_union([Polygon(r.polygon).buffer(0) for r in plan_a.rooms])
    footprint_b = unary_union([Polygon(registration.apply(np.asarray(r.polygon))).buffer(0) for r in plan_b.rooms])
    report = {
        "gate": asdict(gate) | {"status": gate.status.value},
        "registration": {
            "quarter_turns": registration.quarter_turns,
            "angle_deg": registration.angle_deg,
            "translation_m": registration.translation.tolist(),
            "wall_cells_within_5cm": registration.within_5cm,
            "wall_cells_within_10cm": registration.within_10cm,
        },
        "footprint": {
            "area_a_m2": footprint_a.area,
            "area_b_m2": footprint_b.area,
            "iou": footprint_a.intersection(footprint_b).area / max(footprint_a.union(footprint_b).area, 1e-9),
        },
        "rooms": [asdict(p) for p in pairs],
    }
    (out_dir / "repeatability.json").write_text(json.dumps(report, indent=2, default=float))
    draw_registered(plan_a, plan_b, evidence_a, registration, out_dir / "repeat_overlay.png")
    return gate
