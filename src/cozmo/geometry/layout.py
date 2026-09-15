"""Rooms from what the scan saw: wall barriers, doorways, and floor the operator could stand on.

The cell complex (`geometry/cellcomplex.py`) partitions the whole property by every wall line and
labels each face. On the assignment's two whole-flat scans that left the hallway in no room,
drew rooms across floor nobody saw, and put the same flat's rooms in different places from one
scan to the other. This module builds rooms from the evidence directly, in four steps.

1. **Barriers.** A cell is wall where vertical structure was measured in it: returns in the
   structural band above furniture, and every return on a fitted wall plane, which includes the
   wall under a window sill. Low furniture does not block; wardrobes and fridges do.
2. **Doorways.** A doorway is a gap in a wall. Gaps are proposed from the fitted wall planes (two
   runs on one line with a door-sized gap between them, or a run that stops short of the wall
   across its end) and from the raster (a gap in a thin wall run, where no plane was fitted).
   Each accepted gap is closed with a bridge, except where the walls either side of it carry on
   in both directions at both ends: that is a corridor crossing the line, not a door in it.
3. **Rooms.** Interior evidence is floor, ceiling, furniture tops, carved free space next to
   those, and the walk. Its connected pieces between barriers and bridges are rooms. A piece
   nobody walked into is left out and named in the notes; a sliver joins the room it touches most;
   a part of a room reached only through a neck narrower than a door, and never walked, is cut off.
4. **Outlines.** Each room is fitted with a rectilinear outline whose edges lie on the measured
   wall faces that face into it, so a wall's length comes from plane positions rather than from
   the raster. An edge standing at furniture in front of a wall face seen from inside the room is
   pushed out to that face, by at most 0.35 m.

The frame must be axis-aligned (`walls.canonical_rotation`) for steps 2 and 4.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np
from shapely.geometry import Polygon, box
from shapely.geometry.polygon import orient
from shapely.ops import unary_union

from cozmo.geometry.fusion import FusedCloud
from cozmo.geometry.grid import Grid2D
from cozmo.geometry.occupancy import OccupancyMaps
from cozmo.geometry.refine import polygon_mask, stairwell_drops
from cozmo.geometry.walls import WallSegment

AXIS_TOLERANCE_DEG = 3.0
BARRIER_MIN_HEIGHT_M = 0.15
# Line bridges: a gap between two runs on one wall line, or between a run's end and the wall across it.
LINE_GAP_M = (0.35, 1.40)
LINE_GROUP_TOLERANCE_M = 0.05
# Raster bridges: gaps in thin wall runs where no plane was fitted.
RASTER_GAP_M = (0.06, 1.40)
RASTER_MIN_RUN_M = 0.40
RASTER_HOLE_RUN_M = 0.20
HOLE_MAX_M = 0.50
MAX_WALL_THICKNESS_M = 0.35
# A bridge is a corridor crossing when barrier carries on this far both ways at both of its ends.
CROSSING_EXTENT_M = 0.50
TRACK_REACH_M = 0.25
WALK_REACH_M = 0.35
INTERIOR_REACH_M = 0.45
MIN_ROOM_AREA_M2 = 1.0
UNWALKED_REPORT_M2 = 2.0
SLIVER_AREA_M2 = 1.2
UNWALKED_NECK_M = 1.0
UNWALKED_PIECE_M2 = 0.8
MAX_HOLE_FILL_M2 = 1.5
# Outline fitting.
MIN_BOUNDARY_RUN_M = 0.24
FACE_SNAP_M = 0.15
LINE_MERGE_M = 0.05
MAX_PUSH_M = 0.35
MIN_PUSH_M = 0.10
PUSH_MIN_OVERLAP = 0.6
# Doorways.
DOOR_MIN_WIDTH_M = 0.55
JAMB_BAND_M = 0.20
JAMB_BIN_M = 0.01
# A door head is above this; returns lower in the gap are a door leaf, a curtain or clutter.
HEAD_MIN_M = 1.80


@dataclass
class WallLine:
    """A fitted wall face on the axis-aligned frame. H: z = coord along x; V: x = coord along z."""

    kind: str
    coord: float
    lo: float
    hi: float
    normal_sign: float
    weight: float


@dataclass
class Bridge:
    kind: str
    coord: float
    lo: float
    hi: float
    source: str
    accepted: bool = True
    note: str = ""

    @property
    def width(self) -> float:
        return self.hi - self.lo

    def endpoints(self) -> tuple[np.ndarray, np.ndarray]:
        if self.kind == "H":
            return np.array([self.lo, self.coord]), np.array([self.hi, self.coord])
        return np.array([self.coord, self.lo]), np.array([self.coord, self.hi])

    def point(self, along: float, across: float = 0.0) -> np.ndarray:
        if self.kind == "H":
            return np.array([along, self.coord + across])
        return np.array([self.coord + across, along])


@dataclass
class Doorway:
    """A way through a wall between two rooms, or out of one room into space no room holds."""

    kind: str
    coord: float
    lo: float
    hi: float
    room_a: int
    room_b: int | None
    width_m: float
    width_sigma_m: float
    width_source: str
    head_height_m: float | None
    crossings: int

    @property
    def centre(self) -> np.ndarray:
        mid = 0.5 * (self.lo + self.hi)
        return np.array([mid, self.coord]) if self.kind == "H" else np.array([self.coord, mid])

    @property
    def direction(self) -> np.ndarray:
        return np.array([1.0, 0.0]) if self.kind == "H" else np.array([0.0, 1.0])


@dataclass
class Layout:
    polygons: dict[int, Polygon]
    masks: dict[int, np.ndarray]
    doorways: list[Doorway]
    bridges: list[Bridge]
    barrier: np.ndarray
    interior: np.ndarray
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------------------------
# Raster helpers


def _disc(radius_m: float, res: float) -> np.ndarray:
    r = max(int(round(radius_m / res)), 1)
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))


def _drop_small(mask: np.ndarray, min_cells: int) -> np.ndarray:
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    keep = np.zeros(n, bool)
    keep[1:] = stats[1:, cv2.CC_STAT_AREA] >= min_cells
    return keep[labels]


def _fill_holes(mask: np.ndarray, max_cells: int) -> np.ndarray:
    n, labels, stats, _ = cv2.connectedComponentsWithStats((~mask).astype(np.uint8), connectivity=4)
    h, w = mask.shape
    out = mask.copy()
    for k in range(1, n):
        x, y, bw, bh, area = stats[k]
        if area <= max_cells and x > 0 and y > 0 and x + bw < w and y + bh < h:
            out[labels == k] = True
    return out


def _track_mask(occ: OccupancyMaps, reach_m: float) -> np.ndarray:
    mask = np.zeros(occ.grid.shape, np.uint8)
    if len(occ.camera_track):
        cells = np.round(occ.camera_track[:, ::-1]).astype(np.int32).reshape(-1, 1, 2)
        cv2.polylines(mask, [cells], False, 1, 1)
        mask = cv2.dilate(mask, _disc(reach_m, occ.grid.resolution))
    return mask.astype(bool)


def _run_lengths(mask: np.ndarray) -> np.ndarray:
    """Length, in cells, of the vertical run (along rows) each True cell belongs to."""
    out = np.zeros(mask.shape, np.int32)
    for j in range(mask.shape[1]):
        column = mask[:, j].astype(np.int8)
        edges = np.flatnonzero(np.diff(np.concatenate([[0], column, [0]])))
        for start, end in zip(edges[0::2], edges[1::2]):
            out[start:end, j] = end - start
    return out


# ---------------------------------------------------------------------------------------------
# Step 1: barriers and interior evidence


def barrier_map(occ: OccupancyMaps, walls: list[WallSegment], cloud: FusedCloud, floor_y: float) -> np.ndarray:
    grid = occ.grid
    barrier = occ.wall_weight > 0
    if walls:
        indices = np.unique(np.concatenate([w.point_indices for w in walls]))
        points = cloud.points[indices]
        keep = (points[:, 1] - floor_y) > BARRIER_MIN_HEIGHT_M
        cells = grid.to_cell(points[keep][:, [0, 2]].astype(np.float64))
        ok = grid.inside(cells)
        barrier[cells[ok, 0], cells[ok, 1]] = True
    barrier = _drop_small(barrier, 6)
    return cv2.morphologyEx(barrier.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8)) > 0


def interior_evidence(occ: OccupancyMaps, barrier: np.ndarray) -> np.ndarray:
    res = occ.grid.resolution
    track = _track_mask(occ, TRACK_REACH_M)
    core = occ.floor_hits | track
    if occ.ceiling_hits is not None:
        core |= occ.ceiling_hits & ~barrier
    if occ.surface_hits is not None:
        core |= occ.surface_hits
    near = cv2.dilate(core.astype(np.uint8), _disc(INTERIOR_REACH_M, res)) > 0
    interior = core | (occ.free_mask & occ.observed & near)
    interior = cv2.morphologyEx(interior.astype(np.uint8), cv2.MORPH_CLOSE, _disc(0.09, res)) > 0
    for drop in stairwell_drops(occ):
        interior &= ~(cv2.dilate(drop.astype(np.uint8), _disc(0.06, res)) > 0)
    return interior


# ---------------------------------------------------------------------------------------------
# Step 2: doorways


def wall_lines(walls: list[WallSegment]) -> list[WallLine]:
    cos_tol = np.cos(np.deg2rad(AXIS_TOLERANCE_DEG))
    out: list[WallLine] = []
    for wall in walls:
        d, n = wall.direction, wall.normal_xz
        if abs(d[0]) >= cos_tol:
            out.append(WallLine("H", 0.5 * float(wall.start[1] + wall.end[1]), float(min(wall.start[0], wall.end[0])),
                                float(max(wall.start[0], wall.end[0])), float(np.sign(n[1])), wall.support_weight))
        elif abs(d[1]) >= cos_tol:
            out.append(WallLine("V", 0.5 * float(wall.start[0] + wall.end[0]), float(min(wall.start[1], wall.end[1])),
                                float(max(wall.start[1], wall.end[1])), float(np.sign(n[0])), wall.support_weight))
    return out


def line_bridges(lines: list[WallLine]) -> list[Bridge]:
    bridges: list[Bridge] = []
    for kind in ("H", "V"):
        same = sorted((ln for ln in lines if ln.kind == kind), key=lambda ln: ln.coord)
        across = [ln for ln in lines if ln.kind != kind]
        groups: list[list[WallLine]] = []
        for line in same:
            if groups and abs(line.coord - groups[-1][-1].coord) <= LINE_GROUP_TOLERANCE_M:
                groups[-1].append(line)
            else:
                groups.append([line])
        for group in groups:
            weights = np.array([max(ln.hi - ln.lo, 1e-3) for ln in group])
            coord = float(np.average([ln.coord for ln in group], weights=weights))
            spans: list[list[float]] = []
            for lo, hi in sorted([ln.lo, ln.hi] for ln in group):
                if spans and lo <= spans[-1][1] + 0.02:
                    spans[-1][1] = max(spans[-1][1], hi)
                else:
                    spans.append([lo, hi])
            for (_, end), (start, _) in zip(spans, spans[1:]):
                if LINE_GAP_M[0] <= start - end <= LINE_GAP_M[1]:
                    bridges.append(Bridge(kind, coord, end, start, "gap between two runs of one wall"))
            for end, direction in ((spans[-1][1], 1.0), (spans[0][0], -1.0)):
                best: tuple[float, float] | None = None
                for other in across:
                    if not (other.lo - 0.10 <= coord <= other.hi + 0.10):
                        continue
                    gap = (other.coord - end) * direction
                    if LINE_GAP_M[0] <= gap <= LINE_GAP_M[1] and (best is None or gap < best[0]):
                        best = (gap, other.coord)
                if best is not None:
                    lo, hi = sorted((end, best[1]))
                    bridges.append(Bridge(kind, coord, lo, hi, "wall run stopping short of the wall across its end"))
    return bridges


def _barrier_extent(barrier: np.ndarray, grid: Grid2D, start: np.ndarray, step: np.ndarray,
                    lateral: np.ndarray, max_m: float = 0.9) -> float:
    res = grid.resolution
    last, misses = 0, 0
    for s in range(1, int(max_m / res) + 1):
        found = False
        for k in (-2, -1, 0, 1, 2):
            p = start + step * s * res + lateral * k * res
            r, c = grid.to_cell(p[None, :])[0]
            if 0 <= r < barrier.shape[0] and 0 <= c < barrier.shape[1] and barrier[r, c]:
                found = True
                break
        if found:
            last, misses = s, 0
        else:
            misses += 1
            if misses > 2:
                break
    return last * res


def is_corridor_crossing(bridge: Bridge, barrier: np.ndarray, grid: Grid2D, offset_m: float = 0.06) -> bool:
    along = np.array([1.0, 0.0]) if bridge.kind == "H" else np.array([0.0, 1.0])
    across = np.array([0.0, 1.0]) if bridge.kind == "H" else np.array([1.0, 0.0])
    ends = []
    for t, outward in ((bridge.lo, -1.0), (bridge.hi, 1.0)):
        start = bridge.point(t) + along * outward * offset_m
        ends.append((_barrier_extent(barrier, grid, start, across, along),
                     _barrier_extent(barrier, grid, start, -across, along)))
    return all(a >= CROSSING_EXTENT_M and b >= CROSSING_EXTENT_M for a, b in ends)


def raster_bridges(barrier: np.ndarray, interior: np.ndarray, grid: Grid2D) -> list[Bridge]:
    """Gaps in thin wall runs, for walls no plane was fitted to."""
    res = grid.resolution
    gap_lo, gap_hi = max(int(round(RASTER_GAP_M[0] / res)), 1), int(round(RASTER_GAP_M[1] / res))
    min_run, hole_run = int(round(RASTER_MIN_RUN_M / res)), int(round(RASTER_HOLE_RUN_M / res))
    hole_max = int(round(HOLE_MAX_M / res))
    max_thick = int(round(MAX_WALL_THICKNESS_M / res))
    out: list[Bridge] = []
    for horizontal in (True, False):
        wall = barrier if horizontal else barrier.T
        inside = interior if horizontal else interior.T
        thin = wall & (_run_lengths(wall) <= max_thick)
        thick = wall & ~thin
        h, w = wall.shape
        found: dict[tuple[int, int], list[tuple[int, int, int]]] = {}
        for r in range(1, h - 1):
            row_thin = thin[r - 1:r + 2].any(axis=0)
            classes = np.where(row_thin, 1, np.where(thick[r], 2, 0))
            if np.count_nonzero(classes) < 2:
                continue
            breaks = np.flatnonzero(np.diff(classes) != 0) + 1
            starts = np.concatenate([[0], breaks])
            ends = np.concatenate([breaks, [len(classes)]])
            runs = [(int(s), int(e), int(classes[s])) for s, e in zip(starts, ends)]
            for i in range(1, len(runs) - 1):
                s, e, cls = runs[i]
                if cls != 0 or not (gap_lo <= e - s <= gap_hi):
                    continue
                ls, le, lc = runs[i - 1]
                rs, re_, rc = runs[i + 1]
                left = (le - ls) if lc == 1 else 0
                right = (re_ - rs) if rc == 1 else 0
                if e - s < hole_max:
                    ok = left >= hole_run and right >= hole_run
                else:
                    ok = (lc == 1 and left >= min_run and rc in (1, 2)) or (rc == 1 and right >= min_run and lc in (1, 2))
                if ok:
                    found.setdefault((s // 2, e // 2), []).append((r, s, e))
        for rows in found.values():
            rows.sort()
            groups = [[rows[0]]]
            for item in rows[1:]:
                if item[0] - groups[-1][-1][0] <= 2:
                    groups[-1].append(item)
                else:
                    groups.append([item])
            for group in groups:
                r, s, e = group[len(group) // 2]
                above = inside[max(r - 8, 0):max(r - 2, 0), s:e]
                below = inside[r + 3:r + 9, s:e]
                if above.size == 0 or below.size == 0 or above.mean() < 0.25 or below.mean() < 0.25:
                    continue
                if horizontal:
                    a = grid.to_world(np.array([[r, s - 0.5]]))[0]
                    b = grid.to_world(np.array([[r, e - 0.5]]))[0]
                    bridge = Bridge("H", float(a[1]), float(a[0]), float(b[0]), "gap in a thin wall run on the raster")
                else:
                    a = grid.to_world(np.array([[s - 0.5, r]]))[0]
                    b = grid.to_world(np.array([[e - 0.5, r]]))[0]
                    bridge = Bridge("V", float(a[0]), float(a[1]), float(b[1]), "gap in a thin wall run on the raster")
                out.append(bridge)
    return out


def rasterize_bridges(bridges: list[Bridge], grid: Grid2D, thickness_cells: int = 3) -> np.ndarray:
    mask = np.zeros(grid.shape, np.uint8)
    for bridge in bridges:
        if not bridge.accepted:
            continue
        p, q = bridge.endpoints()
        pc, qc = grid.to_cell(p[None, :])[0], grid.to_cell(q[None, :])[0]
        cv2.line(mask, (int(pc[1]), int(pc[0])), (int(qc[1]), int(qc[0])), 1, thickness_cells)
    return mask > 0


# ---------------------------------------------------------------------------------------------
# Step 3: rooms


def _prune_unwalked(mask: np.ndarray, walked: np.ndarray, res: float) -> tuple[np.ndarray, float]:
    radius = UNWALKED_NECK_M / 2
    opened = cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_OPEN, _disc(radius, res)) > 0
    n, labels = cv2.connectedComponents(opened.astype(np.uint8), connectivity=4)
    if n <= 2:
        return mask, 0.0
    walked_cores = [k for k in range(1, n) if (walked & (labels == k)).any()]
    if not walked_cores:
        return mask, 0.0
    keep = cv2.dilate(np.isin(labels, walked_cores).astype(np.uint8), _disc(radius + res, res)) > 0
    rest = mask & ~keep
    m, rest_labels, stats, _ = cv2.connectedComponentsWithStats(rest.astype(np.uint8), connectivity=4)
    out, removed = mask.copy(), 0.0
    for k in range(1, m):
        piece = rest_labels == k
        area = stats[k, cv2.CC_STAT_AREA] * res * res
        if area >= UNWALKED_PIECE_M2 and not (piece & walked).any():
            out &= ~piece
            removed += area
    n2, labels2 = cv2.connectedComponents(out.astype(np.uint8), connectivity=4)
    if n2 > 2:
        counts = np.bincount(labels2[walked & out].ravel(), minlength=n2)
        if counts[1:].any():
            out = labels2 == int(np.argmax(counts[1:]) + 1)
    return out, removed


def _rooms_from_pieces(free: np.ndarray, occ: OccupancyMaps, notes: list[str]) -> dict[int, np.ndarray]:
    grid = occ.grid
    res = grid.resolution
    cell = res * res
    n, labels = cv2.connectedComponents(free.astype(np.uint8), connectivity=4)
    areas = np.bincount(labels.ravel(), minlength=n) * cell
    walked = _track_mask(occ, WALK_REACH_M)
    walked_count = np.bincount(labels[walked].ravel(), minlength=n)
    rooms = [k for k in range(1, n) if areas[k] >= MIN_ROOM_AREA_M2 and walked_count[k] > 0]
    for k in range(1, n):
        if areas[k] >= UNWALKED_REPORT_M2 and walked_count[k] == 0:
            centre = grid.to_world(np.argwhere(labels == k).mean(axis=0)[None, :])[0]
            notes.append(f"a {areas[k]:.2f} m2 space around ({centre[0]:.2f}, {centre[1]:.2f}) was seen but nobody walked "
                         "into it, so it is not a room in this plan")
    owner = {k: k for k in rooms}
    ring = np.ones((7, 7), np.uint8)
    for k in range(1, n):
        if k in owner or areas[k] >= SLIVER_AREA_M2 or areas[k] < 0.02:
            continue
        grown = cv2.dilate((labels == k).astype(np.uint8), ring) > 0
        touching = [t for t in labels[grown & (labels != k) & (labels > 0)] if t in rooms]
        if touching:
            values, counts = np.unique(touching, return_counts=True)
            owner[k] = int(values[np.argmax(counts)])
    masks: dict[int, np.ndarray] = {}
    for k, room in owner.items():
        masks.setdefault(room, np.zeros(grid.shape, bool))
        masks[room] |= labels == k
    out: dict[int, np.ndarray] = {}
    for room, mask in masks.items():
        mask = cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_CLOSE, _disc(0.06, res)) > 0
        mask = _fill_holes(mask, int(MAX_HOLE_FILL_M2 / cell))
        mask, removed = _prune_unwalked(mask, walked, res)
        if removed:
            notes.append(f"cut {removed:.2f} m2 off a room: reached only through a neck narrower than "
                         f"{UNWALKED_NECK_M:.1f} m and never walked")
        if mask.sum() * cell >= MIN_ROOM_AREA_M2:
            out[room] = mask
    return out


# ---------------------------------------------------------------------------------------------
# Step 4: outlines


def _pick(group: list[tuple[float, float, bool]]) -> float:
    walls = [v for v in group if v[2]]
    if walls:
        return max(walls, key=lambda v: v[1])[0]
    return float(np.average([v[0] for v in group], weights=[v[1] for v in group]))


def _cluster(values: list[tuple[float, float, bool]], radius: float) -> list[float]:
    out, group = [], []
    for value in sorted(values, key=lambda v: v[0]):
        if group and value[0] - group[-1][0] > radius:
            out.append(_pick(group))
            group = []
        group.append(value)
    if group:
        out.append(_pick(group))
    return sorted(set(out))


def rectilinear_outline(mask: np.ndarray, grid: Grid2D, lines: list[WallLine]) -> Polygon:
    """Union of the cells, between wall faces and boundary lines, that the room mask covers."""
    res = grid.resolution
    ox, oz = grid.origin
    rows, cols = np.nonzero(mask)
    if len(rows) == 0:
        return Polygon()
    r0, r1, c0, c1 = rows.min(), rows.max(), cols.min(), cols.max()
    sub = mask[r0:r1 + 1, c0:c1 + 1]
    pad = np.pad(sub, 1)
    left, right = sub & ~pad[1:-1, :-2], sub & ~pad[1:-1, 2:]
    low, high = sub & ~pad[:-2, 1:-1], sub & ~pad[2:, 1:-1]

    def x_of(j, side):
        return ox + (c0 + j + 0.5 * side) * res

    def z_of(i, side):
        return oz + (r0 + i + 0.5 * side) * res

    lz, lx = np.nonzero(left)
    rz, rx = np.nonzero(right)
    bz, bx = np.nonzero(low)
    tz, tx = np.nonzero(high)
    boundary = {
        ("V", 1.0): (x_of(lx, -1), z_of(lz, 0)),
        ("V", -1.0): (x_of(rx, 1), z_of(rz, 0)),
        ("H", 1.0): (z_of(bz, -1), x_of(bx, 0)),
        ("H", -1.0): (z_of(tz, 1), x_of(tx, 0)),
    }
    xs: list[tuple[float, float, bool]] = []
    zs: list[tuple[float, float, bool]] = []
    for line in lines:
        across, along = boundary.get((line.kind, line.normal_sign), (np.zeros(0), np.zeros(0)))
        if len(across) == 0:
            continue
        near = (np.abs(across - line.coord) <= FACE_SNAP_M) & (along >= line.lo - 0.10) & (along <= line.hi + 0.10)
        run = near.sum() * res
        if run >= MIN_BOUNDARY_RUN_M:
            (xs if line.kind == "V" else zs).append((line.coord, run * 3.0, True))
    wall_x, wall_z = [v[0] for v in xs], [v[0] for v in zs]
    for j in range(sub.shape[1]):
        for side, edges in ((-1, left), (1, right)):
            run = edges[:, j].sum() * res
            x = x_of(j, side)
            if run >= MIN_BOUNDARY_RUN_M and all(abs(x - v) > FACE_SNAP_M for v in wall_x):
                xs.append((x, run, False))
    for i in range(sub.shape[0]):
        for side, edges in ((-1, low), (1, high)):
            run = edges[i, :].sum() * res
            z = z_of(i, side)
            if run >= MIN_BOUNDARY_RUN_M and all(abs(z - v) > FACE_SNAP_M for v in wall_z):
                zs.append((z, run, False))
    # The mask's own extremes bound the fit, except where a wall face lies just beyond them: the cell
    # between the two holds no floor evidence, and a line there would stop the room short of its wall.
    for x in (x_of(0, -1), x_of(sub.shape[1] - 1, 1)):
        if all(abs(x - v) > FACE_SNAP_M for v in wall_x):
            xs.append((x, 0.01, False))
    for z in (z_of(0, -1), z_of(sub.shape[0] - 1, 1)):
        if all(abs(z - v) > FACE_SNAP_M for v in wall_z):
            zs.append((z, 0.01, False))
    if len({round(v[0], 6) for v in xs}) < 2 or len({round(v[0], 6) for v in zs}) < 2:
        return Polygon()
    x_lines, z_lines = _cluster(xs, LINE_MERGE_M), _cluster(zs, LINE_MERGE_M)

    table = cv2.integral(mask.astype(np.uint8))

    def covered(ra, rb, ca, cb):
        ra, rb = max(ra, 0), min(rb, mask.shape[0])
        ca, cb = max(ca, 0), min(cb, mask.shape[1])
        if rb <= ra or cb <= ca:
            return 0, 0
        return int(table[rb, cb] - table[ra, cb] - table[rb, ca] + table[ra, ca]), (rb - ra) * (cb - ca)

    cells = []
    for i in range(len(x_lines) - 1):
        ca = int(np.ceil((x_lines[i] - ox) / res - 1e-9))
        cb = int(np.floor((x_lines[i + 1] - ox) / res + 1e-9)) + 1
        for j in range(len(z_lines) - 1):
            ra = int(np.ceil((z_lines[j] - oz) / res - 1e-9))
            rb = int(np.floor((z_lines[j + 1] - oz) / res + 1e-9)) + 1
            hit, total = covered(ra, rb, ca, cb)
            if total == 0:
                cx = int(round((0.5 * (x_lines[i] + x_lines[i + 1]) - ox) / res))
                cz = int(round((0.5 * (z_lines[j] + z_lines[j + 1]) - oz) / res))
                hit, total = covered(cz, cz + 1, cx, cx + 1)
            if total and hit / total >= 0.5:
                cells.append(box(x_lines[i], z_lines[j], x_lines[i + 1], z_lines[j + 1]))
    if not cells:
        return Polygon()
    shape = unary_union(cells)
    if shape.geom_type == "MultiPolygon":
        shape = max(shape.geoms, key=lambda p: p.area)
    holes = [ring for ring in shape.interiors if Polygon(ring).area >= 0.5]
    return Polygon(shape.exterior, holes).buffer(0).simplify(0.005, preserve_topology=True)


def push_to_faces(polygons: dict[int, Polygon], lines: list[WallLine], notes: list[str]) -> dict[int, Polygon]:
    """Move an edge standing at furniture out to the wall face behind it, seen from inside the room."""
    out: dict[int, Polygon] = {}
    keys = list(polygons)
    for key in keys:
        polygon = orient(polygons[key], 1.0)
        others = unary_union([polygons[k] for k in keys if k != key]) if len(keys) > 1 else Polygon()
        strips = []
        ring = np.asarray(polygon.exterior.coords)[:-1]
        for i in range(len(ring)):
            a, b = ring[i], ring[(i + 1) % len(ring)]
            d = b - a
            length = float(np.hypot(*d))
            if length < 0.4:
                continue
            if abs(d[1]) < 1e-6:
                kind, coord, lo, hi, inward = "H", a[1], min(a[0], b[0]), max(a[0], b[0]), float(np.sign(d[0]))
            elif abs(d[0]) < 1e-6:
                kind, coord, lo, hi, inward = "V", a[0], min(a[1], b[1]), max(a[1], b[1]), float(-np.sign(d[1]))
            else:
                continue
            best = None
            for line in lines:
                if line.kind != kind or line.normal_sign != inward:
                    continue
                push = (coord - line.coord) * inward
                if not (MIN_PUSH_M <= push <= MAX_PUSH_M):
                    continue
                lo_o, hi_o = max(lo, line.lo), min(hi, line.hi)
                if hi_o - lo_o < PUSH_MIN_OVERLAP * length:
                    continue
                if best is None or push > best[0]:
                    best = (push, line.coord, lo_o, hi_o)
            if best is None:
                continue
            push, face, lo_o, hi_o = best
            strip = (box(lo_o, min(face, coord), hi_o, max(face, coord)) if kind == "H"
                     else box(min(face, coord), lo_o, max(face, coord), hi_o))
            if not others.is_empty and strip.intersection(others).area > 0.02:
                continue
            strips.append(strip)
            notes.append(f"moved a {length:.2f} m edge {push:.2f} m out to the wall face behind what stands against it")
        if strips:
            merged = unary_union([polygon] + strips)
            if merged.geom_type == "MultiPolygon":
                merged = max(merged.geoms, key=lambda p: p.area)
            polygon = merged.simplify(0.005, preserve_topology=True)
        out[key] = polygon
    return out


# ---------------------------------------------------------------------------------------------
# Doorways


def _room_at(label_raster: np.ndarray, grid: Grid2D, point: np.ndarray) -> int:
    r, c = grid.to_cell(point[None, :])[0]
    if 0 <= r < label_raster.shape[0] and 0 <= c < label_raster.shape[1]:
        return int(label_raster[r, c])
    return 0


def _crossings(bridge: Bridge, track_world: np.ndarray) -> int:
    if len(track_world) < 2:
        return 0
    p, q = bridge.endpoints()
    along = q - p
    a, b = track_world[:-1], track_world[1:]

    def side(u, v, w):
        return (v[..., 0] - u[..., 0]) * (w[..., 1] - u[..., 1]) - (v[..., 1] - u[..., 1]) * (w[..., 0] - u[..., 0])

    pp = np.broadcast_to(p - 0.05 * along / max(np.linalg.norm(along), 1e-9), a.shape)
    qq = np.broadcast_to(q + 0.05 * along / max(np.linalg.norm(along), 1e-9), a.shape)
    s1, s2 = side(pp, qq, a), side(pp, qq, b)
    s3, s4 = side(a, b, pp), side(a, b, qq)
    return int(np.count_nonzero((s1 * s2 < 0) & (s3 * s4 < 0)))


def _refine_doorway(bridge: Bridge, cloud: FusedCloud, floor_y: float) -> tuple[float, float, float, float, str, float | None]:
    """Jamb to jamb from the returns beside the gap, and the head from the returns above it."""
    if bridge.kind == "H":
        along, across = cloud.points[:, 0], cloud.points[:, 2] - bridge.coord
    else:
        along, across = cloud.points[:, 2], cloud.points[:, 0] - bridge.coord
    height = cloud.points[:, 1] - floor_y
    vertical = np.abs(cloud.normals[:, 1]) < 0.5
    near = vertical & (np.abs(across) <= JAMB_BAND_M) & (along >= bridge.lo - 0.30) & (along <= bridge.hi + 0.30)
    body = near & (height >= 0.30) & (height <= 1.90)
    lo, hi, source = bridge.lo, bridge.hi, "gap between wall runs"
    sigma = 0.03
    if body.sum() >= 20:
        origin = bridge.lo - 0.30
        bins = np.floor((along[body] - origin) / JAMB_BIN_M).astype(int)
        n_bins = int(np.ceil((bridge.width + 0.60) / JAMB_BIN_M)) + 1
        counts = np.bincount(np.clip(bins, 0, n_bins - 1), minlength=n_bins)
        empty = counts <= 1
        mid = int(round((0.5 * (bridge.lo + bridge.hi) - origin) / JAMB_BIN_M))
        if 0 <= mid < n_bins and empty[mid]:
            a = mid
            while a > 0 and empty[a - 1]:
                a -= 1
            b = mid
            while b < n_bins - 1 and empty[b + 1]:
                b += 1
            width = (b - a + 1) * JAMB_BIN_M
            if 0.6 * bridge.width <= width <= 1.5 * bridge.width + 0.10:
                lo, hi = origin + a * JAMB_BIN_M, origin + (b + 1) * JAMB_BIN_M
                source, sigma = "jamb to jamb from the returns beside the gap", 0.012
    head = None
    over = near & (along >= lo + 0.05) & (along <= hi - 0.05) & (height >= HEAD_MIN_M) & (height <= 2.70)
    if over.sum() >= 10:
        levels = np.sort(height[over])
        # The lowest height with ten returns within 5 cm above it: the face of the wall over the door.
        for k in range(len(levels) - 9):
            if levels[k + 9] - levels[k] <= 0.05:
                head = float(levels[k])
                break
    return lo, hi, hi - lo, sigma, source, head


def find_doorways(bridges: list[Bridge], polygons: dict[int, Polygon], grid: Grid2D, cloud: FusedCloud,
                  floor_y: float, track_world: np.ndarray) -> list[Doorway]:
    labels = np.zeros(grid.shape, np.int32)
    for key, polygon in polygons.items():
        labels[polygon_mask(polygon, grid)] = key
    candidates: list[tuple[Bridge, int, int | None]] = []
    for bridge in bridges:
        if not bridge.accepted or bridge.width < DOOR_MIN_WIDTH_M:
            continue
        mid = 0.5 * (bridge.lo + bridge.hi)
        sides = []
        for sign in (-1.0, 1.0):
            room = 0
            for depth in (0.12, 0.20, 0.30, 0.45):
                room = _room_at(labels, grid, bridge.point(mid, sign * depth))
                if room:
                    break
            sides.append(room)
        rooms = {s for s in sides if s}
        if not rooms or (len(rooms) == 1 and sides[0] == sides[1]):
            continue
        a, b = (sorted(rooms) + [None])[:2]
        candidates.append((bridge, a, b))
    # One doorway can be proposed several times: once per wall face and once on the raster.
    merged: list[tuple[Bridge, int, int | None]] = []
    for bridge, a, b in sorted(candidates, key=lambda c: (c[0].source.startswith("gap in a thin"), -c[0].width)):
        duplicate = False
        for kept, ka, kb in merged:
            if kept.kind != bridge.kind or {ka, kb} != {a, b} or abs(kept.coord - bridge.coord) > 0.40:
                continue
            overlap = min(kept.hi, bridge.hi) - max(kept.lo, bridge.lo)
            if overlap >= 0.5 * min(kept.width, bridge.width):
                duplicate = True
                break
        if not duplicate:
            merged.append((bridge, a, b))
    doorways = []
    for bridge, a, b in merged:
        crossings = _crossings(bridge, track_world)
        if b is None and crossings == 0:
            # A gap onto space no room holds, that nobody walked through, is as likely a floor-length
            # window or a hole in the scan as a door, and a phantom opening scores as a miss.
            continue
        lo, hi, width, sigma, source, head = _refine_doorway(bridge, cloud, floor_y)
        doorways.append(Doorway(bridge.kind, bridge.coord, lo, hi, a, b, width, sigma, source, head, crossings))
    return doorways


# ---------------------------------------------------------------------------------------------


def build_layout(occ: OccupancyMaps, cloud: FusedCloud, walls: list[WallSegment], floor_y: float) -> Layout:
    notes: list[str] = []
    grid = occ.grid
    barrier = barrier_map(occ, walls, cloud, floor_y)
    interior = interior_evidence(occ, barrier)
    lines = wall_lines(walls)
    bridges = line_bridges(lines)
    for bridge in bridges:
        if is_corridor_crossing(bridge, barrier, grid):
            bridge.accepted = False
            bridge.note = "walls carry on both ways at both ends: a corridor crossing, not a doorway"
    bridges += raster_bridges(barrier, interior, grid)
    free = interior & ~(barrier | rasterize_bridges(bridges, grid))
    masks = _rooms_from_pieces(free, occ, notes)
    polygons: dict[int, Polygon] = {}
    for key, mask in masks.items():
        outline = rectilinear_outline(mask, grid, lines)
        if not outline.is_empty and outline.geom_type == "Polygon" and outline.area >= MIN_ROOM_AREA_M2:
            polygons[key] = outline
    polygons = push_to_faces(polygons, lines, notes)
    masks = {key: masks[key] for key in polygons}
    track_world = grid.to_world(occ.camera_track) if len(occ.camera_track) else np.zeros((0, 2))
    doorways = find_doorways(bridges, polygons, grid, cloud, floor_y, track_world)
    return Layout(polygons, masks, doorways, bridges, barrier, interior, notes)
