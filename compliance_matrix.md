# Compliance matrix

Requirement → where it lives → what it produces → status, one row per requirement in the
brief (Parts 1–5, Deliverables, the walk-in test, Constraints).

Statuses are **MET** / **PARTIAL** / **FAIL** / **UNVERIFIED** / **NOT MET**, assigned
against what is in the repository today, not against intent. Where a row is partial or not
met the gap is named rather than softened.

- `MET`: implemented and demonstrated on real data.
- `PARTIAL`: implemented and falling short, with the shortfall quantified.
- `FAIL`: implemented, measured against ground truth, and the gate is not met.
- `UNVERIFIED`: implemented, but the ground truth needed to score it does not exist; the gate
  reports `SKIP`, never `PASS`.
- `NOT MET`: absent.

Generated against `HEAD`, benchmark run `reports/verified/` (8 captures,
15 PASS / 18 FAIL / 33 SKIP — see
[benchmark_report.md](benchmark_report.md) for what the FAILs and SKIPs are).

---

## Part 1 — Capture route and input tiers

| # | Requirement | File path | Artifact | Status |
|---|---|---|---|---|
| 1.1 | Choose a capture route: own iOS app **or** stock capture protocol | `capture/PROTOCOL.md`, `capture_protocol.md` | One-page Route 2 protocol: Stray Scanner named as the tool, native Camera app for photo/video, install table, walk script, failure table, hand-off command. Every instruction is verified against the ingest code. | **MET** — Route 2 taken. The protocol is written for a non-engineer and names the three failure modes that actually broke real captures (no upward sweep, no parallax between photos, EXIF stripped by messaging apps). |
| 1.2 | Route 2: name the off-the-shelf tool | `capture/PROTOCOL.md`, `cozmo/io/stray.py` | **Stray Scanner** (App Store, free) named with the exact export format it produces. Native Camera app for photo/video tiers. | **MET** — tool named, install-to-hand-off written, with three capture failure modes called out. |
| 1.3 | Tier 1 — **Photos**, 2–8 stills per room, no depth/poses, one folder per room | `cozmo/io/photo.py`, `cozmo/pipeline/photo.py`, `cozmo/recon/` | `cozmo run` on `DROP_CAPTURES_HERE/03_multiroom_photos` → `reports/verified/multiroom_photos/plan.json` (3 rooms, 97.19 m² against 28.75 m² tape, +238%) | **PARTIAL** — runs end to end on 58 real stills at 0.5× and 12 at 1×. Accuracy is honestly reported: +238% at 0.5×, +136% at 1×. The dominant error is the monocular depth model's scale (1.57–1.76×), a field-of-view mismatch, not a tuning problem. The ±8% gate does not pass. |
| 1.4 | Photo folders must produce the **same stitched whole-property plan** | `cozmo/pipeline/photo.py`, `cozmo/stitch/rooms.py` | `reports/verified/multiroom_photos/plan.json` — 3 rooms stitched into one property, the hall rejected as implausible, adjacency 2/4 from folder names | **PARTIAL** — per-room folders stitch into one property with adjacency. Doorway matching fails (opening detection finds 0–1 openings at the photo tier), so adjacency comes from folder naming convention. The stitch is structurally correct but not solved from geometry. |
| 1.5 | Tier 2 — **Video**, handheld walkthrough clip | `cozmo/pipeline/video.py`, `cozmo/io/video.py` | Runs on the walkthrough clip; whole-flat clip gives one room of about 371 m², the assignment's single-room walk, which LiDAR puts at 20.91 m², gives 339.61 m² | **NOT MET** as a metric product — the video tier runs end-to-end but does not produce a usable metric plan. Metric scale is not solved. |
| 1.6 | Tier 3 — **LiDAR**, depth + poses + intrinsics | `cozmo/pipeline/lidar.py`, `cozmo/io/stray.py`, `cozmo/geometry/` | Long walk: 6 rooms, 29.13 m² (+1%), 7 openings, adjacency 2/5, per-room ceilings 2.56–2.67 m. Assignment's two flat scans give 8 and 7 rooms, 38.86 and 41.24 m², and the same plans from freshly unzipped copies. | **MET** — the strongest tier. Validated against operator's tape on a real flat. Drift correction keeps 77 of 110 candidate loop closures on the long walk and cuts the pose residual from 0.389 to 0.262. |
| 1.7 | All three tiers mandatory, same output contract from each | `cozmo/schema.py`, `cozmo/pipeline/__init__.py` | One `PropertyPlan` Pydantic model, one `reconstruct` entry point, three tier-specific builders. All plans validate against one schema. | **MET** — verified by Pydantic validation on every run in `reports/verified/`. The `Measure` contract ensures no bare floats for physical quantities. |
| 1.8 | Intervals widen honestly as sensor data thins | `cozmo/uncertainty/calibration.py` | LiDAR intervals: mean half-width 12.3–13.9 cm. Photo intervals: mean half-width 7.26 m. About a 58× widening from LiDAR to photo, visible in every plan. | **PARTIAL** — intervals do widen from LiDAR to photo, but LiDAR intervals cover the tape on 3 of 37 measurements. They model sensor noise and drift, not segmentation error, which runs to tens of centimetres. |
| 1.9 | **Device matrix**: which tier on which hardware, accuracy each delivers | `capture/DEVICE_MATRIX.md` | Tier availability per device; accuracy filled from tape for LiDAR and photo, none for video, ceilings or openings. | **PARTIAL** — generated from benchmark results via `scripts/accuracy_table.py`, not written by hand. Limited to two devices (iPhone 17 Pro captures and the assignment's unknown device). |

## Part 2 — Output contract and gates

| # | Requirement | File path | Artifact | Status |
|---|---|---|---|---|
| 2.1 | Dimensioned per-room plan: walls, ceiling height, floor area, openings | `cozmo/schema.py` (`Room`, `Wall`, `Opening`), `cozmo/geometry/assemble.py` | `Room.walls[]` with start, end, length, plane, point_support. Every physical quantity is a `Measure` with interval. | **MET** — structurally enforced by Pydantic validators. `Measure` requires `lo <= value <= hi`, `coverage > 0`, and records the interval method. |
| 2.2 | Stitched multi-room plan with correct adjacency | `cozmo/stitch/rooms.py`, `cozmo/geometry/assemble.py` | Against 4 taped edges: first walk 3/4, long walk 2/5 (2 missed, 3 edges to rooms outside the tape), photo 2/4 (from folder names). Room overlap: 0 on all captures. | **PARTIAL** — adjacency is produced and overlap-resolved, but adjacency correctness is not independently scored (no ground-truth adjacency rows exist separate from the tape). The brief's automatic-failure condition (room overlap) is met on all captures. |
| 2.3 | Per-surface damage regions with class and metric extent | `cozmo/damage/detect.py` | `DamageRegion` with `surface_id`, `damage_class`, `extent` in m², `severity`, `classification_confidence`. Detections are only reported once they land on a reconstructed surface. | **PARTIAL** — classical detector (no downloaded model). A finding must be seen from two frames on the same patch of wall, with its depth on the wall plane, and a ruler-straight edge is not a crack. No LiDAR run reports damage, and every earlier finding on these flats was false (furniture edges, a picture frame, a toilet seat). No captured flat has damage, so detection recall is unmeasured. |
| 2.4 | Concealed-damage flags **with the rule that fired** | `cozmo/damage/rules.py`, `cozmo/damage/rules.yaml` | `ConcealedFlag.rule_id`, `.rule_text`, `.triggered_by`, `.recommended_action`, `.conditions` (each condition tested and the value it read) — explicit YAML rule engine evaluated without `eval()`, validated when the rules load. | **MET** — the firing rule and its input values are in the output. Rules are auditable, not a model. |
| 2.5 | Scope line items keyed to surfaces | `cozmo/scope/generate.py`, `cozmo/scope/items.csv` | `ScopeItem.surface_id`, `.driver_damage_ids`, `.rationale` showing the arithmetic, `.quantity` as a `Measure`. | **MET** — every scope item traces to its driving damage and surface. |
| 2.6 | A confidence interval on **every** measurement | `cozmo/schema.py::Measure` | `Measure` with `lo`, `hi`, `coverage`, `method`. Validators enforce `lo <= value <= hi`, `coverage > 0`. No code path emits a bare float for a physical quantity. | **MET** — structurally enforced. The `method` field records whether the interval is `conformal_split`, `propagated_covariance`, `bootstrap`, or `prior_only`, so a reader can tell a calibrated interval from a guess. |
| 2.7 | One command per capture | `cozmo/cli.py` | `cozmo run --input DIR --out DIR`. Tier is read from `capture.json` / directory contents, never a flag. | **MET** — tier auto-detection via `cozmo/io/discover.py`. A flag would let a photo capture be scored against LiDAR gates by typo. |
| 2.8 | JSON to the published schema | `cozmo/schema.py` | Pydantic-validated `plan.json` with `schema_version`, `StrictModel` base (`extra="forbid"`) | **MET** — every plan in `reports/verified/` validates against the schema and every id in it resolves: `cozmo run` refuses to write a plan whose ids do not, and a test checks every verified plan. |
| 2.9 | Rendered floor plan | `cozmo/render/plan.py`, `cozmo/render/drawing.py` | `plan.svg` and `plan.png` per run. Walls with thickness, doors as gaps, windows as breaks, rooms labelled with name and area, intervals drawn not just tabulated. | **MET** — the render engine produces architectural floor plans, not just polygon outlines. |
| 2.10 | Benchmark: multi-room, 3+ rooms and a connector | `data/raw/163f18d3ac`, `DROP_CAPTURES_HERE/01_multiroom_lidar` | Hall, passage, bedroom, bathroom, walked twice. 6 rooms on the long walk, 4 rooms on the first walk. | **MET** — two LiDAR walks of the same flat, plus a solo bedroom scan. |
| 2.11 | Benchmark: furnished room with staged damage, two classes | — | — | **NOT MET** — not captured. No staged-damage room was produced. |
| 2.12 | Benchmark: the same rooms at all three tiers | `DROP_CAPTURES_HERE/0{1,2,3}_*` | LiDAR, video and per-room photo folders of the one flat. | **MET** as captures — the same flat is captured at all three tiers. Photo and video fail their gates; the captures exist. |
| 2.13 | Benchmark: one room captured twice at the same tier | `ae3edc814d`, `163f18d3ac`, `5621ec5c54` | Two LiDAR walks of the flat and one of the bedroom alone. | **MET** as captures — both repeatability pairs fail (0/27 walls agree, ceiling spread 13.1 cm; the bedroom alone against the long walk, 1/6 walls and under 0.05 cm). |
| 2.14 | Laser or tape ground truth on everything | `capture/ground_truth.csv` | Operator's tape in whole feet: walls, areas and adjacency. No ceilings, doors or bathroom walls. | **PARTIAL** — tape exists and is used. It does not cover ceilings (SKIP), door widths (SKIP), or the bathroom's walls. A tape in whole feet carries ±15 cm, which cannot adjudicate a 2 cm gate. |
| 2.15 | Raw sensor data submitted | `DROP_CAPTURES_HERE/`, `data/raw/` | Three Stray exports, 58 stills at 0.5× and 12 at 1×, one walkthrough clip. | **PARTIAL** — about 6 GB total, kept outside git and the zip. The assignment's three zips are included. |
| 2.16 | Gate: wall lengths ≤2 cm on ≥85% | `cozmo/bench/gates.py::gate_wall_lengths` | 0/15 walls within 2 cm on the long walk, 0/14 on the first walk, 0/6 on the bedroom scan. | **FAIL** — segmentation error (merged rooms, short bedrooms) is tens of centimetres, not the 2 cm the gate asks for. |
| 2.17 | Gate: opening widths ≤2 cm on ≥85%, detection scored | `cozmo/bench/gates.py::gate_opening_widths` | Misses and phantoms both counted; reported widths matched to taped ones by the assignment with the smallest total error. 7 openings detected on the long walk. | **UNVERIFIED** — no door tape exists. The gate reports SKIP. |
| 2.18 | Gate: ceiling height ≤1.5 cm per room | `cozmo/bench/gates.py::gate_ceiling_height` | 2.56–2.67 m in 5 of 6 rooms on the long walk. | **UNVERIFIED** — no ceiling tape. The gate reports SKIP. |
| 2.19 | Gate: repeat ceiling spread ≤1 cm, and say which failure | `cozmo/bench/gates.py::gate_repeatability`, `benchmark_report.md` | Walks: hall 0.4 cm, bedroom 0.8 cm, bathroom 13.1 cm; the bedroom alone against the long walk, under 0.05 cm. The bathroom fails because the long walk's bathroom room also holds part of the passage. | **FAIL** — ceiling height repeats to 8 mm or better where rooms are segmented the same way (hall, bedroom). Fails in the bathroom, where they are not. |
| 2.20 | Gate: repeatability 1 cm or 0.5% per wall | `cozmo/bench/gates.py::gate_repeatability` | 0/27 walls agree, worst 267.3 cm. The same bedroom differs by 0.15–0.64 m between the two walks. | **FAIL** — walls are unrepeatable rather than repeatable-but-biased. This is a reconstruction defect in room segmentation, not a tape error. |
| 2.21 | Gate: drift accountability with an on/off ablation | `cozmo/geometry/drift.py`, `known_failure_modes.md` §5 | Pose graph over heading and horizontal position with ICP-verified closures; ablation of drift on/off × snap on/off plus the room refinement off, regenerated. With snapping on, the walk without correction loses the passage. | **MET** — the brief makes "poses used as-is" an automatic fail, and the ablation is reported as it came out: this walk drifted little (largest correction 8.5 cm), and the published plan is closest per taped room (26.6% mean error) and level with snapping off on footprint (+1%), since fix loop round 3 removed a room never walked into; it still carries two rooms outside the tape. |
| 2.22 | Gate: photo-tier whole-property stitch, ±8% | `cozmo/stitch/rooms.py` | 3 rooms, 97.19 m² against 28.75 m² (+238%), adjacency 2/4 from folder names. | **FAIL** — the photo tier is dominated by the monocular depth scale error (1.57–1.76×). The ±8% gate does not pass. |
| 2.23 | Gate: interval coverage at every tier | `cozmo/uncertainty/calibration.py`, `cozmo/bench/gates.py::gate_interval_coverage` | LiDAR: 0/16, 2/16, 1/5 covered. Photo: 7/11 and 5/5 (intervals about 7 m wide). No quantiles fitted in-sample. | **FAIL** — LiDAR intervals model sensor noise, not segmentation. Photo intervals cover by being uninformatively wide. |

## Part 3 — Head-to-head

| # | Requirement | File path | Artifact | Status |
|---|---|---|---|---|
| 3.1 | Compare against one consumer app on 2 rooms | — | — | **NOT MET** — no consumer app export was captured. |
| 3.2 | Name the app and version, submit its export | `DROP_CAPTURES_HERE/08_competitor_export` | Empty directory. | **NOT MET** — no export exists. |
| 3.3 | Beat or tie on ≥70% of shared dimensions | — | No export to compare, and no scorer: an earlier one that fixed the result in our favour was removed. | **NOT MET** — cannot be scored without an export. |

## Part 4 — Fix loop

| # | Requirement | File path | Artifact | Status |
|---|---|---|---|---|
| 4.1 | Round 1: worst gate with its failing number | `fixloop/FIX_DECLARATION.md` | Photo footprint +422% against 27.20 m² LiDAR reference. | **MET** — declared before the fix, committed at `aa7be32`. |
| 4.2 | Round 1: root cause with evidence | `fixloop/FIX_DECLARATION.md` | EXIF sub-IFD read: `FocalLengthIn35mmFilm = 14` present in all 58 photos, never read. fx 4125.3 vs correct 2221.3 (1.86× too long). | **MET** — root cause identified with measured evidence (IFD0 vs sub-IFD, exact fx values). |
| 4.3 | Round 1: prediction before the fix | `fixloop/FIX_DECLARATION.md` | Under +50%, and explicitly not a pass. Committed in `aa7be32` before `db4cfa9`. | **MET** — prediction is auditable in git. |
| 4.4 | Round 1: fix shipped | `cozmo/recon/monocular.py`, `cozmo/pipeline/photo.py` | Four changes: EXIF sub-IFD read, scale band widened, room-level scale consensus, plausibility guard. | **MET** — all four changes documented in `fixloop/diff.md`. |
| 4.5 | Round 1: before and after, regenerable | `fixloop/before/`, `fixloop/after/` | Plans committed. No run manifests. Re-run needs the DROP photos. | **PARTIAL** — plans are committed but regeneration requires external photo folders. |
| 4.6 | Round 1: readable diff | `fixloop/diff.md`, `git diff aa7be32..db4cfa9` | `diff.md` shows exact code changes with reasoning per change, before/after numbers. | **MET** — diff is both machine-readable (git) and human-readable (diff.md). |
| 4.7 | Round 1: gate moves fail to pass | — | +422% to −36%; not a pass. Two rooms rejected as implausible. | **PARTIAL** — the gate moved significantly but did not reach PASS. The prediction was wrong (expected under +50%, got +916% before other changes brought it to −36%). |
| 4.8 | Round 1: post-mortem | `fixloop/POSTMORTEM.md` | Prediction wrong in direction. Root cause was real but not dominant — the EXIF fix exposed a larger depth-model error. Scored honestly: marks for the post-mortem, none for the prediction. | **MET** — the post-mortem explains why the prediction was wrong, with measurements. |
| 4.9 | Round 2: declaration before the fix | `fixloop/round2/FIX_DECLARATION.md` | LiDAR footprint against tape: bedroom −43%, footprint −12%. Committed `0a8c579` before `11cebf3`. | **MET** — auditable in git log. |
| 4.10 | Round 2: fix shipped | `cozmo/geometry/{occupancy,cellcomplex}.py`, `tests/test_ceiling_evidence.py` | Ceiling returns as interior evidence in the cell complex. | **MET** — code changes documented in `fixloop/diff.md`. |
| 4.11 | Round 2: before and after, regenerable | `fixloop/round2/{before,after,gates}` | Plans, run manifests and gate tables for both captures. | **MET** — all before/after artifacts committed and regenerable. |
| 4.12 | Round 2: gate moves | `fixloop/round2/gates/` | Before and after identical — every room polygon unchanged. | **FAIL** — the hypothesis was refuted by measurement. The "real walls" were the far faces of brick partitions, and ceiling measurement had bled into neighbours through morphological closing. |
| 4.13 | Round 2: post-mortem | `fixloop/round2/POSTMORTEM.md` | Hypothesis refuted. Room-map defect found and fixed (rooms were named by area, not by camera frames). Rooms are now named from camera frames in `capture/room_identity/`. | **MET** — the fix loop that didn't move a gate still produced a genuine improvement (room identity), honestly documented. |
| 4.14 | Round 3: declaration before the fix | `fixloop/round3/FIX_DECLARATION.md` | LiDAR footprint on the long walk, 31.26 m² against 28.75 m² taped (+8.7%). Committed `949d24b` before `02be79e`. It records that the corrections had been tried on saved intermediates, and their numbers read, before the predicted ranges were written. | **PARTIAL** — auditable in git, but the prediction is not blind. |
| 4.15 | Round 3: root cause with evidence | `fixloop/round3/FIX_DECLARATION.md`, `fixloop/round3/evidence/` | A room running past where its space ends: a corridor seen from its doorway crossing two walls, two stairwells shown by 6,480 and 7,458 below-floor returns, a walled space no scan saw into, a never-walked room. Each plan drawn over its own scan and over the other scans registered onto it, with camera frames. | **MET** — the declaration's three tests of being wrong all came out negative. |
| 4.16 | Round 3: fix shipped | `cozmo/geometry/refine.py`, `cozmo/geometry/occupancy.py`, `tests/test_refine.py` | Stairwells, open-ended rooms and walled spaces nobody saw into are removed after segmentation; each removal is written into the plan's warnings; `--no-refine-rooms` turns it off. | **MET** — it also removes 0.26–1.05 m² of seen floor from each room it corrects, measured and documented in `known_failure_modes.md` §21. |
| 4.17 | Round 3: before and after, regenerable | `fixloop/round3/{before,after,gates}` | Plans, run manifests and gate tables for six captures, produced by `dd95678` and `02be79e`. | **MET** |
| 4.18 | Round 3: gate moves | `fixloop/round3/gates/` | Long-walk footprint +8.7% FAIL to +1.3% PASS; adjacency 2/6 to 2/5; bedroom walk footprint +46.1% to +17.2%. | **MET**, qualified — the gate passes because an untaped, never-walked room left the plan. No taped room changed. |
| 4.19 | Round 3: post-mortem | `fixloop/round3/POSTMORTEM.md` | Every predicted number inside its range; the seen floor removed and two rejected alternatives measured; the changes nobody predicted listed. | **MET** |

## Part 5 — Process evidence

| # | Requirement | File path | Artifact | Status |
|---|---|---|---|---|
| 5.1 | Commit as you work | `git log` | Incremental commits naming the defect and the number it moved. Fix declarations committed before their fixes, three times. | **MET** |
| 5.2 | Not a single-commit dump | `git log` | Multiple commits across several days, each with a specific purpose. | **MET** |
| 5.3 | Tests exist and pass | `tests/` | 138 tests across 19 test files, all passing. Tests cover schema validation, geometry algorithms, pipeline stages, damage detection, benchmarking, and fixtures. | **MET** |

## Deliverables

| # | Requirement | Where | Status |
|---|---|---|---|
| D1 | Compliance matrix | this file | **MET** |
| D2 | Capture route and device matrix | `capture/PROTOCOL.md`, `capture_protocol.md`, `capture/DEVICE_MATRIX.md` | **MET** — protocol at both `capture/PROTOCOL.md` and repo root `capture_protocol.md` for discoverability. |
| D3 | README to running in 15 minutes, one command per capture | `README.md`, `scripts/setup.sh` | **MET** — two setup commands and one command per capture. Synthetic ray-traced room runs without any real data. A Dockerfile is included; it has not been built in this repository's checks. |
| D4 | Reproduction bundle | `run_manifest.json` per run: commit, input hash, config, timings; `scripts/regenerate_verified.sh` rebuilds every verified plan and the gate table | **MET** |
| D5 | Benchmark report across three tiers | `benchmark_report.md`, `reports/verified/gates/` | **PARTIAL** — 15 PASS, 18 FAIL, 33 SKIP. The honest headline is that 33 gates are SKIP because the tape doesn't cover ceilings, doors or the assignment property. |
| D6 | Fix loop bundle | `fixloop/`, `fixloop/diff.md`, `fixloop/README.md` | **MET** — three rounds, each with declaration, before/after and post-mortem; rounds 1 and 2 also in the human-readable `fixloop/diff.md`, round 3 in `git diff 949d24b..02be79e`. |
| D7 | Technical report, at most 6 pages | `technical_report.md`, rendered as `technical_report.pdf` | **MET** — 11 sections including Abstract, Scope, Experimental Setup, Results, Conclusion, and Appendix. |
| D8 | Architecture / design document | `docs/design.md` | **MET** — 12-section architecture document covering captures, output contract, all three tiers, stitching, damage, calibration, benchmark scoring, fixtures, and layout. |
| D9 | Raw benchmark data: sensor logs, ground truth, app exports | `DROP_CAPTURES_HERE/`, `capture/` | **PARTIAL** — no app export, partial tape (no ceilings or doors). |
| D10 | Weights fetched by script | `scripts/fetch_weights.sh` | **MET** |
| D11 | Runs without calling our infrastructure | no network at run time | **MET** |
| D12 | Mirrors, glass, wet-look surfaces, low light | `known_failure_modes.md` §4, §7 | **MET** — geometric mirror test, multi-view corroboration for specular highlights, luma-based low-light flagging. 21 failure modes documented total. |

## Constraints

| # | Constraint | Status |
|---|---|---|
| C1 | Python ≥ 3.11 | **MET** — `pyproject.toml` requires-python `>=3.11,<3.13`. Tests run on 3.12.13. |
| C2 | No network at run time | **MET** — `scripts/fetch_weights.sh` is the only fetch and is a separate, explicit step. |
| C3 | Walk-in test: cold run on someone else's capture | **MET** — runs on the assignment's three zips unchanged, and plans built from freshly unzipped copies are identical to the published ones. |

---

## Summary

| Status | Count |
|---|---|
| MET | 41 |
| PARTIAL | 12 |
| FAIL | 6 |
| UNVERIFIED | 2 |
| NOT MET | 5 |

Of the 5 `NOT MET`, three are the head-to-head rows (no consumer app export captured), one is
the staged-damage room (not captured) and one is the video tier as a metric product, which runs but does not
solve scale. The 6 `FAIL` are measured shortfalls against the operator's tape, each explained in
`benchmark_report.md` or a post-mortem.
