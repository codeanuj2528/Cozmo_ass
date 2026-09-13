"""Tests for photo tier capture loading and per-room folder ingestion."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from cozmo.io.photo import PhotoCapture, device_model_from_exif
from cozmo.schema import Tier


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_image(path: Path, w: int = 100, h: int = 100) -> Path:
    """Write a minimal JPEG image at the given path."""
    img = np.zeros((h, w, 3), dtype=np.uint8)
    cv2.imwrite(str(path), img)
    return path


# ---------------------------------------------------------------------------
# Single-room loading
# ---------------------------------------------------------------------------

def test_photo_capture_loading(tmp_path: Path):
    room1 = tmp_path / "room_01"
    room1.mkdir()
    _write_image(room1 / "img1.jpg")
    _write_image(room1 / "img2.jpg")

    cap = PhotoCapture(tmp_path, capture_id="test_photo")
    assert cap.meta.tier == Tier.PHOTO
    assert cap.meta.frame_count == 2

    frames = list(cap.frames())
    assert len(frames) == 2
    assert frames[0].rgb_size == (100, 100)


# ---------------------------------------------------------------------------
# Multi-room loading
# ---------------------------------------------------------------------------

def test_photo_capture_multi_room(tmp_path: Path):
    """Two room folders should be discovered as separate rooms."""
    for name in ("room_01", "room_02"):
        room = tmp_path / name
        room.mkdir()
        _write_image(room / "img1.jpg")
        _write_image(room / "img2.jpg")

    cap = PhotoCapture(tmp_path, capture_id="test_multi")
    assert cap.meta.tier == Tier.PHOTO
    assert len(cap.room_folders) == 2
    assert "room_01" in cap.room_folders
    assert "room_02" in cap.room_folders
    assert cap.meta.frame_count == 4  # 2 per room


def test_photo_capture_three_rooms(tmp_path: Path):
    """Three room folders should all be discovered."""
    for i in range(3):
        room = tmp_path / f"room_{i:02d}"
        room.mkdir()
        for j in range(3):
            _write_image(room / f"img_{j}.jpg")

    cap = PhotoCapture(tmp_path, capture_id="test_3rooms")
    assert len(cap.room_folders) == 3
    assert cap.meta.frame_count == 9


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_photo_capture_empty_room_folder_is_skipped(tmp_path: Path):
    """An empty subfolder should be skipped, not crash."""
    empty = tmp_path / "room_01"
    empty.mkdir()
    # room_01 has no images
    full = tmp_path / "room_02"
    full.mkdir()
    _write_image(full / "img1.jpg")
    _write_image(full / "img2.jpg")

    cap = PhotoCapture(tmp_path, capture_id="test_empty")
    assert "room_01" not in cap.room_folders
    assert "room_02" in cap.room_folders


def test_photo_capture_single_image_room(tmp_path: Path):
    """A room with only one image should still be loadable."""
    room = tmp_path / "room_01"
    room.mkdir()
    _write_image(room / "img1.jpg")

    cap = PhotoCapture(tmp_path, capture_id="test_single")
    assert cap.meta.frame_count == 1


def test_photo_capture_root_fallback(tmp_path: Path):
    """If no subdirectories exist, images in root should be used."""
    _write_image(tmp_path / "img1.jpg")
    _write_image(tmp_path / "img2.jpg")

    cap = PhotoCapture(tmp_path, capture_id="test_root")
    assert cap.meta.tier == Tier.PHOTO
    assert cap.meta.frame_count == 2


def test_photo_capture_no_images_raises(tmp_path: Path):
    """A directory with no images at all should raise."""
    (tmp_path / "readme.txt").write_text("nothing here")
    with pytest.raises((FileNotFoundError, ValueError)):
        PhotoCapture(tmp_path, capture_id="test_none")


# ---------------------------------------------------------------------------
# Hidden folders should be ignored
# ---------------------------------------------------------------------------

def test_photo_capture_ignores_dotfiles(tmp_path: Path):
    """Dot-prefixed directories should not be treated as rooms."""
    hidden = tmp_path / ".thumbnails"
    hidden.mkdir()
    _write_image(hidden / "thumb.jpg")

    room = tmp_path / "room_01"
    room.mkdir()
    _write_image(room / "img1.jpg")

    cap = PhotoCapture(tmp_path, capture_id="test_dot")
    assert ".thumbnails" not in cap.room_folders
    assert "room_01" in cap.room_folders


# ---------------------------------------------------------------------------
# EXIF device model extraction
# ---------------------------------------------------------------------------

def test_device_model_from_no_exif_images(tmp_path: Path):
    """Images written by OpenCV have no EXIF; result should be 'unknown'."""
    _write_image(tmp_path / "img1.jpg")
    result = device_model_from_exif([tmp_path / "img1.jpg"])
    assert result == "unknown"


def test_device_model_from_empty_list():
    """An empty list should return 'unknown'."""
    result = device_model_from_exif([])
    assert result == "unknown"
