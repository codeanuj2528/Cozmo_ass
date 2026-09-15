"""The video tier with the joint reconstruction wired in, end to end, with stand-ins for VGGT-1B and MoGe-2.

A clip of flat frames stands for the walk; each frame's grey level names the true view the stand-in model returns
for it, in a frame and scale of the model's own per run, as the real model's output is.
"""

from __future__ import annotations

import numpy as np
import pytest

from cozmo.config import PipelineConfig
from cozmo.recon.multiview import MultiviewReconstruction
from cozmo.recon.sequence import transform_view
from tests.test_joint_pipeline import UNIT_M, _rotation, _view

# Twenty keyframes turning on the spot near the middle of the 4 x 3 m room, 18 degrees apart, tipped 25 degrees down.
WALK = [_view(_rotation(18.0 * i, -25.0), np.array([0.2 * np.cos(0.3 * i), 0.0, 0.2 * np.sin(0.3 * i)])) for i in range(20)]


def _index(image: np.ndarray) -> int:
    return int(round((float(image.mean()) - 10.0) / 12.0))


class _Backbone:
    name = "stand-in for vggt-1b"

    def __init__(self):
        self.calls = 0

    def reconstruct(self, images):
        rng = np.random.default_rng(self.calls)
        self.calls += 1
        s, r, t = float(rng.uniform(0.5, 2.0)), _rotation(float(rng.uniform(-60, 60)), 0.0), rng.normal(size=3)
        return MultiviewReconstruction([transform_view(WALK[_index(image)], s, r, t) for image in images], 0.0, self.name)


class _Metric:
    name = "stand-in for moge-2"

    def estimate(self, image, fov):
        return WALK[_index(image)].depth * UNIT_M


def test_the_video_tier_builds_the_room_from_runs_of_keyframes(tmp_path, monkeypatch):
    cv2 = pytest.importorskip("cv2")
    from cozmo.io.video import VideoCapture
    from cozmo.pipeline.video import build_video_plan

    path = tmp_path / "walk.mp4"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 1, (120, 160))
    for i in range(len(WALK)):
        writer.write(np.full((160, 120, 3), 10 + 12 * i, np.uint8))
    writer.release()

    backbone = _Backbone()
    monkeypatch.setattr("cozmo.recon.multiview.get_joint_models", lambda weights_dir: (backbone, _Metric()))
    plan = build_video_plan(VideoCapture(path), PipelineConfig(build_scope=False, detect_damage=False)).plan
    assert backbone.calls == 4  # runs of 8 sharing 3 over 20 keyframes
    assert len(plan.rooms) == 1
    assert plan.rooms[0].floor_area.value == pytest.approx(12.0, rel=0.05)
    warnings = " ".join(plan.quality.warnings)
    assert "20 of 20 keyframes reconstructed by stand-in for vggt-1b in runs of 8 sharing 3" in warnings
