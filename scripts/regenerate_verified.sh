#!/usr/bin/env bash
# Regenerate every plan in reports/verified/ and its gate table from the raw captures.
#
#     scripts/regenerate_verified.sh
#
# The captures are not in git. COZMO_RAW and COZMO_DROP point at where they are; the defaults are
# the layout next to this repository that the plans were built from. About 15 minutes on a laptop,
# most of it the photo tier.
set -euo pipefail
cd "$(dirname "$0")/.."

RAW="${COZMO_RAW:-../data/raw}"
DROP="${COZMO_DROP:-../DROP_CAPTURES_HERE}"
PY="${PYTHON:-.venv/bin/python}"

run() {
  echo "== $2"
  "$PY" -m cozmo.cli run --input "$1" --out "reports/verified/$2"
}

run "$RAW/163f18d3ac" multiroom_long
run "$DROP/01_multiroom_lidar/ae3edc814d" multiroom_home
run "$DROP/07_repeat_room_lidar/5621ec5c54" bedroom_solo
run "$RAW/c00a170fe1" single_room
run "$RAW/1a8384c3f6" single_scan_floor_only
run "$RAW/c7d28f72c6" single_scan_with_ceiling
run "$DROP/03_multiroom_photos" multiroom_photos
run "$DROP/03b_multiroom_photos_1x" photos_1x

# One capture at three tiers. The video and photo inputs are links, so the loader sees only a
# video or only stills and cannot read the LiDAR export beside them.
run "$RAW/c00a170fe1" one_room/lidar
mkdir -p reports/verified/one_room/inputs/video_single reports/verified/one_room/inputs/photos_bathroom
ln -sfn "$(cd "$RAW/c00a170fe1" && pwd)/rgb.mp4" reports/verified/one_room/inputs/video_single/room.mp4
ln -sfn "$(cd "$DROP/03_multiroom_photos/bathroom" && pwd)" reports/verified/one_room/inputs/photos_bathroom/bathroom
run reports/verified/one_room/inputs/video_single one_room/video
run reports/verified/one_room/inputs/photos_bathroom one_room/photo

# Exits 2 when any gate fails, which several do; that is the reported result, not an error here.
status=0
"$PY" -m cozmo.cli benchmark --runs reports/verified \
  --ground-truth capture/ground_truth.csv --room-map capture/room_map.json \
  --repeat multiroom_home,multiroom_long --repeat bedroom_solo,multiroom_long \
  --out reports/verified/gates || status=$?
if [ "$status" -ne 0 ] && [ "$status" -ne 2 ]; then
  echo "benchmark failed with exit $status" >&2
  exit "$status"
fi
echo "Plans and reports/verified/gates regenerated."
