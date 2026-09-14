# Cozmo AI Case Study — Indoor Reconstruction Engine

Handheld iPhone capture to a dimensioned floor plan, damage map and repair scope. Three input tiers — photos, video, LiDAR — under one output contract.

---

## ⚡ Quick Start (Fresh Machine Setup)

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

## 📊 Pre-built Real Sample Capture Datasets

The repository includes pre-built pipeline outputs and benchmark runs for all sample datasets:

| Capture ID | Description | Input Tier | Rooms | Property Area | Status |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **`c00a170fe1`** | Single Room Scan (`single_room.zip`) | LiDAR | 4 | `26.90 m²` | ✅ PASS |
| **`c7d28f72c6`** | Full Scan with Ceiling (`single_scan_with_ceiling.zip`) | LiDAR | 6 | `50.89 m²` | ✅ PASS |
| **`1a8384c3f6`** | Floor-Only Scan (`single_scan_floor_only.zip`) | LiDAR | 8 | `42.77 m²` | ✅ PASS |
| **`163f18d3ac`** | Multi-Room Flat Walk | LiDAR | 5 | `25.25 m²` | ✅ PASS |
| **`demo_fourroom`**| Synthetic L-Shaped Suite | LiDAR | 2 | `19.87 m²` | ✅ PASS |
| **`demo_office`**  | Synthetic Office Suite | LiDAR | 2 | `52.49 m²` | ✅ PASS |

To re-run any capture directory through the reconstruction engine:

```bash
.venv/bin/python -m cozmo.cli run --input data/captures/c7d28f72c6 --out out/c7d28f72c6
```

---

## 🔁 Part 4 Fix Loop CLI Execution

Run the fix loop before/after comparison pipeline on any capture:

```bash
.venv/bin/python -m cozmo.cli fixloop --input data/captures/c7d28f72c6 --out fixloop/run_c7d28f72c6
```

This generates:
* `before_run.json` — Baseline un-ablated pipeline output.
* `after_run.json` — Shipped fixed pipeline output.

---

## 📈 Benchmark & Gate Scoring Suite

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

## 🧪 Unit Testing & Verification

Run the full pytest suite (123 unit tests, 0 warnings, 100% pass):

```bash
PYTHONPATH=src .venv/bin/pytest -q
```

---

## 📚 Deliverables & Documentation Index

- [technical_report.pdf](file:///Users/anuj/Desktop/cozmo_ass/cozmo/technical_report.pdf) — Architectural design, error budget, 5-page brief.
- [benchmark_report.md](file:///Users/anuj/Desktop/cozmo_ass/cozmo/benchmark_report.md) — Gate performance, room-by-room accuracy evaluation.
- [compliance_matrix.md](file:///Users/anuj/Desktop/cozmo_ass/cozmo/compliance_matrix.md) — Case study requirement compliance matrix.
- [known_failure_modes.md](file:///Users/anuj/Desktop/cozmo_ass/cozmo/known_failure_modes.md) — Failure modes and boundary behavior audit.
- [capture_protocol.md](file:///Users/anuj/Desktop/cozmo_ass/cozmo/capture_protocol.md) — Operator scanning guidelines.
