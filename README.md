# Cozmo AI Case Study — Indoor Reconstruction Engine

Handheld iPhone capture to a dimensioned floor plan, damage map and repair scope. Three input tiers — photos, video, LiDAR — under one output contract.

---

## Quick Start (Fresh Machine Setup)

```bash
git clone <this-repo> && cd cozmo
./scripts/setup.sh                                   # Virtualenv setup & synthetic box test
./scripts/fetch_weights.sh                           # Weights for photo/video depth backbone
```

### Try it now on synthetic data (No capture device needed)

```bash
# Reconstruct synthetic box room
.venv/bin/python -c 'from pathlib import Path; from tests.fixtures.raytrace_room import write_capture; write_capture(Path("out/synthetic_room"), drop_ceiling=False)'
.venv/bin/python -m cozmo.cli run --input out/synthetic_room --out out/synthetic_run
open out/synthetic_run/plan.png
```

---

## Machine Learning & Heavy Vision Model Stack

The pipeline integrates state-of-the-art vision models for multi-tier capture processing:

* **LiDAR Tier**: Sensor Depth + RANSAC Total-Least-Squares + ICP Pose Graph Optimization + TSDF Mesh Fusion.
* **Photo / Video Tier**: Depth Anything V2 / DA3-Giant monocular depth backbone + VGGT-Omega geometry tracking.
* **Damage & Openings**: Grounding DINO text-conditioned candidate detection + SAM 3.1 open-vocabulary segmentation.

---

## Directory Structure

```
cozmo/
├── src/cozmo/                 # Core Python engine source code
│   ├── bench/                 # Benchmark evaluation & gate scoring
│   ├── damage/                # Damage detection & rule engine
│   ├── geometry/              # Cell complex solver, RANSAC, plane fitting
│   ├── io/                    # Sensor stream & capture format parsers
│   ├── pipeline/              # LiDAR, Photo, and Video reconstruction pipelines
│   ├── recon/                 # Monocular backbone & keyframe selection
│   ├── render/                # SVG & PNG floorplan renderers
│   ├── scope/                 # Scope line item generation
│   ├── stitch/                # Loop closure & pose graph stitching
│   └── uncertainty/           # Split-conformal calibration engine
├── capture/                   # Ground truth CSVs & room identity mappings
├── data/captures/             # Extracted LiDAR sample capture datasets
├── fixloop/                   # Part 4 Fix Loop before/after comparison runs
├── out/                       # Generated floorplan plans, SVGs, PNGs & benchmarks
├── tests/                     # Pytest unit test suite (123 tests)
├── Dockerfile                 # Production Docker container definition
├── pyproject.toml             # Dependency & environment specification
└── README.md                  # System documentation
```

---

## Pre-built Real Sample Capture Datasets

The repository includes pre-built pipeline outputs and benchmark runs for all sample datasets:

| Capture ID | Description | Input Tier | Rooms | Property Area | Status |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **`c00a170fe1`** | Single Room Scan (`single_room.zip`) | LiDAR | 4 | 26.90 m² | PASS |
| **`c7d28f72c6`** | Full Scan with Ceiling (`single_scan_with_ceiling.zip`) | LiDAR | 6 | 50.89 m² | PASS |
| **`1a8384c3f6`** | Floor-Only Scan (`single_scan_floor_only.zip`) | LiDAR | 8 | 42.77 m² | PASS |
| **`163f18d3ac`** | Multi-Room Flat Walk | LiDAR | 5 | 25.25 m² | PASS |
| **`demo_fourroom`**| Synthetic L-Shaped Suite | LiDAR | 2 | 19.87 m² | PASS |
| **`demo_office`**  | Synthetic Office Suite | LiDAR | 2 | 52.49 m² | PASS |

To re-run any capture directory through the reconstruction engine:

```bash
.venv/bin/python -m cozmo.cli run --input data/captures/c7d28f72c6 --out out/c7d28f72c6
```

---

## Docker Container Execution

Build and run the pipeline inside a Docker container:

```bash
# Build the Docker image
docker build -t cozmo-ai .

# Run CLI help inside Docker
docker run --rm cozmo-ai --help

# Run reconstruction pipeline on a capture directory
docker run --rm -v $(pwd):/app cozmo-ai run --input data/captures/c00a170fe1 --out out/docker_run
```

---

## Part 4 Fix Loop CLI Execution

Run the fix loop before/after comparison pipeline on any capture:

```bash
.venv/bin/python -m cozmo.cli fixloop --input data/captures/c7d28f72c6 --out fixloop/run_c7d28f72c6
```

This generates:
* `before_run.json` — Baseline un-ablated pipeline output.
* `after_run.json` — Shipped fixed pipeline output.

---

## Benchmark & Gate Scoring Suite

To score all runs against measured physical ground truth (`ground_truth.csv`) and output the official gate table:

```bash
.venv/bin/python -m cozmo.cli benchmark \
    --runs out \
    --ground-truth capture/ground_truth.csv \
    --room-map capture/room_map.json \
    --out out/benchmark_all
```

Outputs written to `out/benchmark_all/`:
* `gate_table.txt` — Plaintext gate matrix (PASS / FAIL / SKIP).
* `results.json` — JSON formatted metric breakdown.

---

## Unit Testing & Verification

Run the full pytest suite (123 unit tests, 0 warnings, 100% pass):

```bash
PYTHONPATH=src .venv/bin/pytest -q
```

---

## Deliverables & Documentation Index

- [technical_report.pdf](file:///Users/anuj/Desktop/cozmo_ass/cozmo/technical_report.pdf) — Architectural design, error budget, 5-page brief.
- [benchmark_report.md](file:///Users/anuj/Desktop/cozmo_ass/cozmo/benchmark_report.md) — Gate performance, room-by-room accuracy evaluation.
- [compliance_matrix.md](file:///Users/anuj/Desktop/cozmo_ass/cozmo/compliance_matrix.md) — Case study requirement compliance matrix.
- [known_failure_modes.md](file:///Users/anuj/Desktop/cozmo_ass/cozmo/known_failure_modes.md) — Failure modes and boundary behavior audit.
- [capture_protocol.md](file:///Users/anuj/Desktop/cozmo_ass/cozmo/capture_protocol.md) — Operator scanning guidelines.
