"""Ray-traced Stray export of a box room whose every metre is known.

The public comparison submission uses the same idea: a 3.60 x 2.80 x 2.50 m box,
yawed off-axis so layout cannot cheat by assuming world-aligned walls, with a
door and a window cut out. This file is ours: depth is 256x192 to match
`cozmo.io.stray.DEPTH_SIZE`, and an unobserved ceiling is written as missing
returns, not as a 2.44 m prior.

    python -m tests.fixtures.raytrace_room

writes `tests/fixtures/captures/synthetic_room` and `synthetic_no_ceiling`.
"""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
CAPTURES = HERE / "captures"

ROOM_WIDTH = 3.60
ROOM_DEPTH = 2.80
CEILING_HEIGHT = 2.50
ROOM_YAW_DEG = 23.0

DOOR = {"face": "z_min", "u0": 1.10, "u1": 1.95, "v0": 0.00, "v1": 2.03}
WINDOW = {"face": "x_max", "u0": 0.85, "u1": 1.95, "v0": 0.90, "v1": 2.10}

# Must match cozmo.io.stray.DEPTH_SIZE. The loader scales RGB intrinsics to this
# size and never inspects the PNG shape, so a smaller depth map would back-project
# with the wrong principal point.
DEPTH_W, DEPTH_H = 256, 192
RGB_W, RGB_H = 960, 720
DEPTH_FOCAL = 183.0
FRAME_COUNT = 16
CAMERA_HEIGHT = 1.40
MAX_RANGE_M = 5.0
# Distance from the room to the shell a ray through the door or window lands on.
OUTER_M = 1.2


def _rotation(yaw: float, pitch: float) -> np.ndarray:
    """Camera-to-world: camera +x right, +y down, +z forward; world +y up."""
    forward = np.array([np.sin(yaw) * np.cos(pitch), np.sin(pitch), np.cos(yaw) * np.cos(pitch)])
    forward /= np.linalg.norm(forward)
    world_up = np.array([0.0, 1.0, 0.0])
    right = np.cross(forward, world_up)
    right /= np.linalg.norm(right)
    down = np.cross(forward, right)
    return np.stack([right, down, forward], axis=1)


def _quaternion(rotation: np.ndarray) -> tuple[float, float, float, float]:
    from cozmo.util.transforms import matrix_to_quat

    q = matrix_to_quat(rotation)
    return float(q[0]), float(q[1]), float(q[2]), float(q[3])


def _in_opening(opening: dict, u: np.ndarray, v: np.ndarray) -> np.ndarray:
    return (
        (u >= opening["u0"])
        & (u <= opening["u1"])
        & (v >= opening["v0"])
        & (v <= opening["v1"])
    )


def _room_to_world() -> np.ndarray:
    yaw = np.radians(ROOM_YAW_DEG)
    c, s = np.cos(yaw), np.sin(yaw)
    return np.array([[c, 0.0, s], [0.0, 1.0, 0.0], [-s, 0.0, c]])


def render_depth(origin: np.ndarray, rotation: np.ndarray, drop_ceiling: bool):
    """Ray-trace one interior depth frame. Depth is camera-z, as Stray writes it."""
    us, vs = np.meshgrid(np.arange(DEPTH_W), np.arange(DEPTH_H))
    dirs_cam = np.stack(
        [
            (us - DEPTH_W / 2) / DEPTH_FOCAL,
            (vs - DEPTH_H / 2) / DEPTH_FOCAL,
            np.ones_like(us, dtype=float),
        ],
        axis=-1,
    )
    dirs_world = dirs_cam @ rotation.T
    room_from_world = _room_to_world().T
    dirs = dirs_world @ room_from_world.T
    origin_room = room_from_world @ origin

    lo = np.array([0.0, 0.0, 0.0])
    hi = np.array([ROOM_WIDTH, CEILING_HEIGHT, ROOM_DEPTH])
    with np.errstate(divide="ignore", invalid="ignore"):
        t_lo = (lo - origin_room) / dirs
        t_hi = (hi - origin_room) / dirs
        t_exit = np.where(dirs > 0, t_hi, t_lo)
    t_exit = np.where(np.isfinite(t_exit), t_exit, np.inf)
    t_exit = np.where(t_exit > 0, t_exit, np.inf)

    axis = np.argmin(t_exit, axis=-1)
    t = np.min(t_exit, axis=-1)
    hit = origin_room + t[..., None] * dirs
    valid = np.isfinite(t) & (t > 0.2) & (t < MAX_RANGE_M)

    if drop_ceiling:
        valid &= ~((axis == 1) & (hit[..., 1] > CEILING_HEIGHT - 0.05))

    # A ray through the door or window carries on to an outer shell OUTER_M beyond the room, its
    # floor level with the room's, the way a real doorway shows the next room. With nothing there
    # the ray returns no depth at all, which a real capture only does for glass, black or distant
    # surfaces, and which the opening detector deliberately does not read as a hole.
    door_face = (axis == 2) & (hit[..., 2] < 0.05)
    window_face = (axis == 0) & (hit[..., 0] > ROOM_WIDTH - 0.05)
    through = (door_face & _in_opening(DOOR, hit[..., 0], hit[..., 1])) | (
        window_face & _in_opening(WINDOW, hit[..., 2], hit[..., 1])
    )
    if through.any():
        outer_lo = np.array([-OUTER_M, 0.0, -OUTER_M])
        outer_hi = np.array([ROOM_WIDTH + OUTER_M, CEILING_HEIGHT + 0.5, ROOM_DEPTH + OUTER_M])
        with np.errstate(divide="ignore", invalid="ignore"):
            s_lo = (outer_lo - origin_room) / dirs
            s_hi = (outer_hi - origin_room) / dirs
            s_exit = np.where(dirs > 0, s_hi, s_lo)
        s_exit = np.where(np.isfinite(s_exit) & (s_exit > 0), s_exit, np.inf)
        t_outer = np.min(s_exit, axis=-1)
        t = np.where(through, t_outer, t)
        valid = np.where(through, np.isfinite(t_outer) & (t_outer < MAX_RANGE_M), valid)

    depth = np.where(valid, t, 0.0).astype(np.float32)
    return depth, valid


def camera_path() -> list[tuple[np.ndarray, float, float]]:
    poses = []
    room_to_world = _room_to_world()
    for i in range(FRAME_COUNT):
        fraction = i / FRAME_COUNT
        yaw = 2 * np.pi * fraction + np.radians(ROOM_YAW_DEG)
        origin = room_to_world @ np.array(
            [
                ROOM_WIDTH / 2 + 0.35 * np.cos(2 * np.pi * fraction),
                CAMERA_HEIGHT,
                ROOM_DEPTH / 2 + 0.35 * np.sin(2 * np.pi * fraction),
            ]
        )
        pitch = 0.45 * np.sin(4 * np.pi * fraction)
        poses.append((origin, yaw, pitch))
    return poses


def write_capture(root: Path, drop_ceiling: bool) -> Path:
    root = Path(root)
    (root / "depth").mkdir(parents=True, exist_ok=True)
    (root / "confidence").mkdir(parents=True, exist_ok=True)

    rows = ["timestamp,frame,x,y,z,qx,qy,qz,qw"]
    for index, (origin, yaw, pitch) in enumerate(camera_path()):
        rotation = _rotation(yaw, pitch)
        depth, valid = render_depth(origin, rotation, drop_ceiling)
        cv2.imwrite(
            str(root / "depth" / f"{index:06d}.png"),
            np.clip(depth * 1000.0, 0, 65535).astype(np.uint16),
        )
        cv2.imwrite(
            str(root / "confidence" / f"{index:06d}.png"),
            np.where(valid, 2, 0).astype(np.uint8),
        )
        qx, qy, qz, qw = _quaternion(rotation)
        rows.append(
            f"{index * 0.1:.6f},{index},{origin[0]:.6f},{origin[1]:.6f},"
            f"{origin[2]:.6f},{qx:.8f},{qy:.8f},{qz:.8f},{qw:.8f}"
        )
    (root / "odometry.csv").write_text("\n".join(rows) + "\n")

    scale = RGB_W / DEPTH_W
    focal = DEPTH_FOCAL * scale
    (root / "camera_matrix.csv").write_text(
        f"{focal:.4f},0.0,{RGB_W / 2:.4f}\n"
        f"0.0,{focal:.4f},{RGB_H / 2:.4f}\n"
        f"0.0,0.0,1.0\n"
    )

    writer = cv2.VideoWriter(
        str(root / "rgb.mp4"), cv2.VideoWriter_fourcc(*"mp4v"), 10.0, (RGB_W, RGB_H)
    )
    for _ in range(FRAME_COUNT):
        writer.write(np.full((RGB_H, RGB_W, 3), 200, dtype=np.uint8))
    writer.release()

    (root / "truth.json").write_text(
        json.dumps(
            {
                "width_m": ROOM_WIDTH,
                "depth_m": ROOM_DEPTH,
                "ceiling_m": CEILING_HEIGHT,
                "floor_area_m2": ROOM_WIDTH * ROOM_DEPTH,
                "yaw_deg": ROOM_YAW_DEG,
                "ceiling_observed": not drop_ceiling,
                "door_width_m": DOOR["u1"] - DOOR["u0"],
                "window_width_m": WINDOW["u1"] - WINDOW["u0"],
            },
            indent=2,
        )
        + "\n"
    )
    return root


def write_default_captures() -> tuple[Path, Path]:
    observed = write_capture(CAPTURES / "synthetic_room", drop_ceiling=False)
    missing = write_capture(CAPTURES / "synthetic_no_ceiling", drop_ceiling=True)
    return observed, missing


if __name__ == "__main__":
    a, b = write_default_captures()
    print("ceiling observed :", a)
    print("ceiling dropped  :", b)
    print(f"truth: {ROOM_WIDTH} x {ROOM_DEPTH} x {CEILING_HEIGHT} m  area {ROOM_WIDTH * ROOM_DEPTH:.2f} m2")
