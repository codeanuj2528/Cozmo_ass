"""Which part makes a photo room too large: the room layout the core uses for photos, or VGGT's geometry?

For a room's stills, the reconstruction core (build_lidar_plan, drift correction off) is run on two inputs, each
with the two ways the core can find rooms:

  inputs   lidar  LiDAR depth and ARKit poses of the still frames: ideal geometry, as many views as stills
           vggt   VGGT-1B's depth and poses, as the pipeline gets them, scaled by the LiDAR depth ratio
  layouts  cellcomplex  what the core uses for photo and video: faces of the wall-line arrangement
           evidence     what it uses for LiDAR: wall barriers, doorways and interior evidence, then the room
                        refinement (reached here by labelling the source LiDAR)

    oracle_core.py capture:room [capture:room ...]
"""
import json
import re
import sys
from pathlib import Path

import cv2
import numpy as np

REPO = Path("/Users/anujmishra/Desktop/cozmoo/Cozmo_ass")
sys.path.insert(0, str(REPO / "src"))
from cozmo.config import PipelineConfig  # noqa: E402
from cozmo.io.posed import PosedFrameSource  # noqa: E402
from cozmo.io.stray import StrayCapture  # noqa: E402
from cozmo.pipeline.lidar import build_lidar_plan  # noqa: E402
from cozmo.recon.multiview import ViewGeometry, down_axis_outliers, views_to_frames  # noqa: E402
from cozmo.schema import Tier  # noqa: E402
from cozmo.uncertainty.calibration import IntervalBook  # noqa: E402

S = Path("/private/tmp/claude-501/-Users-anujmishra-Desktop-Trend-proper/0f96a43f-0747-4d85-8198-c1e028c36039/scratchpad")
RAW = REPO.parent / "data" / "raw"
IDS = {"single_room": "c00a170fe1", "single_scan_floor_only": "1a8384c3f6", "single_scan_with_ceiling": "c7d28f72c6"}
truth_scale = {(r["capture"], r["room"]): r["depth_ratio_scale"] for r in json.loads((REPO / "fixloop/round4/evidence/intrinsics_check.json").read_text())}
book = IntervalBook.load(REPO / "calibration" / "intervals.json")


def describe(plan):
    parts = []
    for room in sorted(plan.rooms, key=lambda r: -r.floor_area.value):
        span = np.ptp(np.asarray(room.polygon, float), axis=0)
        parts.append(f"{room.floor_area.value:.2f} m2 ({span[0]:.2f} x {span[1]:.2f})")
    return f"{len(plan.rooms)} room(s): " + "; ".join(parts) if parts else "no room"


def run(frames, images, layout):
    tier = Tier.PHOTO if layout == "cellcomplex" else Tier.LIDAR
    config = PipelineConfig(drift_correction=False, detect_damage=False, build_scope=False, layout=layout)
    source = PosedFrameSource(frames=frames, capture_id=layout, tier=tier, device_model="iPhone", images=images)
    try:
        return describe(build_lidar_plan(source, config, book).plan)
    except Exception as exc:  # the core raises when it finds no floor
        return f"failed: {type(exc).__name__}: {exc}"


for spec in sys.argv[1:]:
    capture, room = spec.split(":")
    reference = json.loads((REPO / "data" / "tier_inputs" / capture / "reference.json").read_text())
    truth = next(r for r in reference["rooms"] if r["room_id"] == room)
    lidar_span = np.ptp(np.asarray(truth["polygon"], float), axis=0)
    print(f"== {capture} {room}: LiDAR room {truth['floor_area_m2']:.2f} m2 ({lidar_span[0]:.2f} x {lidar_span[1]:.2f})", flush=True)

    stray = StrayCapture(RAW / IDS[capture])
    row_of = {frame: row for row, frame in enumerate(stray.frame_indices())}
    numbers = [int(s["frame"]) for s in truth["stills"]]
    lidar_frames = list(stray.frames([row_of[n] for n in numbers]))
    assert [f.index for f in lidar_frames] == numbers, "LiDAR frames are not the still frames"
    rgb = stray.load_rgb_batch(numbers)
    lidar_images = {}
    for i, frame in enumerate(lidar_frames):
        lidar_images[i] = cv2.resize(rgb[frame.index], (frame.depth.shape[1], frame.depth.shape[0]), interpolation=cv2.INTER_AREA)
        frame.index = i

    data = np.load(S / "vggt_views" / f"{capture}__{room}.npz")
    folder = REPO / "data" / "tier_inputs" / capture / "photo" / room
    by_frame = {int(re.findall(r"\d+", p.stem)[-1]): p for p in folder.glob("*.jpg")}
    images = [cv2.cvtColor(cv2.imread(str(by_frame[int(f)])), cv2.COLOR_BGR2RGB) for f in data["frames"]]
    size = tuple(int(v) for v in data["image_size"])
    views = [ViewGeometry(data["depth"][i], data["confidence"][i], data["intrinsics"][i], data["rotation_wc"][i],
                          data["centre"][i], size) for i in range(len(data["frames"]))]
    up, _ = down_axis_outliers(views)
    vggt_frames, vggt_images = views_to_frames(views, truth_scale[(capture, room)], up, 0, images)

    for layout in ("cellcomplex", "evidence"):
        print(f"   lidar {layout:11s}: {run(lidar_frames, lidar_images, layout)}", flush=True)
        print(f"   vggt  {layout:11s}: {run(vggt_frames, vggt_images, layout)}", flush=True)
