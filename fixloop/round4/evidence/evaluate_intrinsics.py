"""Do VGGT-1B's own intrinsics or the stills' EXIF intrinsics give geometry closer to LiDAR? Uses cache_vggt_views.py.

For every still, the LiDAR depth of the same frame, turned upright as the still was, is a ground-truth depth map on
the same image. Each confident pixel is then a pair: the point VGGT's depth and pose put in its frame, back-projected
with either focal length, and the point LiDAR and ARKit put in theirs. One similarity per room is fitted to the pairs
(robustly: refitted on pairs within three median residuals), and the residual that remains is geometry the model got
wrong. The focal length that leaves less is the one to back-project with.
"""
import json
import sys
from pathlib import Path

import cv2
import numpy as np

REPO = Path("/Users/anujmishra/Desktop/cozmoo/Cozmo_ass")
sys.path.insert(0, str(REPO / "src"))
from cozmo.io.stray import StrayCapture  # noqa: E402
from cozmo.recon.sequence import umeyama  # noqa: E402
from cozmo.util.orientation import rotate_intrinsics, rotate_quarter  # noqa: E402
from cozmo.util.transforms import scale_intrinsics  # noqa: E402

CACHE = Path(sys.argv[1])
RAW = REPO.parent / "data" / "raw"
PAIRS = {"single_room": "c00a170fe1", "single_scan_floor_only": "1a8384c3f6", "single_scan_with_ceiling": "c7d28f72c6"}
STRIDE = 3


def backproject(depth, k, rows, cols):
    rays = np.stack([cols, rows, np.ones(len(rows))], axis=1) @ np.linalg.inv(k).T
    return rays * depth[rows, cols][:, None]


def robust_similarity(source, target):
    keep = np.ones(len(source), bool)
    for _ in range(4):
        s, r, t = umeyama(source[keep], target[keep])
        residual = np.linalg.norm(source * s @ r.T + t - target, axis=1)
        keep = residual < 3.0 * np.median(residual[keep])
    return s, r, t, residual, keep


results = []
for path in sorted(CACHE.glob("*.npz")):
    data = dict(np.load(path))
    name, room = path.stem.split("__")
    capture = StrayCapture(RAW / PAIRS[name])
    reference = json.loads((REPO / "data" / "tier_inputs" / name / "reference.json").read_text())
    stills = {int(s["frame"]): s for r in reference["rooms"] if r["room_id"] == room for s in r["stills"]}
    focal = float(json.loads(str(data["focal_35mm"])))
    frames = {frame.index: frame for frame in capture.frames([int(f) for f in data["frames"]])}
    lidar_points, vggt_points, depth_ratios = [], {"vggt": [], "exif": []}, []
    for i, frame_number in enumerate(int(f) for f in data["frames"]):
        frame = frames.get(frame_number)
        if frame is None or frame.depth is None:
            continue
        turns = int(stills[frame_number]["turns"])
        depth = rotate_quarter(frame.depth, turns)
        confidence = rotate_quarter(frame.confidence, turns)
        rotated = rotate_intrinsics(frame.k_depth, (frame.depth.shape[1], frame.depth.shape[0]), turns)
        k_lidar = rotated[0] if isinstance(rotated, tuple) else rotated
        height, width = data["depth"][i].shape
        # The still and the depth map cover the same field of view, so the LiDAR map is resampled onto VGGT's grid.
        k_lidar = scale_intrinsics(k_lidar, (depth.shape[1], depth.shape[0]), (width, height))
        depth = cv2.resize(depth, (width, height), interpolation=cv2.INTER_NEAREST)
        confidence = cv2.resize(confidence, (width, height), interpolation=cv2.INTER_NEAREST)
        rows, cols = np.mgrid[0:height:STRIDE, 0:width:STRIDE]
        rows, cols = rows.ravel(), cols.ravel()
        good = (confidence[rows, cols] >= 2) & (depth[rows, cols] > 0.2) & (data["depth"][i][rows, cols] > 1e-6)
        good &= data["confidence"][i][rows, cols] >= np.quantile(data["confidence"][i], 0.3)
        rows, cols = rows[good], cols[good]
        # Metres per model unit from depth alone, pixel by pixel, which no error in a pose can touch.
        depth_ratios.append(depth[rows, cols] / data["depth"][i][rows, cols])
        from cozmo.util.orientation import camera_roll

        pose = frame.pose
        rotation = pose[:3, :3] @ camera_roll(turns)
        lidar_points.append(backproject(depth, k_lidar, rows, cols) @ rotation.T + pose[:3, 3])
        k_exif = np.array([[max(width, height) * focal / 36.0, 0.0, width / 2.0], [0.0, max(width, height) * focal / 36.0, height / 2.0], [0.0, 0.0, 1.0]])
        for label, k in (("vggt", data["intrinsics"][i]), ("exif", k_exif)):
            vggt_points[label].append(backproject(data["depth"][i], k, rows, cols) @ data["rotation_wc"][i].T + data["centre"][i])
    target = np.vstack(lidar_points)
    row = {"capture": name, "room": room, "pairs": int(len(target)),
           "depth_ratio_scale": float(np.median(np.concatenate(depth_ratios)))}
    for label in ("vggt", "exif"):
        s, r, t, residual, keep = robust_similarity(np.vstack(vggt_points[label]), target)
        row[label] = {"scale": float(s), "median_residual_cm": float(100 * np.median(residual)),
                      "p75_residual_cm": float(100 * np.quantile(residual, 0.75)), "inlier_fraction": float(keep.mean())}
    results.append(row)
    print(f"{name:26s} {room}: pairs {row['pairs']:6d} | VGGT focal: median {row['vggt']['median_residual_cm']:5.1f} cm, p75 {row['vggt']['p75_residual_cm']:5.1f}, "
          f"inliers {row['vggt']['inlier_fraction']:.2f} | EXIF focal: median {row['exif']['median_residual_cm']:5.1f} cm, p75 {row['exif']['p75_residual_cm']:5.1f}, "
          f"inliers {row['exif']['inlier_fraction']:.2f}", flush=True)

Path(sys.argv[2]).write_text(json.dumps(results, indent=2))
for label in ("vggt", "exif"):
    print(label, "median over rooms of median residual:", round(float(np.median([r[label]["median_residual_cm"] for r in results])), 2), "cm")
