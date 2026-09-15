#!/usr/bin/env bash
# One-command setup: interpreter, venv, install, ray-traced smoke test.
#
#     scripts/setup.sh && scripts/fetch_weights.sh && source .venv/bin/activate
#
# All three tiers are installed: the photo and video tiers need torch, VGGT and MoGe-2, and their weights come from
# scripts/fetch_weights.sh. `scripts/setup.sh --lidar-only` skips the models for a machine that will only run the
# LiDAR tier, which needs none. VGGT and MoGe are installed from pinned upstream commits without their own
# dependency pins (VGGT's asks for numpy<2); the versions this was run with are the ones pyproject.toml allows.
#
# Python 3.11-3.12 only: the project pin is >=3.11,<3.13. On a Mac where
# `python3` is 3.13, this script searches for a supported interpreter instead
# of failing halfway through pip.

set -euo pipefail
cd "$(dirname "$0")/.."

VENV="${VENV:-.venv}"
PY_MIN_MINOR=11
PY_MAX_MINOR=12

python_ok() {
  local v major minor
  v="$("$1" -c 'import sys;print(f"{sys.version_info.major} {sys.version_info.minor}")' 2>/dev/null)" || return 1
  # ${var# *} needs a leading space. "3 12" has none, so minor stayed "3 12"
  # and every interpreter failed the numeric test.
  major="${v%% *}"
  minor="${v##* }"
  [ "$major" = "3" ] && [ "$minor" -ge "$PY_MIN_MINOR" ] && [ "$minor" -le "$PY_MAX_MINOR" ]
}

PYTHON="${PYTHON:-}"
if [ -n "$PYTHON" ]; then
  python_ok "$PYTHON" || {
    echo "PYTHON=$PYTHON is $("$PYTHON" --version 2>&1), need 3.$PY_MIN_MINOR-3.$PY_MAX_MINOR." >&2
    exit 1
  }
else
  for candidate in python3.12 python3.11 python3 /usr/bin/python3 \
      "$HOME/.local/bin/python3.12" "$HOME/.local/bin/python3.11"; do
    command -v "$candidate" >/dev/null 2>&1 || continue
    if python_ok "$candidate"; then PYTHON="$candidate"; break; fi
  done
fi

if [ -z "$PYTHON" ]; then
  echo "No CPython 3.$PY_MIN_MINOR-3.$PY_MAX_MINOR on PATH." >&2
  echo "  brew install python@3.12   &&  scripts/setup.sh" >&2
  echo "  PYTHON=/path/to/python3.12 scripts/setup.sh" >&2
  exit 1
fi

echo "==> creating $VENV with $PYTHON ($($PYTHON --version 2>&1))"
[ -d "$VENV" ] || "$PYTHON" -m venv "$VENV"

echo "==> upgrading pip"
"$VENV/bin/python" -m pip install --quiet --upgrade pip setuptools wheel

if [ "${1:-}" = "--lidar-only" ]; then
  echo "==> installing cozmo [dev], LiDAR tier only"
  "$VENV/bin/python" -m pip install --quiet -e ".[dev]"
else
  echo "==> installing cozmo [dev,ml]"
  "$VENV/bin/python" -m pip install --quiet -e ".[dev,ml]"
  echo "==> installing VGGT and MoGe-2 at pinned commits"
  "$VENV/bin/python" -m pip install --quiet --no-deps \
    "vggt @ https://github.com/facebookresearch/vggt/archive/a288dd0f14786c93483e45524328726ab7b1b4ce.zip" \
    "moge @ https://github.com/microsoft/MoGe/archive/74fbce054ebed49800de42d0ad0e83495065719a.zip" \
    "utils3d_moge @ https://github.com/EasternJournalist/utils3d-moge/archive/62f09d58509485564e24d5d9f6aac9ee9ebc0c37.zip" \
    "pipeline @ https://github.com/EasternJournalist/pipeline/archive/1c511390d90226c00c101f34b84df26a0f8789b4.zip"
fi

echo "==> ray-traced box (3.60 x 2.80 x 2.50 m)"
"$VENV/bin/python" - <<'PY'
from pathlib import Path
from tests.fixtures.raytrace_room import write_capture
write_capture(Path("out/synthetic_room"), drop_ceiling=False)
print("    wrote out/synthetic_room")
PY

echo "==> smoke test: reconstructing the synthetic room"
"$VENV/bin/python" -m cozmo.cli run \
    -i out/synthetic_room \
    -o out/_setup_check

"$VENV/bin/python" - <<'PY'
import json
from pathlib import Path
plan = json.loads(Path("out/_setup_check/plan.json").read_text())
area = plan["total_floor_area"]["value"]
rooms = len(plan["rooms"])
ceiling = plan["rooms"][0].get("ceiling_height") if plan["rooms"] else None
ceiling_s = f"{ceiling['value']:.2f} m" if ceiling else "unmeasured"
print(f"    {rooms} room(s), footprint {area:.2f} m2, ceiling {ceiling_s}")
print("    truth: 1 room, 10.08 m2, ceiling 2.50 m")
PY

cat <<'EOF'

Setup complete.

    source .venv/bin/activate
    python -m cozmo.cli run -i <capture-dir> -o runs/my_capture

Photo and video tiers also need the model weights, about 6 GB:
    scripts/fetch_weights.sh
EOF
