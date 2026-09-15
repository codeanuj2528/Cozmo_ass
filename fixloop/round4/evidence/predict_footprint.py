"""The photo-tier footprint each export would get from MoGe-2's scale error alone, and which rooms that cannot cover.

Inputs: evaluate_moge_fov.py's output (MoGe-2's scale for each room, for each way of giving it the field of view),
evaluate_intrinsics.py's output (the true scale of each room's reconstruction) and pose_checks.json (which stills
VGGT-1B posed wrong). The true scale is the median ratio of LiDAR depth to VGGT depth on the same pixels, which no
error in a pose can reach; evaluate_moge_fov.py reports against the similarity fitted to LiDAR points, which a
wrongly posed still distorts, so its errors are re-referenced here.

A room's area goes as the square of its scale, so with the geometry otherwise right its area error is
(1 + e)^2 - 1, and an export's footprint error is those errors weighted by the LiDAR areas of its photo rooms. A
room with a wrongly posed still is outside what this predicts, and is listed.

    python fixloop/round4/evidence/predict_footprint.py moge_fov.json intrinsics_check.json pose_checks.json [variant]
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[3]
rows = json.loads(Path(sys.argv[1]).read_text())
truth = {(r["capture"], r["room"]): r["depth_ratio_scale"] for r in json.loads(Path(sys.argv[2]).read_text())}
checks = json.loads(Path(sys.argv[3]).read_text())
variants = ("exif", "vggt", "none")
wrong = defaultdict(int)
for still in checks:
    wrong[(still["capture"], still["room"])] += int(still["wrong"])

for row in rows:
    factor_truth = truth[(row["capture"], row["room"])]
    for label in variants:
        row[f"{label}_error"] = (1.0 + row[label]) * row["true_scale"] / factor_truth - 1.0

print("scale error against LiDAR depth, over the 15 rooms (and over the rooms with no wrongly posed still):")
for label in variants:
    errors = np.array([r[f"{label}_error"] for r in rows])
    clean = np.array([r[f"{label}_error"] for r in rows if not wrong[(r["capture"], r["room"])]])
    print(f"  field of view {label:5s} median {100 * np.median(errors):+.1f}%, range {100 * errors.min():+.1f}% to "
          f"{100 * errors.max():+.1f}%  (clean rooms: median {100 * np.median(clean):+.1f}%, range "
          f"{100 * clean.min():+.1f}% to {100 * clean.max():+.1f}%)")

variant = sys.argv[4] if len(sys.argv) > 4 else "exif"
by_capture = defaultdict(list)
for row in rows:
    by_capture[row["capture"]].append(row)
print(f"\nfootprint from scale alone, MoGe-2 given the field of view from {variant}:")
for capture, rooms in sorted(by_capture.items()):
    reference = json.loads((REPO / "data" / "tier_inputs" / capture / "reference.json").read_text())
    areas = {room["room_id"]: float(room["floor_area_m2"]) for room in reference["rooms"]}
    total = sum(areas[r["room"]] for r in rooms)
    change = sum(areas[r["room"]] * ((1.0 + r[f"{variant}_error"]) ** 2 - 1.0) for r in rooms) / total
    clean = [r for r in rooms if wrong[(capture, r["room"])] == 0]
    clean_total = sum(areas[r["room"]] for r in clean)
    clean_change = sum(areas[r["room"]] * ((1.0 + r[f"{variant}_error"]) ** 2 - 1.0) for r in clean) / max(clean_total, 1e-9)
    print(f"  {capture}: {100 * change:+.1f}% over {total:.2f} m2; {100 * clean_change:+.1f}% over the {len(clean)} "
          f"rooms with no wrongly posed still ({clean_total:.2f} m2)")
    for r in rooms:
        flag = f"  {wrong[(capture, r['room'])]} still(s) posed wrong" if wrong[(capture, r["room"])] else ""
        error = r[f"{variant}_error"]
        print(f"      {r['room']}: {areas[r['room']]:6.2f} m2, scale {100 * error:+5.1f}%, area {100 * ((1 + error) ** 2 - 1):+6.1f}%{flag}")
