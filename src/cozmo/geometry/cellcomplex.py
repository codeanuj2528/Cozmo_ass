"""Floor plan as a cell complex induced by the wall lines.

A flood fill over a raster is the wrong tool for this job. Its boundary is quantised to the
grid, it rounds every corner, and it escapes through any gap in wall coverage -- a window,
an open front door, a stretch of wall the operator never faced -- after which it
cheerfully reports the corridor outside as part of the property.

Partitioning the floor by the wall lines instead makes leaking geometrically impossible.
Each face of the arrangement is bounded by lines on all sides, so labelling a face interior
or exterior is a decision about that face alone and a mistake cannot propagate. Corners
come out as exact line intersections rather than as staircases of pixels, which is what
lets a room polygon inherit the millimetre-level offset uncertainty of the plane fits that
produced it.

The two decisions are kept deliberately separate:

  * Which faces are inside the property. Answered by direct evidence: observed floor,
    carved free space, and the camera's own track, all of which are proof of standability.
  * Where one room ends and the next begins. Answered by whether the boundary between two
    interior faces is actually built. A boundary that runs along observed wall material is
    a wall, even if it has a doorway in it; a boundary with no material behind it is an
    artefact of the arrangement and the two faces are one room.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

import cv2
import numpy as np
from shapely.geometry import LineString, MultiLineString, Polygon, box
from shapely.ops import polygonize, unary_union

from cozmo.geometry.grid import Grid2D
from cozmo.geometry.occupancy import OccupancyMaps
from cozmo.geometry.walls import WallCandidate, WallSegment
from cozmo.util.polygons import clean_polygon

MAX_LINES = 44
MIN_FACE_AREA_M2 = 0.15
INTERIOR_EVIDENCE_THRESHOLD = 0.22
WALL_SUPPORT_THRESHOLD = 0.45
WALL_PROXIMITY_M = 0.14
MIN_ROOM_AREA_M2 = 1.5
# A doorway is narrower than this. A shared boundary whose unsupported stretch is wider
# is an open span (an L-shaped room, a missing wall, furniture that looked like a
# partition) and the faces either side are one room.
MAX_DOOR_WIDTH_M = 1.60
# Faces of one room that meet only at a corner union into two polygons, and a room that is
# not one polygon is dropped. Closing the union by this much joins them without moving a wall
# by an amount the plan could report.
ROOM_CLOSING_M = 0.03
# An unwalked strip narrower than this that touches the rest of the interior only at its ends
# is the gap between two close parallel wall lines, not floor anyone could stand on.
APPENDAGE_MAX_WIDTH_M = 0.30


@dataclass
class Face:
    index: int
    polygon: Polygon
    evidence: float
    track_cells: int
    area: float
    interior: bool = False
    room: int = -1


@dataclass
class CellComplex:
    faces: list[Face]
    label_raster: np.ndarray = field(repr=False)
    grid: Grid2D = field(repr=False)
    lines: list[LineString] = field(default_factory=list, repr=False)


def _clip_line(normal_xz: np.ndarray, offset: float, bounds: Polygon) -> LineString | None:
    """The infinite line n . x = offset, clipped to `bounds`."""
    direction = np.array([-normal_xz[1], normal_xz[0]])
    point = normal_xz * offset
    far = 10_000.0
    seg = LineString([point - direction * far, point + direction * far])
    clipped = seg.intersection(bounds)
    if clipped.is_empty:
        return None
    if isinstance(clipped, MultiLineString):
        clipped = max(clipped.geoms, key=lambda g: g.length)
    if not isinstance(clipped, LineString) or clipped.length < 0.2:
        return None
    return clipped


def build_cell_complex(
    occ: OccupancyMaps,
    candidates: list[WallCandidate],
    segments: list[WallSegment],
    max_lines: int = MAX_LINES,
) -> CellComplex:
    """Partition the observed footprint by the wall lines and label the faces."""
    grid = occ.grid
    extent = box(
        grid.origin[0],
        grid.origin[1],
        grid.origin[0] + grid.shape[1] * grid.resolution,
        grid.origin[1] + grid.shape[0] * grid.resolution,
    )

    ordered = sorted(candidates, key=lambda c: c.weight, reverse=True)[:max_lines]
    lines: list[LineString] = []
    for cand in ordered:
        line = _clip_line(cand.normal_xz, cand.offset, extent)
        if line is not None:
            lines.append(line)
    if not lines:
        return CellComplex([], np.zeros(grid.shape, np.int32), grid, [])

    noded = unary_union(lines + [extent.exterior])
    polygons = [p for p in polygonize(noded) if p.area >= MIN_FACE_AREA_M2]
    if not polygons:
        return CellComplex([], np.zeros(grid.shape, np.int32), grid, lines)

    label_raster = np.zeros(grid.shape, dtype=np.int32)
    for i, poly in enumerate(polygons, start=1):
        ring = np.asarray(poly.exterior.coords)
        cells = grid.to_cell_float(ring)
        cv2.fillPoly(label_raster, [np.round(cells[:, ::-1]).astype(np.int32)], int(i))

    n = len(polygons)
    # Direct interior evidence. The camera track is counted separately because a single
    # cell of it outweighs any amount of ambiguity: the operator physically stood there.
    evidence_mask = occ.floor_hits | (occ.free_mask & occ.observed)
    # Ceiling above a face is interior evidence too, and it is what recovers the floor that
    # furniture hides (see OccupancyMaps.ceiling_hits). It is kept out of cells that carry
    # wall returns, so ceiling voxels averaged against the top of a partition cannot label
    # the partition's own thickness as floor and bridge the rooms either side of it. Room
    # separation is unaffected either way: two interior faces are still split into two
    # rooms wherever their shared boundary has built wall behind it.
    if occ.ceiling_hits is not None:
        evidence_mask = evidence_mask | (occ.ceiling_hits & ~(occ.wall_weight > 0))
    flat = label_raster.ravel()
    total = np.bincount(flat, minlength=n + 1)[1:]
    hits = np.bincount(flat[evidence_mask.ravel()], minlength=n + 1)[1:]

    track_counts = np.zeros(n + 1, dtype=np.int64)
    if len(occ.camera_track):
        track = np.round(occ.camera_track).astype(int)
        ok = (
            (track[:, 0] >= 0) & (track[:, 0] < grid.shape[0])
            & (track[:, 1] >= 0) & (track[:, 1] < grid.shape[1])
        )
        track_labels = label_raster[track[ok, 0], track[ok, 1]]
        track_counts = np.bincount(track_labels, minlength=n + 1)

    faces: list[Face] = []
    for i, poly in enumerate(polygons, start=1):
        cells = max(int(total[i - 1]), 1)
        faces.append(
            Face(
                index=i,
                polygon=poly,
                evidence=float(hits[i - 1] / cells),
                track_cells=int(track_counts[i]),
                area=float(poly.area),
            )
        )

    _label_interior(faces, segments)
    _assign_rooms(faces, label_raster, grid, segments)
    return CellComplex(faces, label_raster, grid, lines)


def _label_interior(faces: list[Face], segments: list[WallSegment]) -> None:
    """Decide which faces are inside the property."""
    for face in faces:
        if face.track_cells > 0:
            face.interior = True
            continue
        face.interior = face.evidence >= INTERIOR_EVIDENCE_THRESHOLD

    # A face that no wall faces cannot be a room. Walls are observed from inside, so their
    # normals point into the rooms they bound; a face with no wall pointing at it is either
    # outside the property or inside a solid, and in both cases it is not floor.
    if not segments:
        return
    for face in faces:
        if not face.interior or face.track_cells > 0:
            continue
        if not _faced_by_wall(face.polygon, segments):
            face.interior = False


def _faced_by_wall(poly: Polygon, segments: list[WallSegment], probe_m: float = 0.12) -> bool:
    centroid = poly.representative_point()
    for seg in segments:
        mid = seg.midpoint
        probe = mid + seg.normal_xz * probe_m
        if poly.contains(Polygon([probe, probe, probe]).centroid):
            return True
    # Fall back to a containment test on a short probe from each wall midpoint.
    from shapely.geometry import Point

    for seg in segments:
        for t in (0.25, 0.5, 0.75):
            base = seg.start + seg.direction * (seg.length * t)
            if poly.contains(Point(base + seg.normal_xz * probe_m)):
                return True
    _ = centroid
    return False


def _shared_boundaries(label_raster: np.ndarray) -> dict[tuple[int, int], list[tuple[int, int]]]:
    """Cells where two face labels touch, keyed by the ordered label pair."""
    pairs: dict[tuple[int, int], list[tuple[int, int]]] = {}
    h, w = label_raster.shape
    for dr, dc in ((0, 1), (1, 0)):
        a = label_raster[: h - dr, : w - dc]
        b = label_raster[dr:, dc:]
        diff = (a > 0) & (b > 0) & (a != b)
        if not diff.any():
            continue
        rr, cc = np.nonzero(diff)
        for r, c, la, lb in zip(rr, cc, a[diff], b[diff]):
            key = (int(min(la, lb)), int(max(la, lb)))
            pairs.setdefault(key, []).append((int(r), int(c)))
    return pairs


def _unsupported_span_m(
    cells: np.ndarray, grid: Grid2D, segments: list[WallSegment]
) -> float:
    """Length of the longest stretch of a shared boundary that is not on a wall.

    Points are projected onto the boundary's own principal axis so the span is a
    length in metres, not a count of raster cells.
    """
    if len(cells) < 2:
        return 0.0
    world = grid.to_world(cells.astype(float))
    supported = np.zeros(len(world), dtype=bool)
    for seg in segments:
        rel = world - seg.start
        t = rel @ seg.direction
        perp = np.abs(rel @ seg.normal_xz)
        supported |= (t >= -0.05) & (t <= seg.length + 0.05) & (perp <= WALL_PROXIMITY_M)
    open_pts = world[~supported]
    if len(open_pts) < 2:
        return 0.0
    centred = open_pts - open_pts.mean(axis=0)
    _, _, vt = np.linalg.svd(centred, full_matrices=False)
    along = centred @ vt[0]
    return float(along.max() - along.min())


def _wall_support(cells: np.ndarray, grid: Grid2D, segments: list[WallSegment]) -> float:
    """Fraction of a shared boundary that runs along observed wall material."""
    if len(cells) == 0 or not segments:
        return 0.0
    world = grid.to_world(cells.astype(float))
    supported = np.zeros(len(world), dtype=bool)
    for seg in segments:
        rel = world - seg.start
        t = rel @ seg.direction
        perp = np.abs(rel @ seg.normal_xz)
        on = (t >= -0.05) & (t <= seg.length + 0.05) & (perp <= WALL_PROXIMITY_M)
        supported |= on
        if supported.all():
            break
    return float(supported.mean())


def _recover_walked_floor(
    faces: list[Face],
    label_raster: np.ndarray,
    grid: Grid2D,
    segments: list[WallSegment],
) -> None:
    """Mark floor reachable from where the operator walked, without crossing a wall, as interior.

    `_label_interior` drops an unwalked face that no wall faces, which keeps the outdoors and
    reflections out of the plan. It also drops the middle of any room too wide for a wall probe
    to reach, and on the first home walk that left the hall at 6.04 m2 against a taped 14.86 m2.
    A face with interior evidence that joins a walked face across a boundary with no wall
    behind it is part of the same room.
    """
    by_index = {f.index: f for f in faces}
    neighbours: dict[int, list[int]] = {}
    for (a, b), cells in _shared_boundaries(label_raster).items():
        if len(cells) * grid.resolution < 0.12:
            continue
        if _wall_support(np.array(cells), grid, segments) < WALL_SUPPORT_THRESHOLD:
            neighbours.setdefault(a, []).append(b)
            neighbours.setdefault(b, []).append(a)
    queue = deque(f.index for f in faces if f.track_cells > 0)
    reached = set(queue)
    while queue:
        u = queue.popleft()
        for v in neighbours.get(u, ()):
            face = by_index.get(v)
            if v in reached or face is None or face.evidence < INTERIOR_EVIDENCE_THRESHOLD:
                continue
            face.interior = True
            reached.add(v)
            queue.append(v)


def _remove_appendage_strips(faces: list[Face]) -> None:
    """Drop unwalked strips narrower than a person that meet the interior only at their ends.

    Two close parallel wall lines cut a sliver the thickness of a partition. Inside a room the
    sliver has floor along both long sides and stays. Where it runs out past the room along a
    wall, only its end touches the room and it draws as a spike rather than a place to stand;
    in the shipped plan of the assignment's with-ceiling scan such spikes carried 1.8 m2.
    """
    for _ in range(10):
        interior = [f for f in faces if f.interior]
        removed = False
        for face in interior:
            if face.track_cells > 0:
                continue
            width, length = _rectangle_sides_m(face.polygon)
            if width >= APPENDAGE_MAX_WIDTH_M or length < 3 * width:
                continue
            shared = sum(
                face.polygon.boundary.intersection(other.polygon.boundary).length
                for other in interior
                if other is not face and face.polygon.intersects(other.polygon)
            )
            if shared <= 2 * width + 0.10:
                face.interior = False
                removed = True
        if not removed:
            break


def _assign_rooms(
    faces: list[Face],
    label_raster: np.ndarray,
    grid: Grid2D,
    segments: list[WallSegment],
) -> None:
    """Group interior faces into rooms by union-find over unsupported boundaries."""
    _recover_walked_floor(faces, label_raster, grid, segments)
    _remove_appendage_strips(faces)
    interior = {f.index: f for f in faces if f.interior}
    if not interior:
        return

    parent = {i: i for i in interior}

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for (a, b), cells in _shared_boundaries(label_raster).items():
        if a not in interior or b not in interior:
            continue
        if len(cells) * grid.resolution < 0.12:
            continue
        support = _wall_support(np.array(cells), grid, segments)
        # A boundary with material behind it separates two rooms even when a doorway
        # punches through it; a boundary with nothing behind it is an artefact of extending
        # a wall line across the arrangement, and the faces either side are one room.
        if support < WALL_SUPPORT_THRESHOLD:
            union(a, b)
            continue
        # A wall line that continues across an open span scores as "supported" because
        # the line is near the cells, even when most of those cells have no wall
        # material on them. The longest unsupported stretch is the opening; if it is
        # wider than a door the faces are one room.
        span = _unsupported_span_m(np.array(cells), grid, segments)
        if span > MAX_DOOR_WIDTH_M:
            union(a, b)

    roots = {}
    for idx in interior:
        r = find(idx)
        roots.setdefault(r, len(roots))
    for idx, face in interior.items():
        face.room = roots[find(idx)]

    _absorb_fragments(faces, label_raster, grid)
    _drop_unwalked_rooms(faces)
    _renumber_rooms(faces)


def _absorb_fragments(
    faces: list[Face],
    label_raster: np.ndarray,
    grid: Grid2D,
    min_room_area_m2: float = MIN_ROOM_AREA_M2,
) -> None:
    """Fold sub-room fragments into the neighbouring room they share most boundary with.

    Extending every wall line across the whole arrangement slices a corridor into a chain
    of pieces, each bounded by the line of some perpendicular wall in a room off it. Those
    pieces are not rooms at any threshold, and the boundary test cannot merge them because
    a boundary that lies along a real wall line looks supported even where the wall itself
    stops short. Area is the honest discriminator: below roughly a square metre and a half
    a region is part of something, not a room in its own right.
    """
    by_room: dict[int, list[Face]] = {}
    for face in faces:
        if face.room >= 0:
            by_room.setdefault(face.room, []).append(face)

    for _ in range(30):
        areas = {room: sum(f.area for f in fs) for room, fs in by_room.items()}
        small = [r for r, a in areas.items() if a < min_room_area_m2]
        if not small or len(by_room) <= 1:
            break

        boundaries = _shared_boundaries(label_raster)
        face_room = {f.index: f.room for f in faces if f.room >= 0}
        contact: dict[tuple[int, int], int] = {}
        for (a, b), cells in boundaries.items():
            ra, rb = face_room.get(a, -1), face_room.get(b, -1)
            if ra < 0 or rb < 0 or ra == rb:
                continue
            key = (min(ra, rb), max(ra, rb))
            contact[key] = contact.get(key, 0) + len(cells)

        target = min(small, key=lambda r: areas[r])
        options = [
            (other, n)
            for (ra, rb), n in contact.items()
            for other in ((rb,) if ra == target else (ra,) if rb == target else ())
        ]
        if not options:
            for f in by_room.pop(target):
                f.room = -1
                f.interior = False
            continue
        winner = max(options, key=lambda t: (t[1], areas.get(t[0], 0.0)))[0]
        for f in by_room.pop(target):
            f.room = winner
            by_room.setdefault(winner, []).append(f)
    _ = grid


def _drop_unwalked_rooms(faces: list[Face]) -> None:
    """Discard rooms the operator never stood in.

    A region that was seen but not entered is usually not a room: it is the outdoors
    behind a window, a balcony beyond a glass door, or the reflection of the room the
    operator was standing in. Glass and mirrors both put returns on the far side of a
    surface, and those returns land in faces with genuine floor evidence and no track.
    Requiring that a room was walked is the cheapest defence against all three, and it is
    exactly what the capture protocol asks the operator to do.
    """
    by_room: dict[int, list[Face]] = {}
    for face in faces:
        if face.room >= 0:
            by_room.setdefault(face.room, []).append(face)
    for room, fs in by_room.items():
        if sum(f.track_cells for f in fs) == 0:
            for f in fs:
                f.room = -1
                f.interior = False
        _ = room


def _renumber_rooms(faces: list[Face]) -> None:
    rooms = sorted({f.room for f in faces if f.room >= 0})
    mapping = {old: new for new, old in enumerate(rooms)}
    for f in faces:
        if f.room >= 0:
            f.room = mapping[f.room]


MIN_INSCRIBED_RADIUS_M = 0.33


def room_polygons(
    complex_: CellComplex,
    min_room_area_m2: float = 1.2,
    min_inscribed_radius_m: float = MIN_INSCRIBED_RADIUS_M,
) -> dict[int, Polygon]:
    """Merge the faces of each room into one polygon.

    Area alone does not separate a room from a sliver. Two wall lines that meet at a
    shallow angle enclose a long tapering wedge which can carry several square metres and
    is nowhere wide enough to stand in; unfiltered it swallows the corridor it lies along
    and takes the rooms off that corridor with it.

    The test is whether the room contains a disc a person could stand in. A negative buffer
    is empty exactly when it does not, and a corridor at 0.9 m wide passes comfortably while
    a wedge that is 20 cm across for most of its length does not.
    """
    grouped: dict[int, list[Polygon]] = {}
    for face in complex_.faces:
        if face.interior and face.room >= 0:
            grouped.setdefault(face.room, []).append(face.polygon)

    out: dict[int, Polygon] = {}
    for room, polys in grouped.items():
        merged = unary_union([p.buffer(1e-6) for p in polys]).buffer(-1e-6)
        merged = merged.buffer(ROOM_CLOSING_M, join_style=2).buffer(-ROOM_CLOSING_M, join_style=2)
        if merged.is_empty:
            continue
        merged = clean_polygon(merged)
        if merged.is_empty or merged.geom_type != "Polygon" or merged.area < min_room_area_m2:
            continue
        if merged.buffer(-min_inscribed_radius_m).is_empty:
            continue
        out[room] = merged
    return _merge_diagonal_splits(out)


def _rectangle_sides_m(geometry) -> tuple[float, float]:
    """Short and long side of the smallest rectangle, at any angle, around a geometry."""
    # GEOS flags a division by zero on edges parallel to an axis; the rectangle is still right.
    with np.errstate(divide="ignore", invalid="ignore"):
        xs, ys = geometry.minimum_rotated_rectangle.exterior.coords.xy
    a = float(np.hypot(xs[1] - xs[0], ys[1] - ys[0]))
    b = float(np.hypot(xs[2] - xs[1], ys[2] - ys[1]))
    return min(a, b), max(a, b)


def _merge_diagonal_splits(
    polygons: dict[int, Polygon],
    min_span_m: float = MAX_DOOR_WIDTH_M,
    strip_width_m: float = 0.25,
) -> dict[int, Polygon]:
    """Join rooms that are the same space cut by a wall line.

    A real partition overlap is a long thin strip (the wall thickness). An L-shaped
    room sliced by a wall line that continues across open floor overlaps in a fat
    region whose shorter side is metres, not centimetres. Only the second case merges.
    """
    keys = list(polygons)
    if len(keys) < 2:
        return polygons
    parent = {k: k for k in keys}

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for i, a in enumerate(keys):
        for b in keys[i + 1 :]:
            pa, pb = polygons[a], polygons[b]
            inter = pa.intersection(pb)
            if inter.is_empty or inter.area < 1e-4:
                inter = pa.buffer(0.03).intersection(pb.buffer(0.03))
            if inter.is_empty:
                continue
            # Width is measured in the overlap's own frame. An axis-aligned bounding box calls a
            # partition strip that runs diagonally across the world axes fat, and the
            # assignment's living room and bathroom, scanned about 40 degrees off those axes,
            # were joined into one room that way.
            if inter.buffer(-strip_width_m / 2).is_empty:
                continue
            if _rectangle_sides_m(inter)[1] >= min_span_m:
                union(a, b)

    groups: dict[int, list[Polygon]] = {}
    for k in keys:
        groups.setdefault(find(k), []).append(polygons[k])
    out: dict[int, Polygon] = {}
    for room, polys in groups.items():
        merged = unary_union(polys)
        merged = clean_polygon(merged)
        if merged.is_empty or merged.geom_type != "Polygon":
            continue
        out[room] = merged
    return out


def room_masks(complex_: CellComplex) -> dict[int, np.ndarray]:
    """Raster footprint of each room, for restricting measurements to one room's points."""
    grouped: dict[int, list[int]] = {}
    for face in complex_.faces:
        if face.interior and face.room >= 0:
            grouped.setdefault(face.room, []).append(face.index)
    return {room: np.isin(complex_.label_raster, ids) for room, ids in grouped.items()}
