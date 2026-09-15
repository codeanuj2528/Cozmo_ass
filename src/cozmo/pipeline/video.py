"""Video tier: a handheld walkthrough clip to a stitched whole-property plan.

The video tier sits between the other two and its structure should reflect that rather
than copying either. Like the photo tier it has no depth and no poses and must predict
both. Unlike the photo tier it has continuity: consecutive frames overlap heavily, so
poses chain, and the walk returns past places it has already been, so loop closure has
something to close.

That continuity is worth using rather than discarding. Treating a walkthrough as a bag of
per-room photo folders throws away the one advantage the tier has and forces the property
back together through doorway matching, which is a harder problem than sequential
registration and a strictly worse answer when the trajectory is right there in the file.
So the video tier registers frames in sequence into one property-wide cloud and then runs
the same core as the LiDAR tier, drift correction included.

Since fix loop round 4 the registering is done by a multi-view model, not by ICP between
monocular clouds: runs of keyframes are reconstructed together by VGGT-1B, joined by the
frames consecutive runs share, and scaled by MoGe-2 (`recon/sequence.py`). The monocular path
is kept behind `--no-multiview`; on the assignment's walks it gave footprints of +109% to
+197%.

Frame selection matters more here than anywhere else. A walkthrough is mostly redundant and
partly unusable: a phone swung through a doorway produces frames whose motion blur destroys
both the depth prediction and the registration that depends on it. Frames are therefore
scored on sharpness before anything else looks at them, and a blurred frame is dropped
rather than fed to a depth model that will confidently hallucinate a surface for it.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

from cozmo.config import PipelineConfig
from cozmo.io.discover import find_videos
from cozmo.io.base import CaptureSource, Frame, Provenance
from cozmo.io.posed import PosedFrameSource
from cozmo.pipeline.common import PipelineResult
from cozmo.recon.backbone import get_backbone
from cozmo.recon.monocular import intrinsics_from_exif, make_metric
from cozmo.recon.register import register_sequential
from cozmo.schema import Tier
from cozmo.uncertainty.calibration import IntervalBook
from cozmo.util.transforms import make_pose, scale_intrinsics

log = logging.getLogger(__name__)

WORKING_WIDTH = 320
TARGET_KEYFRAMES = 120
# Variance of the Laplacian, the standard sharpness proxy. The absolute value depends on
# resolution and content, so it is used relatively: frames in the bottom fraction of the
# clip's own sharpness distribution are dropped rather than compared to a fixed number.
BLUR_REJECT_FRACTION = 0.25


def _sharpness(image: np.ndarray) -> float:
    grey = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY) if image.ndim == 3 else image
    return float(cv2.Laplacian(grey, cv2.CV_64F).var())


def extract_keyframes(
    video_path: Path,
    target: int = TARGET_KEYFRAMES,
    blur_reject_fraction: float = BLUR_REJECT_FRACTION,
) -> tuple[list[int], list[np.ndarray], dict[str, float]]:
    """Sample a clip down to sharp, spread-out keyframes.

    Sampled at a uniform stride first and filtered for sharpness second, so the frames that
    survive still cover the whole walk. Filtering first and then sampling would bias the
    selection toward whichever rooms the operator moved slowly through.
    """
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise ValueError(f"cannot open video: {video_path}")

    total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    if total <= 0:
        total = 1 << 20
    # Oversample, then let the blur filter take its cut without dropping below target.
    wanted = max(int(target / max(1.0 - blur_reject_fraction, 0.1)), target)
    stride = max(int(total / wanted), 1)

    indices: list[int] = []
    images: list[np.ndarray] = []
    sharpness: list[float] = []
    position = 0
    while True:
        ok = capture.grab()
        if not ok:
            break
        if position % stride == 0:
            ok, bgr = capture.retrieve()
            if ok:
                rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
                height = max(int(round(WORKING_WIDTH * rgb.shape[0] / rgb.shape[1])), 8)
                small = cv2.resize(rgb, (WORKING_WIDTH, height), interpolation=cv2.INTER_AREA)
                indices.append(position)
                images.append(small)
                sharpness.append(_sharpness(small))
        position += 1
    capture.release()

    stats = {"frames_in_clip": float(position), "frames_sampled": float(len(indices))}
    if not indices:
        return [], [], stats

    threshold = float(np.quantile(sharpness, blur_reject_fraction))
    keep = [i for i, s in enumerate(sharpness) if s >= threshold]
    if len(keep) > target:
        step = len(keep) / target
        keep = [keep[int(i * step)] for i in range(target)]

    stats["frames_kept"] = float(len(keep))
    stats["blur_threshold"] = threshold
    stats["median_sharpness"] = float(np.median(sharpness))
    return [indices[i] for i in keep], [images[i] for i in keep], stats


def _build_video_plan_multiview(
    source: CaptureSource,
    config: PipelineConfig,
    book: IntervalBook,
    video_path: Path,
    backbone,
    metric_model,
    started: float,
) -> PipelineResult:
    """The walk's keyframes reconstructed in overlapping runs (`recon/sequence.py`), then the LiDAR core."""
    from cozmo.pipeline.lidar import build_lidar_plan
    from cozmo.recon.sequence import RUN_LENGTH, RUN_OVERLAP, reconstruct_sequence, sample_keyframes

    numbers, images, stats = sample_keyframes(video_path)
    if len(images) < 2:
        raise ValueError(f"no usable frames extracted from {video_path}")
    warnings = [
        f"video: {int(stats['frames'])} frames at {stats['fps']:.0f} fps; the sharpest of every "
        f"{int(stats['window_frames'])} kept, {len(images)} keyframes"
    ]
    reconstruction, notes = reconstruct_sequence(images, backbone, metric_model)
    warnings.extend(notes)
    if reconstruction is None or len(reconstruction.frames) < 4:
        raise ValueError("the walk could not be reconstructed: " + "; ".join(notes or ["too few keyframes"]))
    fps = stats["fps"] or 30.0
    for frame, keyframe in zip(reconstruction.frames, reconstruction.keyframes_used):
        frame.timestamp = numbers[keyframe] / fps
    scale = reconstruction.scale
    warnings.append(
        f"video: {len(reconstruction.frames)} of {len(images)} keyframes reconstructed by {backbone.name} in runs "
        f"of {RUN_LENGTH} sharing {RUN_OVERLAP}; metric scale {scale.factor:.3f} "
        f"+-{100 * scale.relative_uncertainty:.1f}% from {scale.source} over {scale.views_used} keyframes, "
        f"given the field of view {backbone.name} estimated"
    )

    posed = PosedFrameSource(
        frames=reconstruction.frames,
        capture_id=source.meta.capture_id,
        tier=Tier.VIDEO,
        device_model=source.meta.device_model,
        root=Path(source.meta.root),
        images=reconstruction.images,
        notes={"video": str(video_path), "focal_source": f"{backbone.name} intrinsics"},
    )
    # Drift correction stays on: chained runs accumulate error as any sequential registration does, and a walk
    # that comes back past a place gives loop closure something to close.
    result = build_lidar_plan(posed, config, book)
    result.plan.tier = Tier.VIDEO
    result.plan.created_at = datetime.now(timezone.utc)
    result.plan.quality.tier = Tier.VIDEO
    result.plan.quality.warnings = list(result.plan.quality.warnings) + warnings
    result.plan.runtime_seconds = time.perf_counter() - started
    result.artifacts.warnings.extend(warnings)
    return result


def build_video_plan(
    source: CaptureSource,
    config: PipelineConfig | None = None,
    book: IntervalBook | None = None,
) -> PipelineResult:
    """Reconstruct a property from one continuous walkthrough clip."""
    from cozmo.pipeline.lidar import build_lidar_plan
    from cozmo.pipeline.photo import _cloud_from_depth

    config = config or PipelineConfig()
    book = book or IntervalBook.load(config.calibration_path)
    started = time.perf_counter()
    warnings: list[str] = []

    video_path = getattr(source, "video_path", None)
    if video_path is None:
        candidates = find_videos(Path(source.meta.root))
        if not candidates:
            raise ValueError("video tier needs a .mp4 or .mov in the capture directory")
        video_path = candidates[0]

    from cozmo.recon.multiview import get_joint_models

    joint = get_joint_models(Path(config.weights_dir)) if config.multiview else None
    if joint is not None:
        return _build_video_plan_multiview(source, config, book, Path(video_path), *joint, started)
    if config.multiview:
        warnings.append(
            "VGGT-1B or MoGe-2 is not installed (scripts/setup.sh, scripts/fetch_weights.sh); keyframes are "
            "built one at a time from a monocular depth model instead, which fix loop round 4 measured at "
            "+109% to +197% on the footprint"
        )

    numbers, images, stats = extract_keyframes(Path(video_path))
    if not images:
        raise ValueError(f"no usable frames extracted from {video_path}")
    warnings.append(
        f"video: {int(stats['frames_in_clip'])} frames in clip, "
        f"{int(stats['frames_sampled'])} sampled, {int(stats.get('frames_kept', 0))} kept "
        f"after blur rejection at the {BLUR_REJECT_FRACTION:.0%} quantile"
    )

    backbone = get_backbone(Path(config.weights_dir))
    height, width = images[0].shape[:2]
    # A clip carries no EXIF, so the focal length comes from the device prior. The
    # provenance is recorded because an assumed focal length is a scale error waiting to
    # happen and the reader should know it was assumed.
    k_full, focal_source = intrinsics_from_exif(None, width, height)
    warnings.append(f"video intrinsics: {focal_source}")

    # Predict depth for every keyframe before registering any of them, because the scale
    # each frame recovers on its own is not usable as it stands.
    #
    # make_metric recovers metric scale per frame from that frame's own floor plane. On this
    # clip only 12 of 40 keyframes find a floor at all; the other 28 keep the backbone's raw
    # output at factor 1.000. The factors that are recovered span 0.670 to 1.757, so the
    # clouds differ in size by up to 2.62x. ICP aligns rigidly, and no rigid transform can
    # reconcile two clouds of different scale, so sequential registration was being handed
    # an impossible problem: 9 of 40 keyframes failed outright, the walk broke into two
    # disconnected stretches, and the surviving trajectory drifted 6.70 m vertically inside
    # a single-storey flat, producing a point cloud 12.6 m tall.
    #
    # A walkthrough is one camera in one building, so there is one scale, not one per frame.
    # The frames that did see a floor are the only evidence of it, so their median sets the
    # scale for the whole clip and the frames that saw no floor inherit it instead of
    # silently asserting 1.000. This is the same consensus the photo tier applies across
    # rooms, for the same reason.
    predictions: list[tuple[np.ndarray, np.ndarray, float, str]] = []
    for image in images:
        geometry = make_metric(
            backbone.estimate(image), k_full, seed=config.seed,
            backbone_is_metric=backbone.is_metric(),
        )
        predictions.append(
            (geometry.depth_m, geometry.gravity_rotation,
             float(geometry.scale.factor), geometry.scale.source)
        )

    grounded = [f for _, _, f, source in predictions if source == "camera_height_correction"]
    if grounded:
        consensus_scale = float(np.median(grounded))
        warnings.append(
            f"video scale: consensus {consensus_scale:.3f} from the median of "
            f"{len(grounded)} of {len(predictions)} keyframes that resolved a floor plane "
            f"(those ranged {min(grounded):.3f}-{max(grounded):.3f}); applied to every "
            "keyframe so the clouds registration sees are mutually consistent in size"
        )
    else:
        consensus_scale = 1.0
        warnings.append(
            "video scale: no keyframe resolved a floor plane, so the backbone's own metric "
            "output is used unscaled and every dimension inherits its bias"
        )

    clouds: list[tuple[np.ndarray, np.ndarray]] = []
    depths: list[np.ndarray] = []
    gravities: list[np.ndarray] = []

    for depth_m, gravity, own_factor, _source in predictions:
        # Undo the frame's own correction before applying the consensus, so a frame that
        # found a floor is not scaled twice.
        depth = (depth_m * (consensus_scale / max(own_factor, 1e-6))).astype(np.float32)
        clouds.append(_cloud_from_depth(depth, k_full, gravity))
        depths.append(depth)
        gravities.append(gravity)

    poses, registration_warnings = register_sequential(clouds)
    warnings.extend(registration_warnings)

    frames: list[Frame] = []
    frame_images: dict[int, np.ndarray] = {}
    for index, (pose, depth, gravity, image) in enumerate(zip(poses, depths, gravities, images)):
        if pose is None:
            continue
        frames.append(
            Frame(
                index=index,
                timestamp=float(numbers[index]),
                k_depth=k_full,
                k_rgb=k_full,
                rgb_size=(width, height),
                depth=depth,
                depth_sigma=np.full(depth.shape, 0.25 * float(np.median(depth)), dtype=np.float32),
                confidence=np.full(depth.shape, 2, dtype=np.uint8),
                pose=pose @ make_pose(gravity, np.zeros(3)),
                depth_provenance=Provenance.PREDICTED,
                pose_provenance=Provenance.ESTIMATED,
            )
        )
        frame_images[index] = image

    if len(frames) < 4:
        raise ValueError(
            f"only {len(frames)} frames registered from {len(images)} keyframes; "
            "the clip is too blurred or too fast to reconstruct"
        )
    warnings.append(f"video: {len(frames)} of {len(images)} keyframes registered")

    posed = PosedFrameSource(
        frames=frames,
        capture_id=source.meta.capture_id,
        tier=Tier.VIDEO,
        device_model=source.meta.device_model,
        root=Path(source.meta.root),
        images=frame_images,
        notes={"video": str(video_path), "focal_source": focal_source},
    )

    # Drift correction stays on: a walkthrough revisits places, which is precisely the
    # condition loop closure needs and precisely what the photo tier lacks.
    result = build_lidar_plan(posed, config, book)
    result.plan.tier = Tier.VIDEO
    result.plan.created_at = datetime.now(timezone.utc)
    result.plan.quality.tier = Tier.VIDEO
    result.plan.quality.warnings = list(result.plan.quality.warnings) + warnings
    result.plan.runtime_seconds = time.perf_counter() - started
    result.artifacts.warnings.extend(warnings)
    return result
