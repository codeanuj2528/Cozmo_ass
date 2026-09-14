"""Free space and wall evidence as 2D rasters.

The depth image is reduced to a synthetic 2D range scan per keyframe: points are binned by
azimuth around the camera and the nearest return in each bin is kept. That turns a
1.2-million-point fusion problem into ordinary occupancy grid mapping, and it is what makes
free-space carving affordable. Without carving, an unobserved region and an open region
look identical on the map, and a floor plan built on that distinction alone will happily
close a wall across a doorway it never looked through.

Two height bands are maintained rather than one.

  * The traversable band, just above the floor, answers "could the camera see through
    here". It is the free-space evidence and it is where doorways show up.
  * The structural band, above typical furniture height, answers "is there a wall here".
    Sofas, beds and counters sit below it, so wall evidence is not dragged inward by a
    room's contents, which is the single largest bias in naive contour-based floor plans.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from cozmo.geometry.grid import Grid2D

AZIMUTH_BINS = 720
LOG_ODDS_FREE = -0.22
LOG_ODDS_OCCUPIED = 0.85
LOG_ODDS_CLAMP = 8.0
CEILING_EVIDENCE_MIN_HEIGHT_M = 1.95
CEILING_EVIDENCE_NORMAL = 0.90


@dataclass
class OccupancyMaps:
    grid: Grid2D
    free_log_odds: np.ndarray
    wall_weight: np.ndarray
    traversable_hits: np.ndarray
    camera_track: np.ndarray
    observed: np.ndarray
    floor_hits: np.ndarray
    structural_hits: np.ndarray
    ceiling_hits: np.ndarray | None = None
    # Channels the room outlines are corrected against after segmentation (geometry/refine.py).
    # Upward-facing returns below the floor plane: floor that drops away, as over a stairwell.
    drop_hits: np.ndarray | None = None
    deep_drop_hits: np.ndarray | None = None
    # Upward-facing returns above the floor: the tops of furniture, which stands inside a room.
    surface_hits: np.ndarray | None = None
    # Near-vertical returns at body height, whether or not they fit a wall plane.
    wall_point_hits: np.ndarray | None = None

    @property
    def free_mask(self) -> np.ndarray:
        return self.free_log_odds < -0.5

    @property
    def occupied_mask(self) -> np.ndarray:
        return self.free_log_odds > 0.5


def _march_rays(
    grid: Grid2D,
    origins_rc: np.ndarray,
    targets_rc: np.ndarray,
    stop_short_cells: float = 1.0,
) -> np.ndarray:
    """Flat indices of cells crossed by each ray, stopping short of the endpoint.

    Sampling along the ray at grid pitch rather than walking a Bresenham line lets the
    whole batch run as a single vectorised operation. Duplicate cells are harmless: the
    log-odds update is additive and the result is clamped.
    """
    delta = targets_rc - origins_rc
    lengths = np.linalg.norm(delta, axis=1)
    usable = lengths > (stop_short_cells + 1.0)
    if not usable.any():
        return np.zeros(0, dtype=np.int64)

    origins_rc = origins_rc[usable]
    delta = delta[usable]
    lengths = lengths[usable]
    steps = int(np.ceil(lengths.max())) + 1
    # Fraction of the way along each ray at which to stop, so the surface itself is not
    # carved away by the very ray that measured it.
    stop_fraction = np.clip(1.0 - stop_short_cells / lengths, 0.0, 1.0)
    t = np.linspace(0.0, 1.0, steps)[None, :] * stop_fraction[:, None]

    rr = origins_rc[:, 0:1] + delta[:, 0:1] * t
    cc = origins_rc[:, 1:2] + delta[:, 1:2] * t
    rr = np.round(rr).astype(np.int64).ravel()
    cc = np.round(cc).astype(np.int64).ravel()

    ok = (rr >= 0) & (rr < grid.shape[0]) & (cc >= 0) & (cc < grid.shape[1])
    return rr[ok] * grid.shape[1] + cc[ok]


def _azimuth_reduce(points_xz: np.ndarray, camera_xz: np.ndarray, bins: int) -> np.ndarray:
    """Nearest return per azimuth bin around the camera. Returns the selected points."""
    rel = points_xz - camera_xz
    ranges = np.linalg.norm(rel, axis=1)
    good = ranges > 1e-3
    if not good.any():
        return np.zeros((0, 2))
    rel, ranges, points_xz = rel[good], ranges[good], points_xz[good]

    angle = np.arctan2(rel[:, 1], rel[:, 0])
    idx = ((angle + np.pi) / (2 * np.pi) * bins).astype(np.int64) % bins
    order = np.lexsort((ranges, idx))
    idx_sorted = idx[order]
    first = np.ones(len(idx_sorted), dtype=bool)
    first[1:] = idx_sorted[1:] != idx_sorted[:-1]
    return points_xz[order][first]


def build_occupancy(
    cloud,
    floor_y: float,
    ceiling_y: float | None,
    resolution: float = 0.025,
    traversable_band: tuple[float, float] = (0.25, 1.15),
    structural_band: tuple[float, float] = (1.45, 2.10),
    camera_positions: np.ndarray | None = None,
    camera_frames: list[int] | None = None,
) -> OccupancyMaps:
    """Build free-space and wall-evidence rasters from a fused cloud.

    `camera_positions` is an (M, 3) array of keyframe camera centres in the same frame as
    the cloud. Free-space carving needs a ray origin, and the camera is the only honest one.
    `camera_frames` gives each camera's frame index, which lets a frame's own returns be
    looked up rather than searched for.
    """
    points_xz = cloud.points[:, [0, 2]].astype(np.float64)
    height = cloud.points[:, 1] - floor_y

    all_xz = points_xz
    if camera_positions is not None and len(camera_positions):
        all_xz = np.vstack([points_xz, camera_positions[:, [0, 2]]])
    grid = Grid2D.covering(all_xz, resolution)

    free_log_odds = grid.empty(np.float32)
    wall_weight = grid.empty(np.float32)
    traversable_hits = grid.empty(np.float32)
    floor_hits = np.zeros(grid.shape, dtype=bool)
    structural_hits = np.zeros(grid.shape, dtype=bool)
    observed = np.zeros(grid.shape, dtype=bool)

    # Cells where the floor itself was seen. This is the strongest interior evidence there
    # is: a floor return is proof that the cell is standable, with no inference in between.
    floor_seen = (np.abs(height) < 0.07) & (cloud.normals[:, 1] > 0.90)
    if floor_seen.any():
        cells = grid.to_cell(points_xz[floor_seen])
        keep = grid.inside(cells)
        floor_hits[cells[keep, 0], cells[keep, 1]] = True

    any_structure = height > 0.10
    if any_structure.any():
        cells = grid.to_cell(points_xz[any_structure])
        keep = grid.inside(cells)
        structural_hits[cells[keep, 0], cells[keep, 1]] = True

    # Cells with ceiling above them. Floor evidence is the direct proof that a cell is
    # standable, but it is exactly the evidence furniture destroys: the floor under a
    # wardrobe or a bed is never seen, camera rays stop at the bed top, and nobody walks
    # into the strip between a wardrobe front and the wall. The ceiling above that strip is
    # hidden by nothing. On the benchmark flat the ceiling observed above the bedroom
    # covers 9.10 m2 against a taped 9.29 m2, while floor evidence alone produced a 5.28 m2
    # room bounded at the wardrobe front.
    #
    # Downward-facing surfaces above head height are ceilings, soffits, door heads and the
    # undersides of wall-mounted units -- all of them inside a room. A table or shelf
    # underside is below the cut and does not count.
    ceiling_hits = np.zeros(grid.shape, dtype=bool)
    ceiling_seen = (cloud.normals[:, 1] < -CEILING_EVIDENCE_NORMAL) & (
        height > CEILING_EVIDENCE_MIN_HEIGHT_M
    )
    if ceiling_y is not None:
        ceiling_seen &= height < (ceiling_y - floor_y) + 0.15
    if ceiling_seen.any():
        cells = grid.to_cell(points_xz[ceiling_seen])
        keep = grid.inside(cells)
        ceiling_hits[cells[keep, 0], cells[keep, 1]] = True

    # Wall evidence: vertical surfaces in the structural band, weighted by precision.
    top = structural_band[1]
    if ceiling_y is not None:
        top = min(top, ceiling_y - floor_y - 0.12)
    structural = (height >= structural_band[0]) & (height <= max(top, structural_band[0] + 0.1))
    vertical = cloud.verticality > 0.88
    wall_points = structural & vertical
    if wall_points.any():
        cells = grid.to_cell(points_xz[wall_points])
        keep = grid.inside(cells)
        np.add.at(
            wall_weight,
            (cells[keep, 0], cells[keep, 1]),
            cloud.weight[wall_points][keep].astype(np.float32),
        )

    traversable = (height >= traversable_band[0]) & (height <= traversable_band[1])
    if traversable.any():
        cells = grid.to_cell(points_xz[traversable])
        keep = grid.inside(cells)
        np.add.at(traversable_hits, (cells[keep, 0], cells[keep, 1]), 1.0)

    track_cells: list[np.ndarray] = []
    if camera_positions is not None and len(camera_positions):
        # Each point already records the frame that first observed it, so a frame's scan is
        # a lookup rather than a radius search. Selecting a camera's points by distance
        # instead costs one pass over the whole cloud per camera, which on a 2.6 M point
        # capture with 600 keyframes is 1.6 billion distance evaluations and dominated the
        # entire pipeline at 53 s. Grouping by frame does the same work in one pass.
        order = np.argsort(cloud.frame_index, kind="stable")
        sorted_ids = cloud.frame_index[order]
        unique_ids, starts = np.unique(sorted_ids, return_index=True)
        bounds = np.append(starts, len(sorted_ids))
        by_frame = {
            int(fid): order[bounds[i] : bounds[i + 1]] for i, fid in enumerate(unique_ids)
        }

        frame_ids = camera_frames if camera_frames is not None else [None] * len(camera_positions)
        flat_free: list[np.ndarray] = []
        for cam, fid in zip(camera_positions, frame_ids):
            cam_xz = cam[[0, 2]]
            cam_cell = grid.to_cell_float(cam_xz[None, :])[0]
            track_cells.append(cam_cell)

            if fid is not None and int(fid) in by_frame:
                sel = by_frame[int(fid)]
                sel = sel[traversable[sel]]
            else:
                nearby = np.linalg.norm(points_xz - cam_xz, axis=1) < 6.0
                sel = np.flatnonzero(nearby & traversable)
            if len(sel) < 8:
                continue
            scan = _azimuth_reduce(points_xz[sel], cam_xz, AZIMUTH_BINS)
            if len(scan) == 0:
                continue
            target_cells = grid.to_cell_float(scan)
            origins = np.repeat(cam_cell[None, :], len(target_cells), axis=0)
            flat_free.append(_march_rays(grid, origins, target_cells))
        if flat_free:
            flat = np.concatenate(flat_free)
            counts = np.bincount(flat, minlength=grid.shape[0] * grid.shape[1]).astype(np.float32)
            free_log_odds += (counts * LOG_ODDS_FREE).reshape(grid.shape)
            observed |= counts.reshape(grid.shape) > 0

    hit_cells = grid.to_cell(points_xz[traversable]) if traversable.any() else np.zeros((0, 2), dtype=np.int64)
    if len(hit_cells):
        keep = grid.inside(hit_cells)
        np.add.at(free_log_odds, (hit_cells[keep, 0], hit_cells[keep, 1]), LOG_ODDS_OCCUPIED)
        observed[hit_cells[keep, 0], hit_cells[keep, 1]] = True

    np.clip(free_log_odds, -LOG_ODDS_CLAMP, LOG_ODDS_CLAMP, out=free_log_odds)

    # Evidence the room outlines are corrected against once rooms exist (geometry/refine.py). Each
    # is a count of returns per cell, so a single stray return cannot mark a cell.
    upward = cloud.normals[:, 1] > 0.85
    min_returns = 2

    def _returns(mask: np.ndarray) -> np.ndarray:
        counts = np.zeros(grid.shape, dtype=np.int32)
        if mask.any():
            cells = grid.to_cell(points_xz[mask])
            keep = grid.inside(cells)
            np.add.at(counts, (cells[keep, 0], cells[keep, 1]), 1)
        return counts >= min_returns

    # Below the floor by more than a tiled step or a shower tray, and by more than any of those.
    drop_hits = _returns(upward & (height < -0.10))
    deep_drop_hits = _returns(upward & (height < -0.30))
    surface_hits = _returns(upward & (height > 0.07) & (height < 2.10))
    wall_point_hits = _returns((np.abs(cloud.normals[:, 1]) < 0.30) & (height > 0.30) & (height < 2.00))

    return OccupancyMaps(
        grid=grid,
        free_log_odds=free_log_odds,
        wall_weight=wall_weight,
        traversable_hits=traversable_hits,
        camera_track=np.array(track_cells) if track_cells else np.zeros((0, 2)),
        observed=observed | floor_hits | structural_hits,
        floor_hits=floor_hits,
        structural_hits=structural_hits,
        ceiling_hits=ceiling_hits,
        drop_hits=drop_hits,
        deep_drop_hits=deep_drop_hits,
        surface_hits=surface_hits,
        wall_point_hits=wall_point_hits,
    )
