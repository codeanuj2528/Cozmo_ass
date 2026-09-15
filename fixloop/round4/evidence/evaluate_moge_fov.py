"""Room scale from MoGe-2 ViT-L given EXIF's field of view, VGGT-1B's, or none, against the scale LiDAR implies.

The true scale of a room's VGGT reconstruction (metres per model unit) is the similarity fitted to LiDAR in
evaluate_intrinsics.py. Each variant's scale is what cozmo.recon.metric_scale computes from MoGe-2's depth.
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
from cozmo.pipeline.photo import MULTIVIEW_LONG_SIDE  # noqa: E402
from cozmo.recon.metric_scale import get_metric_depth, horizontal_fov_deg, scale_from_metric_depth  # noqa: E402
from cozmo.recon.multiview import ViewGeometry  # noqa: E402

CACHE, TRUTH = Path(sys.argv[1]), Path(sys.argv[2])
truth = {(r["capture"], r["room"]): r["vggt"]["scale"] for r in json.loads(TRUTH.read_text())}
model = get_metric_depth(REPO / "weights")
rows = []
for path in sorted(CACHE.glob("*.npz")):
    data = np.load(path)
    capture, room = path.stem.split("__")
    folder = REPO / "data" / "tier_inputs" / capture / "photo" / room
    by_frame = {int(re.findall(r"\d+", p.stem)[-1]): p for p in folder.glob("*.jpg")}
    focal = float(json.loads(str(data["focal_35mm"])))
    views, images = [], []
    for i, frame in enumerate(data["frames"]):
        rgb = read_image(by_frame[int(frame)])
        factor = MULTIVIEW_LONG_SIDE / max(rgb.shape[:2])
        if factor < 1.0:
            rgb = cv2.resize(rgb, (int(round(rgb.shape[1] * factor)), int(round(rgb.shape[0] * factor))), interpolation=cv2.INTER_AREA)
        images.append(rgb)
        views.append(ViewGeometry(data["depth"][i], data["confidence"][i], data["intrinsics"][i], data["rotation_wc"][i], data["centre"][i], tuple(data["image_size"])))
    height, width = images[0].shape[:2]
    exif_fov = float(np.degrees(2 * np.arctan(width / 2 / (max(width, height) * focal / 36.0))))
    variants = {
        "exif": [exif_fov] * len(views),
        "vggt": [horizontal_fov_deg(v.intrinsics, v.depth.shape[1]) for v in views],
        "none": [None] * len(views),
    }
    row = {"capture": capture, "room": room, "true_scale": truth[(capture, room)]}
    for label, fovs in variants.items():
        scale = scale_from_metric_depth(views, [model.estimate(image, fov) for image, fov in zip(images, fovs)])
        row[label] = scale.factor / row["true_scale"] - 1.0
    rows.append(row)
    print(f"{capture:26s} {room}: MoGe-2 scale error given EXIF FOV {100 * row['exif']:+6.1f}%, VGGT FOV {100 * row['vggt']:+6.1f}%, no FOV {100 * row['none']:+6.1f}%", flush=True)

Path(sys.argv[3]).write_text(json.dumps(rows, indent=2))
for label in ("exif", "vggt", "none"):
    errors = np.array([r[label] for r in rows])
    print(f"{label:5s} median {100 * np.median(errors):+.1f}%, range {100 * errors.min():+.1f}% to {100 * errors.max():+.1f}%, median |e| {100 * np.median(np.abs(errors)):.1f}%")
