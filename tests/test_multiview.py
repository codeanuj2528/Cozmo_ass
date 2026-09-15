"""Joint reconstruction of several stills (recon/multiview.py) and its metric scale (recon/metric_scale.py).

Nothing here loads a model. The geometry around the model is what turns its output into the frames the
reconstruction core fuses, and each piece is checked against a case whose answer is known.
"""

from __future__ import annotations

import numpy as np
import pytest

from cozmo.recon.metric_scale import MODEL_RELATIVE_SIGMA, horizontal_fov_deg, scale_from_metric_depth
from cozmo.recon.multiview import (
    ViewGeometry,
    crop_views,
    down_axis_outliers,
    pad_to_square,
    square_padding,
    views_to_frames,
)
from cozmo.util.orientation import camera_roll

# A camera looking along world +z with gravity at the bottom of its frame, in a world whose +y is up.
LEVEL = np.array([[1.0, 0.0, 0.0], [0.0, -1.0, 0.0], [0.0, 0.0, 1.0]]) @ np.diag([1.0, 1.0, 1.0])


def _yaw(deg: float) -> np.ndarray:
    t = np.deg2rad(deg)
    return np.array([[np.cos(t), 0.0, np.sin(t)], [0.0, 1.0, 0.0], [-np.sin(t), 0.0, np.cos(t)]])


def _view(rotation_wc: np.ndarray, centre=(0.0, 0.0, 0.0), depth=None, confidence=None) -> ViewGeometry:
    depth = np.full((30, 40), 2.0, np.float32) if depth is None else depth
    confidence = np.ones_like(depth) if confidence is None else confidence
    k = np.array([[30.0, 0.0, 20.0], [0.0, 30.0, 15.0], [0.0, 0.0, 1.0]])
    return ViewGeometry(depth, confidence, k, rotation_wc, np.asarray(centre, float), (40, 30))


def test_a_landscape_and_a_portrait_still_are_padded_whole():
    assert square_padding(1920, 1440) == (63, 0, 392, 518)
    assert square_padding(1440, 1920) == (0, 63, 518, 392)
    canvas, box = pad_to_square(np.zeros((1920, 1440, 3), np.uint8))
    top, left, height, width = box
    assert canvas.shape == (518, 518, 3)
    assert np.all(canvas[top:top + height, left:left + width] == 0.0)
    assert np.all(canvas[:, :left] == 1.0) and np.all(canvas[:, left + width:] == 1.0)


def test_cropped_views_carry_the_pose_and_shifted_intrinsics():
    depth = np.arange(518 * 518, dtype=np.float32).reshape(1, 518, 518)
    rotation_cw = _yaw(30.0).T
    centre = np.array([1.0, 0.5, -2.0])
    extrinsic = np.concatenate([rotation_cw, (-rotation_cw @ centre)[:, None]], axis=1)[None]
    intrinsic = np.array([[[400.0, 0.0, 259.0], [0.0, 400.0, 259.0], [0.0, 0.0, 1.0]]])
    (view,) = crop_views(depth, depth, extrinsic, intrinsic, [(0, 63, 518, 392)], [(1440, 1920)])
    assert view.depth.shape == (518, 392)
    assert view.depth[0, 0] == depth[0, 0, 63]
    assert np.allclose(view.rotation_wc, _yaw(30.0))
    assert np.allclose(view.centre, centre)
    assert view.intrinsics[0, 2] == pytest.approx(259.0 - 63.0) and view.intrinsics[1, 2] == pytest.approx(259.0)


def test_a_still_posed_upside_down_disagrees_with_the_rest():
    upright = np.array([[1.0, 0.0, 0.0], [0.0, -1.0, 0.0], [0.0, 0.0, -1.0]])
    tilt = np.deg2rad(12.0)
    pitch = np.array([[1.0, 0.0, 0.0], [0.0, np.cos(tilt), -np.sin(tilt)], [0.0, np.sin(tilt), np.cos(tilt)]])
    views = [_view(_yaw(a) @ pitch @ upright) for a in (0, 60, 120, 180, 240, 300)]
    views.append(_view(_yaw(45) @ upright @ camera_roll(2)))
    up, outliers = down_axis_outliers(views)
    assert outliers == [6]
    assert np.degrees(np.arccos(up @ np.array([0.0, 1.0, 0.0]))) < 3.0


def test_frames_are_metric_and_gravity_aligned():
    # A VGGT world is the first camera's: +y is down. A floor 1.4 model units below that camera.
    k = np.array([[30.0, 0.0, 20.0], [0.0, 30.0, 15.0], [0.0, 0.0, 1.0]])
    rows, cols = np.mgrid[0:30, 0:40]
    depth = np.zeros((30, 40), np.float32)
    below = rows > 16
    depth[below] = 1.4 * 30.0 / (rows[below] - 15.0)
    first = ViewGeometry(depth, np.ones_like(depth), k, np.eye(3), np.zeros(3), (40, 30))
    second = ViewGeometry(depth, np.ones_like(depth), k, np.eye(3), np.array([0.5, 0.0, 0.0]), (40, 30))
    frames, colours = views_to_frames([first, second], scale=1.25, up=np.array([0.0, -1.0, 0.0]),
                                      images=[np.zeros((60, 80, 3), np.uint8)] * 2)
    from cozmo.util.transforms import backproject, transform_points

    points, _ = backproject(frames[0].depth, frames[0].k_depth, frames[0].depth > 0)
    world = transform_points(frames[0].pose, points)
    assert np.allclose(world[:, 1], -1.75, atol=1e-4)
    assert np.allclose(frames[1].pose[:3, 3], [0.625, 0.0, 0.0])
    assert colours[1].shape == (30, 40, 3)


def test_confidence_becomes_three_classes_and_a_depth_sigma():
    confidence = np.linspace(1.0, 10.0, 30 * 40, dtype=np.float32).reshape(30, 40)
    frames, _ = views_to_frames([_view(np.eye(3), confidence=confidence)], scale=1.0, up=np.array([0.0, -1.0, 0.0]))
    classes = frames[0].confidence
    assert set(np.unique(classes)) == {0, 1, 2}
    assert classes[0, 0] == 0 and classes[-1, -1] == 2
    assert frames[0].depth_sigma[-1, -1] < frames[0].depth_sigma[0, 0]


def test_the_scale_is_the_median_ratio_of_metric_to_model_depth():
    rng = np.random.default_rng(1)
    views, metric = [], []
    for true_ratio in (2.00, 2.04, 1.97, 2.02, 5.0):
        depth = rng.uniform(1.0, 4.0, (30, 40)).astype(np.float32)
        views.append(_view(np.eye(3), depth=depth))
        noisy = depth * true_ratio * (1.0 + rng.normal(0.0, 0.01, depth.shape))
        noisy[:5] = np.nan
        metric.append(np.kron(noisy, np.ones((2, 2))).astype(np.float32))
    scale = scale_from_metric_depth(views, metric)
    assert scale.views_used == 5
    assert scale.factor == pytest.approx(2.02, abs=0.02)
    assert scale.relative_uncertainty >= MODEL_RELATIVE_SIGMA


def test_no_scale_without_enough_valid_pixels():
    view = _view(np.eye(3))
    assert scale_from_metric_depth([view], [np.full((30, 40), np.nan, np.float32)]) is None


def test_the_field_of_view_comes_from_the_focal_length():
    k = np.array([[1600.0, 0.0, 720.0], [0.0, 1600.0, 960.0], [0.0, 0.0, 1.0]])
    assert horizontal_fov_deg(k, 1440) == pytest.approx(48.46, abs=0.01)


def test_chunked_attention_matches_the_model_s_own():
    torch = pytest.importorskip("torch")
    attention_module = pytest.importorskip("vggt.layers.attention")
    from cozmo.recon import multiview

    torch.manual_seed(0)
    layer = attention_module.Attention(dim=32, num_heads=4).eval()
    x = torch.randn(1, 300, 32)
    original = getattr(attention_module.Attention, "_cozmo_original_forward", attention_module.Attention.forward)
    with torch.no_grad():
        expected = original(layer, x)
        multiview.use_chunked_attention(64)
        try:
            actual = layer(x)
        finally:
            multiview.use_chunked_attention(multiview.ATTENTION_CHUNK)
    assert torch.allclose(actual, expected, atol=1e-5)
