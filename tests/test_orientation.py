"""Quarter turns between a stored frame and an upright one (util/orientation.py).

A walkthrough recorded by Stray Scanner is stored sideways when the phone was held in portrait. The photo
inputs made from it are turned upright the way the Camera app stores a still, and every model that is
sensitive to which way is up sees them that way. These tests pin the turn, the image rotation and the
intrinsics to one another, so a turned still projects a world point to the turned pixel.
"""

from __future__ import annotations

import numpy as np
import pytest

from cozmo.util.orientation import camera_roll, quarter_turns_upright, rotate_intrinsics, rotate_quarter

# A camera looking along world -z with gravity at the bottom of its frame: +x right, +y down (OpenCV).
UPRIGHT = np.array([[1.0, 0.0, 0.0], [0.0, -1.0, 0.0], [0.0, 0.0, -1.0]])


def _stored_rotation(turns: int) -> np.ndarray:
    """The camera whose stored frame needs `turns` clockwise quarter turns to become UPRIGHT."""
    return UPRIGHT @ camera_roll(turns).T


@pytest.mark.parametrize("turns", [0, 1, 2, 3])
def test_the_turn_read_from_the_pose_makes_the_frame_upright(turns: int):
    stored = _stored_rotation(turns)
    assert quarter_turns_upright(stored) == turns
    assert np.allclose(stored @ camera_roll(turns), UPRIGHT)


def test_a_small_tilt_does_not_change_the_turn():
    tilt = np.deg2rad(30.0)
    pitch = np.array([[1.0, 0.0, 0.0], [0.0, np.cos(tilt), -np.sin(tilt)], [0.0, np.sin(tilt), np.cos(tilt)]])
    for turns in range(4):
        assert quarter_turns_upright(pitch @ _stored_rotation(turns)) == turns


def test_a_clockwise_turn_sends_the_right_edge_to_the_bottom():
    image = np.zeros((4, 6), np.uint8)
    image[1, 5] = 255
    turned = rotate_quarter(image, 1)
    assert turned.shape == (6, 4)
    assert turned[5].max() == 255
    assert np.array_equal(rotate_quarter(turned, -1), image)
    assert np.array_equal(rotate_quarter(image, 4), image)


@pytest.mark.parametrize("turns", [1, 2, 3])
def test_turned_intrinsics_project_a_point_to_the_turned_pixel(turns: int):
    k = np.array([[1600.0, 0.0, 955.5], [0.0, 1590.0, 717.8], [0.0, 0.0, 1.0]])
    size = (1920, 1440)
    point = np.array([0.4, -0.3, 2.5])

    def project(kk, xyz):
        return np.array([kk[0, 0] * xyz[0] / xyz[2] + kk[0, 2], kk[1, 1] * xyz[1] / xyz[2] + kk[1, 2]])

    u, v = project(k, point)
    turned_k, turned_size = rotate_intrinsics(k, size, turns)
    turned_point = camera_roll(turns).T @ point
    expected = {1: (size[1] - 1 - v, u), 2: (size[0] - 1 - u, size[1] - 1 - v), 3: (v, size[0] - 1 - u)}[turns]
    assert np.allclose(project(turned_k, turned_point), expected, atol=1e-6)
    assert turned_size == ((size[1], size[0]) if turns % 2 else size)
