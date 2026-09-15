"""Metric scale for a multi-view reconstruction, from a depth model told the camera's field of view.

A joint reconstruction is right up to one unknown factor per room. A monocular model that predicts metric depth
supplies it, and which model matters more than anything else in the photo tier. Measured against LiDAR on the 15
photo rooms of the assignment's walks (fix loop round 4, `evidence/multiview.json`), the room scale each gives:

  Depth Anything V2 Metric Indoor, small     median +37.6%, range +9.3% to +53.1%
  Depth Anything V2 Metric Indoor, large     median +35.1%, range +22.0% to +64.5%
  MoGe-2 ViT-L, left to guess the view       median -7.3%, range -11.2% to +1.2%
  MoGe-2 ViT-L, given the field of view      median -4.5%, range -7.4% to +2.6%

A metric model infers distance from apparent size, and apparent size depends on the focal length. MoGe-2 (Wang et
al. 2025, MIT) accepts the horizontal field of view, which an iPhone still records in EXIF, so it does not have
to guess it. The Depth Anything models cannot be told, and on upright iPhone frames they over-predict.

The room's scale is the median, over its images, of each image's median ratio of MoGe-2 depth to the joint
reconstruction's depth. Its interval carries the model's error measured above, a 5% sigma, and the spread
between images.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger("cozmo.recon.metric_scale")

WEIGHTS_SUBDIR = "moge-2-vitl-normal"
# One sigma of the model's own error on a room's scale: the measured range above is -7.4% to +2.6%.
MODEL_RELATIVE_SIGMA = 0.05
# MoGe-2 sizes its own tokens; a still is given to it with its long side at this length.
INPUT_LONG_SIDE = 960
MIN_PIXELS = 200


@dataclass
class MetricScale:
    factor: float
    relative_uncertainty: float
    views_used: int
    per_view: list[float] = field(default_factory=list)
    source: str = "moge-2-vitl given the field of view"


def horizontal_fov_deg(k: np.ndarray, width: int) -> float:
    return float(np.degrees(2.0 * np.arctan(width / 2.0 / float(k[0, 0]))))


class MoGeDepth:
    name = "moge-2-vitl"

    def __init__(self, weights: Path, device: str | None = None) -> None:
        import torch
        from moge.model.v2 import MoGeModel

        self._torch = torch
        self.device = device or ("mps" if torch.backends.mps.is_available() else "cuda" if torch.cuda.is_available() else "cpu")
        self._model = MoGeModel.from_pretrained(str(weights)).to(self.device).eval()

    def estimate(self, rgb: np.ndarray, fov_x_deg: float | None) -> np.ndarray:
        """Metric depth in metres at the image's size; NaN where the model masks the image out."""
        torch = self._torch
        height, width = rgb.shape[:2]
        factor = min(1.0, INPUT_LONG_SIDE / max(height, width))
        small = cv2.resize(rgb, (int(round(width * factor)), int(round(height * factor))), interpolation=cv2.INTER_AREA) if factor < 1.0 else rgb
        image = torch.from_numpy(small.astype(np.float32) / 255.0).permute(2, 0, 1).to(self.device)
        with torch.no_grad():
            output = self._model.infer(image, fov_x=fov_x_deg, use_fp16=False)
        depth = output["depth"].float().cpu().numpy()
        mask = output.get("mask")
        if mask is not None:
            depth = np.where(mask.cpu().numpy().astype(bool), depth, np.nan)
        return cv2.resize(depth.astype(np.float32), (width, height), interpolation=cv2.INTER_LINEAR)


_MODELS: dict[str, MoGeDepth] = {}


def get_metric_depth(weights_dir: Path) -> MoGeDepth | None:
    path = Path(weights_dir) / WEIGHTS_SUBDIR / "model.pt"
    if not path.exists():
        log.warning("no MoGe-2 weights at %s; run scripts/fetch_weights.sh", path)
        return None
    try:
        import moge  # noqa: F401
    except ImportError:
        log.warning("the moge package is not installed; run scripts/setup.sh")
        return None
    key = str(path.resolve())
    if key not in _MODELS:
        _MODELS[key] = MoGeDepth(path)
    return _MODELS[key]


def fov_source(fovs: list[float | None], given: str = "from EXIF") -> str:
    """How MoGe-2 came by the field of view of the images a scale was read from."""
    known = sum(fov is not None for fov in fovs)
    if known == len(fovs):
        return f"moge-2-vitl given the field of view {given}"
    if known == 0:
        return "moge-2-vitl estimating the field of view"
    return f"moge-2-vitl given the field of view {given} for {known} of {len(fovs)} images"


def scale_from_metric_depth(views, metric_depths: list[np.ndarray], source: str = "moge-2-vitl") -> MetricScale | None:
    """Metres per model unit, from each view's median ratio of metric depth to the joint reconstruction's depth."""
    ratios = []
    for view, metric in zip(views, metric_depths):
        resized = cv2.resize(metric, (view.depth.shape[1], view.depth.shape[0]), interpolation=cv2.INTER_AREA)
        confident = view.confidence >= np.quantile(view.confidence, 0.3)
        valid = np.isfinite(resized) & (resized > 0.1) & (view.depth > 1e-6) & confident
        if valid.sum() >= MIN_PIXELS:
            ratios.append(float(np.median(resized[valid] / view.depth[valid])))
    if not ratios:
        return None
    factor = float(np.median(ratios))
    spread = 1.4826 * float(np.median(np.abs(np.asarray(ratios) - factor))) / factor
    uncertainty = float(np.hypot(MODEL_RELATIVE_SIGMA, spread / np.sqrt(len(ratios))))
    return MetricScale(factor, uncertainty, len(ratios), ratios)
