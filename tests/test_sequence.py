"""Overlapping runs of keyframes joined into one frame (recon/sequence.py), and stills reconstructed with a check.

The runs are synthetic: one scene seen by a moving camera, each run given its own arbitrary similarity
transform, as a multi-view model's output has its own frame and scale per call.
"""

from __future__ import annotations

import numpy as np
import pytest

from cozmo.recon.multiview import ViewGeometry, reconstruct_stills
from cozmo.recon.sequence import chain_runs, chunk_ranges, shared_view_transform, transform_view, umeyama, view_points
from cozmo.util.orientation import camera_roll

HEIGHT, WIDTH = 120, 160
K = np.array([[120.0, 0.0, 80.0], [0.0, 120.0, 60.0], [0.0, 0.0, 1.0]])


def _rotation(yaw_deg: float, pitch_deg: float = 0.0) -> np.ndarray:
    a, b = np.deg2rad(yaw_deg), np.deg2rad(pitch_deg)
    yaw = np.array([[np.cos(a), 0.0, np.sin(a)], [0.0, 1.0, 0.0], [-np.sin(a), 0.0, np.cos(a)]])
    pitch = np.array([[1.0, 0.0, 0.0], [0.0, np.cos(b), -np.sin(b)], [0.0, np.sin(b), np.cos(b)]])
    return yaw @ pitch


def _box_view(rotation_wc: np.ndarray, centre: np.ndarray) -> ViewGeometry:
    """Depth of a 6 x 3 x 8 box (x in [-3, 3], y in [-1.5, 1.5], z in [-4, 4]) seen from inside, by ray casting."""
    rows, cols = np.mgrid[0:HEIGHT, 0:WIDTH]
    rays = np.stack([cols, rows, np.ones_like(cols)], axis=-1).reshape(-1, 3).astype(float) @ np.linalg.inv(K).T
    directions = rays @ rotation_wc.T
    bounds = np.array([[-3.0, 3.0], [-1.5, 1.5], [-4.0, 4.0]])
    hits = np.full(len(directions), np.inf)
    for axis in range(3):
        d = directions[:, axis]
        with np.errstate(divide="ignore", invalid="ignore"):
            for bound in bounds[axis]:
                t = (bound - centre[axis]) / d
                hits = np.where((t > 0) & (t < hits), t, hits)
    depth = (hits * rays[:, 2]).reshape(HEIGHT, WIDTH).astype(np.float32)
    confidence = np.random.default_rng(0).uniform(1.0, 5.0, depth.shape).astype(np.float32)
    return ViewGeometry(depth, confidence, K, rotation_wc, centre.astype(float), (WIDTH, HEIGHT))


def _similarity(scale, yaw_deg, shift):
    return scale, _rotation(yaw_deg), np.asarray(shift, float)


def test_chunk_ranges_cover_every_keyframe_with_overlap():
    assert chunk_ranges(5, 8, 3) == [[0, 1, 2, 3, 4]]
    ranges = chunk_ranges(20, 8, 3)
    assert ranges[0] == list(range(8)) and ranges[-1][-1] == 19
    assert all(len(r) == 8 for r in ranges)
    assert all(len(set(a) & set(b)) >= 3 for a, b in zip(ranges, ranges[1:]))
    with pytest.raises(ValueError):
        chunk_ranges(20, 8, 8)


def test_umeyama_recovers_a_similarity():
    rng = np.random.default_rng(2)
    points = rng.normal(size=(500, 3))
    scale, rotation, shift = _similarity(1.7, 35.0, (0.3, -2.0, 5.0))
    s, r, t = umeyama(points, (scale * points @ rotation.T) + shift)
    assert s == pytest.approx(1.7, rel=1e-9)
    assert np.allclose(r, rotation) and np.allclose(t, shift)


def test_one_image_reconstructed_twice_gives_the_transform_between_the_two_frames():
    view = _box_view(_rotation(20.0, -10.0), np.array([0.5, 0.2, -1.0]))
    scale, rotation, shift = _similarity(0.6, -50.0, (1.0, 0.0, 2.0))
    # The same image in a second frame: world points map by (s, R, t), so its depth scales and its pose moves.
    other = transform_view(view, scale, rotation, shift)
    s, r, t, pairs = shared_view_transform(other, view)
    assert pairs > 100
    assert s == pytest.approx(scale, rel=1e-4)
    assert np.allclose(r, rotation, atol=1e-5) and np.allclose(t, shift, atol=1e-4)
    assert np.allclose(view_points(other, view.depth > 0)[:5], (scale * view_points(view, view.depth > 0)[:5] @ rotation.T) + shift, atol=1e-4)


def test_runs_in_their_own_frames_are_chained_into_the_first():
    truth = [_box_view(_rotation(10.0 * i), np.array([0.2 * i, 0.0, -2.0 + 0.3 * i])) for i in range(13)]
    ranges = chunk_ranges(13, 6, 2)
    frames_of_run = [_similarity(1.0, 0.0, (0, 0, 0)), _similarity(1.8, 40.0, (2.0, 0.5, -1.0)),
                     _similarity(0.7, -25.0, (-1.0, 1.0, 3.0))]
    runs = []
    for r, indices in enumerate(ranges):
        s, rot, t = frames_of_run[r]
        runs.append([transform_view(truth[i], s, rot, t) for i in indices])
    chained = chain_runs(runs, ranges)
    assert sorted(chained.views) == list(range(13))
    for i in range(13):
        assert np.allclose(chained.views[i].centre, truth[i].centre, atol=1e-3)
        assert np.allclose(chained.views[i].rotation_wc, truth[i].rotation_wc, atol=1e-5)
        assert np.allclose(chained.views[i].depth, truth[i].depth, rtol=1e-3)
    assert chained.link_scales[0] == pytest.approx(1 / 1.8, rel=1e-3)


def test_a_run_that_shares_nothing_breaks_the_walk_and_the_longest_stretch_is_kept():
    truth = [_box_view(_rotation(10.0 * i), np.array([0.2 * i, 0.0, -2.0])) for i in range(13)]
    ranges = chunk_ranges(13, 6, 2)
    runs = [[truth[i] for i in indices] for indices in ranges]
    # The first run is confident only on odd pixels and the second run's copies of the two keyframes they share only
    # on even ones, so the two runs have no confident pixel in common.
    parity = np.arange(HEIGHT * WIDTH).reshape(HEIGHT, WIDTH) % 2

    def confident_on(view, odd):
        confidence = np.where(parity == odd, 10.0, 0.0).astype(np.float32)
        return ViewGeometry(view.depth, confidence, view.intrinsics, view.rotation_wc, view.centre, view.image_size)

    runs[0] = [confident_on(view, 1) for view in runs[0]]
    runs[1][:2] = [confident_on(view, 0) for view in runs[1][:2]]
    chained = chain_runs(runs, ranges)
    assert chained.breaks == [1]
    assert sorted(chained.views) == list(range(4, 13))


def test_keyframes_are_the_sharpest_frame_of_each_second(tmp_path):
    cv2 = pytest.importorskip("cv2")
    path = tmp_path / "walk.mp4"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 10, (160, 120))
    rng = np.random.default_rng(0)
    for i in range(40):
        frame = np.full((120, 160, 3), 128, np.uint8)
        if i % 10 == 4:  # one sharp frame in each second, the rest flat
            frame = rng.integers(0, 255, (120, 160, 3), dtype=np.uint8)
        writer.write(frame)
    writer.release()
    from cozmo.recon.sequence import sample_keyframes

    numbers, images, stats = sample_keyframes(path, interval_s=1.0, long_side=80)
    assert numbers == [4, 14, 24, 34]
    assert images[0].shape == (60, 80, 3)
    assert stats["window_frames"] == 10


class _Reframing:
    """A multi-view model stand-in: every call returns the true views in a frame and scale of its own."""

    def __init__(self, truth):
        self.truth, self.scales = truth, []

    def reconstruct(self, images):
        from cozmo.recon.multiview import MultiviewReconstruction

        rng = np.random.default_rng(len(self.scales))
        s, r, t = float(rng.uniform(0.5, 2.0)), _rotation(float(rng.uniform(-60, 60))), rng.normal(size=3)
        self.scales.append(s)
        return MultiviewReconstruction([transform_view(self.truth[int(im[0, 0, 0])], s, r, t) for im in images], 0.0)


def test_a_walk_reconstructed_in_runs_comes_back_metric_and_in_one_frame():
    from cozmo.recon.sequence import reconstruct_sequence
    from cozmo.util.transforms import backproject, transform_points

    # The truth is in OpenCV cameras, so the box's up is -y, and one unit of it is 1.6 m. The camera is tipped 5
    # degrees down throughout, which the mean of the down axes would have taken for a tilt of the world.
    truth = [_box_view(_rotation(8.0 * i, -5.0), np.array([0.15 * i, 0.0, -2.0 + 0.2 * i])) for i in range(20)]
    images = [np.full((HEIGHT, WIDTH, 3), i, np.uint8) for i in range(20)]

    class _Metric:
        def estimate(self, image, fov):
            return truth[int(image[0, 0, 0])].depth * 1.6

    backbone = _Reframing(truth)
    result, notes = reconstruct_sequence(images, backbone, _Metric(), run_length=8, overlap=3)
    assert len(result.frames) == 20
    # Metres per unit of the first run's frame, whatever scale the model gave that run.
    assert result.scale.factor == pytest.approx(1.6 / backbone.scales[0], rel=1e-3)
    centres = np.array([frame.pose[:3, 3] for frame in result.frames])
    walked = np.linalg.norm(centres - centres[0], axis=1)
    truth_walked = np.linalg.norm(np.array([view.centre for view in truth]) - truth[0].centre, axis=1)
    assert np.allclose(walked, 1.6 * truth_walked, atol=1e-3)
    # The box floor is 1.5 units below every camera: 2.4 m below it with +y up.
    first = result.frames[0]
    points, _ = backproject(first.depth, first.k_depth, first.depth > 0)
    heights = transform_points(first.pose, points)[:, 1] - first.pose[1, 3]
    assert np.min(heights) == pytest.approx(-2.4, abs=0.02)


class _FakeBackbone:
    """Returns views of the box; the still at `upside_down` comes back rolled half a turn."""

    def __init__(self, truth, upside_down=None):
        self.truth, self.upside_down, self.calls = truth, upside_down, []

    def reconstruct(self, images):
        indices = [int(image[0, 0, 0]) for image in images]
        self.calls.append(indices)
        views = []
        for i in indices:
            view = self.truth[i]
            if i == self.upside_down:
                view = ViewGeometry(view.depth, view.confidence, view.intrinsics, view.rotation_wc @ camera_roll(2), view.centre, view.image_size)
            views.append(view)
        from cozmo.recon.multiview import MultiviewReconstruction

        return MultiviewReconstruction(views, 0.0)


class _FakeMetric:
    def __init__(self, truth, ratio):
        self.truth, self.ratio = truth, ratio

    def estimate(self, image, fov):
        return self.truth[int(image[0, 0, 0])].depth * self.ratio


def test_a_still_posed_upside_down_is_left_out_and_the_room_reconstructed_again():
    # Model frame: OpenCV cameras (+y down), so the box's up is -y there.
    truth = [_box_view(_rotation(60.0 * i, 8.0 * (-1) ** i), np.array([0.3 * i, 0.0, 0.0])) for i in range(6)]
    images = [np.full((HEIGHT, WIDTH, 3), i, np.uint8) for i in range(6)]
    backbone = _FakeBackbone(truth, upside_down=4)
    result, notes = reconstruct_stills(images, [None] * 6, backbone, _FakeMetric(truth, 2.5))
    assert backbone.calls == [[0, 1, 2, 3, 4, 5], [0, 1, 2, 3, 5]]
    assert result.dropped == [4] and result.kept == [0, 1, 2, 3, 5]
    assert result.scale.factor == pytest.approx(2.5)
    assert np.degrees(np.arccos(result.up @ np.array([0.0, -1.0, 0.0]))) < 3.0
    assert "reconstructed again" in notes[0]
    assert len(result.frames) == 5


def test_too_few_agreeing_stills_is_no_reconstruction():
    truth = [_box_view(_rotation(90.0 * i), np.zeros(3)) for i in range(3)]
    images = [np.full((HEIGHT, WIDTH, 3), i, np.uint8) for i in range(3)]

    class _Scrambled(_FakeBackbone):
        def reconstruct(self, images):
            out = super().reconstruct(images)
            for j, view in enumerate(out.views):
                view.rotation_wc = view.rotation_wc @ camera_roll(j % 4)
            return out

    result, notes = reconstruct_stills(images, [None] * 3, _Scrambled([
        ViewGeometry(v.depth, v.confidence, v.intrinsics, v.rotation_wc.copy(), v.centre, v.image_size) for v in truth
    ]), _FakeMetric(truth, 1.0))
    assert result is None
    assert "too few" in notes[-1] or "disagree" in notes[-1]
