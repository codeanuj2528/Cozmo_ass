# Cozmo

Handheld iPhone capture to a dimensioned floor plan, damage map and repair scope. Three input
tiers — photos, video, LiDAR — and one output contract.

## Setup

```bash
git clone <this repo> && cd cozmo
./scripts/setup.sh            # Python 3.11–3.12 venv, installs .[dev], reconstructs a ray-traced box
./scripts/fetch_weights.sh    # photo and video tiers only: Depth Anything V2 Metric Indoor, about 95 MB
```

`scripts/setup.sh` ray-traces a 3.60 × 2.80 m room with a 2.50 m ceiling and reconstructs it:
10.08 m², every wall within 6 mm, ceiling 2.499 m, the 0.85 m door at 0.84 m and the 1.10 m window at
1.10 m. `reports/verified/synthetic_room` scores it against those exact dimensions. The LiDAR
tier needs no model weights; photo and video also need `.[ml]` and the weights above. Nothing reaches
the network at run time.

## One command per capture

```bash
.venv/bin/python -m cozmo.cli run --input <capture-dir> --out runs/my_capture
```

The tier is detected from what the directory holds:

| Tier | Input | Detected by |
|---|---|---|
| LiDAR | Stray Scanner export folder | `odometry.csv` present |
| Video | Folder holding one `.mov` / `.mp4` (any capitalisation) | a video file present |
| Photo | Folder of per-room subfolders of stills, `.jpg` / `.heic` | neither of the above |

It writes `plan.json` (the schema-validated output contract), `plan.svg`, `plan.png` and
`run_manifest.json` (git commit, input hash, config, per-stage timings). It refuses to write a plan
in which any id names a room, wall, surface, opening or damage region the plan does not contain.

## The assignment's three samples

Unzip each and point `cozmo run` at the folder inside it that holds `odometry.csv`:

```bash
unzip single_room.zip -d samples/single_room
.venv/bin/python -m cozmo.cli run --input samples/single_room/<folder with odometry.csv> --out runs/single_room
```

| Zip | Walk | Rooms | Floor area, 90% interval | Ceilings | Openings | Damage | Loop closures kept |
|---|---|---|---|---|---|---|---|
| `single_room.zip` (`c00a170fe1`) | 37 s, no upward frames | 5 | 23.47 m² [22.06, 24.88] | unmeasured | 5 doors, 1 window | none | 0 of 16 candidates |
| `single_scan_floor_only.zip` (`1a8384c3f6`) | 115 s, no upward frames | 6 | 50.82 m² [47.77, 53.87] | unmeasured | 9 doors, 4 windows, 1 pass-through | none | 1 of 19 |
| `single_scan_with_ceiling.zip` (`c7d28f72c6`) | 215 s, 16.6% of frames look up | 9 | 49.88 m² [46.89, 52.88] | 2.28–3.08 m, all 9 rooms | 8 doors, 4 windows, 1 pass-through | none | 21 of 41 |

These are the plans in `reports/verified/`, built at `0fd7baa` from the three files unzipped afresh.
Each folder also holds `plan_on_scan.png`, the plan drawn over its own scan. That flat has no tape, so
every accuracy gate on it reports SKIP. What can be checked without tape:

- `single_room.zip` is not one room. The walk covers a living room (9.12 m²), its bathroom (4.20 m²)
  and the lobby between them (1.64 and 1.50 m²), and stands at the mouth of a corridor, which the plan
  draws at 7.02 m² from the floor seen along it (`known_failure_modes.md` §23).
- The two whole-flat scans, registered on their walls by `cozmo repeat`, put 71% of wall cells within
  5 cm of each other. Their footprints are 49.88 and 50.82 m², 1.9% apart, overlapping at an
  intersection-over-union of 0.735; the cell-complex plans published before them were 7% apart at
  0.61. Wall by wall they do not repeat: 0 of 52 walls agree within 1 cm or 0.5%, median difference
  24.7 cm (`reports/verified/repeatability/`).
- Rooms are drawn where they were measured and joined through the doorways found between them. A
  space seen but never walked into is left out and named in `quality.warnings`: 2.37 m² on the
  floor-only scan, 4.92 m² on the with-ceiling scan.
- No damage is reported, and none is visible in the sampled video frames.

## The home flat, against tape

These plans were built at `a92927c`, before the 15 Sep changes to the canonical frame and to how LiDAR
rooms are found, and have not been regenerated since: their raw captures were not on the machine those
changes were made on (`known_failure_modes.md` §23). `scripts/regenerate_verified.sh` rebuilds them.

| Capture | Rooms | Footprint against 28.75 m² | Per room |
|---|---|---|---|
| `163f18d3ac`, long walk, protocol followed | 6 | 29.13 m², +1% | hall −8%, bedroom −39%, bathroom +48% (it holds part of the passage), passage −11%; two rooms outside the tape. The footprint is inside ±5% because those errors offset and a never-walked room left the plan in fix loop round 3 |
| `ae3edc814d`, first walk, no ceiling lap | 4 | 25.49 m², −11% | hall −7%, bedroom −18%, bathroom +2%, passage −24% |
| `5621ec5c54`, the bedroom alone | 2 | 10.88 m² against the bedroom's 9.29 m² | bedroom 8.90 m², −4%, plus 1.98 m² of the passage it was entered from |

Against the tape the gates read 15 PASS, 18 FAIL and 33 SKIP
(`reports/verified/gates/gate_table.txt`). The same table scores the ray-traced room against its exact
dimensions, 11 PASS and 5 SKIP, which shows the measurement is unbiased on a noiseless room and says
nothing about accuracy on a real one. The photo tier reads +238% on 58 stills at 0.5× and +136%
on the hall at 1×; the video tier does not produce a metric plan. `benchmark_report.md` has every
gate and room.

## What runs underneath

- **LiDAR:** sensor depth and ARKit poses. Fusion, walls from a Hough accumulator over measured
  normals, rooms built from wall barriers, doorways and the floor the scan saw (`geometry/layout.py`;
  `--layout cellcomplex` gives the earlier cell complex), and a keyframe pose graph over heading and
  horizontal position with ICP-verified loop closures. No learned model.
- **Photo and video:** Depth Anything V2 Metric Indoor (small) for depth, then the same
  reconstruction as LiDAR.
- **Damage:** classical colour-anomaly and ridge detectors. A finding must be seen from two frames
  on the same patch of a reconstructed wall, and a ruler-straight edge is not a crack. A YAML rule
  engine raises concealed-damage flags naming the rule and every value it tested.
- **Not used:** VGGT, Depth Anything 3, SAM, Grounding DINO or any other learned detector or
  segmenter. `docs/design.md` §12 lists what is not here yet.

## Docker

```bash
docker build -t cozmo .
docker run --rm -v /path/to/stray_export:/capture -v "$PWD/runs:/runs" cozmo run --input /capture --out /runs/plan
```

That image runs the LiDAR tier. For photo and video, build with `--build-arg EXTRAS=dev,ml` and mount
the weights: `-v "$PWD/weights:/app/weights"`. The Dockerfile has not been built as part of this
repository's checks; no Docker engine was available where the plans were regenerated.

## Other commands

```bash
# Every plan in reports/verified/ and its gate table, from the raw captures
./scripts/regenerate_verified.sh

# Score runs against measured ground truth. Gates with no truth behind them report SKIP, never PASS.
.venv/bin/python -m cozmo.cli benchmark --runs reports/verified --ground-truth capture/ground_truth.csv \
    --room-map capture/room_map.json --repeat multiroom_home,multiroom_long \
    --repeat bedroom_solo,multiroom_long --out reports/verified/gates

# Fit split-conformal interval quantiles from measured residuals. Writes nothing if there are none.
.venv/bin/python -m cozmo.cli calibrate --runs reports/verified --ground-truth capture/ground_truth.csv

# Drift and wall-snapping ablation, and the room refinement of fix loop round 3 switched off
.venv/bin/python -m cozmo.cli run --input <dir> --out runs/no_drift --no-drift-correction --no-snap-walls
.venv/bin/python -m cozmo.cli run --input <dir> --out runs/no_refine --no-refine-rooms

# Self-consistency checks on any plan, no ground truth needed
.venv/bin/python scripts/audit_plans.py

# Two captures of one space, registered on their walls and compared room by room and wall by wall
.venv/bin/python -m cozmo.cli repeat --a runs/scan_a --b runs/scan_b --out runs/repeat_ab
```

`cozmo fixloop` runs one capture with wall snapping off and on. It is not the Part 4 fix loop, whose
three rounds are in `fixloop/`.

## Tests

```bash
.venv/bin/python -m pytest -q
```

159 tests. They cover geometry primitives, the canonical frame, room layout, wall registration, the ray-traced box, drift and loop-closure gates, damage
detection, the rule engine, the gates, intervals and the schema contract, including a check that every
id in every verified plan resolves. Several exist because a defect got past review.

## Reading order

- [technical_report.pdf](technical_report.pdf): design decisions, the error budget and the state of the evidence.
- [benchmark_report.md](benchmark_report.md): every gate against the tape, room by room.
- [compliance_matrix.md](compliance_matrix.md): each requirement, where it lives, and its status.
- [known_failure_modes.md](known_failure_modes.md): what does not work, with numbers.

## Where to look

| | |
|---|---|
| What to capture, and how | [capture/PROTOCOL.md](capture/PROTOCOL.md) |
| What was measured with the tape | [capture/RECORDING_SHEET.md](capture/RECORDING_SHEET.md), [capture/ground_truth.csv](capture/ground_truth.csv) |
| Which reconstructed room is which | [capture/room_map.json](capture/room_map.json), `capture/room_identity/` |
| Which tier runs on which device | [capture/DEVICE_MATRIX.md](capture/DEVICE_MATRIX.md) |
| Architecture | [docs/design.md](docs/design.md) |
| The Part 4 fix loop | [fixloop/](fixloop/README.md) |
| The benchmark shot list and how to stage damage | [capture/BENCHMARK_PLAN.md](capture/BENCHMARK_PLAN.md) |

## Layout

```
src/cozmo/
  schema.py        the output contract; every quantity is a Measure with an interval
  config.py        every default, in one place
  cli.py           run / benchmark / calibrate / fixloop
  io/              capture readers, one per input format
  recon/           depth backbone, monocular metric recovery, registration
  geometry/        fusion, planes, levels, walls, openings, cell complex, drift, ICP, assembly
  stitch/          joining separately reconstructed rooms
  damage/          detection, projection to surfaces, concealed-damage rules
  scope/           repair line items
  uncertainty/     split-conformal calibration
  render/          plan drawing, SVG and PNG backends
  bench/           ground truth, gates, repeatability
capture/           protocol, tape, room map and room identity frames
reports/verified/  the published plans and their gate table
fixloop/           the two fix-loop rounds
scripts/           setup, weights, regeneration, accuracy table, plan audit, PDF rendering
tests/             pytest suite and ray-traced fixtures
```

A tier's job is to produce frames carrying intrinsics, metric depth and a pose. After that, all three
tiers run the same reconstruction core. LiDAR is handed all three; photo and video manufacture them.
