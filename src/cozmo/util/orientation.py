"""Quarter turns between the frame a phone stored and the upright frame a person would see.

A phone camera stores frames in sensor orientation. The iPhone Camera app records which way up the phone
was held, as the EXIF orientation of a still and the rotation of a video, and anything that displays the
frame applies it. Stray Scanner records no such tag, so a walkthrough held in portrait is stored sideways.
Its pose says where gravity points in the frame, and the nearest quarter turn to it is the one the Camera
app would have written.

Conventions are the codebase's own (`util/transforms.py`): OpenCV camera, +x right and +y down; world
gravity-aligned with +y up. A turn is clockwise.
"""

from __future__ import annotations

import cv2
import numpy as np

_ROTATE_CODES = {1: cv2.ROTATE_90_CLOCKWISE, 2: cv2.ROTATE_180, 3: cv2.ROTATE_90_COUNTERCLOCKWISE}


def quarter_turns_upright(rotation_wc: np.ndarray) -> int:
    """Clockwise quarter turns that put gravity at the bottom of a frame taken with this camera rotation."""
    down = np.asarray(rotation_wc, float).T @ np.array([0.0, -1.0, 0.0])
    dx, dy = float(down[0]), float(down[1])
    if abs(dy) >= abs(dx):
        return 0 if dy > 0 else 2
    return 1 if dx > 0 else 3


def rotate_quarter(image: np.ndarray, turns: int) -> np.ndarray:
    """The image turned clockwise by `turns` quarter turns; negative turns go anticlockwise."""
    turns %= 4
    return image if turns == 0 else cv2.rotate(image, _ROTATE_CODES[turns])


def camera_roll(turns: int) -> np.ndarray:
    """M with R_turned = R_stored @ M: the axes of the turned image's camera in the stored camera's frame.

    Turning an image clockwise sends its right edge to the bottom, so the turned camera's +y is the stored
    camera's +x and its +x is the stored camera's -y. The optical axis does not move.
    """
    turns %= 4
    if turns == 0:
        return np.eye(3)
    if turns == 1:
        return np.array([[0.0, 1.0, 0.0], [-1.0, 0.0, 0.0], [0.0, 0.0, 1.0]])
    if turns == 2:
        return np.diag([-1.0, -1.0, 1.0])
    return np.array([[0.0, -1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]])


def rotate_intrinsics(k: np.ndarray, size: tuple[int, int], turns: int) -> tuple[np.ndarray, tuple[int, int]]:
    """Pinhole intrinsics and (width, height) of an image of `size` after `turns` clockwise quarter turns."""
    turns %= 4
    w, h = size
    fx, fy, cx, cy = float(k[0, 0]), float(k[1, 1]), float(k[0, 2]), float(k[1, 2])
    if turns == 0:
        return np.array(k, float), (w, h)
    if turns == 1:
        fx, fy, cx, cy, w, h = fy, fx, (h - 1) - cy, cx, h, w
    elif turns == 2:
        cx, cy = (w - 1) - cx, (h - 1) - cy
    else:
        fx, fy, cx, cy, w, h = fy, fx, cy, (w - 1) - cx, h, w
    return np.array([[fx, 0.0, cx], [0.0, fy, cy], [0.0, 0.0, 1.0]]), (w, h)
