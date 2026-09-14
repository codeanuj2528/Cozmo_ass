"""Doors, windows and pass-throughs, found by unrolling each wall into an elevation.

Each wall plane is rasterised into a (distance along wall, height above floor) image
carrying two channels: where material was measured, and where the sensor saw straight
through the plane to something behind it. An opening is where the first is absent and the
second is present. Absence of material on its own is not enough -- a wall behind a wardrobe
also has no returns -- which is why occlusion and transparency are separated rather than
both read as holes.

Mirrors are handled explicitly. A mirror returns depth at the distance of the reflected
scene, so the surface itself measures as empty and the reflection measures as structure
behind the wall. To a see-through test that is indistinguishable from a window, and an
untested pipeline reports a phantom opening on every mirror in the property. The test used
here is geometric: reflect the suspect points back across the wall plane and ask whether
they land on the room that is actually in front of it. A reflection does; a courtyard does
not. That single check is also what keeps bathroom and wardrobe walls from being scored as
missed openings, and detection is scored with phantoms counted as misses.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import ndimage
from scipy.spatial import cKDTree

from cozmo.geometry.fusion import FusedCloud
from cozmo.geometry.walls import WallSegment
from cozmo.schema import OpeningType
from cozmo.util.raster import remove_small_blobs

ELEVATION_RESOLUTION_M = 0.02
MATERIAL_BAND_M = 0.055
BEYOND_BAND_M = 0.12
MIRROR_MATCH_RADIUS_M = 0.08
MIRROR_FRACTION_THRESHOLD = 0.35

DOOR_MIN_WIDTH_M = 0.55
DOOR_MAX_WIDTH_M = 1.45
MAX_OPENING_WIDTH_M = 2.80
DOOR_MIN_HEIGHT_M = 1.55
DOOR_MAX_SILL_M = 0.22
WINDOW_MIN_SILL_M = 0.25
MIN_OPENING_AREA_M2 = 0.25
SIDE_SUPPORT_THRESHOLD = 0.30
MIN_OCCLUDER_STANDOFF_M = 0.35
OCCLUSION_RATIO = 1.5


@dataclass
class Elevation:
    """A wall unrolled onto a raster: u along the wall, v above the floor."""

    wall: WallSegment
    material: np.ndarray
    beyond: np.ndarray
    occluded: np.ndarray
    resolution: float
    u_origin: float
    v_origin: float

    def to_uv(self, cells_rc: np.ndarray) -> np.ndarray:
        return np.stack(
            [
                cells_rc[:, 1] * self.resolution + self.u_origin,
                cells_rc[:, 0] * self.resolution + self.v_origin,
            ],
            axis=1,
        )


@dataclass
class DetectedOpening:
    wall: WallSegment
    opening_type: OpeningType
    u_min: float
    u_max: float
    v_min: float
    v_max: float
    width: float
    height: float
    sill: float
    confidence: float
    beyond_fraction: float
    mirror_fraction: float
    evidence_frames: list[int]

    @property
    def centre_world(self) -> np.ndarray:
        u = 0.5 * (self.u_min + self.u_max)
        return self.wall.start + self.wall.direction * u


def build_elevation(
    wall: WallSegment,
    cloud: FusedCloud,
    floor_y: float,
    ceiling_y: float | None,
    resolution: float = ELEVATION_RESOLUTION_M,
) -> Elevation:
    """Rasterise one wall into material and see-through channels."""
    normal3 = np.array([wall.normal_xz[0], 0.0, wall.normal_xz[1]])
    dir3 = np.array([wall.direction[0], 0.0, wall.direction[1]])
    origin = np.array([wall.start[0], floor_y, wall.start[1]])

    rel = cloud.points - origin
    u = rel @ dir3
    v = cloud.points[:, 1] - floor_y
    signed = rel @ normal3

    top = (ceiling_y - floor_y) if ceiling_y is not None else float(np.percentile(v, 99.5))
    top = max(top, 1.0)

    in_span = (u > -0.10) & (u < wall.length + 0.10) & (v > -0.05) & (v < top + 0.05)
    if not in_span.any():
        shape = (1, 1)
        zero = np.zeros(shape, dtype=np.float32)
        return Elevation(wall, zero, zero.copy(), zero.copy(), resolution, 0.0, 0.0)

    n_u = max(int(np.ceil((wall.length + 0.20) / resolution)), 2)
    n_v = max(int(np.ceil((top + 0.10) / resolution)), 2)
    material = np.zeros((n_v, n_u), dtype=np.float32)
    beyond = np.zeros((n_v, n_u), dtype=np.float32)
    occluded = np.zeros((n_v, n_u), dtype=np.float32)

    cols = np.clip(((u + 0.10) / resolution).astype(int), 0, n_u - 1)
    rows = np.clip(((v + 0.05) / resolution).astype(int), 0, n_v - 1)

    on_plane = in_span & (np.abs(signed) < MATERIAL_BAND_M)
    np.add.at(material, (rows[on_plane], cols[on_plane]), cloud.weight[on_plane])

    # Points on the far side of the plane. Where a ray from the camera reached them, it
    # must have passed through the plane, so the crossing point is a hole in the wall.
    far = in_span & (signed < -BEYOND_BAND_M)
    if far.any():
        cams = cloud.cameras[far]
        pts = cloud.points[far]
        cam_side = (cams - origin) @ normal3
        crossing_ok = cam_side > BEYOND_BAND_M
        if crossing_ok.any():
            cams, pts = cams[crossing_ok], pts[crossing_ok]
            d_cam = (cams - origin) @ normal3
            d_pt = (pts - origin) @ normal3
            t = d_cam / np.maximum(d_cam - d_pt, 1e-6)
            hit = cams + (pts - cams) * t[:, None]
            hu = (hit - origin) @ dir3
            hv = hit[:, 1] - floor_y
            good = (hu > -0.10) & (hu < wall.length + 0.10) & (hv > -0.05) & (hv < top + 0.05)
            if good.any():
                hc = np.clip(((hu[good] + 0.10) / resolution).astype(int), 0, n_u - 1)
                hr = np.clip(((hv[good] + 0.05) / resolution).astype(int), 0, n_v - 1)
                np.add.at(beyond, (hr, hc), 1.0)

    # Occlusion is a statement about line of sight, not about proximity. A point occludes
    # the wall cell the ray through it would have reached had the point not stopped it, so
    # the cell is found by extending the camera ray past the point onto the plane -- the
    # same intersection as the see-through channel, extrapolated rather than interpolated.
    #
    # Marking instead every cell that has some point in front of it is wrong in a way that
    # costs real detections: the floor in front of a doorway sits in front of the door
    # plane, and projecting it straight back onto the plane blanks out the bottom of every
    # doorway in the property. Traced as a ray from a camera at standing height, that same
    # floor point extends to below the floor line and occludes nothing.
    near = in_span & (signed > MATERIAL_BAND_M) & (signed < 2.0)
    if near.any():
        cams = cloud.cameras[near]
        pts = cloud.points[near]
        d_cam = (cams - origin) @ normal3
        d_pt = (pts - origin) @ normal3
        # The camera must be in front of the plane and further from it than the point is,
        # or the point is not between the sensor and the wall at all.
        # Guard the extrapolation. A camera almost touching the plane, which happens on
        # every frame taken while walking through the doorway itself, divides by a tiny
        # gap and throws the hit point far down the wall, occluding cells nothing was ever
        # in front of.
        usable = (d_cam > MIN_OCCLUDER_STANDOFF_M) & (d_pt > 0) & (d_pt < 0.75 * d_cam)
        if usable.any():
            cams, pts = cams[usable], pts[usable]
            d_cam, d_pt = d_cam[usable], d_pt[usable]
            t = d_cam / np.maximum(d_cam - d_pt, 1e-6)
            hit = cams + (pts - cams) * t[:, None]
            hu = (hit - origin) @ dir3
            hv = hit[:, 1] - floor_y
            good = (hu > -0.10) & (hu < wall.length + 0.10) & (hv > -0.05) & (hv < top + 0.05)
            if good.any():
                hc = np.clip(((hu[good] + 0.10) / resolution).astype(int), 0, n_u - 1)
                hr = np.clip(((hv[good] + 0.05) / resolution).astype(int), 0, n_v - 1)
                np.add.at(occluded, (hr, hc), 1.0)

    return Elevation(wall, material, beyond, occluded, resolution, -0.10, -0.05)


def _mirror_fraction(
    wall: WallSegment,
    cloud: FusedCloud,
    tree: cKDTree,
    u_range: tuple[float, float],
    v_range: tuple[float, float],
    floor_y: float,
) -> float:
    """Share of the see-through points that are reflections of the room in front.

    Reflecting a point across the wall plane and finding the real scene where it lands is
    the signature of a mirror. A window onto a courtyard reflects onto empty space.
    """
    normal3 = np.array([wall.normal_xz[0], 0.0, wall.normal_xz[1]])
    dir3 = np.array([wall.direction[0], 0.0, wall.direction[1]])
    origin = np.array([wall.start[0], floor_y, wall.start[1]])

    rel = cloud.points - origin
    u = rel @ dir3
    v = cloud.points[:, 1] - floor_y
    signed = rel @ normal3
    behind = (
        (signed < -BEYOND_BAND_M)
        & (u > u_range[0]) & (u < u_range[1])
        & (v > v_range[0]) & (v < v_range[1])
    )
    if behind.sum() < 25:
        return 0.0

    pts = cloud.points[behind]
    distance = (pts - origin) @ normal3
    reflected = pts - 2.0 * distance[:, None] * normal3[None, :]
    found = tree.query_ball_point(reflected, r=MIRROR_MATCH_RADIUS_M, return_length=True)
    return float((np.asarray(found) > 0).mean())


def detect_openings(
    wall: WallSegment,
    walls: list[WallSegment],
    cloud: FusedCloud,
    tree: cKDTree,
    floor_y: float,
    ceiling_y: float | None,
) -> tuple[list[DetectedOpening], Elevation]:
    """Find and classify the openings in one wall."""
    elevation = build_elevation(wall, cloud, floor_y, ceiling_y)
    material, beyond, occluded = elevation.material, elevation.beyond, elevation.occluded
    if material.size < 16:
        return [], elevation

    res = elevation.resolution
    has_material = material > 0
    # Dilate material slightly: a 2 cm raster of a real wall has pinholes wherever the
    # sensor happened not to return, and undilated they fragment every opening boundary.
    solid = ndimage.binary_dilation(has_material, np.ones((3, 3)), iterations=1)
    see_through = beyond > 0
    see_through = ndimage.binary_closing(see_through, np.ones((3, 3)), iterations=2)

    # See-through evidence has to outweigh occlusion evidence, rather than occlusion
    # having to be absent. A hard zero lets a single stray ray past a chair leg veto a
    # doorway that a thousand rays looked straight through.
    candidate = see_through & ~solid & (beyond > OCCLUSION_RATIO * occluded)
    candidate = remove_small_blobs(candidate, min_cells=int(MIN_OPENING_AREA_M2 / (res * res)))
    if not candidate.any():
        return [], elevation

    labels, n = ndimage.label(candidate, structure=np.ones((3, 3)))
    openings: list[DetectedOpening] = []
    for label in range(1, n + 1):
        mask = labels == label
        rows, cols = np.nonzero(mask)
        u_min = cols.min() * res + elevation.u_origin
        u_max = (cols.max() + 1) * res + elevation.u_origin
        v_min = rows.min() * res + elevation.v_origin
        v_max = (rows.max() + 1) * res + elevation.v_origin
        width = u_max - u_min
        height = v_max - v_min
        if width < 0.30 or height < 0.30 or width > MAX_OPENING_WIDTH_M:
            continue

        # Fill ratio guards against an L-shaped union of two unrelated gaps being reported
        # as one large rectangular opening.
        fill = mask.sum() * res * res / max(width * height, 1e-6)
        if fill < 0.45:
            continue

        # An opening is a hole in a wall, so each side of it must be closed by something:
        # either measured wall, or a corner where a perpendicular wall meets this one.
        # Bridging a wall run across its doorway also bridges it across the mouth of a
        # corridor, and this is what tells the two apart.
        left_ok = _side_support(
            has_material, rows.min(), rows.max(), cols.min(), cols.max(), res, "left"
        ) >= SIDE_SUPPORT_THRESHOLD or _corner_at(wall, u_min, walls)
        right_ok = _side_support(
            has_material, rows.min(), rows.max(), cols.min(), cols.max(), res, "right"
        ) >= SIDE_SUPPORT_THRESHOLD or _corner_at(wall, u_max, walls)
        if not (left_ok and right_ok):
            continue

        mirror = _mirror_fraction(wall, cloud, tree, (u_min, u_max), (v_min, v_max), floor_y)
        if mirror > MIRROR_FRACTION_THRESHOLD:
            continue

        beyond_fraction = float((beyond[mask] > 0).mean())
        if beyond_fraction < 0.25:
            continue

        opening_type = _classify(width, height, v_min, ceiling_y, floor_y)
        if opening_type is None:
            continue

        # Confidence blends how transparent the region read, how cleanly it filled its
        # own bounding box, and how far it sits from a mirror verdict.
        confidence = float(
            np.clip(0.45 * beyond_fraction + 0.35 * fill + 0.20 * (1.0 - mirror / MIRROR_FRACTION_THRESHOLD), 0.05, 0.99)
        )
        openings.append(
            DetectedOpening(
                wall=wall,
                opening_type=opening_type,
                u_min=u_min, u_max=u_max, v_min=v_min, v_max=v_max,
                width=width, height=height, sill=v_min,
                confidence=confidence,
                beyond_fraction=beyond_fraction,
                mirror_fraction=mirror,
                evidence_frames=[],
            )
        )
    return openings, elevation


def _side_support(
    has_material: np.ndarray,
    row_min: int,
    row_max: int,
    col_min: int,
    col_max: int,
    resolution: float,
    side: str,
    reach_m: float = 0.45,
) -> float:
    """How much of an opening's own height has measured wall beside it.

    Restricted to the opening's rows and asking only whether material exists anywhere
    within reach. Averaging over the full elevation instead counts the rows above whatever
    height the operator stopped scanning at, so a perfectly solid jamb on a wall scanned to
    2.2 m of a 3.1 m room scores barely half and fails any useful threshold.
    """
    reach = max(int(round(reach_m / resolution)), 2)
    if side == "left":
        block = has_material[row_min : row_max + 1, max(col_min - reach, 0) : col_min]
    else:
        block = has_material[row_min : row_max + 1, col_max + 1 : col_max + 1 + reach]
    if block.size == 0:
        return 0.0
    return float(block.any(axis=1).mean())


def _corner_at(wall: WallSegment, u: float, walls: list[WallSegment], tolerance_m: float = 0.40) -> bool:
    """True when another wall meets this one at distance `u` along it.

    A doorway in the middle of a wall has a jamb either side. A doorway at the end of a
    wall has one jamb and a corner, and the wall that forms the corner is perpendicular so
    it lives in its own elevation and cannot appear in this one. Without this test every
    door next to a room corner is discarded, and the brief scores a missed opening exactly
    as hard as a phantom one.
    """
    world = wall.start + wall.direction * u
    for other in walls:
        if other is wall:
            continue
        if abs(float(other.direction @ wall.direction)) > np.cos(np.deg2rad(25.0)):
            continue
        perpendicular = abs(float((world - other.start) @ other.normal_xz))
        along = float((world - other.start) @ other.direction)
        if perpendicular < tolerance_m and -0.40 <= along <= other.length + 0.40:
            return True
    return False


def _classify(
    width: float, height: float, sill: float, ceiling_y: float | None, floor_y: float
) -> OpeningType | None:
    room_height = (ceiling_y - floor_y) if ceiling_y is not None else 2.6
    if sill <= DOOR_MAX_SILL_M:
        # A gap that reaches the floor but is narrower than the narrowest door is not a way
        # through: it is the space between a wall end and a column, or between a wardrobe and
        # a wall. The pass-through branch below tests only height, and it put a 0.38 m
        # pass-through into the plan of the assignment's with-ceiling scan.
        if width < DOOR_MIN_WIDTH_M:
            return None
        if height >= DOOR_MIN_HEIGHT_M and width <= DOOR_MAX_WIDTH_M:
            return OpeningType.DOOR
        if width > DOOR_MAX_WIDTH_M or height >= min(DOOR_MIN_HEIGHT_M, room_height * 0.6):
            return OpeningType.PASS_THROUGH
        return None
    if sill >= WINDOW_MIN_SILL_M and width >= 0.35 and height >= 0.35:
        return OpeningType.WINDOW
    return None


def detect_all_openings(
    walls: list[WallSegment], cloud: FusedCloud, floor_y: float, ceiling_y: float | None
) -> dict[int, list[DetectedOpening]]:
    """Openings per wall index, sharing one spatial index across all walls."""
    if len(cloud) == 0:
        return {}
    tree = cKDTree(cloud.points)
    out: dict[int, list[DetectedOpening]] = {}
    for i, wall in enumerate(walls):
        found, _ = detect_openings(wall, walls, cloud, tree, floor_y, ceiling_y)
        if found:
            out[i] = found
    return out
