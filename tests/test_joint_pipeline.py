"""The photo tier with the joint reconstruction wired in, end to end, with stand-ins for VGGT-1B and MoGe-2.

The stand-ins return the true geometry of a ray-cast room, in model units of half a metre: what is tested is
everything around the models (reading the stills, the scale, gravity, the frames handed to the reconstruction core,
the room the core makes of them), not the models.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from cozmo.config import PipelineConfig
from cozmo.io.photo import PhotoCapture
from cozmo.recon.multiview import MultiviewReconstruction, ViewGeometry

# A 4 x 3 m room with a 2.5 m ceiling, in the model's OpenCV-style frame: +y is down, the cameras are at y = 0,
# the floor 1.4 m below them. Lengths are stored in model units of 0.5 m.
ROOM = np.array([[-2.0, 2.0], [-1.1, 1.4], [-1.5, 1.5]])
UNIT_M = 0.5
WIDTH, HEIGHT, FOCAL = 120, 160, 90.0
K = np.array([[FOCAL, 0.0, WIDTH / 2], [0.0, FOCAL, HEIGHT / 2], [0.0, 0.0, 1.0]])


def _rotation(yaw_deg: float, pitch_deg: float) -> np.ndarray:
    a, b = np.deg2rad(yaw_deg), np.deg2rad(pitch_deg)
    yaw = np.array([[np.cos(a), 0.0, np.sin(a)], [0.0, 1.0, 0.0], [-np.sin(a), 0.0, np.cos(a)]])
    pitch = np.array([[1.0, 0.0, 0.0], [0.0, np.cos(b), -np.sin(b)], [0.0, np.sin(b), np.cos(b)]])
    return yaw @ pitch


def _view(rotation_wc: np.ndarray, centre_m: np.ndarray) -> ViewGeometry:
    rows, cols = np.mgrid[0:HEIGHT, 0:WIDTH]
    rays = np.stack([cols, rows, np.ones_like(cols)], axis=-1).reshape(-1, 3).astype(float) @ np.linalg.inv(K).T
    directions = rays @ rotation_wc.T
    hits = np.full(len(directions), np.inf)
    for axis in range(3):
        with np.errstate(divide="ignore", invalid="ignore"):
            for bound in ROOM[axis]:
                t = (bound - centre_m[axis]) / directions[:, axis]
                hits = np.where((t > 0) & (t < hits), t, hits)
    depth_m = (hits * rays[:, 2]).reshape(HEIGHT, WIDTH)
    confidence = np.random.default_rng(1).uniform(1.0, 5.0, depth_m.shape).astype(np.float32)
    return ViewGeometry((depth_m / UNIT_M).astype(np.float32), confidence, K, rotation_wc, centre_m / UNIT_M, (WIDTH, HEIGHT))


# Eight stills from near the middle of the room, 45 degrees apart and tipped 25 degrees down, as a phone is held.
VIEWS = [_view(_rotation(45.0 * i, -25.0), np.array([0.3 * np.cos(i), 0.0, 0.3 * np.sin(i)])) for i in range(8)]


def _index(image: np.ndarray) -> int:
    return int(round((float(image.mean()) - 10.0) / 20.0))


class _Backbone:
    name = "stand-in for vggt-1b"

    def reconstruct(self, images):
        return MultiviewReconstruction([VIEWS[_index(image)] for image in images], 0.0, self.name)


class _Metric:
    name = "stand-in for moge-2"

    def estimate(self, image, fov):
        return VIEWS[_index(image)].depth * UNIT_M


def _write_room(folder: Path) -> None:
    folder.mkdir(parents=True)
    for i in range(len(VIEWS)):
        cv2.imwrite(str(folder / f"IMG_{i:04d}.png"), np.full((HEIGHT, WIDTH, 3), 10 + 20 * i, np.uint8))


def test_the_photo_tier_builds_the_room_from_a_joint_reconstruction(tmp_path, monkeypatch):
    from cozmo.pipeline import photo

    _write_room(tmp_path / "living_room")
    monkeypatch.setattr("cozmo.recon.multiview.get_joint_models", lambda weights_dir: (_Backbone(), _Metric()))
    result = photo.build_photo_plan(PhotoCapture(tmp_path, capture_id="joint"), PipelineConfig(build_scope=False))
    plan = result.plan
    assert len(plan.rooms) == 1
    assert plan.rooms[0].floor_area.value == pytest.approx(12.0, rel=0.05)
    warnings = " ".join(plan.quality.warnings)
    assert "8 of 8 stills reconstructed together by stand-in for vggt-1b" in warnings
    assert "metric scale 0.500" in warnings
    assert "depth backbone: stand-in for vggt-1b with stand-in for moge-2 scale" in warnings


def test_without_the_models_the_photo_tier_says_it_fell_back(tmp_path, monkeypatch):
    from cozmo.pipeline import photo

    _write_room(tmp_path / "living_room")
    monkeypatch.setattr("cozmo.recon.multiview.get_joint_models", lambda weights_dir: None)
    calls = []

    def build_room_frames(paths, backbone, config, fallback_scale=None):
        calls.append(len(paths))
        return [], {}, [], ["stand-in: no geometry"], None

    monkeypatch.setattr(photo, "build_room_frames", build_room_frames)
    plan = photo.build_photo_plan(PhotoCapture(tmp_path, capture_id="fallback"), PipelineConfig(build_scope=False)).plan
    assert calls == [8]
    assert any("VGGT-1B or MoGe-2 is not installed" in w for w in plan.quality.warnings)


def test_the_ablation_switch_keeps_the_models_out(tmp_path, monkeypatch):
    from cozmo.pipeline import photo

    _write_room(tmp_path / "living_room")

    def fail(weights_dir):
        raise AssertionError("the joint models were asked for with multiview off")

    monkeypatch.setattr("cozmo.recon.multiview.get_joint_models", fail)
    monkeypatch.setattr(photo, "build_room_frames", lambda paths, backbone, config, fallback_scale=None: ([], {}, [], [], None))
    plan = photo.build_photo_plan(PhotoCapture(tmp_path, capture_id="ablation"), PipelineConfig(multiview=False)).plan
    assert not any("is not installed" in w for w in plan.quality.warnings)
