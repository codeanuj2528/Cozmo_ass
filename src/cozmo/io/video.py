"""Video-tier capture loader.

Extracts keyframes from a handheld walkthrough video, filters out blurry frames, and estimates camera movement.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterator, List, Optional

import cv2
import numpy as np

from cozmo.io.discover import find_videos
from cozmo.io.base import CaptureMeta, CaptureSource, Frame
from cozmo.schema import Tier

log = logging.getLogger("cozmo.io.video")

DEFAULT_BLUR_THRESHOLD = 50.0
DEFAULT_STRIDE_FRAMES = 5
DEFAULT_MAX_FRAMES = 30


class VideoCapture(CaptureSource):
    """Reads frames from a handheld video clip."""

    def __init__(
        self,
        video_path: Path,
        capture_id: Optional[str] = None,
        device_model: str = "unknown",
        stride: int = DEFAULT_STRIDE_FRAMES,
        blur_threshold: float = DEFAULT_BLUR_THRESHOLD,
        max_frames: int = DEFAULT_MAX_FRAMES,
    ) -> None:
        self.video_path = Path(video_path)
        if self.video_path.is_dir():
            candidates = find_videos(self.video_path)
            if candidates:
                self.video_path = candidates[0]
            else:
                raise FileNotFoundError(f"No video file (.mp4/.mov) found under {video_path}")

        self.cap = cv2.VideoCapture(str(self.video_path))
        if not self.cap.isOpened():
            raise ValueError(f"Could not open video at {self.video_path}")

        self.total_frames = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT))
        self.width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.fps = self.cap.get(cv2.CAP_PROP_FPS) or 30.0

        focal_px = max(self.width, self.height) * 0.82
        self.k_rgb = np.array(
            [[focal_px, 0.0, self.width / 2.0], [0.0, focal_px, self.height / 2.0], [0.0, 0.0, 1.0]]
        )

        self.meta = CaptureMeta(
            capture_id=capture_id or self.video_path.stem,
            tier=Tier.VIDEO,
            device_model=device_model,
            root=self.video_path.parent,
            frame_count=self.total_frames,
            notes={"resolution": f"{self.width}x{self.height}", "fps": str(self.fps)},
        )

        self._selected_indices = self._sample_keyframes(stride, blur_threshold, max_frames)

    def _sample_keyframes(self, stride: int, blur_threshold: float, max_frames: int) -> List[int]:
        indices: List[int] = []
        curr = 0
        while curr < self.total_frames:
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, curr)
            ret, frame = self.cap.read()
            if not ret or frame is None:
                break
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            laplacian_var = cv2.Laplacian(gray, cv2.CV_64F).var()
            if laplacian_var >= blur_threshold:
                indices.append(curr)
                if len(indices) >= max_frames:
                    break
            curr += stride
        if not indices:
            indices = list(range(0, min(self.total_frames, max_frames * stride), stride))
        return indices

    def frames(self, indices: Optional[List[int]] = None) -> Iterator[Frame]:
        # This class only identifies the clip. Depth and pose are predicted in
        # `pipeline.video.build_video_plan`, which reads the file itself. Inventing a
        # circular orbit of 2.5 m depths here used to silently produce a 500 m2 plan
        # if anything consumed these frames.
        raise RuntimeError(
            "VideoCapture.frames() does not produce depth or poses. "
            "Run the video tier through cozmo.pipeline.reconstruct / build_video_plan."
        )

    def load_rgb(self, frame: Frame) -> np.ndarray:
        return np.zeros((self.height, self.width, 3), dtype=np.uint8)

    def close(self) -> None:
        if getattr(self, "cap", None):
            self.cap.release()
            self.cap = None

    def __del__(self) -> None:
        self.close()
