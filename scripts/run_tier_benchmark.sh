#!/usr/bin/env bash
# The photo and video tiers on inputs made from the assignment's three Stray exports, each plan scored
# against the LiDAR plan of the same walk.
#
#     scripts/run_tier_benchmark.sh OUT_DIR
#
# For each export: builds data/tier_inputs/<name>/ with scripts/make_tier_inputs.py if it is missing,
# runs `cozmo run` on its photo and its video capture, and scores each plan with `cozmo tiers` against
# reports/verified/<name>/, the LiDAR run of that walk. Writes OUT_DIR/<name>_<tier>/ (plan, manifest,
# tiers.json), OUT_DIR/timing.csv and OUT_DIR/tier_table.txt.
#
# COZMO_RAW points at the unzipped exports (default ../data/raw). COZMO_TIERS picks the tiers to run
# (default "photo video"). The reference is a reconstruction, not tape; see src/cozmo/bench/tiers.py.

set -uo pipefail
cd "$(dirname "$0")/.."

OUT="${1:?usage: scripts/run_tier_benchmark.sh OUT_DIR}"
RAW="${COZMO_RAW:-../data/raw}"
PY="${PYTHON:-.venv/bin/python}"
TIERS="${COZMO_TIERS:-photo video}"

mkdir -p "$OUT"
echo "capture,tier,seconds,exit_code" > "$OUT/timing.csv"

for pair in c00a170fe1:single_room 1a8384c3f6:single_scan_floor_only c7d28f72c6:single_scan_with_ceiling; do
  id="${pair%%:*}"
  name="${pair##*:}"
  inputs="data/tier_inputs/$name"
  if [ ! -f "$inputs/reference.json" ]; then
    echo "==> building $inputs from $RAW/$id"
    "$PY" scripts/make_tier_inputs.py --capture "$RAW/$id" --out "$inputs" || { echo "could not build $inputs" >&2; continue; }
  fi
  for tier in $TIERS; do
    run="$OUT/${name}_$tier"
    rm -rf "$run"
    start=$(date +%s)
    "$PY" -m cozmo.cli run --input "$inputs/$tier" --out "$run" > "$OUT/${name}_$tier.log" 2>&1
    code=$?
    seconds=$(( $(date +%s) - start ))
    echo "$name,$tier,$seconds,$code" >> "$OUT/timing.csv"
    if [ "$code" -eq 0 ]; then
      "$PY" -m cozmo.cli tiers --run "$run" --reference "$inputs/reference.json" \
        --lidar-run "reports/verified/$name" >> "$OUT/${name}_$tier.log" 2>&1
    fi
    printf '  %-26s %-6s %5ss  exit=%s\n' "$name" "$tier" "$seconds" "$code"
  done
done

"$PY" - "$OUT" > "$OUT/tier_table.txt" <<'PY'
import json, sys
from pathlib import Path
from cozmo.bench.gates import GateResult, Status, format_table

results = []
for path in sorted(Path(sys.argv[1]).glob("*/tiers.json")):
    for gate in json.loads(path.read_text())["gates"]:
        results.append(GateResult(gate["gate"], gate["scope"], gate["tier"], gate["measured"], gate["threshold"],
                                  Status(gate["status"]), gate.get("detail", {})))
print(format_table(results))
PY
cat "$OUT/tier_table.txt"
