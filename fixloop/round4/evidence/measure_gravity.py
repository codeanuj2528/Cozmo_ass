"""How far each gravity estimate tilts from ARKit's, on the photo stills and video keyframes made from the three walks.

mean_down : the mean of the cameras' down axes (what the first draft of recon/multiview.down_axis_outliers returned)
level_x w : the direction every camera's x axis is perpendicular to, the down axes breaking ties with weight w
"""
import json
import sys
from pathlib import Path

import numpy as np

REPO = Path("/Users/anujmishra/Desktop/cozmoo/Cozmo_ass")
sys.path.insert(0, str(REPO / "src"))
from cozmo.io.stray import StrayCapture  # noqa: E402
from cozmo.util.orientation import camera_roll  # noqa: E402

RAW = REPO.parent / "data" / "raw"
PAIRS = {"single_room": "c00a170fe1", "single_scan_floor_only": "1a8384c3f6", "single_scan_with_ceiling": "c7d28f72c6"}
UP = np.array([0.0, 1.0, 0.0])


def angle(a, b):
    return float(np.degrees(np.arccos(np.clip(a @ b / np.linalg.norm(a) / np.linalg.norm(b), -1.0, 1.0))))


def mean_down(rotations):
    down = np.mean([r[:, 1] for r in rotations], axis=0)
    return -down / np.linalg.norm(down)


def level_x(rotations, weight):
    xs = np.array([r[:, 0] for r in rotations])
    ds = np.array([r[:, 1] for r in rotations])
    _, vectors = np.linalg.eigh(xs.T @ xs - weight * ds.T @ ds)
    up = vectors[:, 0]
    return -up if up @ ds.mean(axis=0) > 0 else up


def describe(rotations):
    roll = [np.degrees(np.arcsin(np.clip(r[:, 0] @ UP, -1, 1))) for r in rotations]
    pitch = [np.degrees(np.arcsin(np.clip(r[:, 2] @ UP, -1, 1))) for r in rotations]
    return float(np.median(np.abs(roll))), float(np.mean(pitch)), float(np.std(pitch))


def estimates(rotations):
    return {
        "mean_down": angle(mean_down(rotations), UP),
        "level_x_0.02": angle(level_x(rotations, 0.02), UP),
        "level_x_0.1": angle(level_x(rotations, 0.1), UP),
    }


report = {}
for name, capture_id in PAIRS.items():
    reference = json.loads((REPO / "data" / "tier_inputs" / name / "reference.json").read_text())
    capture = StrayCapture(RAW / capture_id)
    poses = capture.poses()
    row_of = {frame: row for row, frame in enumerate(capture.frame_indices())}
    rooms = []
    for room in reference["rooms"]:
        if len(room.get("stills") or []) < 2:
            continue
        rotations = [poses[row_of[s["frame"]]][:3, :3] @ camera_roll(int(s["turns"])) for s in room["stills"]]
        roll, pitch_mean, pitch_sd = describe(rotations)
        rooms.append({"room": room["room_id"], "stills": len(rotations), "median_abs_roll": roll,
                      "mean_pitch": pitch_mean, "pitch_sd": pitch_sd, **estimates(rotations)})
    video = reference.get("video") or {}
    turns = int(video.get("rotation_turns", 1))
    every = 60
    rotations = [pose[:3, :3] @ camera_roll(turns) for pose in poses[::every]]
    roll, pitch_mean, pitch_sd = describe(rotations)
    walk = {"keyframes": len(rotations), "median_abs_roll": roll, "mean_pitch": pitch_mean, "pitch_sd": pitch_sd,
            **estimates(rotations)}
    runs = [estimates(rotations[i:i + 12]) for i in range(0, max(len(rotations) - 11, 1), 8)]
    walk["runs_of_12_worst"] = {key: max(run[key] for run in runs) for key in runs[0]}
    report[name] = {"photo_rooms": rooms, "video_walk": walk}

    print(f"== {name}  (video turns {turns})")
    for room in rooms:
        print(f"  {room['room']:8s} n={room['stills']}  roll {room['median_abs_roll']:4.1f}  pitch {room['mean_pitch']:+5.1f}±{room['pitch_sd']:4.1f}"
              f"  tilt: mean_down {room['mean_down']:5.2f}  level_x.02 {room['level_x_0.02']:5.2f}  level_x.1 {room['level_x_0.1']:5.2f}")
    print(f"  video    n={walk['keyframes']}  roll {walk['median_abs_roll']:4.1f}  pitch {walk['mean_pitch']:+5.1f}±{walk['pitch_sd']:4.1f}"
          f"  tilt: mean_down {walk['mean_down']:5.2f}  level_x.02 {walk['level_x_0.02']:5.2f}  level_x.1 {walk['level_x_0.1']:5.2f}")
    print(f"  worst run of 12: " + "  ".join(f"{k} {v:5.2f}" for k, v in walk["runs_of_12_worst"].items()))

Path(sys.argv[1] if len(sys.argv) > 1 else "gravity_check.json").write_text(json.dumps(report, indent=2))
