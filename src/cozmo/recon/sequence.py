"""A walkthrough clip reconstructed as overlapping runs of keyframes, joined by the frames they share.

A multi-view model holds a limited number of images at once: on a 16 GB machine about a dozen at 518 px. A walk
through a flat is a hundred keyframes and more, and frames sampled across the whole of it do not overlap enough
for the model to pose them (on the assignment's single-room walk, 16 frames spread over 37 s came back with
rotations 110 degrees wrong). Consecutive keyframes do overlap, so the walk is cut into runs of `chunk` keyframes
that share `overlap` keyframes with the run before.

Two runs that reconstruct the same image give two depth maps of it on the same pixel grid, so every confident
pixel is a pair of corresponding points, one in each run's frame. A similarity transform fitted to those pairs
(Umeyama 1991) carries the later run into the earlier one's frame, scale included, and the runs are chained back
to the first. What the chain accumulates is left to the reconstruction core's pose graph and loop closures, as at
the LiDAR tier.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from cozmo.recon.multiview import DOWN_AXIS_TOLERANCE_DEG, ViewGeometry, down_axis_outliers, views_to_frames

MIN_PAIRS = 200
PAIR_STRIDE = 4
# One keyframe per second of walk, the sharpest in its second, up to a cap; runs of 8 sharing 3. Eight images at
# 518 px took 12.4 GB on the 16 GB machine this was built on (fixloop/round4/evidence/multiview_stills.json), and
# the step from six images to eight was 1.5 GB, so twelve would not fit.
KEYFRAME_INTERVAL_S = 1.0
MAX_KEYFRAMES = 150
KEYFRAME_LONG_SIDE = 960
RUN_LENGTH = 8
RUN_OVERLAP = 3
# MoGe-2 is run on every third keyframe: the scale is a median over the walk, not a per-frame quantity.
METRIC_EVERY = 3


def chunk_ranges(count: int, chunk: int, overlap: int) -> list[list[int]]:
    """Index runs of length `chunk` covering `count` items, each sharing `overlap` items with the one before."""
    if count <= 0:
        return []
    if count <= chunk:
        return [list(range(count))]
    if not 0 <= overlap < chunk:
        raise ValueError("overlap must be at least 0 and less than the chunk length")
    step = chunk - overlap
    ranges, start = [], 0
    while True:
        end = min(start + chunk, count)
        ranges.append(list(range(max(end - chunk, 0), end)))
        if end == count:
            return ranges
        start += step


def umeyama(source: np.ndarray, target: np.ndarray, weights: np.ndarray | None = None) -> tuple[float, np.ndarray, np.ndarray]:
    """Scale s, rotation R and translation t minimising sum w |s R x + t - y|^2 (Umeyama 1991)."""
    w = np.ones(len(source)) if weights is None else np.asarray(weights, float)
    w = w / w.sum()
    mu_x, mu_y = w @ source, w @ target
    x, y = source - mu_x, target - mu_y
    covariance = (y * w[:, None]).T @ x
    u, singular, vt = np.linalg.svd(covariance)
    d = np.eye(3)
    if np.linalg.det(u) * np.linalg.det(vt) < 0:
        d[2, 2] = -1.0
    rotation = u @ d @ vt
    variance = float(w @ (x ** 2).sum(axis=1))
    scale = float(np.trace(np.diag(singular) @ d) / max(variance, 1e-12))
    return scale, rotation, mu_y - scale * rotation @ mu_x


def view_points(view: ViewGeometry, mask: np.ndarray) -> np.ndarray:
    """World points of the masked pixels of one view."""
    rows, cols = np.nonzero(mask)
    rays = np.stack([cols, rows, np.ones(len(rows))], axis=1) @ np.linalg.inv(view.intrinsics).T
    camera = rays * view.depth[rows, cols][:, None]
    return camera @ view.rotation_wc.T + view.centre


def shared_view_transform(earlier: ViewGeometry, later: ViewGeometry) -> tuple[float, np.ndarray, np.ndarray, int]:
    """The similarity taking the later run's frame onto the earlier run's, from one image both reconstructed."""
    if earlier.depth.shape != later.depth.shape:
        raise ValueError("the two reconstructions of one image must share its pixel grid")
    grid = np.zeros(earlier.depth.shape, bool)
    grid[::PAIR_STRIDE, ::PAIR_STRIDE] = True
    confident = (earlier.confidence >= np.quantile(earlier.confidence, 0.5)) & (later.confidence >= np.quantile(later.confidence, 0.5))
    mask = grid & confident & (earlier.depth > 1e-6) & (later.depth > 1e-6)
    count = int(mask.sum())
    if count < MIN_PAIRS:
        raise ValueError(f"only {count} confident pixels shared between the two reconstructions")
    return (*umeyama(view_points(later, mask), view_points(earlier, mask)), count)


def transform_view(view: ViewGeometry, scale: float, rotation: np.ndarray, translation: np.ndarray) -> ViewGeometry:
    return ViewGeometry(
        depth=(view.depth * scale).astype(np.float32),
        confidence=view.confidence,
        intrinsics=view.intrinsics,
        rotation_wc=rotation @ view.rotation_wc,
        centre=scale * rotation @ view.centre + translation,
        image_size=view.image_size,
    )


@dataclass
class ChainedRun:
    views: dict[int, ViewGeometry]
    link_scales: list[float]
    link_pairs: list[int]
    breaks: list[int] = field(default_factory=list)


def _link(previous: dict[int, ViewGeometry], current: dict[int, ViewGeometry]) -> tuple[float, np.ndarray, np.ndarray, int]:
    shared = sorted(set(previous) & set(current))
    if not shared:
        raise ValueError("the two runs share no keyframe")
    sources, targets = [], []
    for index in shared:
        grid = np.zeros(previous[index].depth.shape, bool)
        grid[::PAIR_STRIDE, ::PAIR_STRIDE] = True
        both = (grid & (previous[index].confidence >= np.quantile(previous[index].confidence, 0.5))
                & (current[index].confidence >= np.quantile(current[index].confidence, 0.5))
                & (previous[index].depth > 1e-6) & (current[index].depth > 1e-6))
        sources.append(view_points(current[index], both))
        targets.append(view_points(previous[index], both))
    source, target = np.vstack(sources), np.vstack(targets)
    if len(source) < MIN_PAIRS:
        raise ValueError(f"the two runs share only {len(source)} confident pixels")
    return (*umeyama(source, target), len(source))


def chain_runs(runs: list[list[ViewGeometry]], ranges: list[list[int]]) -> ChainedRun:
    """Every keyframe's view in the first run's frame. A keyframe in two runs keeps its view from the earlier one.

    Each link is fitted on every image the two runs share, pooled. `link_scales` records the scale of each link,
    which is how much the model's units drift along the walk. Where a link cannot be fitted the walk is broken
    there, and the longest unbroken stretch is kept; `breaks` names the runs that started a new stretch.
    """
    segments: list[ChainedRun] = []
    current = ChainedRun({i: v for i, v in zip(ranges[0], runs[0])}, [], [])
    to_first = (1.0, np.eye(3), np.zeros(3))
    breaks: list[int] = []
    for k in range(1, len(runs)):
        previous = {i: v for i, v in zip(ranges[k - 1], runs[k - 1])}
        this = {i: v for i, v in zip(ranges[k], runs[k])}
        try:
            s, r, t, pairs = _link(previous, this)
        except ValueError:
            segments.append(current)
            breaks.append(k)
            current = ChainedRun(dict(this), [], [])
            to_first = (1.0, np.eye(3), np.zeros(3))
            continue
        s0, r0, t0 = to_first
        to_first = (s0 * s, r0 @ r, s0 * r0 @ t + t0)
        current.link_scales.append(s)
        current.link_pairs.append(pairs)
        for index, view in this.items():
            if index not in current.views:
                current.views[index] = transform_view(view, *to_first)
    segments.append(current)
    best = max(segments, key=lambda segment: len(segment.views))
    best.breaks = breaks
    return best


def sample_keyframes(video_path: Path, interval_s: float = KEYFRAME_INTERVAL_S, max_keyframes: int = MAX_KEYFRAMES,
                     long_side: int = KEYFRAME_LONG_SIDE) -> tuple[list[int], list[np.ndarray], dict[str, float]]:
    """The sharpest frame of every `interval_s` of the clip, resized so its long side is `long_side`.

    Sharpness is the variance of the Laplacian on a small grey copy. Choosing the sharpest frame of each window
    keeps the whole walk covered while leaving out the motion blur of a phone swung through a doorway. A clip
    longer than `max_keyframes` windows gets proportionally longer windows. OpenCV applies the clip's rotation tag,
    so a portrait video arrives upright.
    """
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise ValueError(f"cannot open video: {video_path}")
    fps = capture.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT)) or 1
    window = max(int(round(fps * interval_s)), 1)
    window = max(window, int(np.ceil(total / max_keyframes)))
    numbers: list[int] = []
    images: list[np.ndarray] = []
    best: tuple[float, int, np.ndarray] | None = None
    # Every second frame is enough to find the sharpest in a window of a 30 or 60 fps clip, and halves the decoding.
    # A window of a frame or two would then hold no decoded frame at all and its keyframe would be lost.
    stride = 2 if window >= 4 else 1
    index = 0
    try:
        while True:
            if not capture.grab():
                break
            if index % stride == 0:
                ok, bgr = capture.retrieve()
                if ok:
                    small = cv2.resize(bgr, (320, max(int(round(320 * bgr.shape[0] / bgr.shape[1])), 8)), interpolation=cv2.INTER_AREA)
                    sharpness = float(cv2.Laplacian(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var())
                    if best is None or sharpness > best[0]:
                        best = (sharpness, index, bgr)
            index += 1
            if index % window == 0 and best is not None:
                _, number, bgr = best
                factor = long_side / max(bgr.shape[:2])
                size = (int(round(bgr.shape[1] * factor)), int(round(bgr.shape[0] * factor)))
                numbers.append(number)
                images.append(cv2.cvtColor(cv2.resize(bgr, size, interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2RGB))
                best = None
        if best is not None:
            _, number, bgr = best
            factor = long_side / max(bgr.shape[:2])
            size = (int(round(bgr.shape[1] * factor)), int(round(bgr.shape[0] * factor)))
            numbers.append(number)
            images.append(cv2.cvtColor(cv2.resize(bgr, size, interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2RGB))
    finally:
        capture.release()
    return numbers, images, {"fps": float(fps), "frames": float(index), "window_frames": float(window)}


@dataclass
class SequenceReconstruction:
    frames: list
    images: dict[int, np.ndarray]
    scale: object
    up: np.ndarray
    keyframes_used: list[int]
    link_scales: list[float]
    notes: list[str]
    seconds: float


def reconstruct_sequence(images: list[np.ndarray], backbone, metric_model, fov_x_deg: float | None = None,
                         run_length: int = RUN_LENGTH, overlap: int = RUN_OVERLAP, metric_every: int = METRIC_EVERY,
                         tolerance_deg: float = DOWN_AXIS_TOLERANCE_DEG) -> tuple[SequenceReconstruction | None, list[str]]:
    """Keyframes of one walk to metric, gravity-aligned frames in one frame, or None and the reason."""
    from cozmo.recon.metric_scale import horizontal_fov_deg, scale_from_metric_depth

    started = time.perf_counter()
    notes: list[str] = []
    if len(images) < 2:
        return None, ["fewer than two keyframes"]
    ranges = chunk_ranges(len(images), run_length, overlap)
    runs = [backbone.reconstruct([images[i] for i in indices]).views for indices in ranges]
    chained = chain_runs(runs, ranges)
    if chained.breaks:
        notes.append(f"the walk broke into {len(chained.breaks) + 1} stretches where runs shared too little; "
                     f"the longest, {len(chained.views)} of {len(images)} keyframes, is reconstructed")
    if chained.link_scales:
        drift = np.cumprod(chained.link_scales)
        notes.append(f"model units drifted by a factor of {drift.min():.3f}-{drift.max():.3f} along the walk "
                     f"over {len(chained.link_scales)} links")
    order = sorted(chained.views)
    views = [chained.views[i] for i in order]
    up, outliers = down_axis_outliers(views, tolerance_deg)
    if outliers:
        notes.append(f"{len(outliers)} keyframe(s) disagree on which way is down by more than {tolerance_deg:.0f} deg and are left out")
    kept = [order[j] for j in range(len(order)) if j not in set(outliers)]
    if len(kept) < 2:
        return None, notes + ["fewer than two keyframes agree on which way is down"]

    sampled = kept[::max(metric_every, 1)]
    metric = []
    for i in sampled:
        view = chained.views[i]
        fov = fov_x_deg if fov_x_deg is not None else horizontal_fov_deg(view.intrinsics, view.depth.shape[1])
        metric.append(metric_model.estimate(images[i], fov))
    source = "moge-2-vitl given the clip's field of view" if fov_x_deg is not None else f"moge-2-vitl given the field of view {getattr(backbone, 'name', 'the multi-view model')} estimated"
    scale = scale_from_metric_depth([chained.views[i] for i in sampled], metric, source)
    if scale is None:
        return None, notes + ["the metric depth model gave too few valid pixels to scale the walk"]
    frames, colours = views_to_frames([chained.views[i] for i in kept], scale.factor, up, 0, [images[i] for i in kept])
    return SequenceReconstruction(frames, colours, scale, up, kept, chained.link_scales, notes,
                                  time.perf_counter() - started), notes
