"""Room outlines corrected against what the scan saw inside them.

The cell complex decides rooms a face at a time, and a face is only as fine as the wall lines
around it. Three errors survive that on the assignment's scans. Each was found by drawing the
plan over its own scan and over the other two scans registered onto it, and checked against
the camera frames taken inside the room:

  * A stairwell. The floor stops at the railing and beyond it the returns are the treads of a
    flight going down, but the face they sit in has carved free space and ceiling above it and
    is labelled floor. Both whole-flat scans drew the well into the stair hall.
  * A room seen along its length from its doorway. The single-room scan stood at the mouth of
    a corridor; no wall line crossed its far end, so the corridor's face ran on across the
    passage beyond it and into a bathroom, through two walls.
  * A walled space nobody saw into. The box between the bathroom and the living room has no
    floor, furniture or ceiling return in any of the three scans, and two of them drew it as
    part of a room.

Each correction removes floor only where the scan's own returns say it is not floor, and none
of them moves a wall. A room is never grown here.
"""

from __future__ import annotations

import cv2
import numpy as np
from shapely import affinity
from shapely.geometry import LineString, Polygon, box

from cozmo.geometry.cellcomplex import MAX_DOOR_WIDTH_M, WALL_PROXIMITY_M
from cozmo.geometry.grid import Grid2D
from cozmo.geometry.occupancy import OccupancyMaps
from cozmo.geometry.walls import WallSegment
from cozmo.util.polygons import clean_polygon

# A drop component must be at least this big, and at least this much of it must be deep, to be a
# stairwell. Shower trays and a slightly tilted floor fit give shallow drops only.
MIN_DROP_AREA_M2 = 0.25
MIN_DEEP_DROP_AREA_M2 = 0.10
# Removed regions are grown by this much, and what is left is opened by a disc of this radius so a
# strip too narrow to stand in does not survive between a removed region and a wall.
REMOVAL_MARGIN_M = 0.05
SLIVER_RADIUS_M = 0.15
# Evidence that a cell is inside a room: floor, the top of furniture, ceiling, or the operator's
# walk. The walk is widened to about a person's reach and every channel by one sensor footprint.
TRACK_REACH_M = 0.30
EVIDENCE_REACH_M = 0.10
WALL_REACH_M = 0.15
# A space with no evidence in it is closed when this share of its outline runs along wall returns.
MIN_CLOSED_AREA_M2 = 0.60
CLOSED_WALL_SHARE = 0.60
MIN_PIECE_M2 = 0.30
SLICE_M = 0.05


def _disc(radius_m: float, resolution: float) -> np.ndarray:
    r = max(int(round(radius_m / resolution)), 1)
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))


def room_axis(polygon) -> float:
    """The room's own wall direction, as the length-weighted mean of its edges modulo 90 degrees."""
    angles, weights = [], []
    for part in getattr(polygon, "geoms", [polygon]):
        xy = np.asarray(part.exterior.coords)
        d = np.diff(xy, axis=0)
        angles.append(np.arctan2(d[:, 1], d[:, 0]))
        weights.append(np.hypot(d[:, 0], d[:, 1]))
    a, w = np.concatenate(angles), np.concatenate(weights)
    return float(np.arctan2((w * np.sin(4 * a)).sum(), (w * np.cos(4 * a)).sum()) / 4)


def polygon_mask(polygon, grid: Grid2D) -> np.ndarray:
    mask = np.zeros(grid.shape, np.uint8)
    for part in getattr(polygon, "geoms", [polygon]):
        if part.is_empty:
            continue
        cv2.fillPoly(mask, [np.round(grid.to_cell_float(np.asarray(part.exterior.coords))[:, ::-1]).astype(np.int32)], 1)
        for hole in part.interiors:
            cv2.fillPoly(mask, [np.round(grid.to_cell_float(np.asarray(hole.coords))[:, ::-1]).astype(np.int32)], 0)
    return mask.astype(bool)


def stairwell_drops(occ: OccupancyMaps) -> list[np.ndarray]:
    """Connected regions of upward-facing returns below the floor that go deep enough to be a flight down."""
    if occ.drop_hits is None or occ.deep_drop_hits is None:
        return []
    res = occ.grid.resolution
    closed = cv2.morphologyEx(occ.drop_hits.astype(np.uint8), cv2.MORPH_CLOSE, _disc(0.06, res))
    count, labels = cv2.connectedComponents(closed, connectivity=8)
    drops = []
    for k in range(1, count):
        region = labels == k
        if region.sum() * res * res < MIN_DROP_AREA_M2:
            continue
        if (region & occ.deep_drop_hits).sum() * res * res < MIN_DEEP_DROP_AREA_M2:
            continue
        drops.append(region)
    return drops


def _aligned_rectangle(region: np.ndarray, grid: Grid2D, angle: float, margin_m: float) -> Polygon:
    rows, cols = np.nonzero(region)
    world = grid.to_world(np.stack([rows, cols], axis=1).astype(float))
    c, s = np.cos(-angle), np.sin(-angle)
    u = world[:, 0] * c - world[:, 1] * s
    v = world[:, 0] * s + world[:, 1] * c
    half = grid.resolution / 2 + margin_m
    rect = box(u.min() - half, v.min() - half, u.max() + half, v.max() + half)
    return affinity.rotate(rect, angle, origin=(0, 0), use_radians=True)


def _without(polygon, hole):
    """The polygon minus a region, with any strip too narrow to stand in removed, as one piece."""
    rest = polygon.difference(hole)
    rest = rest.buffer(-SLIVER_RADIUS_M, join_style=2).buffer(SLIVER_RADIUS_M, join_style=2).intersection(rest)
    return _largest(rest)


def _largest(geometry) -> Polygon:
    parts = [g for g in getattr(geometry.buffer(0), "geoms", [geometry.buffer(0)]) if g.geom_type == "Polygon" and g.area >= MIN_PIECE_M2]
    return max(parts, key=lambda g: g.area) if parts else Polygon()


def _wall_hits(points: np.ndarray, along: int, starts, directions, lengths, max_angle_deg: float = 20.0) -> np.ndarray:
    """Which local points lie on a wall segment that runs along the given local axis."""
    if len(points) == 0 or len(starts) == 0:
        return np.zeros(len(points), bool)
    parallel = np.abs(directions[:, along]) >= np.cos(np.deg2rad(max_angle_deg))
    s, d, length = starts[parallel], directions[parallel], lengths[parallel]
    if len(s) == 0:
        return np.zeros(len(points), bool)
    rel = points[:, None, :] - s[None, :, :]
    t = (rel * d[None, :, :]).sum(axis=-1)
    perp = np.abs(rel[..., 0] * d[None, :, 1] - rel[..., 1] * d[None, :, 0])
    return ((perp <= WALL_PROXIMITY_M) & (t >= -0.05) & (t <= length[None, :] + 0.05)).any(axis=1)


def trim_open_ends(polygon: Polygon, segments: list[WallSegment], gap_m: float = MAX_DOOR_WIDTH_M) -> Polygon:
    """Cut a room off where, towards one end, neither of its sides has a wall for more than a door width.

    A room is a space between walls. Where a stretch of it has no wall on either side for longer
    than any doorway, what lies beyond is not bounded by this room's walls: it is the next space,
    seen from inside this one. The corridor of the single-room scan was bounded by its measured
    walls for its first few metres and by nothing after that, and it ran on through two walls.
    The threshold is the door width `_assign_rooms` already uses for an open span.
    """
    if polygon.is_empty or not segments:
        return polygon
    angle = room_axis(polygon)
    c, s = np.cos(-angle), np.sin(-angle)
    rot = np.array([[c, -s], [s, c]])
    starts = np.array([rot @ np.asarray(seg.start, float) for seg in segments])
    ends = np.array([rot @ np.asarray(seg.end, float) for seg in segments])
    vectors = ends - starts
    lengths = np.hypot(vectors[:, 0], vectors[:, 1])
    usable = lengths > 1e-6
    starts, vectors, lengths = starts[usable], vectors[usable], lengths[usable]
    directions = vectors / lengths[:, None]

    local = affinity.rotate(polygon, -angle, origin=(0, 0), use_radians=True)
    for _ in range(2):
        minx, miny, maxx, maxy = local.bounds
        cut = [minx, miny, maxx, maxy]
        for axis in (0, 1):
            lo, hi = (minx, maxx) if axis == 0 else (miny, maxy)
            across_lo, across_hi = (miny, maxy) if axis == 0 else (minx, maxx)
            centres = np.arange(lo + SLICE_M / 2, hi, SLICE_M)
            present = np.zeros(len(centres), bool)
            walled = np.zeros(len(centres), bool)
            for i, t in enumerate(centres):
                line = (LineString([(t, across_lo - 1), (t, across_hi + 1)]) if axis == 0
                        else LineString([(across_lo - 1, t), (across_hi + 1, t)]))
                crossing = local.intersection(line)
                coords = [xy for g in getattr(crossing, "geoms", [crossing])
                          if g.geom_type in ("LineString", "LinearRing") for xy in g.coords]
                if not coords:
                    continue
                present[i] = True
                across = np.array(coords)[:, 1 if axis == 0 else 0]
                sides = np.array([[t, across.min()], [t, across.max()]]) if axis == 0 else \
                    np.array([[across.min(), t], [across.max(), t]])
                walled[i] = bool(_wall_hits(sides, axis, starts, directions, lengths).any())
            index = np.flatnonzero(present)
            for order in (index, index[::-1]):
                unwalled = 0
                for i in order:
                    if walled[i]:
                        break
                    unwalled += 1
                if unwalled == len(order) or unwalled * SLICE_M < gap_m:
                    continue
                first = order[unwalled]
                if order is index:
                    cut[axis] = max(cut[axis], centres[first] - SLICE_M / 2)
                else:
                    cut[axis + 2] = min(cut[axis + 2], centres[first] + SLICE_M / 2)
        clipped = local.intersection(box(*cut))
        if abs(clipped.area - local.area) < 1e-6:
            break
        local = clipped
    return _largest(affinity.rotate(local, angle, origin=(0, 0), use_radians=True))


def interior_evidence(occ: OccupancyMaps) -> np.ndarray:
    res = occ.grid.resolution
    seen = occ.floor_hits.copy()
    if occ.surface_hits is not None:
        seen |= occ.surface_hits
    if occ.ceiling_hits is not None:
        seen |= occ.ceiling_hits & ~(occ.wall_weight > 0)
    track = np.zeros(occ.grid.shape, np.uint8)
    if len(occ.camera_track):
        cells = np.round(occ.camera_track[:, ::-1]).astype(np.int32).reshape(-1, 1, 2)
        cv2.polylines(track, [cells], False, 1, 1)
        track = cv2.dilate(track, _disc(TRACK_REACH_M, res))
    return cv2.dilate((seen | track.astype(bool)).astype(np.uint8), _disc(EVIDENCE_REACH_M, res)).astype(bool)


def closed_unobserved(polygon: Polygon, occ: OccupancyMaps, evidence: np.ndarray, near_wall: np.ndarray) -> list[np.ndarray]:
    """Regions of a room with no evidence in them whose outline is mostly wall returns."""
    res = occ.grid.resolution
    empty = polygon_mask(polygon, occ.grid) & ~evidence
    count, labels = cv2.connectedComponents(empty.astype(np.uint8), connectivity=4)
    found = []
    for k in range(1, count):
        region = labels == k
        if region.sum() * res * res < MIN_CLOSED_AREA_M2:
            continue
        outline = region & ~cv2.erode(region.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
        if (outline & near_wall).sum() >= CLOSED_WALL_SHARE * max(outline.sum(), 1):
            found.append(region)
    return found


def refine_rooms(
    polygons: dict[int, Polygon],
    occ: OccupancyMaps,
    segments: list[WallSegment],
    min_room_area_m2: float,
    min_inscribed_radius_m: float,
) -> tuple[dict[int, Polygon], dict[int, list[str]]]:
    """Apply the three corrections to every room. Returns the rooms kept and what was done to each."""
    grid = occ.grid
    drops = stairwell_drops(occ)
    evidence = interior_evidence(occ)
    near_wall = np.zeros(grid.shape, bool)
    if occ.wall_point_hits is not None:
        near_wall = cv2.dilate(occ.wall_point_hits.astype(np.uint8), _disc(WALL_REACH_M, grid.resolution)).astype(bool)

    kept: dict[int, Polygon] = {}
    notes: dict[int, list[str]] = {}
    for key, polygon in polygons.items():
        room = polygon
        done: list[str] = []
        for drop in drops:
            hole = _aligned_rectangle(drop, grid, room_axis(room), REMOVAL_MARGIN_M)
            if room.is_empty or hole.intersection(room).area < 0.05:
                continue
            before = room.area
            room = _without(room, hole)
            done.append(f"removed {before - room.area:.2f} m2 over a stairwell: the floor there drops away below the floor plane")
        if not room.is_empty:
            before = room.area
            room = trim_open_ends(room, segments)
            if before - room.area > 0.01:
                done.append(f"cut {before - room.area:.2f} m2 off an end with no wall on either side for more than a door width")
        if not room.is_empty:
            for region in closed_unobserved(room, occ, evidence, near_wall):
                before = room.area
                room = _without(room, _aligned_rectangle(region, grid, room_axis(room), REMOVAL_MARGIN_M))
                done.append(f"removed {before - room.area:.2f} m2 of a walled space with no floor, furniture or ceiling seen in it")
                if room.is_empty:
                    break
        if not room.is_empty:
            room = clean_polygon(room)
        valid = (not room.is_empty and room.geom_type == "Polygon" and room.area >= min_room_area_m2
                 and not room.buffer(-min_inscribed_radius_m).is_empty)
        if valid:
            kept[key] = room
        elif done:
            done.append(f"dropped: {polygon.area:.2f} m2 before, too little room left to stand in")
        notes[key] = done
    return kept, notes
