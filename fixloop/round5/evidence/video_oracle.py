"""The video tier's reconstruction core on ideal geometry: LiDAR depth and ARKit poses of one frame per second.

For each assignment walk, the frames a second apart are handed to build_lidar_plan twice: labelled a video capture
(the cell complex, drift correction on, as the video tier runs it) and labelled a LiDAR capture (the evidence
layout). If the video labelling comes out near the LiDAR plan, the core is not what makes the video tier wrong.

    .venv/bin/python fixloop/round5/evidence/video_oracle.py > fixloop/round5/evidence/video_oracle.txt
"""
import json
from pathlib import Path

import cv2

from cozmo.config import PipelineConfig
from cozmo.io.posed import PosedFrameSource
from cozmo.io.stray import StrayCapture
from cozmo.pipeline.lidar import build_lidar_plan
from cozmo.schema import Tier
from cozmo.uncertainty.calibration import IntervalBook

REPO = Path(__file__).resolve().parents[3]
RAW = REPO.parent / "data" / "raw"
IDS = {"single_room": "c00a170fe1", "single_scan_floor_only": "1a8384c3f6", "single_scan_with_ceiling": "c7d28f72c6"}
book = IntervalBook.load(REPO / "calibration" / "intervals.json")

for name, capture_id in IDS.items():
    reference = json.loads((REPO / "data" / "tier_inputs" / name / "reference.json").read_text())
    stray = StrayCapture(RAW / capture_id)
    chosen, last = [], -1e9
    for i, row in enumerate(stray._rows):
        if row["timestamp"] - last >= 1.0:
            chosen.append(i)
            last = row["timestamp"]
    frames = list(stray.frames(chosen))
    rgb = stray.load_rgb_batch([f.index for f in frames])
    images = {}
    for i, frame in enumerate(frames):
        if frame.index in rgb:
            images[i] = cv2.resize(rgb[frame.index], (frame.depth.shape[1], frame.depth.shape[0]), interpolation=cv2.INTER_AREA)
        frame.index = i
    print(f"{name}: LiDAR plan {reference['lidar_footprint_m2']:.2f} m2, {len(frames)} keyframes at 1 per second", flush=True)
    for tier, layout in ((Tier.VIDEO, "cellcomplex"), (Tier.LIDAR, "evidence")):
        config = PipelineConfig(detect_damage=False, build_scope=False, layout=layout)
        source = PosedFrameSource(frames=frames, capture_id=name, tier=tier, device_model="iPhone", images=images)
        try:
            plan = build_lidar_plan(source, config, book).plan
            total = plan.total_floor_area.value
            print(f"   LiDAR depth + ARKit poses, as {tier.value:5s} ({layout:11s}): {len(plan.rooms)} rooms, {total:6.2f} m2 "
                  f"({100 * (total / reference['lidar_footprint_m2'] - 1):+.1f}%)", flush=True)
        except Exception as exc:
            print(f"   as {tier.value}: failed: {type(exc).__name__}: {str(exc).splitlines()[0]}", flush=True)
