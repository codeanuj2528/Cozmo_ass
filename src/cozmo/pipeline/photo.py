"""Photo tier: per-room folders of unposed stills to a stitched whole-property plan.

The tier's whole job is to manufacture the two things the LiDAR tier is handed for free --
metric depth and a pose per frame -- and then hand the result to the same reconstruction
core. Everything after that point is literally the same code as the LiDAR tier: the same
wall extraction, the same cell complex, the same opening detection, the same ceiling
measurement. That is deliberate, and it is the only way the three tiers stay comparable
instead of quietly becoming three different products that happen to share a repository.

Per room the sequence is: predict depth, level and scale each image against the floor,
register the images to each other in the three degrees of freedom that survive levelling,
fuse, then reconstruct. Rooms arrive in separate folders with nothing in common, so the
whole-property plan is assembled afterwards by matching the doorways that two rooms both
saw -- which is the gate the brief adds specifically because a photo path that handles
single rooms only fails.

What this tier cannot do is pretend to LiDAR accuracy. Measured against LiDAR on the sample
capture, the depth model's mean absolute relative error is 0.28 and its per-frame scale
varies by a third. The intervals reported here are wide because that is what the
measurement says, and a narrow interval on this input would be exactly the confident
garbage the brief penalises.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

from cozmo import __version__
from cozmo.config import PipelineConfig
from cozmo.io.base import CaptureSource, Frame, Provenance
from cozmo.io.discover import read_image
from cozmo.io.posed import PosedFrameSource
from cozmo.pipeline.common import PipelineArtifacts, PipelineResult
from cozmo.recon.backbone import get_backbone
from cozmo.util.imaging import low_light_fraction
from cozmo.recon.frames import select_diverse_frames
from cozmo.recon.monocular import intrinsics_from_exif, make_metric
from cozmo.recon.register import register_room
from cozmo.schema import (
    CalibrationReport,
    DriftReport,
    IntervalMethod,
    PropertyPlan,
    QualityReport,
    Room,
    Tier,
)
from cozmo.stitch.rooms import stitch_property
from cozmo.uncertainty.calibration import IntervalBook
from cozmo.util.transforms import make_pose, scale_intrinsics

log = logging.getLogger(__name__)

# Geometry runs at a working resolution rather than the photograph's own. A 12 MP still
# back-projects to twelve million points per image, which the fusion stage would then
# immediately voxel-reduce away; the LiDAR tier reasons at 256x192 and does well.
WORKING_WIDTH = 320
MAX_IMAGES_PER_ROOM = 8

# A reconstructed room has to be a room. These bounds do not encode what a nice room looks
# like; they encode what a dwelling cannot be, and a reconstruction outside them is not a
# wide estimate but a wrong one.
#
# This guard exists because the photo tier reported a 113 m2 bedroom and a 4.36 m ceiling in
# a flat whose LiDAR reconstruction measures 27.20 m2 total with 2.49-2.68 m ceilings.
# Publishing that with a wide interval attached would still be publishing it. The brief is
# explicit that confident garbage on thin input caps the total score, and a number that is
# wrong by a factor of ten is not rescued by admitting it might be wrong by a factor of two.
#
# A room failing this is dropped and the reason recorded, so the plan reports fewer rooms
# rather than absurd ones. That costs coverage, which is the correct thing to pay.
PLAUSIBLE_CEILING_M = (1.80, 4.20)
PLAUSIBLE_ROOM_AREA_M2 = (1.0, 60.0)
PLAUSIBLE_ROOM_SPAN_M = 14.0


@dataclass
class RoomReconstruction:
    room_id: str
    label: str
    room: Room | None
    frames_used: int
    registered: int
    scale_sources: list[str]
    warnings: list[str]


def _prepare_image(path: Path) -> tuple[np.ndarray, np.ndarray] | None:
    """Load a still and return it with its working-resolution copy."""
    try:
        rgb = read_image(path)
    except Exception as exc:
        log.warning("could not read %s: %s", path.name, exc)
        return None
    height = max(int(round(WORKING_WIDTH * rgb.shape[0] / rgb.shape[1])), 8)
    small = cv2.resize(rgb, (WORKING_WIDTH, height), interpolation=cv2.INTER_AREA)
    return rgb, small


_DEPTH_CACHE: dict[str, np.ndarray] = {}


def build_room_frames(
    image_paths: list[Path],
    backbone,
    config: PipelineConfig,
    fallback_scale: float | None = None,
) -> tuple[list[Frame], dict[int, np.ndarray], list[str], list[str], float | None]:
    """Depth, level, scale and register one room's stills into posed frames."""
    warnings: list[str] = []
    scale_sources: list[str] = []

    loaded: list[tuple[Path, np.ndarray, np.ndarray]] = []
    for path in image_paths[: MAX_IMAGES_PER_ROOM * 2]:
        prepared = _prepare_image(path)
        if prepared is not None:
            loaded.append((path, prepared[0], prepared[1]))
    if not loaded:
        return [], {}, [], ["no readable images"], None

    if len(loaded) > MAX_IMAGES_PER_ROOM:
        keep = select_diverse_frames([small for _, _, small in loaded], MAX_IMAGES_PER_ROOM)
        loaded = [loaded[i] for i in sorted(keep)]

    clouds: list[tuple[np.ndarray, np.ndarray]] = []
    per_image: list[dict] = []
    pending: list[dict] = []

    for path, full, small in loaded:
        k_full, focal_source = intrinsics_from_exif(path, full.shape[1], full.shape[0])
        k_small = scale_intrinsics(
            k_full, (full.shape[1], full.shape[0]), (small.shape[1], small.shape[0])
        )
        cache_key = str(path)
        if cache_key in _DEPTH_CACHE:
            predicted = _DEPTH_CACHE[cache_key]
        else:
            predicted = backbone.estimate(small)
            _DEPTH_CACHE[cache_key] = predicted
        geometry = make_metric(
            predicted, k_small, seed=config.seed, backbone_is_metric=backbone.is_metric()
        )
        scale_sources.append(geometry.scale.source)
        pending.append(
            {
                "path": path,
                "full": full,
                "small": small,
                "k_small": k_small,
                "geometry": geometry,
                "focal_source": focal_source,
            }
        )

    if not pending:
        return [], {}, scale_sources, warnings + ["no image produced usable geometry"], None

    # One room has one scale. Most photographs of a room do not show enough floor for the
    # camera-height prior to fire -- on the benchmark set it fires on three photographs in
    # twenty, because the operator was also asked to shoot the ceiling. But the scale it
    # recovers is a property of the camera and the depth model, not of the individual
    # photograph, so the photographs that did see the floor can speak for the ones that did
    # not. Taking the median makes one bad floor fit harmless.
    confident = [
        e["geometry"].scale.factor
        for e in pending
        if e["geometry"].floor_found and e["geometry"].scale.source == "camera_height_correction"
    ]
    room_scale = float(np.median(confident)) if confident else None
    if room_scale is not None:
        warnings.append(
            f"room scale {room_scale:.3f} from {len(confident)} of {len(pending)} photographs "
            f"that showed enough floor; applied to all"
        )
    elif fallback_scale is not None:
        room_scale = fallback_scale
        warnings.append(
            f"no photograph in this room showed enough floor to recover scale; "
            f"using property consensus scale {fallback_scale:.3f} from other rooms"
        )
    else:
        warnings.append(
            f"no photograph in this room showed enough floor to recover scale; "
            f"the depth model's own metric output is used and intervals widen"
        )

    for entry in pending:
        geometry = entry["geometry"]
        depth = geometry.depth_m
        if room_scale is not None and geometry.scale.source != "camera_height_correction":
            # Re-scale an image that could not recover its own scale onto the room's.
            depth = depth * (room_scale / max(geometry.scale.factor, 1e-6))
        points, normals = _cloud_from_depth(depth, entry["k_small"], geometry.gravity_rotation)
        if len(points) < 300:
            warnings.append(f"{entry['path'].name}: too few depth points to use")
            continue
        clouds.append((points, normals))
        per_image.append(
            {
                "path": entry["path"],
                "full": entry["full"],
                "small": entry["small"],
                "k_small": entry["k_small"],
                "depth": depth,
                "gravity": geometry.gravity_rotation,
                "focal_source": entry["focal_source"],
                "scale": geometry.scale,
            }
        )

    if not clouds:
        return [], {}, scale_sources, warnings + ["no image produced usable geometry"], None

    registration = register_room(clouds, floor_y=0.0)
    if registration.failed:
        warnings.append(
            f"{len(registration.failed)} of {len(clouds)} photographs did not register"
        )

    frames: list[Frame] = []
    images: dict[int, np.ndarray] = {}
    for entry in registration.frames:
        meta = per_image[entry.index]
        # The frame's pose is the registration transform composed with the gravity
        # rotation that levelled it, since the depth is expressed in the camera's own
        # frame and the core expects a world-from-camera pose.
        pose = entry.pose @ make_pose(meta["gravity"], np.zeros(3))
        index = len(frames)
        frames.append(
            Frame(
                index=index,
                timestamp=float(index),
                k_depth=meta["k_small"],
                k_rgb=meta["k_small"],
                rgb_size=(meta["small"].shape[1], meta["small"].shape[0]),
                depth=meta["depth"],
                depth_sigma=np.full(
                    meta["depth"].shape,
                    max(meta["scale"].relative_uncertainty, 0.10) * float(np.median(meta["depth"])),
                    dtype=np.float32,
                ),
                confidence=np.full(meta["depth"].shape, 2, dtype=np.uint8),
                pose=pose,
                depth_provenance=Provenance.PREDICTED,
                pose_provenance=Provenance.ESTIMATED,
                rgb_path=meta["path"],
            )
        )
        images[index] = meta["small"]

    return frames, images, scale_sources, warnings, room_scale


def _cloud_from_depth(
    depth: np.ndarray, k: np.ndarray, gravity: np.ndarray, stride: int = 2
) -> tuple[np.ndarray, np.ndarray]:
    """Back-project a depth map into a gravity-aligned cloud with normals."""
    from cozmo.geometry.fusion import image_normals

    h, w = depth.shape
    vs, us = np.mgrid[0:h, 0:w]
    valid = np.isfinite(depth) & (depth > 0.2) & (depth < 12.0)
    z = depth.astype(np.float32)
    x = (us - k[0, 2]) * z / k[0, 0]
    y = (vs - k[1, 2]) * z / k[1, 1]
    grid = np.stack([x, y, z], axis=2).astype(np.float32)

    normals, ok = image_normals(grid, valid)
    keep = valid & ok
    keep[::stride, ::stride] &= True
    sub = np.zeros_like(keep)
    sub[::stride, ::stride] = True
    keep &= sub
    if keep.sum() < 50:
        return np.zeros((0, 3)), np.zeros((0, 3))

    points = grid[keep].astype(np.float64)
    vectors = normals[keep].astype(np.float64)
    flip = np.einsum("ij,ij->i", vectors, points) > 0
    vectors[flip] *= -1.0
    return points @ gravity.T, vectors @ gravity.T


def _implausible(room) -> str | None:
    """Why this reconstruction cannot be a room, or None if it could be."""
    area = room.floor_area.value
    if not (PLAUSIBLE_ROOM_AREA_M2[0] <= area <= PLAUSIBLE_ROOM_AREA_M2[1]):
        return f"floor area {area:.1f} m2 outside {PLAUSIBLE_ROOM_AREA_M2[0]:.0f}-{PLAUSIBLE_ROOM_AREA_M2[1]:.0f} m2"

    height = room.ceiling_height.value if room.ceiling_height is not None else None
    if height is not None and height > 0 and not (PLAUSIBLE_CEILING_M[0] <= height <= PLAUSIBLE_CEILING_M[1]):
        return f"ceiling height {height:.2f} m outside {PLAUSIBLE_CEILING_M[0]:.1f}-{PLAUSIBLE_CEILING_M[1]:.1f} m"

    if room.polygon:
        ring = np.asarray(room.polygon, dtype=float)
        span = float(np.ptp(ring, axis=0).max())
        if span > PLAUSIBLE_ROOM_SPAN_M:
            return f"longest span {span:.1f} m exceeds {PLAUSIBLE_ROOM_SPAN_M:.0f} m"
    return None


def _rekey_room(room: Room, room_id: str, label: str) -> Room:
    """The room under its property-wide id, with every id inside it renamed to follow.

    Each folder is reconstructed on its own, so its room comes back as `room_01`, with walls
    `room_01_w00`, surfaces `room_01_s00` and openings `room_01_o00`. Renaming only the room left
    every surface of a stitched plan saying it sat in `room_01`: on the 58-still flat, 14 surfaces
    named a room they were not in.
    """
    old, new = f"{room.room_id}_", f"{room_id}_"

    def rename(value: str) -> str:
        return new + value[len(old):] if value.startswith(old) else value

    walls = [w.model_copy(update={"wall_id": rename(w.wall_id), "surface_id": rename(w.surface_id)}) for w in room.walls]
    surfaces = [s.model_copy(update={"surface_id": rename(s.surface_id), "room_id": room_id}) for s in room.surfaces]
    openings = [o.model_copy(update={"opening_id": rename(o.opening_id), "wall_id": rename(o.wall_id)}) for o in room.openings]
    return room.model_copy(
        update={"room_id": room_id, "label": label, "walls": walls, "surfaces": surfaces, "openings": openings}
    )


def build_photo_plan(
    source: CaptureSource,
    config: PipelineConfig | None = None,
    book: IntervalBook | None = None,
) -> PipelineResult:
    """Reconstruct a property from per-room photo folders."""
    from cozmo.pipeline.lidar import build_lidar_plan

    config = config or PipelineConfig()
    book = book or IntervalBook.load(config.calibration_path)
    started = time.perf_counter()
    warnings: list[str] = []

    folders = getattr(source, "room_folders", None)
    if not folders:
        raise ValueError("photo tier needs a capture exposing room_folders")

    backbone = get_backbone(Path(config.weights_dir))
    if not backbone.is_metric():
        warnings.append(
            f"depth backbone '{backbone.name}' is not metric; photo-tier scale rests "
            "entirely on the camera-height prior and intervals widen accordingly"
        )

    # Loop closure needs a revisit after going elsewhere, which a handful of stills of one
    # room does not contain. Running it here would find nothing and cost a minute.
    room_config = config.with_overrides(drift_correction=False, detect_damage=False)

    reconstructions: list[RoomReconstruction] = []
    all_scale_sources: list[str] = []

    # First pass: build room frames and collect camera height scales
    room_data = []
    for ordinal, (name, paths) in enumerate(sorted(folders.items()), start=1):
        room_id = f"room_{ordinal:02d}"
        frames, images, scale_sources, room_warnings, room_scale = build_room_frames(
            paths, backbone, config
        )
        all_scale_sources.extend(scale_sources)
        room_data.append(
            (room_id, name, paths, frames, images, scale_sources, room_warnings, room_scale)
        )

    property_scales = [scale for *_, scale in room_data if scale is not None]
    known_property_scale: float | None = float(np.median(property_scales)) if property_scales else None

    for room_id, name, paths, frames, images, scale_sources, room_warnings, _room_scale in room_data:
        if not frames:
            reconstructions.append(
                RoomReconstruction(room_id, name, None, 0, 0, scale_sources, room_warnings)
            )
            warnings.extend(f"{name}: {w}" for w in room_warnings)
            continue

        room_source = PosedFrameSource(
            frames=frames,
            capture_id=f"{source.meta.capture_id}:{name}",
            tier=Tier.PHOTO,
            device_model=source.meta.device_model,
            images=images,
        )
        try:
            result = build_lidar_plan(room_source, room_config, book)
            rooms = result.plan.rooms
        except Exception:
            rooms = []

        largest = max(rooms, key=lambda r: r.floor_area.value) if rooms else None
        reason = _implausible(largest) if largest else "no_room"

        # If native room reconstruction failed or was implausible, retry with property scale consensus
        if (reason or not rooms) and known_property_scale is not None:
            retry_frames, retry_images, retry_sources, retry_warnings, _retry_scale = build_room_frames(
                paths, backbone, config, fallback_scale=known_property_scale
            )
            if retry_frames:
                retry_source = PosedFrameSource(
                    frames=retry_frames,
                    capture_id=f"{source.meta.capture_id}:{name}",
                    tier=Tier.PHOTO,
                    device_model=source.meta.device_model,
                    images=retry_images,
                )
                try:
                    retry_result = build_lidar_plan(retry_source, room_config, book)
                    retry_rooms = retry_result.plan.rooms
                    if retry_rooms:
                        retry_largest = max(retry_rooms, key=lambda r: r.floor_area.value)
                        retry_reason = _implausible(retry_largest)
                        if not retry_reason:
                            rooms = retry_rooms
                            largest = retry_largest
                            reason = None
                            room_warnings = retry_warnings
                except Exception:
                    pass

        if not rooms or reason:
            warnings.append(
                f"{name}: reconstruction rejected as physically implausible ({reason}); "
                f"reported as not reconstructed rather than published"
            )
            reconstructions.append(
                RoomReconstruction(room_id, name, None, len(frames), len(frames), scale_sources, room_warnings)
            )
            continue

        renamed = _rekey_room(largest, room_id, name)
        reconstructions.append(
            RoomReconstruction(room_id, name, renamed, len(frames), len(frames), scale_sources, room_warnings)
        )
        warnings.extend(f"{name}: {w}" for w in room_warnings)

    recovered = [r for r in reconstructions if r.room is not None]
    stitched, adjacency, stitch_warnings = stitch_property([r.room for r in recovered])
    warnings.extend(stitch_warnings)

    from cozmo.geometry.assemble import total_area, unmet_adjacency_warnings

    warnings.extend(unmet_adjacency_warnings(stitched, adjacency))

    plan = PropertyPlan(
        pipeline_version=__version__,
        capture_id=source.meta.capture_id,
        tier=Tier.PHOTO,
        created_at=datetime.now(timezone.utc),
        rooms=stitched,
        adjacency=adjacency,
        damage=[],
        concealed_flags=[],
        scope_items=[],
        drift=DriftReport(
            method=(
                "not applicable at the photo tier: loop closure requires revisiting a place "
                "after going elsewhere, and per-room stills contain no trajectory to close"
            ),
            loop_closures_found=0,
            residual_before_m=0.0,
            residual_after_m=0.0,
            max_pose_correction_m=0.0,
            footprint_area_before_m2=float(sum(r.floor_area.value for r in stitched)),
            footprint_area_after_m2=float(sum(r.floor_area.value for r in stitched)),
            applied=False,
        ),
        calibration=CalibrationReport(
            method=IntervalMethod.CONFORMAL if book.entries else IntervalMethod.PRIOR,
            nominal_coverage=book.coverage,
            empirical_coverage={
                f"{k[0]}/{k[1]}": v.empirical_coverage for k, v in book.entries.items()
            },
            residual_quantiles={f"{k[0]}/{k[1]}": v.quantile for k, v in book.entries.items()},
            fitted_on=book.source,
        ),
        quality=QualityReport(
            tier=Tier.PHOTO,
            device_model=source.meta.device_model,
            frames_available=source.meta.frame_count,
            frames_used=sum(r.frames_used for r in reconstructions),
            median_depth_confidence=None,
            surface_coverage=float(len(recovered) / max(len(reconstructions), 1)),
            low_light_fraction=low_light_fraction(
                [image for entry in room_data for image in entry[4].values()]
            ),
            warnings=warnings + [f"depth backbone: {backbone.name}"],
        ),
        total_floor_area=total_area(stitched, book, Tier.PHOTO),
        runtime_seconds=time.perf_counter() - started,
    )

    artifacts = PipelineArtifacts(
        cloud=None,
        cameras=np.zeros((0, 3)),
        occupancy=None,
        complex=None,
        walls=[],
        levels=None,
        world_rotation=np.eye(3),
        keyframes=[],
        warnings=warnings,
        timings={"total_s": time.perf_counter() - started},
    )
    return PipelineResult(plan=plan, artifacts=artifacts)
