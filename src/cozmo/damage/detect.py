"""Damage detection: from pixels, to a surface, to a metric extent.

This module previously returned two hardcoded regions per room -- a `water_stain` of
0.18 m2 at confidence 0.92 and a `crack` of 1.15 m, with fixed evidence frames -- and the
pipeline called it without passing an image at all, so the same two findings appeared in
every room of every property including ones with no damage. Both carried
`IntervalMethod.CONFORMAL`, which is the one field a reader uses to tell a calibrated
interval from a guess.

What replaces it responds to the image: a classical detector for discolouration and cracks,
which have specific, separable appearances and need no downloaded model, as the walk-in
test requires. An open-vocabulary path that returned the same fixed box at 0.88 confidence
for every prompt whenever its weights directory existed has been removed rather than left
to fire.

Detections are only reported once they land on a surface. A bounding box in an image is
not a finding; a region of a named wall with an area in square metres is. Projection uses
the frame's own depth and pose, so the extent is measured rather than assumed, and a
detection whose pixels do not fall on any reconstructed surface is dropped rather than
attached to the nearest guess.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence

import cv2
import numpy as np

from cozmo.schema import DamageClass, DamageRegion, ExtentKind, Measure, Tier
from cozmo.uncertainty.calibration import IntervalBook
from cozmo.util.imaging import LOW_LIGHT_MEAN_LUMA, mean_luma

log = logging.getLogger(__name__)

# A stain must cover this much of the frame before it is worth reporting. Below it the
# region is smaller than the depth sensor can measure the extent of anyway.
MIN_STAIN_AREA_FRACTION = 0.0025
MIN_CRACK_LENGTH_PX = 60
MIN_CRACK_ELONGATION = 6.0
# A crack in plaster or concrete follows the weakest path through the material and wanders. The
# edge of a picture frame, a shelf or a panel photographs as a straight line to within a pixel or
# two. On the assignment's floor-only scan the only crack left after every other test was the
# lower edge of a picture frame, whose centreline stayed within 1.4 px of a straight line over
# 471 px. Below this length a centreline is too short to tell a straight crack from an edge.
STRAIGHTNESS_TEST_MIN_LENGTH_PX = 150
MAX_EDGE_DEVIATION_SHARE = 0.01
MAX_DETECTIONS_PER_FRAME = 8
# Damage is on a surface, so its depth points lie on the wall plane. A vanity front, the top edge
# of a fridge or a picture frame sits centimetres in front of the wall: the only two findings on
# the assignment's scans were 6-15 cm and 9-10 cm off it. Wall depth from the LiDAR is good to
# about a centimetre at room distances.
SURFACE_ASSIGN_TOLERANCE_M = 0.04
MIN_ON_SURFACE_FRACTION = 0.70
MERGE_DISTANCE_M = 0.35
# Within that radius, two sightings are one finding only where they also land on the same patch of
# the same wall. Pose and depth put a revisited point within a few centimetres. The radius alone
# joined the rim of a toilet lid and the edge of its seat, 15 cm apart on the wall, into one crack
# seen from two frames on the first home walk.
SAME_PATCH_TOLERANCE_M = 0.05

# A finding must be seen from more than one viewpoint. This is the defence against the
# three surfaces the brief calls out -- mirrors, glass and wet-look floors -- and against
# specular highlights generally. A reflection is view-dependent: it sits at a different
# place on the wall from every camera position, so it never reprojects to the same patch
# of surface twice. Real damage is attached to the surface and does.
MIN_EVIDENCE_FRAMES = 2


@dataclass
class ImageDetection:
    """One candidate region in one frame, before it has been given a surface."""

    frame_index: int
    damage_class: DamageClass
    bbox: tuple[float, float, float, float]
    score: float
    detector: str
    mask: Optional[np.ndarray] = field(default=None, repr=False)


def _wall_colour_reference(rgb: np.ndarray) -> np.ndarray:
    """The room's own wall colour, as the modal hue and value of the brightest surfaces.

    Discolouration is a departure from the surrounding wall, not an absolute colour. A
    magnolia wall and a grey wall have very different "normal", and a fixed brown-hue
    threshold flags the first and misses a stain on the second.
    """
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    value = hsv[:, :, 2]
    bright = value > np.percentile(value, 55)
    if bright.sum() < 64:
        bright = np.ones_like(value, dtype=bool)
    return np.array(
        [
            float(np.median(hsv[:, :, 0][bright])),
            float(np.median(hsv[:, :, 1][bright])),
            float(np.median(value[bright])),
        ]
    )


def detect_water_stains(rgb: np.ndarray) -> list[ImageDetection]:
    """Regions that are yellower, browner or dirtier than the wall around them."""
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV).astype(np.float32)
    reference = _wall_colour_reference(rgb)

    hue_delta = np.abs(((hsv[:, :, 0] - reference[0] + 90.0) % 180.0) - 90.0)
    saturation_gain = hsv[:, :, 1] - reference[1]
    value_drop = reference[2] - hsv[:, :, 2]

    # A stain is a shift toward yellow-brown, more saturated than the wall, and slightly
    # darker. Requiring all three separates a stain from a shadow, which drops the value
    # without changing hue or saturation, and from a coloured object, which shifts hue
    # much further than a stain does.
    candidate = (hue_delta > 4) & (hue_delta < 40) & (saturation_gain > 18) & (value_drop > 8) & (value_drop < 110)
    candidate = cv2.morphologyEx(
        candidate.astype(np.uint8), cv2.MORPH_OPEN, np.ones((5, 5), np.uint8)
    )
    candidate = cv2.morphologyEx(candidate, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))

    height, width = candidate.shape
    min_area = MIN_STAIN_AREA_FRACTION * height * width
    count, labels, stats, _ = cv2.connectedComponentsWithStats(candidate, connectivity=8)

    out: list[ImageDetection] = []
    for label in range(1, count):
        x, y, w, h, area = stats[label]
        if area < min_area:
            continue
        fill = area / max(w * h, 1)
        if fill < 0.30:
            continue
        # Confidence rises with how far the region departs from the wall and how solidly
        # it fills its own bounding box. It is a score, not a probability, and it is not
        # allowed to reach certainty.
        region = labels[y : y + h, x : x + w] == label
        strength = float(np.mean(saturation_gain[y : y + h, x : x + w][region])) / 60.0
        score = float(np.clip(0.25 + 0.35 * fill + 0.30 * min(strength, 1.0), 0.1, 0.85))
        out.append(
            ImageDetection(
                frame_index=-1,
                damage_class=DamageClass.WATER_STAIN,
                bbox=(float(x), float(y), float(x + w), float(y + h)),
                score=score,
                detector="classical_colour_anomaly",
                mask=region,
            )
        )
    return sorted(out, key=lambda d: -d.score)[:MAX_DETECTIONS_PER_FRAME]


def detect_cracks(rgb: np.ndarray) -> list[ImageDetection]:
    """Thin dark linear features on an otherwise smooth surface.

    A black-hat transform keeps structures darker than their surroundings and narrower
    than the kernel, which is what a crack is and what a shadow, a skirting board and a
    door frame are not. The elongation test then discards blobs: a crack is long and thin,
    and the ratio of its principal axes is the cheapest way to say so. The edge of a picture
    frame or a shelf is long and thin too, but ruler-straight, and a crack is not.
    """
    grey = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    grey = cv2.bilateralFilter(grey, 7, 40, 40)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (13, 13))
    blackhat = cv2.morphologyEx(grey, cv2.MORPH_BLACKHAT, kernel)
    if blackhat.max() < 12:
        return []

    threshold = max(12, int(np.percentile(blackhat, 99.3)))
    binary = (blackhat >= threshold).astype(np.uint8)
    binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))

    count, labels, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
    out: list[ImageDetection] = []
    for label in range(1, count):
        x, y, w, h, area = stats[label]
        if area < 30:
            continue
        ys, xs = np.nonzero(labels[y : y + h, x : x + w] == label)
        if len(xs) < 30:
            continue
        coords = np.stack([xs, ys], axis=1).astype(np.float64)
        coords -= coords.mean(axis=0)
        eigenvalues = np.linalg.eigvalsh(np.cov(coords.T) + 1e-9 * np.eye(2))
        major = float(np.sqrt(max(eigenvalues[1], 1e-9)))
        minor = float(np.sqrt(max(eigenvalues[0], 1e-9)))
        elongation = major / max(minor, 1e-6)
        length = 4.0 * major
        if elongation < MIN_CRACK_ELONGATION or length < MIN_CRACK_LENGTH_PX:
            continue
        principal = np.linalg.eigh(np.cov(coords.T) + 1e-9 * np.eye(2))[1][:, 1]
        if _is_straight_edge(coords, principal):
            continue
        score = float(np.clip(0.20 + 0.05 * min(elongation, 12.0), 0.1, 0.80))
        angle = float(np.degrees(np.arctan2(principal[1], principal[0])) % 180.0)
        out.append(
            (
                ImageDetection(
                    frame_index=-1,
                    damage_class=DamageClass.CRACK,
                    bbox=(float(x), float(y), float(x + w), float(y + h)),
                    score=score,
                    detector="classical_blackhat_ridge",
                    mask=labels[y : y + h, x : x + w] == label,
                ),
                angle,
            )
        )

    kept = _reject_tiling_pattern(out)
    return sorted(kept, key=lambda d: -d.score)[:MAX_DETECTIONS_PER_FRAME]


def _is_straight_edge(coords: np.ndarray, principal: np.ndarray) -> bool:
    """Whether a long thin component's centreline is a straight line, as an edge's is and a crack's is not."""
    along = coords @ principal
    across = coords @ np.array([-principal[1], principal[0]])
    span = float(np.ptp(along))
    if span < STRAIGHTNESS_TEST_MIN_LENGTH_PX:
        return False
    bins = np.floor((along - along.min()) / 8.0).astype(int)
    keys = np.unique(bins)
    position = np.array([np.median(along[bins == b]) for b in keys])
    centre = np.array([np.median(across[bins == b]) for b in keys])
    line = np.polyval(np.polyfit(position, centre, 1), position)
    return float(np.abs(centre - line).max()) < MAX_EDGE_DEVIATION_SHARE * span


def _reject_tiling_pattern(
    candidates: list[tuple[ImageDetection, float]],
    angle_tolerance_deg: float = 10.0,
    min_family_size: int = 3,
) -> list[ImageDetection]:
    """Drop dark linear features that belong to a repeating parallel family.

    Tile grout, panel joints, floorboard gaps and brick courses all read to a ridge filter
    exactly as a crack does: thin, dark, straight and long. What separates them is that
    they come in sets. A building has many parallel evenly spaced joints and generally one
    crack, so a family of three or more near-parallel features in a single frame is far
    more likely to be a tiled surface than three independent cracks.

    On a bathroom-heavy property this is most of the false positive rate. It costs recall
    in the specific case of a crack that runs parallel to two other cracks in the same
    frame, which is rare enough, and visible enough when it happens, to be the better
    trade.
    """
    if len(candidates) < min_family_size:
        return [c for c, _ in candidates]

    angles = np.array([a for _, a in candidates])
    suppressed = np.zeros(len(candidates), dtype=bool)
    for i, angle in enumerate(angles):
        # Circular distance on a 180 degree period: a line and its reverse are one angle.
        delta = np.abs((angles - angle + 90.0) % 180.0 - 90.0)
        family = np.flatnonzero(delta <= angle_tolerance_deg)
        if len(family) >= min_family_size:
            suppressed[family] = True
    return [c for i, (c, _) in enumerate(candidates) if not suppressed[i]]


def detect_in_image(rgb: np.ndarray, frame_index: int = 0) -> tuple[list[ImageDetection], str]:
    """All damage candidates in one frame, plus the name of the detector that found them.

    The detector name is returned rather than logged because it belongs in the plan, so a
    reader can tell what produced a finding and how far to trust it.
    """
    classical = detect_water_stains(rgb) + detect_cracks(rgb)
    for detection in classical:
        detection.frame_index = frame_index
    return classical, "classical"


def _severity(damage_class: DamageClass, extent: float) -> str:
    """Severity from metric extent, with thresholds that differ by what is being measured."""
    if damage_class is DamageClass.CRACK:
        return "minor" if extent < 0.6 else "moderate" if extent < 1.8 else "severe"
    return "minor" if extent < 0.10 else "moderate" if extent < 0.50 else "severe"


def project_detection(
    detection: ImageDetection,
    depth: np.ndarray,
    k_depth: np.ndarray,
    pose_world: np.ndarray,
    rgb_size: tuple[int, int],
    walls,
    wall_surface_ids: dict[int, str],
    floor_y: float,
) -> Optional[tuple[str, int, np.ndarray, np.ndarray]]:
    """Place an image detection on a wall.

    Returns (surface_id, wall index, surface-local uv points, world points), or None when
    the detection's pixels do not land on any reconstructed wall. Returning None is the
    point: a box floating on a curtain or a piece of furniture is not damage to a surface,
    and attaching it to the nearest wall would invent both a location and an extent.
    """
    x0, y0, x1, y1 = detection.bbox
    scale_x = depth.shape[1] / rgb_size[0]
    scale_y = depth.shape[0] / rgb_size[1]
    c0, c1 = int(x0 * scale_x), max(int(x1 * scale_x), int(x0 * scale_x) + 1)
    r0, r1 = int(y0 * scale_y), max(int(y1 * scale_y), int(y0 * scale_y) + 1)
    c0, c1 = np.clip([c0, c1], 0, depth.shape[1])
    r0, r1 = np.clip([r0, r1], 0, depth.shape[0])
    if c1 - c0 < 2 or r1 - r0 < 2:
        return None

    patch = depth[r0:r1, c0:c1]
    valid = np.isfinite(patch) & (patch > 0.2) & (patch < 6.0)
    if valid.sum() < 12:
        return None

    rows, cols = np.nonzero(valid)
    z = patch[valid].astype(np.float64)
    us = (cols + c0).astype(np.float64)
    vs = (rows + r0).astype(np.float64)
    x = (us - k_depth[0, 2]) * z / k_depth[0, 0]
    y = (vs - k_depth[1, 2]) * z / k_depth[1, 1]
    camera_points = np.stack([x, y, z], axis=1)
    world = camera_points @ pose_world[:3, :3].T + pose_world[:3, 3]

    best_index, best_inliers = None, 0
    for index, wall in enumerate(walls):
        normal = np.array([wall.normal_xz[0], 0.0, wall.normal_xz[1]])
        distance = np.abs((world - np.array([wall.start[0], floor_y, wall.start[1]])) @ normal)
        along = (world[:, [0, 2]] - wall.start) @ wall.direction
        on_wall = (
            (distance < SURFACE_ASSIGN_TOLERANCE_M)
            & (along > -0.15)
            & (along < wall.length + 0.15)
        )
        if int(on_wall.sum()) > best_inliers:
            best_inliers, best_index = int(on_wall.sum()), index

    if best_index is None or best_inliers < max(10, MIN_ON_SURFACE_FRACTION * len(world)):
        return None

    wall = walls[best_index]
    surface_id = wall_surface_ids.get(best_index)
    if surface_id is None:
        return None

    normal = np.array([wall.normal_xz[0], 0.0, wall.normal_xz[1]])
    distance = np.abs((world - np.array([wall.start[0], floor_y, wall.start[1]])) @ normal)
    keep = distance < SURFACE_ASSIGN_TOLERANCE_M
    on_surface = world[keep]
    u = (on_surface[:, [0, 2]] - wall.start) @ wall.direction
    v = on_surface[:, 1] - floor_y
    return surface_id, best_index, np.stack([u, v], axis=1), on_surface


def _footprint_gap_m(a: np.ndarray, b: np.ndarray) -> float:
    """Distance between the surface bounding boxes of two sets of (u, v) points; zero where they overlap."""
    du = max(0.0, max(a[:, 0].min(), b[:, 0].min()) - min(a[:, 0].max(), b[:, 0].max()))
    dv = max(0.0, max(a[:, 1].min(), b[:, 1].min()) - min(a[:, 1].max(), b[:, 1].max()))
    return float(np.hypot(du, dv))


def build_damage_regions(
    projections: Sequence[tuple[str, str, np.ndarray, np.ndarray, ImageDetection]],
    book: IntervalBook,
    tier: Tier,
    detector_name: str,
) -> list[DamageRegion]:
    """Turn surface-projected detections into contract regions, merging repeat sightings.

    The same stain seen from four frames is one finding. Merging is by class, by proximity in
    world coordinates and by overlap on the same surface, and the merged extent is taken from
    the union of the observations rather than from the largest, because a partial view of a
    stain understates it and the union is the closest thing available to the whole.
    """
    merged: list[dict] = []
    for room_id, surface_id, uv, world, detection in projections:
        placed = False
        for group in merged:
            if group["room_id"] != room_id or group["class"] is not detection.damage_class:
                continue
            near = np.linalg.norm(group["centroid"] - world.mean(axis=0)) < MERGE_DISTANCE_M
            same_patch = group["surface_id"] == surface_id and _footprint_gap_m(group["uv"], uv) <= SAME_PATCH_TOLERANCE_M
            if near and same_patch:
                group["uv"] = np.vstack([group["uv"], uv])
                group["world"] = np.vstack([group["world"], world])
                group["centroid"] = group["world"].mean(axis=0)
                group["frames"].append(detection.frame_index)
                group["score"] = max(group["score"], detection.score)
                placed = True
                break
        if not placed:
            merged.append(
                {
                    "room_id": room_id,
                    "surface_id": surface_id,
                    "class": detection.damage_class,
                    "uv": uv,
                    "world": world,
                    "centroid": world.mean(axis=0),
                    "frames": [detection.frame_index],
                    "score": detection.score,
                }
            )

    regions: list[DamageRegion] = []
    index = 0
    for group in merged:
        frames = sorted(set(int(f) for f in group["frames"]))
        if len(frames) < MIN_EVIDENCE_FRAMES:
            continue
        index += 1
        uv = group["uv"]
        u_min, v_min = uv.min(axis=0)
        u_max, v_max = uv.max(axis=0)
        width, height = float(u_max - u_min), float(v_max - v_min)

        if group["class"] is DamageClass.CRACK:
            kind = ExtentKind.LENGTH
            value = float(np.hypot(width, height))
            unit, quantity = "m", "damage_length"
        else:
            kind = ExtentKind.AREA
            # Area of the observed points, not of the bounding box: a diagonal stain fills
            # about half its box and billing for the box overstates the repair.
            cell = 0.02
            occupied = {(int(a / cell), int(b / cell)) for a, b in uv}
            value = float(len(occupied) * cell * cell)
            unit, quantity = "m2", "damage_area"

        regions.append(
            DamageRegion(
                damage_id=f"dmg_{index:03d}",
                room_id=group["room_id"],
                surface_id=group["surface_id"],
                damage_class=group["class"],
                extent_kind=kind,
                extent=book.measure(quantity, value, tier, unit),
                bbox_on_surface=(float(u_min), float(v_min), float(u_max), float(v_max)),
                polygon_on_surface=[
                    (float(u_min), float(v_min)),
                    (float(u_max), float(v_min)),
                    (float(u_max), float(v_max)),
                    (float(u_min), float(v_max)),
                ],
                severity=_severity(group["class"], value),
                classification_confidence=float(
                    np.clip(
                        group["score"]
                        * (0.7 if detector_name == "classical" else 1.0)
                        # Corroboration across viewpoints is evidence in its own right.
                        * min(1.0, 0.6 + 0.2 * len(frames)),
                        0.05,
                        0.95,
                    )
                ),
                evidence_frames=frames,
            )
        )
    return regions


@dataclass
class _WallView:
    """The minimum a projection needs from a wall, built from the contract objects.

    Projection works against the walls that are actually in the plan, not against the
    internal segment list, so a detection can only ever be attached to a surface the
    output contract also reports. That rules out the failure where damage is keyed to a
    surface_id nobody can look up.
    """

    start: np.ndarray
    direction: np.ndarray
    normal_xz: np.ndarray
    length: float


def _wall_views(room) -> tuple[list[_WallView], dict[int, str]]:
    views: list[_WallView] = []
    surface_ids: dict[int, str] = {}
    for wall in room.walls:
        start = np.array(wall.start, dtype=float)
        end = np.array(wall.end, dtype=float)
        length = float(np.linalg.norm(end - start))
        if length < 0.25:
            continue
        direction = (end - start) / length
        normal = np.array([wall.plane.normal[0], wall.plane.normal[2]])
        norm = float(np.linalg.norm(normal))
        normal = normal / norm if norm > 1e-6 else np.array([-direction[1], direction[0]])
        surface_ids[len(views)] = wall.surface_id
        views.append(_WallView(start=start, direction=direction, normal_xz=normal, length=length))
    return views, surface_ids


def detect_damage_for_rooms(
    rooms,
    source,
    keyframes: Sequence[int],
    camera_positions: np.ndarray,
    world_rotation: np.ndarray,
    room_masks: dict,
    grid,
    floor_y: float,
    book: IntervalBook,
    tier: Tier,
    frames_per_room: int = 6,
) -> tuple[list[DamageRegion], str, int, float | None]:
    """Detect damage across a property and key each finding to a surface.

    Frames are attributed to rooms by where the camera stood, so a bedroom's walls are
    only ever searched in frames taken in the bedroom. Searching every frame for every room
    would be both slower and wrong: the same wall seen through a doorway from the corridor
    is foreshortened, poorly lit and frequently half-occluded, and it produces exactly the
    low-quality detections that a per-surface extent then reports as fact.

    Returns the regions, the name of the detector that produced them, how many frames were
    examined, and the share of those frames too dark to trust (None when none were
    examined). The last three belong in the quality report.
    """
    if not rooms or len(camera_positions) == 0:
        return [], "none", 0, None

    room_by_key = {}
    for room in rooms:
        mask = room_masks.get(room.room_id)
        if mask is not None:
            room_by_key[room.room_id] = mask

    cells = grid.to_cell(camera_positions[:, [0, 2]].astype(np.float64))
    inside = grid.inside(cells)

    wanted: dict[int, list[str]] = {}
    for room in rooms:
        mask = room_by_key.get(room.room_id)
        if mask is None:
            continue
        in_room = np.zeros(len(camera_positions), dtype=bool)
        in_room[inside] = mask[cells[inside, 0], cells[inside, 1]]
        indices = np.flatnonzero(in_room)
        if len(indices) == 0:
            continue
        # Spread the sampled frames over the time spent in the room rather than taking the
        # first few, which would all be taken from the doorway looking in.
        step = max(1, len(indices) // frames_per_room)
        for position in indices[::step][:frames_per_room]:
            wanted.setdefault(int(position), []).append(room.room_id)

    if not wanted:
        return [], "none", 0, None

    frame_numbers = (
        source.frame_indices() if hasattr(source, "frame_indices") else list(range(len(keyframes)))
    )
    position_to_frame = {int(p): int(frame_numbers[keyframes[p]]) for p in wanted}
    try:
        images = source.load_rgb_batch(sorted(set(position_to_frame.values())))
    except Exception as exc:
        log.warning("colour frames unavailable, damage detection skipped: %s", exc)
        return [], "none", 0, None
    if not images:
        return [], "none", 0, None

    depth_by_frame: dict[int, tuple] = {}
    for frame in source.frames([keyframes[p] for p in sorted(wanted)]):
        if frame.depth is not None and frame.pose is not None:
            depth_by_frame[int(frame.index)] = (frame.depth, frame.k_depth, frame.pose, frame.rgb_size)

    rooms_by_id = {room.room_id: room for room in rooms}
    views_cache = {room.room_id: _wall_views(room) for room in rooms}

    projections: list[tuple[str, str, np.ndarray, np.ndarray, ImageDetection]] = []
    detector_name = "classical"
    examined = 0
    dark = 0

    for position in sorted(wanted):
        frame_number = position_to_frame[position]
        rgb = images.get(frame_number)
        entry = depth_by_frame.get(frame_number)
        if rgb is None or entry is None:
            continue
        depth, k_depth, pose, rgb_size = entry
        examined += 1
        if mean_luma(rgb) < LOW_LIGHT_MEAN_LUMA:
            dark += 1

        detections, detector_name = detect_in_image(rgb, frame_number)
        if not detections:
            continue

        # The cloud was rotated onto gravity and then onto the building frame, so the
        # frame's own pose has to be carried through the same rotation before its points
        # can be compared with walls expressed in that frame.
        pose_world = pose.copy()
        pose_world[:3, :3] = world_rotation @ pose[:3, :3]
        pose_world[:3, 3] = world_rotation @ pose[:3, 3]

        for room_id in wanted[position]:
            room = rooms_by_id.get(room_id)
            if room is None:
                continue
            views, surface_ids = views_cache[room_id]
            if not views:
                continue
            for detection in detections:
                placed = project_detection(
                    detection, depth, k_depth, pose_world, rgb_size, views, surface_ids, floor_y
                )
                if placed is None:
                    continue
                surface_id, _, uv, world = placed
                projections.append((room_id, surface_id, uv, world, detection))

    regions = build_damage_regions(projections, book, tier, detector_name)
    return regions, (detector_name if regions else "none"), examined, (dark / examined if examined else None)
