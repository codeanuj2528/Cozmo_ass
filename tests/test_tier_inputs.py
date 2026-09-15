"""The photo and video inputs made from a LiDAR capture, and the depth-scale measurement.

`scripts/make_tier_inputs.py` chooses the stills that stand in for a photographer and writes them the way
an iPhone does; `scripts/measure_depth_scale.py` compares a depth model with LiDAR. The choices they make
are what the photo- and video-tier numbers rest on, so they are tested here rather than trusted.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def tier_inputs():
    return _load("make_tier_inputs")


@pytest.fixture(scope="module")
def depth_scale():
    return _load("measure_depth_scale")


def test_the_number_of_stills_stays_inside_the_brief(tier_inputs):
    counts = [tier_inputs.stills_for_area(a) for a in (2.5, 3.4, 3.5, 5.9, 6.0, 40.0)]
    assert counts == [4, 4, 6, 6, 8, 8]
    assert all(2 <= c <= 8 for c in counts)


def test_the_focal_length_is_the_35mm_equivalent_on_the_long_edge(tier_inputs):
    assert tier_inputs.focal_35mm(1600.0, 1920, 1440) == 30
    assert tier_inputs.focal_35mm(1600.0, 1440, 1920) == 30


def test_a_written_still_carries_its_focal_length_where_an_iphone_puts_it(tier_inputs, tmp_path):
    from cozmo.recon.monocular import _focal_from_exif, intrinsics_from_exif

    path = tmp_path / "IMG_000001.jpg"
    tier_inputs.save_still(path, np.full((1920, 1440, 3), 128, np.uint8), 30)
    assert _focal_from_exif(path) == (30.0, "exif_sub_ifd_35mm_equivalent")
    k, source = intrinsics_from_exif(path, 1440, 1920)
    assert source == "exif_sub_ifd_35mm_equivalent"
    assert k[0, 0] == pytest.approx(1600.0)


def _candidate(frame: int, azimuth_deg: float, sharpness: float, xz=(0.0, 0.0)) -> dict:
    return {"frame": frame, "azimuth": float(np.deg2rad(azimuth_deg)), "sharpness": sharpness, "xz": list(xz)}


def test_stills_are_spread_over_the_room_starting_from_the_sharpest(tier_inputs):
    # Six sharp frames, four blurred ones pointing where the sharp ones do not. The blurred ones fall
    # below the sharpness cut however well they would spread the set.
    candidates = [
        _candidate(10, 0, 90), _candidate(11, 4, 95), _candidate(12, 8, 88),
        _candidate(20, 92, 85), _candidate(30, 181, 86), _candidate(40, 268, 87),
        _candidate(50, 45, 5), _candidate(60, 135, 4), _candidate(70, 225, 3), _candidate(80, 315, 2),
    ]
    chosen = tier_inputs.choose_spread(candidates, 4)
    frames = [c["frame"] for c in chosen]
    assert frames == [11, 20, 30, 40]


def test_a_room_with_few_frames_keeps_them_all(tier_inputs):
    candidates = [_candidate(3, 0, 10), _candidate(1, 90, 20)]
    assert [c["frame"] for c in tier_inputs.choose_spread(candidates, 6)] == [3, 1]


def test_the_video_is_tagged_with_the_turn_most_of_the_walk_was_held_at(tier_inputs):
    from cozmo.util.orientation import camera_roll

    upright = np.array([[1.0, 0.0, 0.0], [0.0, -1.0, 0.0], [0.0, 0.0, -1.0]])
    poses = np.stack([np.eye(4)] * 10)
    for i in range(10):
        poses[i, :3, :3] = upright @ camera_roll(1 if i < 7 else 0).T
    assert tier_inputs.video_turns(poses) == (1, pytest.approx(0.7))


@pytest.mark.skipif(__import__("shutil").which("ffmpeg") is None, reason="needs ffmpeg")
def test_a_sideways_clip_decodes_upright_once_tagged(tier_inputs, tmp_path):
    import cv2

    clip = tmp_path / "clip.mp4"
    writer = cv2.VideoWriter(str(clip), cv2.VideoWriter_fourcc(*"mp4v"), 10, (64, 48))
    for _ in range(5):
        frame = np.zeros((48, 64, 3), np.uint8)
        frame[:, 52:] = 255
        writer.write(frame)
    writer.release()
    note = tier_inputs.write_tagged_video(clip, tmp_path / "video" / "walkthrough.mp4", 1)
    assert "display rotation -90" in note
    capture = cv2.VideoCapture(str(tmp_path / "video" / "walkthrough.mp4"))
    ok, frame = capture.read()
    capture.release()
    assert ok and frame.shape[:2] == (64, 48)
    assert frame[54:].mean() > 200 and frame[:40].mean() < 40


def test_depth_scale_is_the_median_ratio_and_the_shape_error_is_what_remains(depth_scale):
    lidar = np.full((192, 256), 2.0, np.float32)
    lidar[:, 128:] = 3.0
    frame = {"frame": 7, "depth": lidar, "confidence": np.full(lidar.shape, 2, np.uint8)}
    predicted = np.kron(lidar * 1.1, np.ones((2, 2), np.float32))
    row = depth_scale.compare(predicted, frame)
    assert row["ratio"] == pytest.approx(1.1, abs=1e-5)
    assert row["absrel"] == pytest.approx(0.0, abs=1e-5)


def test_depth_scale_ignores_pixels_arkit_is_not_sure_of(depth_scale):
    lidar = np.full((192, 256), 2.0, np.float32)
    confidence = np.full(lidar.shape, 2, np.uint8)
    confidence[:, :128] = 1
    predicted = lidar.copy()
    predicted[:, :128] = 9.0
    row = depth_scale.compare(predicted, {"frame": 1, "depth": lidar, "confidence": confidence})
    assert row["ratio"] == pytest.approx(1.0)
    assert depth_scale.compare(predicted, {"frame": 1, "depth": lidar, "confidence": np.zeros_like(confidence)}) is None
