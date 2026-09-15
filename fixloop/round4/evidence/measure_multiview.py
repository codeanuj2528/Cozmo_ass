#!/usr/bin/env python
"""VGGT on the photo-tier stills of each room, measured against ARKit and LiDAR before the fix was built.

For every photo folder `scripts/make_tier_inputs.py` wrote, the stills go to VGGT-1B as the photo tier would
give them: upright, padded to 518 px. What comes back is compared with what the LiDAR app measured for the same
frames:

  depth shape    absrel of VGGT depth against LiDAR depth once one scale is fitted for the whole room
  depth scale    how far each still's own scale strays from that room scale (a room has one scale)
  rotation       relative rotation error between every pair of stills, which needs no alignment
  scale cues     the room scale each cue would give, against the scale LiDAR says the room has:
                   metric depth   a metric model's depth over VGGT's, median per still, median over stills
                   camera height  1.40 m over the camera height above the floor that VGGT's points imply

The scale cue decides the photo tier's metric error: a room scaled wrong by e has walls wrong by e and floor
area wrong by (1 + e)^2 - 1. None of this runs the photo tier; it measures the parts the fix would be built
from.

    .venv/bin/python fixloop/round4/evidence/measure_multiview.py --inputs data/tier_inputs \
        --model da2-small=weights/depth-anything-v2-metric-indoor-small --out fixloop/round4/evidence/multiview.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image
from safetensors.torch import load_file

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "scripts"))
from measure_depth_scale import load_model  # noqa: E402

from cozmo.io.stray import StrayCapture  # noqa: E402
from cozmo.util.orientation import camera_roll, rotate_quarter  # noqa: E402

SIZE, PATCH = 518, 14
# Global attention over every token of every still, computed a block of queries at a time. The result is the
# same; without it 8 stills at 518 px hold a 7.7 GB attention matrix on a 16 GB machine.
ATTENTION_CHUNK = 2048
CAMERA_HEIGHT_PRIOR_M = 1.40
MIN_DEPTH_M, MAX_DEPTH_M = 0.3, 4.5


def use_chunked_attention() -> None:
    from vggt.layers import attention as vggt_attention

    def forward(self, x, pos=None):
        batch, tokens, channels = x.shape
        qkv = self.qkv(x).reshape(batch, tokens, 3, self.num_heads, self.head_dim).permute(2, 0, 3, 1, 4)
        q, k, v = qkv.unbind(0)
        q, k = self.q_norm(q), self.k_norm(k)
        if self.rope is not None:
            q, k = self.rope(q, pos), self.rope(k, pos)
        if tokens <= ATTENTION_CHUNK:
            out = F.scaled_dot_product_attention(q, k, v)
        else:
            out = torch.cat([F.scaled_dot_product_attention(q[:, :, s:s + ATTENTION_CHUNK], k, v)
                             for s in range(0, tokens, ATTENTION_CHUNK)], dim=2)
        return self.proj_drop(self.proj(out.transpose(1, 2).reshape(batch, tokens, channels)))

    vggt_attention.Attention.forward = forward


def load_vggt(weights: Path, device: str):
    from vggt.models.vggt import VGGT

    model = VGGT(enable_point=False, enable_track=False)
    missing, _ = model.load_state_dict(load_file(str(weights)), strict=False)
    if missing:
        raise RuntimeError(f"VGGT checkpoint is missing {len(missing)} tensors the camera and depth heads need")
    return model.to(device).eval()


def pad_square(rgb: np.ndarray) -> tuple[np.ndarray, tuple[int, int, int, int]]:
    h, w = rgb.shape[:2]
    if w >= h:
        nw, nh = SIZE, int(round(h * SIZE / w / PATCH)) * PATCH
    else:
        nh, nw = SIZE, int(round(w * SIZE / h / PATCH)) * PATCH
    canvas = np.ones((SIZE, SIZE, 3), np.float32)
    top, left = (SIZE - nh) // 2, (SIZE - nw) // 2
    canvas[top:top + nh, left:left + nw] = cv2.resize(rgb, (nw, nh), interpolation=cv2.INTER_CUBIC) / 255.0
    return canvas, (top, left, nh, nw)


def run_vggt(model, images: list[np.ndarray], device: str) -> dict:
    from vggt.utils.pose_enc import pose_encoding_to_extri_intri

    padded = [pad_square(rgb) for rgb in images]
    batch = torch.stack([torch.from_numpy(img).permute(2, 0, 1) for img, _ in padded]).to(device)
    if device == "mps":
        torch.mps.empty_cache()
    started = time.perf_counter()
    with torch.no_grad():
        predictions = model(batch)
    if device == "mps":
        torch.mps.synchronize()
    seconds = time.perf_counter() - started
    extrinsic, intrinsic = pose_encoding_to_extri_intri(predictions["pose_enc"], batch.shape[-2:])
    return {
        "depth": predictions["depth"][0, ..., 0].float().cpu().numpy(),
        "conf": predictions["depth_conf"][0].float().cpu().numpy(),
        "extrinsic": extrinsic[0].float().cpu().numpy(),
        "intrinsic": intrinsic[0].float().cpu().numpy(),
        "boxes": [box for _, box in padded],
        "seconds": seconds,
        "memory_gb": torch.mps.driver_allocated_memory() / 1e9 if device == "mps" else None,
    }


def angle_deg(rotation: np.ndarray) -> float:
    return float(np.degrees(np.arccos(np.clip((np.trace(rotation) - 1.0) / 2.0, -1.0, 1.0))))


def floor_level(heights: np.ndarray, camera_heights: np.ndarray) -> float | None:
    """The lowest strong level below every camera, from a histogram of point heights along up."""
    below = heights[heights < camera_heights.min()]
    if len(below) < 200:
        return None
    extent = float(np.percentile(heights, 99) - np.percentile(heights, 1))
    counts, edges = np.histogram(below, bins=max(int(np.ptp(below) / max(extent / 200.0, 1e-6)), 10))
    strong = np.flatnonzero(counts >= 0.3 * counts.max())
    return float(0.5 * (edges[strong[0]] + edges[strong[0] + 1]))


def evaluate_room(model, room: dict, photo_dir: Path, capture: StrayCapture, device: str) -> dict:
    rows = {int(r["frame"]): i for i, r in enumerate(capture._rows)}
    poses = capture.poses()
    images, lidar, confidence, arkit = [], [], [], []
    for still in room["stills"]:
        images.append(np.asarray(Image.open(photo_dir / room["photo_folder"] / f"IMG_{still['frame']:06d}.jpg").convert("RGB")))
        depth, conf = capture._load_depth(still["frame"])
        lidar.append(rotate_quarter(depth, still["turns"]))
        confidence.append(rotate_quarter(conf, still["turns"]))
        pose = poses[rows[still["frame"]]]
        arkit.append((pose[:3, :3] @ camera_roll(still["turns"]), pose[:3, 3]))
    out = run_vggt(model, images, device)

    crops, frame_ratios, pairs_l, pairs_v = [], [], [], []
    for i, (top, left, nh, nw) in enumerate(out["boxes"]):
        v = out["depth"][i, top:top + nh, left:left + nw]
        c = out["conf"][i, top:top + nh, left:left + nw]
        crops.append((v, c))
        size = (lidar[i].shape[1], lidar[i].shape[0])
        vr = cv2.resize(v, size, interpolation=cv2.INTER_AREA)
        cr = cv2.resize(c, size, interpolation=cv2.INTER_AREA)
        valid = (confidence[i] >= 2) & (lidar[i] > MIN_DEPTH_M) & (lidar[i] < MAX_DEPTH_M) & (vr > 0) & (cr >= np.quantile(cr, 0.3))
        if valid.sum() < 200:
            continue
        frame_ratios.append(float(np.median(lidar[i][valid] / vr[valid])))
        pairs_l.append(lidar[i][valid])
        pairs_v.append(vr[valid])
    all_l, all_v = np.concatenate(pairs_l), np.concatenate(pairs_v)
    room_scale = float(np.median(all_l / all_v))
    absrel = float(np.median(np.abs(room_scale * all_v - all_l) / all_l))

    rot_v = [out["extrinsic"][i][:, :3].T for i in range(len(images))]
    centre_v = [-rot_v[i] @ out["extrinsic"][i][:, 3] for i in range(len(images))]
    rotation_errors, baseline_ratios = [], []
    for i in range(len(images)):
        for j in range(i + 1, len(images)):
            rel_v = rot_v[i].T @ rot_v[j]
            rel_a = arkit[i][0].T @ arkit[j][0]
            rotation_errors.append(angle_deg(rel_v.T @ rel_a))
            baseline_a = float(np.linalg.norm(arkit[j][1] - arkit[i][1]))
            baseline_v = float(np.linalg.norm(centre_v[j] - centre_v[i]))
            if baseline_a > 0.25 and baseline_v > 1e-6:
                baseline_ratios.append(baseline_a / baseline_v)

    # Camera height cue. Upright stills: the image's down axis, averaged over stills, is gravity.
    up = -np.mean([r[:, 1] for r in rot_v], axis=0)
    up /= np.linalg.norm(up)

    # Which stills VGGT posed wrong, and whether that is visible without ARKit. An upright still's down axis
    # should agree with the others'; a still turned the wrong way round does not.
    robust_up = up.copy()
    for _ in range(3):
        angles = np.array([np.degrees(np.arccos(np.clip(-r[:, 1] @ robust_up, -1.0, 1.0))) for r in rot_v])
        agreeing = angles <= 45.0
        if agreeing.sum() < 2:
            break
        robust_up = -np.mean([rot_v[i][:, 1] for i in np.flatnonzero(agreeing)], axis=0)
        robust_up /= np.linalg.norm(robust_up)
    diagnostics = []
    for i in range(len(images)):
        errors_i = [angle_deg((rot_v[i].T @ rot_v[j]).T @ (arkit[i][0].T @ arkit[j][0])) for j in range(len(images)) if j != i]
        diagnostics.append({
            "frame": room["stills"][i]["frame"],
            "down_axis_disagreement_deg": float(np.degrees(np.arccos(np.clip(-rot_v[i][:, 1] @ robust_up, -1.0, 1.0)))),
            "arkit_rotation_error_median_deg": float(np.median(errors_i)) if errors_i else None,
            "vggt_confidence_median": float(np.median(crops[i][1])),
        })
    heights = []
    for i, (top, left, nh, nw) in enumerate(out["boxes"]):
        v, c = crops[i]
        rows_px, cols_px = np.mgrid[0:nh:4, 0:nw:4]
        keep = c[rows_px, cols_px] >= np.quantile(c, 0.3)
        z = v[rows_px, cols_px][keep]
        pixels = np.stack([cols_px[keep] + left, rows_px[keep] + top, np.ones(keep.sum())], axis=1)
        rays = pixels @ np.linalg.inv(out["intrinsic"][i]).T
        world = (rays * z[:, None]) @ rot_v[i].T + centre_v[i]
        heights.append(world @ up)
    camera_heights = np.array([c @ up for c in centre_v])
    floor = floor_level(np.concatenate(heights), camera_heights)
    height_scale = None
    if floor is not None and np.median(camera_heights) - floor > 1e-6:
        height_scale = CAMERA_HEIGHT_PRIOR_M / float(np.median(camera_heights) - floor)

    return {
        "room": room["room_id"],
        "area_m2": room["floor_area_m2"],
        "stills": len(images),
        "vggt_seconds": out["seconds"],
        "vggt_memory_gb": out["memory_gb"],
        "room_scale": room_scale,
        "depth_absrel": absrel,
        "frame_scale_spread": float(np.std(frame_ratios) / np.mean(frame_ratios)) if frame_ratios else None,
        "rotation_error_median_deg": float(np.median(rotation_errors)) if rotation_errors else None,
        "rotation_error_max_deg": float(np.max(rotation_errors)) if rotation_errors else None,
        "baseline_scale_error": float(np.median(baseline_ratios) / room_scale - 1.0) if baseline_ratios else None,
        "camera_height_scale_error": (height_scale / room_scale - 1.0) if height_scale else None,
        "stills_diagnostics": diagnostics,
        "_crops": crops,
        "_images": images,
    }


def metric_cue(model, room: dict) -> float | None:
    ratios = []
    for rgb, (v, c) in zip(room["_images"], room["_crops"]):
        half = cv2.resize(rgb, (rgb.shape[1] // 2, rgb.shape[0] // 2), interpolation=cv2.INTER_AREA)
        # The stills carry a 30 mm equivalent focal length: fx is 30/36 of the long edge, halved with the image.
        fx = max(rgb.shape[:2]) * 30.0 / 36.0 / 2.0
        metric = cv2.resize(model(half, fx=fx), (v.shape[1], v.shape[0]), interpolation=cv2.INTER_AREA)
        valid = np.isfinite(metric) & (metric > 0) & (v > 0) & (c >= np.quantile(c, 0.3))
        if valid.sum() >= 200:
            ratios.append(float(np.median(metric[valid] / v[valid])))
    return float(np.median(ratios)) if ratios else None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--inputs", type=Path, default=REPO / "data" / "tier_inputs")
    parser.add_argument("--vggt", type=Path, default=REPO / "weights" / "vggt-1b" / "model.safetensors")
    parser.add_argument("--model", action="append", default=[], help="a metric depth model, as in scripts/measure_depth_scale.py")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--only", action="append", default=[], help="capture:room_id to measure, repeatable")
    args = parser.parse_args()
    only = {tuple(item.split(":", 1)) for item in args.only}

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    use_chunked_attention()
    vggt = load_vggt(args.vggt, device)
    rooms = []
    for reference_path in sorted(args.inputs.glob("*/reference.json")):
        reference = json.loads(reference_path.read_text())
        capture = StrayCapture(Path(reference["source"]))
        for room in reference["rooms"]:
            if not room["photo_folder"] or (only and (reference["capture"], room["room_id"]) not in only):
                continue
            result = evaluate_room(vggt, room, reference_path.parent / "photo", capture, device)
            result["capture"] = reference["capture"]
            rooms.append(result)
            print(f"{reference['capture']:26s} {room['room_id']} {result['stills']} stills {result['vggt_seconds']:5.1f}s "
                  f"{result['vggt_memory_gb'] or 0:4.1f} GB | absrel {result['depth_absrel']:.3f} spread {result['frame_scale_spread'] or 0:.3f} "
                  f"rot {result['rotation_error_median_deg'] or 0:4.1f}/{result['rotation_error_max_deg'] or 0:4.1f} deg | "
                  f"baseline {result['baseline_scale_error'] if result['baseline_scale_error'] is None else round(result['baseline_scale_error'], 3)} "
                  f"camera height {result['camera_height_scale_error'] if result['camera_height_scale_error'] is None else round(result['camera_height_scale_error'], 3)}",
                  flush=True)
            if (result["rotation_error_max_deg"] or 0) > 30.0:
                for still in result["stills_diagnostics"]:
                    print(f"      still {still['frame']:6d}: down axis off the others by {still['down_axis_disagreement_deg']:5.1f} deg, "
                          f"ARKit rotation error {still['arkit_rotation_error_median_deg']:5.1f} deg, "
                          f"confidence {still['vggt_confidence_median']:.2f}", flush=True)
    del vggt
    if device == "mps":
        torch.mps.empty_cache()

    for spec in args.model:
        name, _, model = load_model(spec)
        for room in rooms:
            scale = metric_cue(model, room)
            room[f"{name}_scale_error"] = (scale / room["room_scale"] - 1.0) if scale else None
        model.close()
        errors = [r[f"{name}_scale_error"] for r in rooms if r[f"{name}_scale_error"] is not None]
        print(f"{name:26s} scale error over {len(errors)} rooms: median {np.median(errors):+.3f}, "
              f"median |e| {np.median(np.abs(errors)):.3f}, range [{min(errors):+.3f}, {max(errors):+.3f}]", flush=True)

    for key in ("camera_height_scale_error", "baseline_scale_error"):
        errors = [r[key] for r in rooms if r[key] is not None]
        if errors:
            print(f"{key:26s} over {len(errors)} rooms: median {np.median(errors):+.3f}, median |e| {np.median(np.abs(errors)):.3f}, "
                  f"range [{min(errors):+.3f}, {max(errors):+.3f}]")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps([{k: v for k, v in r.items() if not k.startswith("_")} for r in rooms], indent=2))


if __name__ == "__main__":
    main()
