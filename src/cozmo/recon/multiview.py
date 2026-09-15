"""Several photographs of one space, reconstructed together.

The photo tier used to build every still on its own: a monocular depth map, a scale from whichever stills showed
floor, then registration of views that barely overlap. On the assignment's walks that gave rooms from +0% to
+408% of the LiDAR room (fix loop round 4). A multi-view model sees the stills together and returns, for each, a
depth map, a camera pose and intrinsics in one frame, so the views agree with each other by construction.

The model is VGGT-1B (Wang et al., CVPR 2025), run from the upstream `vggt` package. Its weights are CC BY-NC
4.0 and its code is under the VGGT licence, both for research use, which a case study is. Measured against ARKit
and LiDAR on the 15 photo rooms of the assignment's walks, before this module was written: depth absrel
0.019-0.069 once one scale is fitted per room, one still's scale within 2-8% of its room's, relative rotations
within 2-4 degrees in 10 rooms. In the other 5 a still came back posed 64-177 degrees wrong.

Its output has no metric scale; `metric_scale.py` supplies that.

Around the model this module does three things:

* **Input.** Each image is resized so its long side is 518 px and padded to a square, the model's own "pad"
  preprocessing, so a portrait and a landscape still go in whole. The padding is cropped off every output and
  the intrinsics are shifted to match.
* **Memory.** Global attention runs over every token of every image. Computed a block of queries at a time the
  result is identical and the attention matrix for 8 images at 518 px no longer has to exist at once.
* **A check that needs no ground truth.** A still is stored upright, so its image's down axis is gravity give or
  take the photographer's tilt. A still whose down axis disagrees with the others' by more than
  `DOWN_AXIS_TOLERANCE_DEG` was posed wrong, and the room is reconstructed again without it.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger("cozmo.recon.multiview")

VGGT_SIZE = 518
VGGT_PATCH = 14
ATTENTION_CHUNK = 2048
# Only a still turned sideways or upside down is this far from up. The stills VGGT-1B posed wrong on the assignment's
# walks are turned about the vertical and sit 5-38 degrees from up, as the right ones do from the phone's tilt, so no
# tolerance separates them (fixloop/round4/evidence/pose_checks.json); a tighter one would drop a still aimed at the
# floor or the ceiling.
DOWN_AXIS_TOLERANCE_DEG = 75.0
# How much the down axes count against the x axes in `level_up`: enough to break a tie, too little to pull up
# towards the way the camera was tipped. 0.02 and 0.1 were compared on the assignment's walks; 0.02 was the better.
DOWN_AXIS_WEIGHT = 0.02
WEIGHTS_SUBDIR = "vggt-1b"


@dataclass
class ViewGeometry:
    """One image as the model reconstructed it, padding removed. Lengths are in the model's own units."""

    depth: np.ndarray
    confidence: np.ndarray
    intrinsics: np.ndarray
    rotation_wc: np.ndarray
    centre: np.ndarray
    image_size: tuple[int, int]

    @property
    def down_axis(self) -> np.ndarray:
        return self.rotation_wc[:, 1]


@dataclass
class MultiviewReconstruction:
    views: list[ViewGeometry]
    seconds: float
    backbone: str = "vggt-1b"


def square_padding(width: int, height: int, size: int = VGGT_SIZE, patch: int = VGGT_PATCH) -> tuple[int, int, int, int]:
    """(top, left, height, width) of an image scaled so its long side is `size`, inside a `size` square.

    The short side is rounded to a multiple of the patch size, as the model's own preprocessing does, so the
    scaling differs between the axes by under 1%; the intrinsics the model returns are for this grid.
    """
    if width >= height:
        new_width = size
        new_height = int(round(height * size / width / patch)) * patch
    else:
        new_height = size
        new_width = int(round(width * size / height / patch)) * patch
    return (size - new_height) // 2, (size - new_width) // 2, new_height, new_width


def pad_to_square(rgb: np.ndarray, size: int = VGGT_SIZE, patch: int = VGGT_PATCH) -> tuple[np.ndarray, tuple[int, int, int, int]]:
    height, width = rgb.shape[:2]
    top, left, new_height, new_width = square_padding(width, height, size, patch)
    canvas = np.ones((size, size, 3), np.float32)
    resized = cv2.resize(rgb, (new_width, new_height), interpolation=cv2.INTER_CUBIC)
    canvas[top:top + new_height, left:left + new_width] = resized.astype(np.float32) / 255.0
    return canvas, (top, left, new_height, new_width)


def crop_views(
    depth: np.ndarray,
    confidence: np.ndarray,
    extrinsic: np.ndarray,
    intrinsic: np.ndarray,
    boxes: list[tuple[int, int, int, int]],
    sizes: list[tuple[int, int]],
) -> list[ViewGeometry]:
    """Per-image geometry from the model's square outputs. Extrinsics are OpenCV camera-from-world."""
    views = []
    for i, (top, left, height, width) in enumerate(boxes):
        k = np.array(intrinsic[i], dtype=np.float64)
        k[0, 2] -= left
        k[1, 2] -= top
        rotation_cw = np.array(extrinsic[i][:, :3], dtype=np.float64)
        rotation_wc = rotation_cw.T
        views.append(
            ViewGeometry(
                depth=np.ascontiguousarray(depth[i, top:top + height, left:left + width], dtype=np.float32),
                confidence=np.ascontiguousarray(confidence[i, top:top + height, left:left + width], dtype=np.float32),
                intrinsics=k,
                rotation_wc=rotation_wc,
                centre=-rotation_wc @ np.array(extrinsic[i][:, 3], dtype=np.float64),
                image_size=tuple(sizes[i]),
            )
        )
    return views


def level_up(views: list[ViewGeometry], down_weight: float = DOWN_AXIS_WEIGHT) -> np.ndarray:
    """The up direction that every camera's x axis is perpendicular to.

    A phone held upright keeps the horizontal of its image level, give or take a few degrees of roll, however far it
    is tipped up or down. So the cameras' x axes lie in the horizontal plane and up is perpendicular to all of them.
    The down axes are a poor estimate on their own because people tip the camera down: on the assignment's walks
    their mean was 1.9-28.9 degrees from ARKit's gravity per photo room, against 0.8-4.9 degrees this way
    (`fixloop/round4/evidence/gravity.json`). They only break the tie when every x axis is parallel.
    """
    xs = np.array([view.rotation_wc[:, 0] for view in views], dtype=np.float64)
    downs = np.array([view.down_axis for view in views], dtype=np.float64)
    _, vectors = np.linalg.eigh(xs.T @ xs - down_weight * downs.T @ downs)
    up = vectors[:, 0]
    return -up if up @ downs.mean(axis=0) > 0 else up


def down_axis_outliers(views: list[ViewGeometry], tolerance_deg: float = DOWN_AXIS_TOLERANCE_DEG) -> tuple[np.ndarray, list[int]]:
    """Gravity as the stills' agreed up direction, and the stills that do not agree with it.

    Up is found from every still (`level_up`); stills whose down axis is beyond the tolerance from it are left out
    and up found again, so one still turned upside down does not drag the estimate towards itself.
    """
    downs = np.array([view.down_axis for view in views], dtype=np.float64)
    up = level_up(views)
    for _ in range(5):
        angles = np.degrees(np.arccos(np.clip(-downs @ up, -1.0, 1.0)))
        agreeing = angles <= tolerance_deg
        if agreeing.sum() < 2:
            break
        refined = level_up([views[i] for i in np.flatnonzero(agreeing)])
        if np.allclose(refined, up, atol=1e-9):
            break
        up = refined
    angles = np.degrees(np.arccos(np.clip(-downs @ up, -1.0, 1.0)))
    return up, [int(i) for i in np.flatnonzero(angles > tolerance_deg)]


def use_chunked_attention(chunk: int = ATTENTION_CHUNK) -> None:
    """Compute VGGT's attention a block of queries at a time. Exact: each query's softmax is over all keys."""
    import torch
    import torch.nn.functional as F
    from vggt.layers import attention as vggt_attention

    cls = vggt_attention.Attention
    if getattr(cls, "_cozmo_chunk", None) == chunk:
        return
    original = getattr(cls, "_cozmo_original_forward", cls.forward)

    def forward(self, x, pos=None):
        batch, tokens, channels = x.shape
        qkv = self.qkv(x).reshape(batch, tokens, 3, self.num_heads, self.head_dim).permute(2, 0, 3, 1, 4)
        q, k, v = qkv.unbind(0)
        q, k = self.q_norm(q), self.k_norm(k)
        if self.rope is not None:
            q, k = self.rope(q, pos), self.rope(k, pos)
        if tokens <= chunk:
            out = F.scaled_dot_product_attention(q, k, v)
        else:
            out = torch.cat(
                [F.scaled_dot_product_attention(q[:, :, start:start + chunk], k, v) for start in range(0, tokens, chunk)],
                dim=2,
            )
        return self.proj_drop(self.proj(out.transpose(1, 2).reshape(batch, tokens, channels)))

    cls._cozmo_original_forward = original
    cls._cozmo_chunk = chunk
    cls.forward = forward


class VGGTReconstructor:
    name = "vggt-1b"

    def __init__(self, weights: Path, device: str | None = None) -> None:
        import torch
        from safetensors.torch import load_file
        from vggt.models.vggt import VGGT

        use_chunked_attention()
        self._torch = torch
        self.device = device or ("mps" if torch.backends.mps.is_available() else "cuda" if torch.cuda.is_available() else "cpu")
        # The point and track heads are not used: depth with the camera is the more accurate of the model's two
        # routes to geometry, and the unused heads only cost memory.
        model = VGGT(enable_point=False, enable_track=False)
        missing, _ = model.load_state_dict(load_file(str(weights)), strict=False)
        if missing:
            raise RuntimeError(f"{weights} is missing {len(missing)} tensors of the camera and depth heads")
        self._model = model.to(self.device).eval()

    def reconstruct(self, images: list[np.ndarray]) -> MultiviewReconstruction:
        torch = self._torch
        from vggt.utils.pose_enc import pose_encoding_to_extri_intri

        if len(images) < 2:
            raise ValueError("a joint reconstruction needs at least two images")
        padded = [pad_to_square(image) for image in images]
        batch = torch.stack([torch.from_numpy(canvas).permute(2, 0, 1) for canvas, _ in padded]).to(self.device)
        started = time.perf_counter()
        with torch.no_grad():
            output = self._model(batch)
        extrinsic, intrinsic = pose_encoding_to_extri_intri(output["pose_enc"], batch.shape[-2:])
        views = crop_views(
            output["depth"][0, ..., 0].float().cpu().numpy(),
            output["depth_conf"][0].float().cpu().numpy(),
            extrinsic[0].float().cpu().numpy(),
            intrinsic[0].float().cpu().numpy(),
            [box for _, box in padded],
            [(image.shape[1], image.shape[0]) for image in images],
        )
        seconds = time.perf_counter() - started
        del output, batch
        if self.device == "mps":
            torch.mps.empty_cache()
        return MultiviewReconstruction(views, seconds, self.name)


_BACKBONES: dict[str, VGGTReconstructor] = {}


def get_multiview_backbone(weights_dir: Path) -> VGGTReconstructor | None:
    """The joint reconstruction model, loaded once per process, or None when its weights or package are missing."""
    path = Path(weights_dir) / WEIGHTS_SUBDIR / "model.safetensors"
    if not path.exists():
        log.warning("no VGGT weights at %s; run scripts/fetch_weights.sh", path)
        return None
    try:
        import vggt  # noqa: F401
    except ImportError:
        log.warning("the vggt package is not installed; run scripts/setup.sh")
        return None
    key = str(path.resolve())
    if key not in _BACKBONES:
        _BACKBONES[key] = VGGTReconstructor(path)
    return _BACKBONES[key]


def get_joint_models(weights_dir: Path):
    """VGGT-1B and MoGe-2 as a pair, or None when either is missing: one without the other reconstructs nothing."""
    from cozmo.recon.metric_scale import get_metric_depth

    backbone = get_multiview_backbone(weights_dir)
    if backbone is None:
        return None
    metric = get_metric_depth(weights_dir)
    return None if metric is None else (backbone, metric)


@dataclass
class StillsReconstruction:
    frames: list
    images: dict[int, np.ndarray]
    scale: object
    up: np.ndarray
    kept: list[int]
    dropped: list[int]
    notes: list[str]
    seconds: float


def reconstruct_stills(
    images: list[np.ndarray],
    fovs_deg: list[float | None],
    backbone,
    metric_model,
    tolerance_deg: float = DOWN_AXIS_TOLERANCE_DEG,
    first_index: int = 0,
    max_rounds: int = 3,
) -> tuple[StillsReconstruction | None, list[str]]:
    """Upright stills of one space to metric, gravity-aligned frames, or None and the reason.

    Stills whose down axis disagrees with the others' are left out and the rest reconstructed again, for up to
    `max_rounds` reconstructions; fewer than two stills left is no reconstruction. The scale is MoGe-2's, given
    each still's field of view where it is known.
    """
    from cozmo.recon.metric_scale import fov_source, scale_from_metric_depth

    started = time.perf_counter()
    kept = list(range(len(images)))
    dropped: list[int] = []
    notes: list[str] = []
    if len(kept) < 2:
        return None, ["fewer than two stills"]
    result = backbone.reconstruct([images[i] for i in kept])
    up, outliers = down_axis_outliers(result.views, tolerance_deg)
    rounds = 1
    while outliers and rounds < max_rounds:
        leaving = [kept[i] for i in outliers]
        if len(kept) - len(leaving) < 2:
            return None, notes + [f"{len(leaving)} of {len(kept)} stills disagree on which way is down; too few left to reconstruct"]
        notes.append(
            f"{len(leaving)} of {len(kept)} stills came back with their down axis more than {tolerance_deg:.0f} deg from "
            "the others'; the room was reconstructed again without them"
        )
        dropped.extend(leaving)
        kept = [i for i in kept if i not in leaving]
        result = backbone.reconstruct([images[i] for i in kept])
        up, outliers = down_axis_outliers(result.views, tolerance_deg)
        rounds += 1
    if outliers:
        notes.append(f"{len(outliers)} still(s) still disagree on which way is down after {rounds} reconstructions")

    metric = [metric_model.estimate(images[i], fovs_deg[i]) for i in kept]
    scale = scale_from_metric_depth(result.views, metric, fov_source([fovs_deg[i] for i in kept]))
    if scale is None:
        return None, notes + ["the metric depth model gave too few valid pixels to scale the reconstruction"]
    frames, colours = views_to_frames(result.views, scale.factor, up, first_index, [images[i] for i in kept])
    return StillsReconstruction(frames, colours, scale, up, kept, dropped, notes, time.perf_counter() - started), notes


def views_to_frames(views: list[ViewGeometry], scale: float, up: np.ndarray, first_index: int = 0,
                    images: list[np.ndarray] | None = None):
    """Metric, gravity-aligned frames for the reconstruction core, and each frame's colour at the depth's size.

    Depth and camera centres are multiplied by `scale`; the world is turned so `up` is +y, as the core expects.
    The model's confidence becomes the core's three classes by rank within each view (lowest 30% dropped by
    fusion) and a depth sigma of 2-8% of range.
    """
    from cozmo.io.base import Frame, Provenance
    from cozmo.util.transforms import gravity_align, make_pose

    align = gravity_align(np.asarray(up, dtype=np.float64))
    frames = []
    colours: dict[int, np.ndarray] = {}
    for i, view in enumerate(views):
        depth = (view.depth * float(scale)).astype(np.float32)
        ranks = np.argsort(np.argsort(view.confidence.ravel())).reshape(view.confidence.shape) / max(view.confidence.size - 1, 1)
        classes = np.zeros(depth.shape, np.uint8)
        classes[ranks >= 0.3] = 1
        classes[ranks >= 0.6] = 2
        sigma = (depth * (0.02 + 0.06 * (1.0 - ranks))).astype(np.float32)
        height, width = depth.shape
        index = first_index + i
        frames.append(
            Frame(
                index=index,
                timestamp=float(index),
                k_depth=view.intrinsics,
                k_rgb=view.intrinsics,
                rgb_size=(width, height),
                depth=depth,
                depth_sigma=sigma,
                confidence=classes,
                pose=make_pose(align @ view.rotation_wc, align @ (view.centre * float(scale))),
                depth_provenance=Provenance.PREDICTED,
                pose_provenance=Provenance.ESTIMATED,
            )
        )
        if images is not None:
            colours[index] = cv2.resize(images[i], (width, height), interpolation=cv2.INTER_AREA)
    return frames, colours
