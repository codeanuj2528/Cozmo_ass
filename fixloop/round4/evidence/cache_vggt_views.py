"""VGGT-1B on the 15 photo rooms, through cozmo.recon.multiview, saved with ARKit's poses of the same stills.

One npz per room, so checks on the model's output can be compared without running the model again.
"""
import json
import re
import sys
import time
from pathlib import Path

import cv2
import numpy as np

REPO = Path("/Users/anujmishra/Desktop/cozmoo/Cozmo_ass")
sys.path.insert(0, str(REPO / "src"))
from cozmo.io.discover import read_image  # noqa: E402
from cozmo.io.stray import StrayCapture  # noqa: E402
from cozmo.pipeline.photo import MULTIVIEW_LONG_SIDE  # noqa: E402
from cozmo.recon.multiview import get_multiview_backbone  # noqa: E402
from cozmo.util.orientation import camera_roll  # noqa: E402

OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)
RAW = REPO.parent / "data" / "raw"
PAIRS = {"single_room": "c00a170fe1", "single_scan_floor_only": "1a8384c3f6", "single_scan_with_ceiling": "c7d28f72c6"}

backbone = get_multiview_backbone(REPO / "weights")
for name, capture_id in PAIRS.items():
    root = REPO / "data" / "tier_inputs" / name
    reference = json.loads((root / "reference.json").read_text())
    capture = StrayCapture(RAW / capture_id)
    poses = capture.poses()
    row_of = {frame: row for row, frame in enumerate(capture.frame_indices())}
    for room in reference["rooms"]:
        stills = room.get("stills") or []
        if len(stills) < 2:
            continue
        folder = root / "photo" / room["photo_folder"]
        by_frame = {int(re.findall(r"\d+", p.stem)[-1]): p for p in folder.glob("*.jpg")}
        images = []
        for still in stills:
            rgb = read_image(by_frame[int(still["frame"])])
            factor = MULTIVIEW_LONG_SIDE / max(rgb.shape[:2])
            if factor < 1.0:
                size = (int(round(rgb.shape[1] * factor)), int(round(rgb.shape[0] * factor)))
                rgb = cv2.resize(rgb, size, interpolation=cv2.INTER_AREA)
            images.append(rgb)
        started = time.perf_counter()
        if len(sys.argv) > 2 and sys.argv[2] == "reverse":
            # The model takes the first image as its reference; the same stills in reverse order show whether a pose
            # depends on which still came first. Views are put back in the original order before saving.
            result = backbone.reconstruct(images[::-1])
            result.views = result.views[::-1]
        else:
            result = backbone.reconstruct(images)
        seconds = time.perf_counter() - started
        arkit = [poses[row_of[int(s["frame"])]] for s in stills]
        np.savez_compressed(
            OUT / f"{name}__{room['room_id']}.npz",
            depth=np.stack([v.depth for v in result.views]).astype(np.float32),
            confidence=np.stack([v.confidence for v in result.views]).astype(np.float32),
            intrinsics=np.stack([v.intrinsics for v in result.views]),
            rotation_wc=np.stack([v.rotation_wc for v in result.views]),
            centre=np.stack([v.centre for v in result.views]),
            arkit_rotation_wc=np.stack([p[:3, :3] @ camera_roll(int(s["turns"])) for p, s in zip(arkit, stills)]),
            arkit_centre=np.stack([p[:3, 3] for p in arkit]),
            frames=np.array([int(s["frame"]) for s in stills]),
            image_size=np.array([images[0].shape[1], images[0].shape[0]]),
            focal_35mm=np.array(json.dumps(reference.get("focal_35mm"))),
            seconds=np.array(seconds),
        )
        print(f"{name} {room['room_id']}: {len(images)} stills {seconds:.1f}s", flush=True)
