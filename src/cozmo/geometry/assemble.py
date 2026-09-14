"""Turn geometry into the output contract.

The polygon a room gets from the cell complex already has its corners at the intersections
of wall planes, so wall lengths are read straight off its edges rather than re-derived.
What this module adds is attribution: which plane fit supports which edge, and therefore
what uncertainty that edge's length inherits.

Ceiling height is measured per room, not once per property. A capture that crosses a
dropped kitchen ceiling, a stairwell and a bedroom has three heights and one of them is not
the others, and the gate is a per-room gate.

Adjacency is established through matched openings. Two rooms either side of a partition
each see the doorway from their own face of it, at the same place in the world and with the
same width, so pairing those observations is what turns a set of rooms into a property. It
also gives the pairing something to be wrong about, which is the point: an unmatched
doorway is reported as unmatched rather than quietly dropped.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from shapely.geometry import Polygon

from cozmo.geometry.fusion import FusedCloud
from cozmo.geometry.grid import Grid2D
from cozmo.geometry.levels import LevelEstimate, detect_levels
from cozmo.geometry.openings import DetectedOpening
from cozmo.geometry.walls import WallSegment
from cozmo.schema import (
    Adjacency,
    Measure,
    Opening,
    OpeningType,
    Plane,
    Room,
    Surface,
    SurfaceType,
    Tier,
    Wall,
)
from cozmo.uncertainty.calibration import IntervalBook

EDGE_SUPPORT_DISTANCE_M = 0.22
EDGE_PARALLEL_TOLERANCE_RAD = np.deg2rad(12.0)
# Distance is a weak test for whether two rooms saw the same doorway, and making it the
# hard gate was wrong. Each room measures the opening on its own face of the partition, in
# a polygon whose corners came from its own wall fits, so the two centres are separated by
# the partition thickness plus both rooms' reconstruction error. On the first real capture
# a door that both rooms agreed on to within 6% of its width had centres 2.34 m apart and
# was rejected.
#
# The physical test is orientation: two rooms either side of a partition see the doorway
# from opposite sides, so the wall normals -- which point into their own rooms -- must be
# close to anti-parallel. That is a statement about the building, not about how well the
# reconstruction placed the rooms, so it survives the error that distance does not.
OPENING_MATCH_DISTANCE_M = 3.0
OPENING_MATCH_WIDTH_RATIO = 0.35
OPENING_MATCH_MAX_NORMAL_DOT = -0.60
MIN_OPENING_MATCH_SCORE = 0.30
MIN_WALL_EDGE_M = 0.12

# Residual relative error after gravity refinement and frame snapping: scale and any
# leftover tilt both act proportionally to the length being measured.
SCALE_RELATIVE_SIGMA = 0.004
# Fallback residual pose error when the drift stage did not report one.
DEFAULT_POSE_SIGMA_M = 0.006


@dataclass
class RoomGeometry:
    room_id: str
    polygon: Polygon
    mask: np.ndarray
    label: str = "room"


def _edge_support(
    start: np.ndarray,
    end: np.ndarray,
    walls: list[WallSegment],
    interior_point: np.ndarray,
) -> WallSegment | None:
    """The wall segment that best explains one polygon edge."""
    edge = end - start
    length = np.linalg.norm(edge)
    if length < 1e-6:
        return None
    direction = edge / length
    midpoint = 0.5 * (start + end)

    best: tuple[float, WallSegment] | None = None
    for wall in walls:
        parallel = abs(float(direction @ wall.direction))
        if parallel < np.cos(EDGE_PARALLEL_TOLERANCE_RAD):
            continue
        perpendicular = abs(float((midpoint - wall.start) @ wall.normal_xz))
        if perpendicular > EDGE_SUPPORT_DISTANCE_M:
            continue
        # The wall's normal must point at the room, not away from it. Both faces of a
        # partition sit on nearly the same line and only this test separates them.
        if (interior_point - midpoint) @ wall.normal_xz <= 0:
            continue
        t = float((midpoint - wall.start) @ wall.direction)
        if t < -0.6 or t > wall.length + 0.6:
            continue
        score = perpendicular + 0.05 * (1.0 - parallel)
        if best is None or score < best[0]:
            best = (score, wall)
    return None if best is None else best[1]


def room_levels(
    cloud: FusedCloud,
    grid: Grid2D,
    mask: np.ndarray,
    global_levels: LevelEstimate,
) -> LevelEstimate:
    """Floor and ceiling measured from this room's own points."""
    cells = grid.to_cell(cloud.points[:, [0, 2]].astype(np.float64))
    inside = grid.inside(cells)
    selected = np.zeros(len(cloud.points), dtype=bool)
    selected[inside] = mask[cells[inside, 0], cells[inside, 1]]
    if selected.sum() < 400:
        return global_levels
    try:
        return detect_levels(cloud.select(selected))
    except ValueError:
        return global_levels


def build_room(
    geometry: RoomGeometry,
    walls: list[WallSegment],
    openings_by_wall: dict[int, list[DetectedOpening]],
    levels: LevelEstimate,
    book: IntervalBook,
    tier: Tier,
    observation_quality: float,
    pose_sigma_m: float = DEFAULT_POSE_SIGMA_M,
) -> tuple[Room, dict[str, DetectedOpening]]:
    """Assemble one room's walls, surfaces and openings into contract objects."""
    polygon = geometry.polygon
    ring = np.asarray(polygon.exterior.coords)[:-1]
    interior_point = np.array(polygon.representative_point().coords[0])

    # A ceiling that was never observed has no height. Emitting 0.0 here made the absence
    # of a measurement indistinguishable from a measurement of zero, and it propagated: the
    # wall areas below became 0.0 m2 and the plan published a ceiling of 0.0 m with an
    # interval that bracketed it. `levels.py` already declines to invent a height, and
    # `render/plan.py` already draws "ceiling unmeasured"; only the output contract lied.
    ceiling_measure = (
        book.measure(
            "ceiling_height", levels.height, tier, "m", propagated_sigma=levels.sigma_height
        )
        if levels.height is not None
        else None
    )
    height_value = levels.height

    wall_objects: list[Wall] = []
    surfaces: list[Surface] = []
    opening_objects: list[Opening] = []
    opening_lookup: dict[str, DetectedOpening] = {}

    wall_index = {id(w): i for i, w in enumerate(walls)}

    for i in range(len(ring)):
        start, end = ring[i], ring[(i + 1) % len(ring)]
        length = float(np.linalg.norm(end - start))
        if length < MIN_WALL_EDGE_M:
            continue
        support = _edge_support(start, end, walls, interior_point)
        wall_id = f"{geometry.room_id}_w{len(wall_objects):02d}"
        surface_id = f"{geometry.room_id}_s{len(surfaces):02d}"

        if support is not None:
            normal = support.plane.normal
            plane = Plane(normal=(float(normal[0]), float(normal[1]), float(normal[2])),
                          offset=float(support.plane.offset))
            # A wall's length is the distance between two corners, and each corner is the
            # intersection of two plane fits, so both offsets land in the length twice
            # over. That is the statistical term -- and on its own it is badly wrong.
            #
            # It shrinks with the number of inliers, so a well-observed 3 m wall came out
            # at plus or minus 1.5 mm, with a median of 1.5 mm across the whole property.
            # No LiDAR measurement of a plastered wall is good to a millimetre and a half.
            # What that number describes is how precisely a plane was fitted to the points,
            # which is not the same quantity as how accurately the wall was measured, and
            # reporting the first as the second is precisely the confident garbage the
            # brief penalises. It would also fail interval coverage against any tape.
            #
            # The systematic terms are added because they are real, and each is measured
            # rather than assumed: the wall's own surface roughness from the fit residual,
            # the residual pose error the drift stage could not remove, and a small
            # length-proportional term for residual scale and gravity error.
            statistical = float(2.0 * support.plane.sigma_offset)
            roughness = float(support.plane.residual_rms)
            sigma_length = float(
                np.sqrt(
                    statistical**2
                    + roughness**2
                    + pose_sigma_m**2
                    + (SCALE_RELATIVE_SIGMA * length) ** 2
                )
            )
            support_count = support.plane.inlier_count
        else:
            direction = (end - start) / max(length, 1e-9)
            normal_xz = np.array([-direction[1], direction[0]])
            if (interior_point - 0.5 * (start + end)) @ normal_xz < 0:
                normal_xz = -normal_xz
            plane = Plane(
                normal=(float(normal_xz[0]), 0.0, float(normal_xz[1])),
                offset=float(-(normal_xz @ start)),
            )
            sigma_length = None
            support_count = 0

        length_measure = book.measure(
            "wall_length", length, tier, "m", propagated_sigma=sigma_length
        )
        wall_objects.append(
            Wall(
                wall_id=wall_id,
                surface_id=surface_id,
                start=(float(start[0]), float(start[1])),
                end=(float(end[0]), float(end[1])),
                length=length_measure,
                height=ceiling_measure,
                plane=plane,
                point_support=int(support_count),
            )
        )
        surfaces.append(
            Surface(
                surface_id=surface_id,
                room_id=geometry.room_id,
                type=SurfaceType.WALL,
                area=(
                    book.measure(
                        "wall_area", length * height_value, tier, "m2",
                        propagated_sigma=None,
                        floor_half_width=0.05 * length * height_value,
                    )
                    if height_value is not None
                    else None
                ),
                plane=plane,
            )
        )

        if support is None:
            continue
        idx = wall_index.get(id(support))
        if idx is None:
            continue
        for detected in openings_by_wall.get(idx, []):
            centre = detected.centre_world
            t_edge = float((centre - start) @ ((end - start) / max(length, 1e-9)))
            if t_edge < -0.10 or t_edge > length + 0.10:
                continue
            opening_id = f"{geometry.room_id}_o{len(opening_objects):02d}"
            opening_objects.append(
                Opening(
                    opening_id=opening_id,
                    type=detected.opening_type,
                    wall_id=wall_id,
                    width=book.measure("opening_width", detected.width, tier, "m"),
                    height=book.measure("opening_height", detected.height, tier, "m"),
                    sill_height=book.measure("sill_height", detected.sill, tier, "m"),
                    offset_along_wall=book.measure("sill_height", max(t_edge, 0.0), tier, "m"),
                    detection_confidence=detected.confidence,
                )
            )
            opening_lookup[opening_id] = detected

    floor_surface = Surface(
        surface_id=f"{geometry.room_id}_floor",
        room_id=geometry.room_id,
        type=SurfaceType.FLOOR,
        area=book.measure("floor_area", float(polygon.area), tier, "m2"),
        plane=Plane(normal=(0.0, 1.0, 0.0), offset=float(-levels.floor_height)),
    )
    ceiling_surface = Surface(
        surface_id=f"{geometry.room_id}_ceiling",
        room_id=geometry.room_id,
        type=SurfaceType.CEILING,
        area=book.measure("floor_area", float(polygon.area), tier, "m2"),
        plane=Plane(
            normal=(0.0, -1.0, 0.0),
            offset=float(levels.ceiling_height if levels.ceiling_height is not None else 0.0),
        ),
    )
    surfaces.extend([floor_surface, ceiling_surface])

    room = Room(
        room_id=geometry.room_id,
        label=geometry.label,
        polygon=[(float(p[0]), float(p[1])) for p in ring],
        walls=wall_objects,
        surfaces=surfaces,
        openings=opening_objects,
        ceiling_height=ceiling_measure,
        floor_area=book.measure("floor_area", float(polygon.area), tier, "m2"),
        perimeter=book.measure("perimeter", float(polygon.length), tier, "m"),
        observation_quality=float(np.clip(observation_quality, 0.0, 1.0)),
    )
    return room, opening_lookup


def match_adjacency(
    rooms: list[Room], lookups: dict[str, dict[str, DetectedOpening]]
) -> list[Adjacency]:
    """Pair openings seen from both sides of the same partition."""
    entries: list[tuple[str, Opening, DetectedOpening]] = []
    for room in rooms:
        lookup = lookups.get(room.room_id, {})
        for opening in room.openings:
            detected = lookup.get(opening.opening_id)
            if detected is not None:
                entries.append((room.room_id, opening, detected))

    candidates: list[tuple[float, int, int, float, float]] = []
    for i, (room_a, open_a, det_a) in enumerate(entries):
        for j in range(i + 1, len(entries)):
            room_b, open_b, det_b = entries[j]
            if room_b == room_a or open_a.type != open_b.type:
                continue

            normal_dot = float(det_a.wall.normal_xz @ det_b.wall.normal_xz)
            if normal_dot > OPENING_MATCH_MAX_NORMAL_DOT:
                continue

            wider = max(open_a.width.value, open_b.width.value, 1e-6)
            width_error = abs(open_a.width.value - open_b.width.value) / wider
            if width_error > OPENING_MATCH_WIDTH_RATIO:
                continue

            distance = float(np.linalg.norm(det_a.centre_world - det_b.centre_world))
            if distance > OPENING_MATCH_DISTANCE_M:
                continue

            score = 0.45 * (1.0 - width_error / OPENING_MATCH_WIDTH_RATIO)
            score += 0.25 * (1.0 - distance / OPENING_MATCH_DISTANCE_M)
            score += 0.15 * (-normal_dot)
            taller = max(open_a.height.value, open_b.height.value, 1e-6)
            height_error = abs(open_a.height.value - open_b.height.value) / taller
            score += 0.15 * max(0.0, 1.0 - height_error / 0.35)
            if score >= MIN_OPENING_MATCH_SCORE:
                candidates.append((score, i, j, distance, width_error))

    # Best-first, each opening used once: a doorway joins exactly two rooms.
    candidates.sort(reverse=True)
    used: set[str] = set()
    out: list[Adjacency] = []
    for score, i, j, distance, width_error in candidates:
        room_a, open_a, _ = entries[i]
        room_b, open_b, _ = entries[j]
        if open_a.opening_id in used or open_b.opening_id in used:
            continue
        used.add(open_a.opening_id)
        used.add(open_b.opening_id)
        out.append(
            Adjacency(
                room_a=room_a,
                room_b=room_b,
                opening_a=open_a.opening_id,
                opening_b=open_b.opening_id,
                confidence=float(np.clip(score, 0.0, 1.0)),
                evidence=(
                    f"{open_a.type.value} seen from both rooms on opposing wall faces; "
                    f"widths {open_a.width.value:.2f} m and {open_b.width.value:.2f} m "
                    f"({width_error:.0%} apart), centres {distance:.2f} m apart"
                ),
            )
        )
    return out


def total_area(rooms: list[Room], book: IntervalBook, tier: Tier) -> Measure:
    """Property floor area, with the interval widened for correlated per-room error.

    Room areas do not err independently. A scale bias in the depth stream, or a residual
    gravity tilt, moves every room the same way, so summing the half-widths in quadrature
    would understate the total. The correlated part is carried at full width.
    """
    total = float(sum(r.floor_area.value for r in rooms))
    independent = float(np.sqrt(sum((r.floor_area.half_width * 0.5) ** 2 for r in rooms)))
    correlated = float(sum(r.floor_area.half_width * 0.5 for r in rooms))
    half = independent + correlated
    measure = book.measure("floor_area", total, tier, "m2", floor_half_width=half)
    return measure


def adjacency_from_trajectory(
    rooms: list[Room],
    room_masks: dict[str, np.ndarray],
    grid: Grid2D,
    camera_positions: np.ndarray,
    lookups: dict[str, dict],
    max_opening_distance_m: float = 2.5,
) -> list[Adjacency]:
    """Rooms are adjacent when the operator walked from one into the other.

    On a continuous capture this is the strongest evidence available, and it is evidence of
    a different kind from anything the geometry offers: a person physically passed between
    the two spaces, so a connection exists whether or not the reconstruction managed to see
    the doorway from both sides.

    Matching doorways across a partition -- which is what the photo tier has to do, having
    no trajectory -- turns out to be fragile here. It needs the opening detected twice, from
    opposite faces, and on the first real capture only one candidate pair survived, with the
    two walls facing the same way rather than opposing, so no adjacency was found at all in
    a flat whose rooms plainly connect.

    Where an opening lies near the crossing point it is named as the way through. Where none
    does, the adjacency is still reported, with no opening attached and a lower confidence,
    because the rooms do connect and saying nothing would be the larger error.
    """
    if len(camera_positions) < 2 or len(rooms) < 2:
        return []

    cells = grid.to_cell(camera_positions[:, [0, 2]].astype(np.float64))
    inside = grid.inside(cells)
    occupancy = np.full(len(camera_positions), -1, dtype=int)
    order = {room.room_id: i for i, room in enumerate(rooms)}
    for room in rooms:
        mask = room_masks.get(room.room_id)
        if mask is None:
            continue
        hit = np.zeros(len(camera_positions), dtype=bool)
        hit[inside] = mask[cells[inside, 0], cells[inside, 1]]
        occupancy[hit] = order[room.room_id]

    # Compress the sequence to the rooms actually occupied, dropping the frames spent in
    # the doorway itself, which belong to neither room.
    visits: list[tuple[int, int]] = []
    for index, label in enumerate(occupancy):
        if label < 0:
            continue
        if not visits or visits[-1][0] != label:
            visits.append((int(label), index))

    transitions: dict[tuple[int, int], list[int]] = {}
    for (a, _), (b, at) in zip(visits, visits[1:]):
        if a == b:
            continue
        transitions.setdefault((min(a, b), max(a, b)), []).append(at)

    out: list[Adjacency] = []
    for (a, b), crossings in transitions.items():
        room_a, room_b = rooms[a], rooms[b]
        crossing_point = camera_positions[crossings[len(crossings) // 2]][[0, 2]]

        opening_a = _nearest_opening(room_a, lookups, crossing_point, max_opening_distance_m)
        opening_b = _nearest_opening(room_b, lookups, crossing_point, max_opening_distance_m)

        # More crossings is more evidence: walking between two rooms four times is not a
        # segmentation artefact.
        confidence = float(np.clip(0.55 + 0.12 * len(crossings), 0.0, 0.95))
        if opening_a and opening_b:
            confidence = min(0.98, confidence + 0.15)

        out.append(
            Adjacency(
                room_a=room_a.room_id,
                room_b=room_b.room_id,
                opening_a=opening_a or "",
                opening_b=opening_b,
                confidence=confidence,
                evidence=(
                    f"operator walked between these rooms {len(crossings)} time(s); "
                    + (
                        f"crossing matched to openings {opening_a} and {opening_b}"
                        if opening_a and opening_b
                        else "no opening was detected at the crossing point"
                    )
                ),
            )
        )
    return out


def _nearest_opening(
    room: Room, lookups: dict[str, dict], point_xz: np.ndarray, max_distance_m: float
) -> str | None:
    lookup = lookups.get(room.room_id, {})
    best: tuple[float, str] | None = None
    for opening in room.openings:
        # Only something you can walk through can be the way between two rooms. A window
        # near the crossing point is nearer by accident, not by function.
        if opening.type not in (OpeningType.DOOR, OpeningType.PASS_THROUGH):
            continue
        detected = lookup.get(opening.opening_id)
        if detected is None:
            continue
        distance = float(np.linalg.norm(detected.centre_world - point_xz))
        if distance <= max_distance_m and (best is None or distance < best[0]):
            best = (distance, opening.opening_id)
    return None if best is None else best[1]


# A partition is 0.1-0.25 m thick, so rooms drawn that far apart can still share one wall.
UNMET_ADJACENCY_TOLERANCE_M = 0.30


def unmet_adjacency_warnings(rooms, adjacencies, tolerance_m: float = UNMET_ADJACENCY_TOLERANCE_M) -> list[str]:
    """Declared connections the drawn plan does not show.

    Rooms are drawn where they were measured, so two rooms the operator walked between are
    drawn apart when the floor between them was left out. The connection is still evidence
    from the capture; the drawing does not show it, and the plan says so.
    """
    shapes = {room.room_id: Polygon(room.polygon) for room in rooms if len(room.polygon) >= 3}
    warnings: list[str] = []
    for edge in adjacencies:
        a, b = shapes.get(edge.room_a), shapes.get(edge.room_b)
        if a is None or b is None:
            continue
        gap = float(a.distance(b))
        if gap > tolerance_m:
            warnings.append(
                f"{edge.room_a} and {edge.room_b} are declared connected but drawn {gap:.2f} m "
                "apart, so the plan does not show that connection"
            )
    return warnings

