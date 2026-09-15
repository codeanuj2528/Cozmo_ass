#!/usr/bin/env python
"""How far a metric depth model's scale is from LiDAR depth, on frames of the same captures.

For frames spread over each Stray Scanner export, the colour frame goes to the model and its depth is
compared with ARKit's LiDAR depth for that frame, on pixels ARKit marks confidence 2 between 0.3 and
4.5 m. Per frame:

  ratio   median(model / LiDAR): the scale the model gets wrong, 1.0 is right
  absrel  median(|model / ratio - LiDAR| / LiDAR): the shape error left once that scale is divided out

Reported per model: the median ratio over frames (its bias), the spread of ratios (how much one frame's
scale can be trusted), and the median absrel. `--upright` turns each frame to gravity first, as an iPhone
still or Camera-app video is stored, and turns the depth back; without it the frame goes in as the
LiDAR app recorded it, often sideways.

    .venv/bin/python scripts/measure_depth_scale.py --capture ../data/raw/c7d28f72c6 \
        --model depth-anything-small=weights/depth-anything-v2-metric-indoor-small --upright --out evidence.json
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import cv2
import numpy as np

from cozmo.util.orientation import quarter_turns_upright, rotate_quarter

MIN_DEPTH_M, MAX_DEPTH_M = 0.3, 4.5
FRAMES_PER_CAPTURE = 40


def sample_frames(capture_dir: Path, count: int) -> list[dict]:
    """Evenly spread frames with depth, the sharper three quarters of them, decoded at half resolution."""
    from cozmo.io.stray import StrayCapture

    source = StrayCapture(capture_dir)
    numbers = source.frame_indices()
    poses = source.poses()
    slots = np.linspace(0, len(numbers) - 1, int(count / 0.75)).round().astype(int)
    wanted = {int(numbers[s]): s for s in slots}
    decoded = source.load_rgb_batch(sorted(wanted))
    frames = []
    for number, rgb in decoded.items():
        loaded = source._load_depth(number)
        if loaded is None:
            continue
        depth, confidence = loaded
        small = cv2.resize(rgb, (rgb.shape[1] // 2, rgb.shape[0] // 2), interpolation=cv2.INTER_AREA)
        grey = cv2.cvtColor(small, cv2.COLOR_RGB2GRAY)
        frames.append({
            "frame": number,
            "rgb": small,
            "depth": depth,
            "confidence": confidence,
            "turns": quarter_turns_upright(poses[wanted[number]][:3, :3]),
            "sharpness": float(cv2.Laplacian(grey, cv2.CV_64F).var()),
        })
    cut = np.quantile([f["sharpness"] for f in frames], 0.25) if frames else 0.0
    return [f for f in frames if f["sharpness"] >= cut][:count]


class HFDepth:
    def __init__(self, model_dir: Path):
        import torch
        from transformers import AutoImageProcessor, AutoModelForDepthEstimation

        self.torch = torch
        self.device = "mps" if torch.backends.mps.is_available() else "cpu"
        self.processor = AutoImageProcessor.from_pretrained(str(model_dir))
        self.model = AutoModelForDepthEstimation.from_pretrained(str(model_dir)).to(self.device).eval()

    def __call__(self, rgb: np.ndarray) -> np.ndarray:
        torch = self.torch
        inputs = self.processor(images=rgb, return_tensors="pt").to(self.device)
        with torch.no_grad():
            outputs = self.model(**inputs)
        size = [(rgb.shape[0], rgb.shape[1])]
        # Depth Anything resizes back to target_sizes; ZoeDepth pads its input and needs source_sizes to take
        # the padding off again. Each processor refuses the other's arguments.
        for kwargs in ({"target_sizes": size}, {"source_sizes": size}):
            try:
                post = self.processor.post_process_depth_estimation(outputs, **kwargs)
                break
            except (TypeError, ValueError):
                continue
        else:
            raise RuntimeError(f"{type(self.processor).__name__} could not post-process a depth prediction")
        return post[0]["predicted_depth"].float().cpu().numpy()

    def close(self) -> None:
        del self.model
        if self.device == "mps":
            self.torch.mps.empty_cache()


def compare(predicted: np.ndarray, frame: dict) -> dict | None:
    depth, confidence = frame["depth"], frame["confidence"]
    resized = cv2.resize(predicted, (depth.shape[1], depth.shape[0]), interpolation=cv2.INTER_AREA)
    valid = (confidence >= 2) & (depth > MIN_DEPTH_M) & (depth < MAX_DEPTH_M) & np.isfinite(resized) & (resized > 0)
    if valid.sum() < 500:
        return None
    ratio = float(np.median(resized[valid] / depth[valid]))
    absrel = float(np.median(np.abs(resized[valid] / ratio - depth[valid]) / depth[valid]))
    return {"frame": frame["frame"], "ratio": ratio, "absrel": absrel, "pixels": int(valid.sum())}


def measure(model, frames: list[dict], upright: bool) -> list[dict]:
    rows = []
    for frame in frames:
        rgb, turns = frame["rgb"], frame["turns"]
        if upright and turns:
            predicted = rotate_quarter(model(rotate_quarter(rgb, turns)), -turns)
        else:
            predicted = model(rgb)
        row = compare(predicted, frame)
        if row is not None:
            rows.append(row | {"turns": turns})
    return rows


def summarise(rows: list[dict]) -> dict:
    ratios = np.array([r["ratio"] for r in rows])
    return {
        "frames": len(rows),
        "median_ratio": float(np.median(ratios)),
        "ratio_p10_p90": [float(np.quantile(ratios, 0.10)), float(np.quantile(ratios, 0.90))],
        "median_absrel": float(np.median([r["absrel"] for r in rows])),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--capture", type=Path, action="append", required=True)
    parser.add_argument("--model", action="append", required=True, help="name=path to a transformers depth model")
    parser.add_argument("--frames", type=int, default=FRAMES_PER_CAPTURE)
    parser.add_argument("--upright", action="store_true", help="also measure with frames turned to gravity")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    frames = {capture.name: sample_frames(capture, args.frames) for capture in args.capture}
    report: dict = {"min_depth_m": MIN_DEPTH_M, "max_depth_m": MAX_DEPTH_M, "models": {}}
    for spec in args.model:
        name, path = spec.split("=", 1)
        started = time.perf_counter()
        model = HFDepth(Path(path))
        entry: dict = {"path": path}
        for variant in (["as_recorded", "upright"] if args.upright else ["as_recorded"]):
            per_capture = {}
            everything = []
            for capture, sample in frames.items():
                rows = measure(model, sample, upright=(variant == "upright"))
                per_capture[capture] = summarise(rows) | {"rows": rows}
                everything.extend(rows)
            entry[variant] = {"all": summarise(everything), "captures": per_capture}
            summary = entry[variant]["all"]
            print(f"{name:28s} {variant:12s} ratio {summary['median_ratio']:.3f} "
                  f"[p10 {summary['ratio_p10_p90'][0]:.3f}, p90 {summary['ratio_p10_p90'][1]:.3f}] "
                  f"absrel {summary['median_absrel']:.3f} over {summary['frames']} frames", flush=True)
        entry["seconds"] = time.perf_counter() - started
        model.close()
        report["models"][name] = entry
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
