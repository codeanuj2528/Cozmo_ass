"""Which check, needing no ground truth, finds the stills VGGT-1B posed wrong? Run on cache_vggt_views.py output.

A still is wrong when the median, over the other stills, of the error of VGGT's relative rotation against ARKit's
exceeds 30 degrees. Candidates, each per still:

  free_space   fraction of its confident points, seen by another still, that lie in front of that still's surface
               by more than 10%: space the other camera saw through
  agreement    fraction of those points within 10% of the other still's depth
  essential    of the other stills it shares enough SIFT matches with, the fraction whose relative rotation from the
               essential matrix disagrees with VGGT's by more than 20 degrees
"""
import json
import re
import sys
from pathlib import Path

import cv2
import numpy as np

REPO = Path("/Users/anujmishra/Desktop/cozmoo/Cozmo_ass")
sys.path.insert(0, str(REPO / "src"))
from cozmo.io.discover import read_image  # noqa: E402

CACHE = Path(sys.argv[1])
STRIDE = 4
TOLERANCE = 0.10


def angle(r):
    return float(np.degrees(np.arccos(np.clip((np.trace(r) - 1.0) / 2.0, -1.0, 1.0))))


def rotation_errors(vggt, arkit):
    n = len(vggt)
    errors = np.zeros((n, n))
    for i in range(n):
        for j in range(n):
            if i != j:
                errors[i, j] = angle((vggt[i].T @ vggt[j]).T @ (arkit[i].T @ arkit[j]))
    return errors


def world_points(depth, confidence, k, rotation, centre):
    rows, cols = np.mgrid[0:depth.shape[0]:STRIDE, 0:depth.shape[1]:STRIDE]
    d = depth[rows, cols]
    keep = (d > 1e-6) & (confidence[rows, cols] >= np.quantile(confidence, 0.3))
    rays = np.stack([cols[keep], rows[keep], np.ones(keep.sum())], axis=1) @ np.linalg.inv(k).T
    return (rays * d[keep][:, None]) @ rotation.T + centre


def free_space(data):
    n = len(data["depth"])
    violations, agreements, visible = np.zeros(n), np.zeros(n), np.zeros(n)
    for i in range(n):
        points = world_points(data["depth"][i], data["confidence"][i], data["intrinsics"][i], data["rotation_wc"][i], data["centre"][i])
        for j in range(n):
            if i == j:
                continue
            camera = (points - data["centre"][j]) @ data["rotation_wc"][j]
            z = camera[:, 2]
            front = z > 1e-3
            uv = camera[front] @ data["intrinsics"][j].T
            u, v = uv[:, 0] / uv[:, 2], uv[:, 1] / uv[:, 2]
            h, w = data["depth"][j].shape
            inside = (u >= 0) & (u < w - 1) & (v >= 0) & (v < h - 1)
            observed = data["depth"][j][v[inside].astype(int), u[inside].astype(int)]
            good = observed > 1e-6
            ratio = z[front][inside][good] / observed[good]
            visible[i] += len(ratio)
            violations[i] += np.sum(ratio < 1.0 - TOLERANCE)
            agreements[i] += np.sum(np.abs(ratio - 1.0) < TOLERANCE)
    return violations / np.maximum(visible, 1), agreements / np.maximum(visible, 1), visible


def essential(images, data):
    sift = cv2.SIFT_create(nfeatures=3000)
    features = []
    for image in images:
        grey = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
        features.append(sift.detectAndCompute(grey, None))
    matcher = cv2.BFMatcher()
    n = len(images)
    checked, disagree = np.zeros(n), np.zeros(n)
    pair_error = {}
    for i in range(n):
        for j in range(i + 1, n):
            (ki, di), (kj, dj) = features[i], features[j]
            if di is None or dj is None or len(ki) < 20 or len(kj) < 20:
                continue
            matches = [m for m, s in matcher.knnMatch(di, dj, k=2) if m.distance < 0.75 * s.distance]
            if len(matches) < 40:
                continue
            # VGGT's intrinsics are for its crop of the image; scale them to the image the features came from.
            scale = images[i].shape[1] / data["depth"][i].shape[1]
            k = data["intrinsics"][i].copy()
            k[:2] *= scale
            pi = np.float64([ki[m.queryIdx].pt for m in matches])
            pj = np.float64([kj[m.trainIdx].pt for m in matches])
            e, mask = cv2.findEssentialMat(pi, pj, k, method=cv2.RANSAC, prob=0.999, threshold=1.0)
            if e is None or e.shape != (3, 3):
                continue
            inliers, r, _, _ = cv2.recoverPose(e, pi, pj, k, mask=mask)
            if inliers < 30:
                continue
            # recoverPose gives R taking points in camera i to camera j: R = R_cw_j R_wc_i.
            vggt = data["rotation_wc"][j].T @ data["rotation_wc"][i]
            error = angle(r.T @ vggt)
            pair_error[(i, j)] = (error, int(inliers))
            for a in (i, j):
                checked[a] += 1
                disagree[a] += error > 20.0
    return checked, disagree, pair_error


rows = []
for path in sorted(CACHE.glob("*.npz")):
    data = dict(np.load(path))
    capture, room = path.stem.split("__")
    errors = rotation_errors(data["rotation_wc"], data["arkit_rotation_wc"])
    wrong = np.median(np.where(np.eye(len(errors)) > 0, np.nan, errors), axis=1) if False else np.array(
        [np.median(np.delete(errors[i], i)) for i in range(len(errors))])
    violation, agreement, visible = free_space(data)
    folder = REPO / "data" / "tier_inputs" / capture / "photo" / room
    by_frame = {int(re.findall(r"\d+", p.stem)[-1]): p for p in folder.glob("*.jpg")}
    images = []
    for frame in data["frames"]:
        rgb = read_image(by_frame[int(frame)])
        factor = 1036 / max(rgb.shape[:2])
        images.append(cv2.resize(rgb, (int(round(rgb.shape[1] * factor)), int(round(rgb.shape[0] * factor))), interpolation=cv2.INTER_AREA) if factor < 1 else rgb)
    checked, disagree, pairs = essential(images, data)
    focal = json.loads(str(data["focal_35mm"]))
    width, height = (int(x) for x in data["image_size"])
    fov_vggt = np.degrees(2 * np.arctan(data["depth"].shape[2] / 2 / data["intrinsics"][:, 0, 0]))
    fov_true = np.degrees(2 * np.arctan(width / 2 / (max(width, height) * float(focal) / 36.0))) if isinstance(focal, (int, float)) else float("nan")
    print(f"== {capture} {room}: VGGT horizontal FOV median {np.median(fov_vggt):.1f} deg, EXIF {fov_true:.1f} deg")
    for i in range(len(errors)):
        verdict = "WRONG" if wrong[i] > 30 else "ok"
        print(f"  still {int(data['frames'][i]):5d} {verdict:5s} rot err {wrong[i]:6.1f} | free-space {violation[i]:.3f} agree {agreement[i]:.3f} "
              f"(visible {int(visible[i])}) | essential {int(disagree[i])}/{int(checked[i])} disagree")
        rows.append({"capture": capture, "room": room, "frame": int(data["frames"][i]), "wrong": bool(wrong[i] > 30),
                     "rotation_error": float(wrong[i]), "free_space": float(violation[i]), "agreement": float(agreement[i]),
                     "visible": int(visible[i]), "essential_checked": int(checked[i]), "essential_disagree": int(disagree[i]),
                     "vggt_fov": float(np.median(fov_vggt)), "exif_fov": float(fov_true)})

out = Path(sys.argv[2])
out.write_text(json.dumps(rows, indent=2))
wrong = [r for r in rows if r["wrong"]]
right = [r for r in rows if not r["wrong"]]
print(f"\n{len(wrong)} wrong stills, {len(right)} right")
for key in ("free_space", "agreement"):
    print(f"{key:10s} wrong: {sorted(round(r[key], 3) for r in wrong)}")
    print(f"{key:10s} right: {sorted(round(r[key], 3) for r in right)}")
