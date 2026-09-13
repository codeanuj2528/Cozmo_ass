"""Tests for video tier capture loading, blur filtering and frame selection."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from cozmo.io.video import DEFAULT_BLUR_THRESHOLD, DEFAULT_STRIDE_FRAMES, VideoCapture
from cozmo.schema import Tier


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_test_video(path: Path, n_frames: int = 20, w: int = 160, h: int = 120,
                      fps: float = 30.0, sharp: bool = True) -> Path:
    """Create a minimal .mp4 video file for testing."""
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(path), fourcc, fps, (w, h))
    rng = np.random.default_rng(42)
    for i in range(n_frames):
        if sharp:
            # High-frequency content → high Laplacian variance → not blurry
            frame = rng.integers(0, 255, (h, w, 3), dtype=np.uint8)
        else:
            # Uniform grey → zero Laplacian variance → blurry
            frame = np.full((h, w, 3), 128, dtype=np.uint8)
        writer.write(frame)
    writer.release()
    return path


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------

def test_video_capture_initialization(tmp_path: Path):
    """VideoCapture should raise on a missing file."""
    fake_video = tmp_path / "non_existent.mp4"
    with pytest.raises((FileNotFoundError, ValueError)):
        VideoCapture(fake_video)


def test_video_capture_loads_valid_file(tmp_path: Path):
    """VideoCapture should load a valid video and detect the video tier."""
    vid = _write_test_video(tmp_path / "test.mp4", n_frames=10)
    cap = VideoCapture(vid)
    assert cap.meta.tier == Tier.VIDEO
    assert cap.total_frames == 10
    assert cap.width == 160
    assert cap.height == 120
    cap.close()


def test_video_capture_detects_from_directory(tmp_path: Path):
    """When given a directory, VideoCapture should find the video inside it."""
    _write_test_video(tmp_path / "walkthrough.mp4", n_frames=5)
    cap = VideoCapture(tmp_path)
    assert cap.meta.tier == Tier.VIDEO
    assert cap.total_frames == 5
    cap.close()


def test_video_capture_raises_on_empty_directory(tmp_path: Path):
    """A directory with no video files should raise FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        VideoCapture(tmp_path)


# ---------------------------------------------------------------------------
# Blur filtering
# ---------------------------------------------------------------------------

def test_sharp_frames_are_selected(tmp_path: Path):
    """Frames with high Laplacian variance should pass the blur filter."""
    vid = _write_test_video(tmp_path / "sharp.mp4", n_frames=20, sharp=True)
    cap = VideoCapture(vid, blur_threshold=1.0, stride=1, max_frames=100)
    assert len(cap._selected_indices) > 0, "sharp frames should be selected"
    cap.close()


def test_blurry_frames_fall_back_to_uniform_sampling(tmp_path: Path):
    """If all frames are below the blur threshold, fallback to uniform stride."""
    vid = _write_test_video(tmp_path / "blurry.mp4", n_frames=20, sharp=False)
    cap = VideoCapture(vid, blur_threshold=99999.0, stride=5, max_frames=10)
    # Fallback: list(range(0, min(total, max*stride), stride))
    assert len(cap._selected_indices) > 0, "fallback should produce indices"
    # Check that indices are uniformly spaced
    for i, idx in enumerate(cap._selected_indices):
        assert idx == i * 5
    cap.close()


# ---------------------------------------------------------------------------
# Stride and max_frames
# ---------------------------------------------------------------------------

def test_stride_controls_sampling_density(tmp_path: Path):
    """A larger stride should produce fewer or equal selected frames."""
    vid = _write_test_video(tmp_path / "test.mp4", n_frames=60, sharp=True)
    cap_dense = VideoCapture(vid, stride=1, blur_threshold=1.0, max_frames=100)
    cap_sparse = VideoCapture(vid, stride=10, blur_threshold=1.0, max_frames=100)
    assert len(cap_sparse._selected_indices) <= len(cap_dense._selected_indices)
    cap_dense.close()
    cap_sparse.close()


def test_max_frames_caps_selection(tmp_path: Path):
    """Selected frames should not exceed max_frames."""
    vid = _write_test_video(tmp_path / "test.mp4", n_frames=100, sharp=True)
    cap = VideoCapture(vid, stride=1, blur_threshold=1.0, max_frames=5)
    assert len(cap._selected_indices) <= 5
    cap.close()


# ---------------------------------------------------------------------------
# Default constants
# ---------------------------------------------------------------------------

def test_default_blur_threshold_is_positive():
    assert DEFAULT_BLUR_THRESHOLD > 0


def test_default_stride_is_positive():
    assert DEFAULT_STRIDE_FRAMES > 0


# ---------------------------------------------------------------------------
# frames() should not silently produce fake data
# ---------------------------------------------------------------------------

def test_frames_raises_rather_than_inventing_data(tmp_path: Path):
    """VideoCapture.frames() should raise, not produce fake depth/poses."""
    vid = _write_test_video(tmp_path / "test.mp4", n_frames=5)
    cap = VideoCapture(vid)
    with pytest.raises(RuntimeError, match="does not produce depth"):
        list(cap.frames())
    cap.close()
