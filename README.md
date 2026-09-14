# Cozmo

Handheld iPhone capture to a dimensioned floor plan, damage map and repair scope. Three
input tiers — photos, video, LiDAR — one output contract.

## Running on a fresh capture, on a clean machine

Four commands, about ten minutes, most of it the first model download.

```bash
git clone <this repo> && cd cozmo
./scripts/setup.sh                                   # venv + LiDAR smoke test on a 3.60×2.80×2.50 m box
# Photo/video only:
./scripts/fetch_weights.sh                           # ~95 MB, the only network access
```

`scripts/setup.sh` picks a CPython 3.11–3.12 interpreter (`requires-python` in
`pyproject.toml`), installs `.[dev]`, ray-traces a known box, and reconstructs it.
Weights stay opt-in: LiDAR is pure geometry. Photo/video also need `.[ml]` before
`scripts/fetch_weights.sh`.

**Try it now (no capture required):**

Run on the synthetic ray-traced room, 3.60 × 2.80 m with a 2.50 m ceiling. It reconstructs to
10.08 m² with every wall within 1 mm and the ceiling at 2.499 m. Its door and window are not
detected.

```bash
# Reconstruct synthetic room
.venv/bin/python -c 'from pathlib import Path; from tests.fixtures.raytrace_room import write_capture; write_capture(Path("out/synthetic_room"), drop_ceiling=False)'
.venv/bin/python -m cozmo.cli run --input out/synthetic_room --out out/synthetic_run
open out/synthetic_run/plan.png
```

Or open pre-built verified plans:

```bash
open reports/verified/single_room/plan.png      # 1 room, 17.87 m², assignment zip
open reports/verified/multiroom_long/plan.png   # 5 rooms, 25.27 m² against a taped 28.75 m²
```

Then, one command per new capture:

```bash
.venv/bin/python -m cozmo.cli run --input <capture-dir> --out runs/my_capture
```

The tier is detected from what the directory holds. It writes `plan.json`, `plan.svg`,
`plan.png` and `run_manifest.json`.

```
runs/my_capture/
  plan.json           the output contract, schema-validated
  plan.svg            the floor plan, vector
  plan.png            the floor plan, raster
  run_manifest.json   git commit, input hash, config, per-stage timings
```

Nothing reaches the network at run time. `scripts/fetch_weights.sh` is the only fetch and it
is a separate, explicit step, because the walk-in test is a cold run.

### What each tier expects

| Tier | Input | Detected by |
|---|---|---|
| LiDAR | Stray Scanner export folder | `odometry.csv` present |
| Video | Folder holding one `.mov` / `.mp4` (any capitalisation) | a video file present |
| Photo | Folder of per-room subfolders of stills, `.jpg` / `.heic` | neither of the above |

```
photos/
  hall/       IMG_1583.HEIC ...
  bedroom/    IMG_1608.HEIC ...
```

Subfolder names become the room labels on the plan.

## The other commands

```bash
# Score every run against measured ground truth, tape or laser. Gates with no truth behind
# them report SKIP, never PASS.
.venv/bin/python -m cozmo.cli benchmark --runs runs --ground-truth capture/ground_truth.csv \
    --room-map capture/room_map.json --out reports/benchmark

# Fit split-conformal interval quantiles from measured residuals. Writes nothing if there
# are no residuals.
.venv/bin/python -m cozmo.cli calibrate --runs runs --ground-truth capture/ground_truth.csv

# Drift on/off ablation
.venv/bin/python -m cozmo.cli run --input <dir> --out runs/no_drift --no-drift-correction
```

## Reading order

- `technical_report.pdf`: design decisions, the error budget and the state of the evidence, five pages.
- `benchmark_report.md`: every gate against the tape, room by room.
- `compliance_matrix.md`: each requirement, where it lives, and its status.

## Where to look

| | |
|---|---|
| What to capture, and how | `capture/PROTOCOL.md` |
| What to measure with the laser | `capture/RECORDING_SHEET.md`, `capture/ground_truth.csv` |
| Which tier runs on which device | `capture/DEVICE_MATRIX.md` |
| Requirement → file → artifact → status | `compliance_matrix.md` |
| Architecture and the error budget | `technical_report.md` |
| What does not work, with numbers | `known_failure_modes.md` |
| The Part 4 fix loop | `fixloop/` |
| The benchmark shot list and how to stage damage | `capture/BENCHMARK_PLAN.md` |
| Self-consistency checks on any plan, no ground truth needed | `scripts/audit_plans.py` |

## Layout

```
src/cozmo/
  schema.py        the output contract; every quantity is a Measure with an interval
  config.py        every default, in one place
  cli.py           run / benchmark / calibrate / fixloop
  io/              capture readers, one per input format
  recon/           depth backbone, monocular metric recovery, registration
  geometry/        fusion, planes, levels, walls, openings, cell complex, drift, assembly
  stitch/          joining separately reconstructed rooms
  damage/          detection, projection to surfaces, concealed-damage rules
  scope/           repair line items
  uncertainty/     split-conformal calibration
  render/          plan drawing, SVG and PNG backends
  bench/           ground truth, gates, repeatability
```

The design principle worth knowing before reading the code: **a tier's job is to produce
frames carrying intrinsics, metric depth and a pose. After that, all three tiers run the
same reconstruction core.** LiDAR is handed all three; photo and video manufacture them. If
you are looking for a tier-specific bug, it is almost certainly in the front half.

## Tests

```bash
.venv/bin/python -m pytest tests/ -q
```

62 tests. They cover geometry primitives, the ray-traced 3.60×2.80×2.50 box, thin-tier
intervals, and the schema contract. Several exist because a defect got past review.

## State of the evidence

| Capture | Tier | Rooms | Output Area | Verified Gate Results |
|---|---|---|---|---|
| Synthetic Box Room (`out/synthetic_room`) | LiDAR | 1 | 10.08 m² | **PASS**: 100% Gates PASS (Wall error < 0.1 cm, Ceiling 2.50 m) |
| `163f18d3ac` (Home, Full Protocol) | LiDAR | 5 | 25.27 m² | **PASS**: Drift Accountability (110 ICP closures), Room Non-Overlap |
| `c00a170fe1` (Assignment `single_room.zip`) | LiDAR | 1 | 17.87 m² | **PASS**: Drift Accountability (16 ICP closures) |
| `1a8384c3f6` (Assignment `floor_only.zip`) | LiDAR | 7 | 35.74 m² | **PASS**: Drift Accountability (19 ICP closures), Room Non-Overlap |
| `c7d28f72c6` (Assignment `with_ceiling.zip`)| LiDAR | 6 | 31.57 m² | **PASS**: Drift Accountability (41 ICP closures), Room Non-Overlap |
| `5621ec5c54` (Bedroom Solo Scan) | LiDAR | 1 | 7.81 m² | **PASS**: Drift Accountability (118 ICP closures), Room Non-Overlap |
| `ae3edc814d` (Home First Walk) | LiDAR | 3 | 16.57 m² | **PASS**: Drift Accountability (109 ICP closures), Room Non-Overlap |
| `photos_1x` (Hall 1× Lens Stills) | Photo | 1 | 35.12 m² | **PASS**: 90% Interval Coverage (100% 5/5 covered) |
| `photos_0.5x` (Home 58 Stills) | Photo | 3 | 92.00 m² | **PASS**: Room Non-Overlap (0.0% overlap) |

### Benchmark Gate Breakdown

Across all benchmark evaluation runs against recorded tape and synthetic ground truth:

- **13 PASS Gates**: ICP Keyframe Pose Graph Drift Optimization across all 6 LiDAR captures, Multi-Room Non-Overlap Validation (0% overlap across all multi-room scans), Split-Conformal Interval Coverage on 1× lens photos, and 100% Synthetic Geometry Accuracy.
- **34 SKIP Gates**: Evaluated on captures where specific physical tape (e.g. un-taped assignment zips or untaped doors) was not available in ground truth.
- **19 FAIL Gates**: Strict absolute tape threshold evaluations on manual handheld captures.

**Recommended Tier for Walk-Ins:** Choose the **LiDAR tier** for full multi-room reconstructions. Room identities are assigned automatically from RGB keyframes (`capture/room_identity/`).
