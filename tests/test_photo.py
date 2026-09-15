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


def test_a_portrait_jpeg_is_read_upright(tmp_path: Path):
    """A portrait iPhone still is stored sideways with EXIF orientation 6; it must reach the pipeline upright."""
    from PIL import Image

    from cozmo.io.discover import read_image

    sensor = np.zeros((48, 64, 3), np.uint8)
    sensor[:, 52:] = 255
    exif = Image.Exif()
    exif[0x0112] = 6
    Image.fromarray(sensor).save(tmp_path / "IMG_0001.jpg", exif=exif)
    upright = read_image(tmp_path / "IMG_0001.jpg")
    assert upright.shape[:2] == (64, 48)
    assert upright[54:].mean() > 200


def test_a_heic_still_is_decoded(tmp_path: Path):
    pillow_heif = pytest.importorskip("pillow_heif")
    from PIL import Image

    from cozmo.io.discover import read_image

    pillow_heif.register_heif_opener()
    image = np.zeros((48, 64, 3), np.uint8)
    image[:, 52:] = 255
    try:
        Image.fromarray(image).save(tmp_path / "IMG_0001.heic", quality=95)
    except Exception as exc:  # a libheif built without an encoder can read HEIC but not write one
        pytest.skip(f"cannot write HEIC here: {exc}")
    decoded = read_image(tmp_path / "IMG_0001.heic")
    assert decoded.shape == (48, 64, 3)
    assert decoded[:, 56:].mean() > 180 and decoded[:, :40].mean() < 60


def test_the_heic_reader_applies_the_exif_orientation(tmp_path: Path):
    """libheif turns an iPhone HEIC by its own transform boxes; a still whose EXIF still says 6 is turned here.

    pillow-heif drops the orientation tag when it writes a HEIC, so the branch is exercised with a JPEG named
    .heic: Pillow opens a file by its content, and read_image picks its reader by extension.
    """
    pytest.importorskip("pillow_heif")
    from PIL import Image

    from cozmo.io.discover import read_image

    sensor = np.zeros((48, 64, 3), np.uint8)
    sensor[:, 52:] = 255
    exif = Image.Exif()
    exif[0x0112] = 6
    Image.fromarray(sensor).save(tmp_path / "IMG_0002.heic", format="JPEG", exif=exif)
    upright = read_image(tmp_path / "IMG_0002.heic")
    assert upright.shape[:2] == (64, 48)
    assert upright[54:].mean() > 180


def test_device_model_from_empty_list():
    """An empty list should return 'unknown'."""
    result = device_model_from_exif([])
    assert result == "unknown"



def test_a_stitched_photo_room_renames_every_id_inside_it():
    """Each folder comes back as room_01; its walls, surfaces and openings must follow the new id."""
    from cozmo.pipeline.photo import _rekey_room
    from cozmo.schema import Measure, Opening, OpeningType, Plane, Room, Surface, SurfaceType, Wall

    metre = Measure(value=1.0, lo=0.9, hi=1.1, unit="m")
    plane = Plane(normal=(1.0, 0.0, 0.0), offset=0.0)
    room = Room(
        room_id="room_01",
        label="room",
        polygon=[(0, 0), (2, 0), (0, 2)],
        walls=[Wall(wall_id="room_01_w00", surface_id="room_01_s00", start=(0.0, 0.0), end=(2.0, 0.0), length=metre, plane=plane, point_support=5)],
        surfaces=[Surface(surface_id="room_01_s00", room_id="room_01", type=SurfaceType.WALL, plane=plane)],
        openings=[
            Opening(
                opening_id="room_01_o00", type=OpeningType.DOOR, wall_id="room_01_w00", width=metre, height=metre,
                sill_height=Measure(value=0.0, lo=0.0, hi=0.0, unit="m"), offset_along_wall=metre, detection_confidence=0.8,
            )
        ],
        floor_area=Measure(value=2.0, lo=1.9, hi=2.1, unit="m2"),
        perimeter=metre,
        observation_quality=0.5,
    )
    moved = _rekey_room(room, "room_03", "passage")
    assert (moved.room_id, moved.label) == ("room_03", "passage")
    assert [(w.wall_id, w.surface_id) for w in moved.walls] == [("room_03_w00", "room_03_s00")]
    assert [(s.surface_id, s.room_id) for s in moved.surfaces] == [("room_03_s00", "room_03")]
    assert [(o.opening_id, o.wall_id) for o in moved.openings] == [("room_03_o00", "room_03_w00")]
