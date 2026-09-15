"""LiDAR tier reconstruction — the reference implementation.

Every other tier is measured against this one, which is why it gets the most
careful treatment.  The pass order matters in two places:

Walls are extracted twice.  The first pass exists only to find the property's
own axes; the cloud is then rotated onto them and walls are re-extracted.
A raster aligned to the walls quantises them along their own direction instead
of across it, and on the sample capture the property sits 27 degrees off the
ARKit axes where an unaligned grid spends its whole cell size staircasing walls.

Levels are measured twice for the same reason at a different scale: once
globally to get a floor reference, then per room for per-room ceiling gates.
"""

from __future__ import annotations

import hashlib
import os
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import numpy as np

from cozmo import __version__
from cozmo.config import PipelineConfig
from cozmo.geometry.assemble import (
    DEFAULT_POSE_SIGMA_M,
    adjacency_from_trajectory,
    RoomGeometry,
    build_room,
    unmet_adjacency_warnings,
    match_adjacency,
    room_levels,
    total_area,
)
from cozmo.geometry.cellcomplex import (
    CellComplex,
    build_cell_complex,
    room_masks,
    room_polygons,
)
from cozmo.geometry.drift import PoseOverride, correct_drift
from cozmo.geometry.fusion import FusedCloud, fuse, select_keyframes
from cozmo.geometry.levels import LevelEstimate, detect_levels, refine_gravity
from cozmo.geometry.occupancy import OccupancyMaps, build_occupancy
from cozmo.geometry.openings import detect_all_openings
from cozmo.geometry.refine import polygon_mask, refine_rooms
from cozmo.geometry.walls import (
    WallCandidate,
    WallSegment,
    canonical_rotation,
    dominant_directions,
    extract_wall_segments,
    merge_runs,
    snap_to_frame,
)
from cozmo.io.base import CaptureSource
from cozmo.pipeline.common import PipelineArtifacts, PipelineResult
from cozmo.schema import (
    CalibrationReport,
    DamageRegion,
    DriftReport,
    IntervalMethod,
    PropertyPlan,
    QualityReport,
    Room,
    Tier,
)
from cozmo.uncertainty.calibration import IntervalBook

log = logging.getLogger("cozmo.pipeline.lidar")

PLAN_FILENAME = "plan.json"
PLAN_PNG_FILENAME = "plan.png"
PLAN_SVG_FILENAME = "plan.svg"
MANIFEST_FILENAME = "run_manifest.json"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _resync_candidates(
    candidates: list[WallCandidate], walls: list[WallSegment]
) -> list[WallCandidate]:
    """Rebuild the line set the cell complex uses from possibly snapped walls.

    One line per distinct (direction, offset), because several runs can share a
    plane and the arrangement wants each line once.
    """
    seen: dict[tuple[int, int], WallCandidate] = {}
    for wall in walls:
        azimuth = int(
            round(
                np.degrees(np.arctan2(wall.normal_xz[1], wall.normal_xz[0])) / 2.0
            )
        )
        offset = float(wall.normal_xz @ wall.start)
        key = (azimuth, int(round(offset / 0.05)))
        existing = seen.get(key)
        if existing is None or wall.support_weight > existing.weight:
            seen[key] = WallCandidate(
                plane=wall.plane,
                normal_xz=wall.normal_xz,
                offset=offset,
                weight=wall.support_weight,
                indices=wall.point_indices,
                segments=[wall],
            )
    merged = list(seen.values())
    merged.sort(key=lambda c: c.weight, reverse=True)
    return merged or candidates


def _frame_indices(source: CaptureSource, keyframes: list[int]) -> list[int]:
    """Map keyframe indices to source frame numbers.

    A keyframe is an index into the pose array.  Occupancy carving looks points
    up by frame number, so conflating the two silently pairs a camera with
    another frame's depth returns.
    """
    if hasattr(source, "frame_indices"):
        table = source.frame_indices()
        return [int(table[i]) for i in keyframes]
    return [int(i) for i in keyframes]


def _gather_poses(source: CaptureSource) -> np.ndarray:
    """Collect all 4×4 camera-to-world poses from the capture source."""
    if hasattr(source, "poses"):
        return source.poses()
    poses = [f.pose for f in source.frames() if f.pose is not None]
    return np.stack(poses) if poses else np.zeros((0, 4, 4))


def _quality_report(
    source: CaptureSource,
    cloud: FusedCloud,
    keyframes: list[int],
    rooms: list[Room],
    occupancy: OccupancyMaps,
    tier: Tier,
    warnings: list[str],
) -> QualityReport:
    """Produce the per-run quality summary embedded in every plan."""
    total_polygon_area = sum(r.floor_area.value for r in rooms)
    observed_floor = float(occupancy.floor_hits.sum() * occupancy.grid.cell_area)
    coverage = float(
        np.clip(observed_floor / max(total_polygon_area, 1e-6), 0.0, 1.0)
    )

    median_confidence = None
    if tier is Tier.LIDAR:
        median_confidence = float(np.median(cloud.sigma))

    return QualityReport(
        tier=tier,
        device_model=source.meta.device_model,
        frames_available=source.meta.frame_count,
        frames_used=len(keyframes),
        median_depth_confidence=median_confidence,
        surface_coverage=coverage,
        warnings=list(warnings),
    )


# Whole-property floor and ceiling levels bound the height bands for wall voting (ceiling less
# 6 cm) and occupancy (ceiling less 12 cm). They are read where the capture started, at the
# world origin, not over the centre of the floor as room measurements are. On the long walk of
# the benchmark flat that choice moves the property ceiling by 2.8 cm, well inside both
# margins, and still moves the footprint from 25.27 to 24.51 m2 and re-segments the bathroom
# and the window bay. Segmentation that sensitive to a band edge is a defect of its own, and
# changing the reference without tape to judge the outcome would swap one unverified plan for
# another; it is recorded in known_failure_modes.md instead.
PROPERTY_LEVEL_REFERENCE_XZ = np.zeros(2)


def _hash_input(input_dir: Path) -> str:
    """Deterministic hash of the capture directory for provenance: file names and sizes.

    Symlinked folders are followed. `Path.rglob` does not follow them, so a photo set linked
    into a run directory hashed as the empty string and the manifest identified no input.
    """
    h = hashlib.sha256()
    files: list[Path] = []
    seen: set[str] = set()
    for root, dirs, names in os.walk(input_dir, followlinks=True):
        real = os.path.realpath(root)
        if real in seen:
            dirs[:] = []
            continue
        seen.add(real)
        files.extend(Path(root) / name for name in names)
    for p in sorted(files):
        if p.is_file() and p.stat().st_size < 50_000_000:
            h.update(p.name.encode())
            h.update(str(p.stat().st_size).encode())
    return h.hexdigest()[:16]


def _git_commit() -> str:
    """Current git short hash, "-dirty" appended when the pipeline source differs from it.

    Without the suffix, a plan regenerated from uncommitted code carries the hash of a commit
    that cannot reproduce it.
    """
    try:
        import subprocess

        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        commit = result.stdout.strip()
        if not commit:
            return "unknown"
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no", "--", str(Path(__file__).resolve().parents[1])],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return f"{commit}-dirty" if status.stdout.strip() else commit
    except Exception:
        return "unknown"


# ---------------------------------------------------------------------------
# Run manifest
# ---------------------------------------------------------------------------


def build_run_manifest(
    source: CaptureSource,
    result: PipelineResult,
    input_path: Optional[Path] = None,
) -> dict[str, Any]:
    """Assemble the run manifest that accompanies every plan output."""
    plan = result.plan
    artifacts = result.artifacts
    manifest: dict[str, Any] = {
        "pipeline_version": plan.pipeline_version,
        "schema_version": "1.0",
        "capture_id": plan.capture_id,
        "tier": plan.tier.value,
        "generated_at": plan.created_at.isoformat(),
        "git_commit": _git_commit(),
        "runtime_seconds": round(plan.runtime_seconds, 3),
        "timings": {k: round(v, 4) for k, v in artifacts.timings.items()},
        "keyframes_used": len(artifacts.keyframes),
        "frames_available": source.meta.frame_count,
        "points_fused": len(artifacts.cloud) if artifacts.cloud is not None else 0,
        "rooms_found": len(plan.rooms),
        "total_floor_area_m2": round(plan.total_floor_area.value, 3),
        "warnings": artifacts.warnings,
    }
    if input_path is not None:
        manifest["input_hash"] = _hash_input(input_path)
    return manifest


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def build_lidar_plan(
    source: CaptureSource,
    config: PipelineConfig | None = None,
    book: IntervalBook | None = None,
) -> PipelineResult:
    """Run the full LiDAR-tier reconstruction for one capture.

    This is the canonical entry point for the pipeline.  Photo and video tiers
    call into their own builders, which share geometric stages but differ in how
    depth and pose are obtained.
    """
    if isinstance(source, Path):
        from cozmo.io import load_capture
        source = load_capture(source)

    config = config or PipelineConfig()
    book = book or IntervalBook.load(config.calibration_path)
    started = time.perf_counter()
    timings: dict[str, float] = {}
    warnings: list[str] = []

    poses = _gather_poses(source)
    if len(poses) == 0:
        raise ValueError("capture has no poses; a tier adapter must supply them")

    mark = time.perf_counter()
    keyframes = select_keyframes(
        poses,
        translation_m=config.keyframe_translation_m,
        rotation_rad=np.deg2rad(config.keyframe_rotation_deg),
        max_frames=config.max_keyframes,
    )

    # Drift correction runs *before* fusion.  Correcting a fused cloud in place
    # would apply one frame's delta to contributions of every frame that shared
    # its voxel.
    drift = DriftReport(
        method="not applied: drift correction disabled by configuration",
        loop_closures_found=0,
        residual_before_m=0.0,
        residual_after_m=0.0,
        max_pose_correction_m=0.0,
        footprint_area_before_m2=None,
        footprint_area_after_m2=0.0,
        applied=False,
    )
    keyframe_poses = poses[keyframes]
    if config.drift_correction:
        solution = correct_drift(source, keyframes, poses, seed=config.seed)
        drift = solution.report
        if solution.report.applied:
            source = PoseOverride(source, keyframes, solution.poses)
            keyframe_poses = solution.poses
    timings["drift_s"] = time.perf_counter() - mark

    mark = time.perf_counter()
    cloud = fuse(
        source,
        keyframes,
        voxel_m=config.voxel_m,
        min_confidence=config.min_depth_confidence,
        max_range_m=config.max_depth_range_m,
    )
    timings["fuse_s"] = time.perf_counter() - mark
    if len(cloud) == 0:
        raise ValueError("fusion produced no points; check depth availability")

    mark = time.perf_counter()
    gravity_rotation, _, gravity_warnings = refine_gravity(cloud)
    warnings.extend(gravity_warnings)
    cloud = cloud.rotated(gravity_rotation)
    cameras = keyframe_poses[:, :3, 3] @ gravity_rotation.T
    world_rotation = gravity_rotation
    timings["gravity_s"] = time.perf_counter() - mark

    mark = time.perf_counter()
    levels = detect_levels(cloud, reference_xz=PROPERTY_LEVEL_REFERENCE_XZ)
    warnings.extend(levels.warnings)
    walls, _ = extract_wall_segments(
        cloud, levels.floor_height, levels.ceiling_height
    )

    if config.canonical_rotation and walls:
        canonical = canonical_rotation(walls)
        cloud = cloud.rotated(canonical)
        cameras = cameras @ canonical.T
        world_rotation = canonical @ world_rotation
        levels = detect_levels(cloud, reference_xz=PROPERTY_LEVEL_REFERENCE_XZ)
        walls, candidates = extract_wall_segments(
            cloud, levels.floor_height, levels.ceiling_height
        )
    else:
        _, candidates = extract_wall_segments(
            cloud, levels.floor_height, levels.ceiling_height
        )

    # Openings are detected against the walls as measured, before any snapping. Snapping
    # rotates a wall by up to the snap tolerance, and six degrees moves the far end of a
    # 4 m wall by 42 cm -- far outside the 5.5 cm band the elevation uses to decide which
    # points lie on the wall. Detecting on snapped geometry silently loses openings: on
    # the first real capture it took the count from 12 down to 3.
    walls_as_measured = list(walls)
    snapped_count, snap_rotation = 0, 0.0
    if config.snap_walls_to_frame and walls:
        frame_angle = dominant_directions(walls)
        walls, snapped_count, snap_rotation = snap_to_frame(
            walls, cloud, frame_angle, tolerance_rad=np.deg2rad(config.snap_tolerance_deg)
        )
        for candidate in candidates:
            candidate.segments = [
                s for s in walls if s.plane is candidate.plane
            ]
        candidates = _resync_candidates(candidates, walls)
    if snapped_count:
        warnings.append(
            f"snapped {snapped_count} wall runs onto the building frame, "
            f"mean rotation {np.degrees(snap_rotation):.2f} deg"
        )
    timings["walls_s"] = time.perf_counter() - mark

    mark = time.perf_counter()
    occupancy = build_occupancy(
        cloud,
        levels.floor_height,
        levels.ceiling_height,
        resolution=config.grid_resolution_m,
        camera_positions=cameras,
        camera_frames=_frame_indices(source, keyframes),
    )
    timings["occupancy_s"] = time.perf_counter() - mark

    mark = time.perf_counter()
    complex_ = build_cell_complex(
        occupancy, candidates, walls, max_lines=config.max_wall_lines
    )
    polygons = room_polygons(
        complex_,
        min_room_area_m2=config.min_room_area_m2,
        min_inscribed_radius_m=config.min_inscribed_radius_m,
    )
    masks = room_masks(complex_)
    refinements: dict[int, list[str]] = {}
    # The corrections read the scan's returns as evidence that floor is absent: returns below the
    # floor, no wall on either side, nothing seen. LiDAR measures those. A monocular depth map does
    # not: its returns below the floor are scale and depth error and its walls are partial, so on
    # the photo and video tiers, which reach this function too, the same rules removed floor that
    # is there. The 1x hall photos went from 35.12 to 2.11 m2 and the 0.5x set gained a room
    # overlap. They run on the LiDAR tier only.
    if config.refine_rooms and source.meta.tier is Tier.LIDAR:
        unrefined_area = {key: polygon.area for key, polygon in polygons.items()}
        polygons, refinements = refine_rooms(
            polygons,
            occupancy,
            walls,
            min_room_area_m2=config.min_room_area_m2,
            min_inscribed_radius_m=config.min_inscribed_radius_m,
        )
        # A room's mask selects the points its floor, ceiling and damage are measured from, so it
        # must lose the floor its outline lost.
        masks = {
            key: mask & polygon_mask(polygons[key], occupancy.grid)
            for key, mask in masks.items()
            if key in polygons
        }
        for key, notes in refinements.items():
            if key not in polygons and notes:
                warnings.append(f"a {unrefined_area[key]:.2f} m2 room was removed: " + "; ".join(notes))
    timings["floorplan_s"] = time.perf_counter() - mark

    mark = time.perf_counter()
    runs = merge_runs(walls_as_measured)
    openings_by_wall = detect_all_openings(
        runs, cloud, levels.floor_height, levels.ceiling_height
    )
    timings["openings_s"] = time.perf_counter() - mark

    tier = source.meta.tier
    # What the drift stage could not remove is a real contribution to every dimension the
    # plan reports, so it is carried into the intervals rather than left in the drift
    # report alone.
    pose_residual_m = max(float(drift.residual_after_m) * 0.02, DEFAULT_POSE_SIGMA_M)

    rooms: list[Room] = []
    lookups: dict[str, dict] = {}
    room_mask_list: list[np.ndarray] = []
    ordered = sorted(polygons.items(), key=lambda kv: -kv[1].area)
    for ordinal, (room_key, polygon) in enumerate(ordered, start=1):
        room_id = f"room_{ordinal:02d}"
        warnings.extend(f"{room_id}: {text}" for text in refinements.get(room_key, []))
        mask = masks.get(
            room_key, np.zeros(occupancy.grid.shape, dtype=bool)
        )
        per_room = room_levels(cloud, occupancy.grid, mask, levels)
        if per_room is not levels and per_room.height is None and levels.height is not None:
            warnings.extend(f"{room_id}: {text}" for text in per_room.warnings)
        geometry = RoomGeometry(
            room_id=room_id,
            polygon=polygon,
            mask=mask,
            label=config.labels.get(room_id, "room"),
        )
        observed = float(
            mask.sum() * occupancy.grid.cell_area / max(polygon.area, 1e-6)
        )
        room, lookup = build_room(
            geometry, runs, openings_by_wall, per_room, book, tier, observed,
            pose_sigma_m=pose_residual_m,
        )
        rooms.append(room)
        room_mask_list.append(mask)
        lookups[room_id] = lookup

    # A continuous capture knows which rooms connect because the operator walked between
    # them. Doorway matching is kept as the fallback for captures with no trajectory.
    adjacency = adjacency_from_trajectory(
        rooms,
        {r.room_id: m for r, m in zip(rooms, room_mask_list)},
        occupancy.grid,
        cameras,
        lookups,
    )
    if not adjacency:
        adjacency = match_adjacency(rooms, lookups)
    # Rooms stay where the scan measured them. They share one world frame, so a gap between two
    # connected rooms is floor the segmentation left out, not a misplaced room, and moving rooms
    # to close it moved them by up to 1.7 m on the assignment's scans.
    warnings.extend(unmet_adjacency_warnings(rooms, adjacency))

    quality = _quality_report(
        source, cloud, keyframes, rooms, occupancy, tier, warnings
    )
    calibration = CalibrationReport(
        method=IntervalMethod.CONFORMAL
        if book.entries
        else IntervalMethod.PROPAGATED,
        nominal_coverage=book.coverage,
        empirical_coverage={
            f"{k[0]}/{k[1]}": v.empirical_coverage
            for k, v in book.entries.items()
        },
        residual_quantiles={
            f"{k[0]}/{k[1]}": v.quantile for k, v in book.entries.items()
        },
        fitted_on=book.source,
    )

    from cozmo.damage.detect import detect_damage_for_rooms
    from cozmo.damage.rules import RuleEngine
    from cozmo.scope.generate import generate_scope_items

    mark = time.perf_counter()
    damage: list[DamageRegion] = []
    damage_detector, frames_examined, low_light = "disabled", 0, None
    if config.detect_damage:
        damage, damage_detector, frames_examined, low_light = detect_damage_for_rooms(
            rooms=rooms,
            source=source,
            keyframes=keyframes,
            camera_positions=cameras,
            world_rotation=world_rotation,
            room_masks={r.room_id: m for r, m in zip(rooms, room_mask_list)},
            grid=occupancy.grid,
            floor_y=levels.floor_height,
            book=book,
            tier=tier,
        )
    timings["damage_s"] = time.perf_counter() - mark
    quality.low_light_fraction = low_light
    warnings.append(
        f"damage detector: {damage_detector}, {frames_examined} frames examined, "
        f"{len(damage)} regions"
    )

    rule_engine = RuleEngine()
    concealed_flags = rule_engine.evaluate_damage(damage)
    scope_items = generate_scope_items(damage, concealed_flags)

    footprint = float(sum(r.floor_area.value for r in rooms))
    drift.footprint_area_after_m2 = footprint
    if not drift.applied:
        drift.footprint_area_before_m2 = footprint

    plan = PropertyPlan(
        pipeline_version=__version__,
        capture_id=source.meta.capture_id,
        tier=tier,
        created_at=datetime.now(timezone.utc),
        rooms=rooms,
        adjacency=adjacency,
        damage=damage,
        concealed_flags=concealed_flags,
        scope_items=scope_items,
        drift=drift,
        calibration=calibration,
        quality=quality,
        total_floor_area=total_area(rooms, book, tier),
        runtime_seconds=time.perf_counter() - started,
    )

    artifacts = PipelineArtifacts(
        cloud=cloud,
        cameras=cameras,
        occupancy=occupancy,
        complex=complex_,
        walls=runs,
        levels=levels,
        world_rotation=world_rotation,
        keyframes=keyframes,
        warnings=warnings,
        timings=timings,
    )
    return PipelineResult(plan=plan, artifacts=artifacts)
