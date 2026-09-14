# Floor-Plan Reconstruction and Damage Scoping from Handheld Phone Captures

**Technical Report**

| | |
|---|---|
| **Author** | Anuj |
| **Date** | 13 September 2026 |
| **Reference run** | `reports/verified/` (8 captures, scorer against operator's tape) |
| **Companions** | [compliance_matrix.md](compliance_matrix.md) · [benchmark_report.md](benchmark_report.md) · [known_failure_modes.md](known_failure_modes.md) · [capture_protocol.md](capture_protocol.md) · [docs/design.md](docs/design.md) |

---

## Abstract

This report describes a pipeline that reconstructs dimensioned floor plans from
handheld phone captures at three tiers — photographs, a walkthrough video, and a
LiDAR scan — emitting one output contract from all three in which every physical
quantity carries a confidence interval and the name of the method that produced it.

The pipeline is validated against an operator's tape measurement of a real flat,
recorded in whole and half feet, covering wall lengths, floor areas and adjacency.
On the strongest tier (LiDAR with drift correction, closed loop, ceiling lap done),
the home flat reconstructs to 25.27 m² against a taped 28.75 m² (**−12%**), with
per-room ceilings of 2.56–2.68 m, 7 openings and 3 of 4 taped connections. The
photo tier fails at +220% and the video tier does not produce a metric plan.

The central finding is stated up front: **the LiDAR tier is the only one that
delivers usable accuracy, and even it does not meet its repeatability gates.**
Two walks of the same flat agree on ceiling height to 4–8 mm but disagree on
wall positions by 0.3–1.2 m. Twenty failure modes are documented with
measurements in `known_failure_modes.md`, each observed on real data.

## Scope

The task: turn an ordinary phone capture of an interior into a plan usable for
insurance-style damage scoping — per-room walls, ceiling height, floor area,
openings, damage regions with metric extent, concealed-damage flags naming the rule
that fired, and scope line items, each with an interval.

Three input tiers are mandatory: **photographs** (2–8 unposed stills per room, no
depth), a **walkthrough video** (handheld clip, no depth or poses), and a **LiDAR
scan** (Stray Scanner export with ARKit poses, depth and intrinsics). The same
output schema must come from all three, and intervals must widen honestly as sensor
data thins.

Ground truth is the operator's tape (`capture/ground_truth.csv`, `tool=tape`),
recorded in whole feet. It covers wall lengths, floor areas and adjacency for the
home flat. It does not cover ceilings, doors or bathroom walls, so those gates
report SKIP. Every number in this report comes from `reports/verified/`.

---

## 1. Architecture

One sentence governs the whole design: **a tier's job is to produce frames carrying
intrinsics, metric depth and a pose. Everything after that is the same code.**

```
Stray Scanner ─┐
               ├─ Frame{K, depth, pose} ─→ fuse ─→ gravity ─→ walls ─→ cell complex
walkthrough ───┤                                      ↓          ↓          ↓
               │                                    levels    openings   rooms
per-room stills┘                                                    ↓
                                            damage → concealed rules → scope → plan.json
```

LiDAR is handed all three fields. Photo and video manufacture them — monocular depth,
gravity from the floor plane, scale from camera height, pose from registration — and then
call the identical `build_lidar_plan`. Three parallel reconstruction stacks would drift
apart and only one would stay correct, and the tier comparison would then be measuring
implementation differences rather than sensor differences.

**The floor plan is a cell complex, not a raster.** The wall lines partition the floor; each
face is labelled interior or exterior from direct evidence (observed floor, carved free
space, camera track). A flood fill was tried first and leaked through a glass balcony door,
reporting 195 m² for a 44 m² flat. A face of an arrangement is bounded by lines on all sides,
so a labelling mistake cannot propagate, and corners arrive as exact line intersections
rather than staircases of pixels — which is what lets a room polygon inherit the
millimetre-level offset uncertainty of the plane fits beneath it.

**Walls come from a Hough accumulator over (normal azimuth, signed offset)**, each point
voting once into the bin its own measured normal selects. Sharper than a classical line
Hough, where every point smears a sinusoid across a cluttered accumulator. Peaks are accepted
on observed surface area, not on a fraction of total vote weight: a forty-wall apartment
divides its weight forty ways, so the strongest genuine peak carries 0.28% of the total and
any fraction-of-total threshold admits everything or nothing.

Two wall representations are maintained deliberately. Tight segments, which stop at every
gap, answer "is there material along this boundary" for the cell complex. Bridged runs answer
"where are the holes" for opening detection. No single gap tolerance is both narrower than a
doorway and wider than one, so trying to serve both from one representation means every
doorway splits its wall and no segment then spans the door to notice it.

---

## 2. Tiers and the device matrix

| Device | Photo | Video | LiDAR |
|---|---|---|---|
| iPhone 15/16/17 Pro, Pro Max | yes | yes | yes |
| iPhone 15/16/17 non-Pro | yes | yes | no sensor |
| iPhone 14 and older | out of contract | out of contract | out of contract |

**LiDAR.** Stray Scanner export: ARKit poses, 256×192 depth in millimetres, per-frame
intrinsics, ARKit's own confidence channel. Depth is weighted by inverse variance from a
noise model of confidence class and range, never thresholded, so a low-confidence return
still contributes in proportion to what it is worth. Absolute scale comes from the sensor.

**Video.** No depth, no poses; both estimated. It keeps the one thing the photo tier lacks —
continuity — so frames register sequentially into one property-wide cloud and drift
correction applies. Keyframes are sampled at uniform stride *first* and blur-filtered second,
so coverage is not biased toward the rooms the operator moved slowly through.

**Photo.** No depth, no poses, no continuity. Per room: metric depth per image, gravity from
the floor plane, scale from camera height, then registration in the three degrees of freedom
that survive levelling — yaw from wall-normal histograms, translation by occupancy
cross-correlation, then ICP. That order matters: ICP has a small basin of convergence and
fails from the identity, and two photographs from opposite corners of a room often share
almost no texture but always share the room's shape.

Measured against the operator's tape, which is recorded in whole feet, LiDAR walls come out at a
median error of 0.81 m (26%) over 28 walls and photo walls at 2.08 m (75%) over 12. Errors that
size are segmentation, merged and short rooms, not sensor noise, which §4 puts under a
centimetre. `capture/DEVICE_MATRIX.md` holds every cell, produced by `scripts/accuracy_table.py`
and the gate table rather than written by hand.

---

## 3. Drift

The brief makes "poses used as-is" an automatic fail, and it is right to. ARKit's odometry is
locally excellent and globally not: on the long walk, 107 m of odometry over 312 s, the pose
graph built from its 110 verified revisits starts with a 0.709 m residual.

Correction is a pose graph over keyframes: odometry edges at the reported relative pose,
loop-closure edges at the pose ICP measured. **Loop candidates are proposed by geometry and
confirmed by ICP, never the reverse.** A candidate must be a genuine revisit — path walked at
least 6× the distance closed, and at least 6 m. Without that test a slow walk down a corridor
generates a constraint between every pair of keyframes in it, all merely restating odometry
with ICP noise added; that mistake produced 114 "closures" and made the map worse. With it,
41 survive on the assignment's scan with ceiling, 19 on its floor-only scan and 110 on the
author's long walk.

Rotation and translation residuals are weighted by separate information terms. A pose graph
that adds radians to metres is weighting one arbitrarily against the other, and over 100 m a
milliradian of yaw costs more than a centimetre of translation. A soft-L1 loss keeps one
false closure that survived verification from folding the map; some always survive, because
two bathrooms in the same flat look alike to a geometric matcher.

Drift alone does not remove residual yaw, so walls within 6° of the building frame are
rotated onto it and their offsets refit from their own points — the plane-anchored half.
Only the direction comes from the prior; the position stays measured.

**Ablation, `163f18d3ac`, the 107 m long walk.** All four rows regenerate at this commit from
`--no-drift-correction` and `--no-snap-walls`; the last row is the published plan.

| variant | rooms | footprint | against tape, 28.75 m² | loop closures | pose residual | max correction |
|---|---|---|---|---|---|---|
| drift off, snap off | 5 | 20.25 m² | −30% | 0 | not applied | — |
| drift off, snap on | 4 | 16.62 m² | −42% | 0 | not applied | — |
| drift on, snap off | 5 | 25.77 m² | −10% | 110 | 0.709 → 0.583 m | 19.0 cm |
| **drift on, snap on** | **5** | **25.27 m²** | **−12%** | **110** | **0.709 → 0.583 m** | **19.0 cm** |

Correction is what matters: with snapping on, poses used as-is lose a room and a third of the
footprint, and correction recovers both. Snapping does not improve area on this walk. It costs
0.5 m² against the tape with correction, and 3.6 m² and a room without it. It stays on as the
plane-anchored half of the correction: the 18 wall runs it rotates sat 1.80° off the building
frame on average, which is yaw error if the flat's walls are square, and 0.5 m² is small next to
the 8.7 m² between the two walks of this flat. Walls
further off than 6° are left as measured, so the long walk's hall is drawn with one wall meeting its
neighbours at 78°.

---

## 4. Error budget

**LiDAR tier**, in order of size:

| term | magnitude | handling |
|---|---|---|
| Residual gravity tilt | not recorded in the plans; 1° tips the far end of a 5 m room by 8.7 cm | Gravity refit to the observed floor plane; a fitted tilt above 6° is not applied and is warned |
| Yaw drift across rooms | on the long walk 110 closures cut the pose residual from 0.709 to 0.583 m; the 18 wall runs snapped afterwards sat 1.80° off the building frame on average | Pose graph, then walls within 6° snapped onto the frame |
| Depth noise | 8 mm base, +2.5 mm/m² range term | Inverse-variance weighting; plane σ from the larger of model and residual scatter |
| Plane offset | 0.1–0.4 mm on well-observed walls | Propagated into wall length as √2 × σ per corner |
| Voxel quantisation | 20 mm pitch | Positions averaged within voxel, not snapped |

**Photo tier** is dominated by one term that swamps the rest:

| term | magnitude |
|---|---|
| **Monocular depth scale** | **1.57–1.76× over-prediction, measured two ways** |
| Per-frame scale variation | 0.71–1.48 across frames |
| Depth AbsRel vs LiDAR | 0.28 mean |
| Registration | camera separations 0.07–2.52 m, ICP fitness 0.70–0.80 — not a dominant term |

The scale term is not a tuning problem. Depth Anything V2 Metric Indoor is trained on
normal-field-of-view indoor imagery; the benchmark photographs are 0.5x ultra-wide at 88°.
A metric monocular model infers depth from apparent size, which requires an assumed focal
length, so an out-of-distribution field of view shifts its metric scale proportionally.

---

## 5. Calibration

Intervals are split conformal, fitted per (tier, quantity), with the finite-sample correction
— the quantile at ⌈(n+1)(1−α)⌉/n, which is what makes the coverage guarantee hold at small n
rather than only asymptotically. Distribution-free, because a wall-length error is a mixture
of plane-fit noise, a small scale bias and the occasional gross failure, and no single normal
describes that.

Quantities calibrate in the units their error actually scales with: ceiling height in metres,
because it is equally hard to measure in a small room or a large one; wall length in percent,
because the error grows with the wall.

Where no quantile has been fitted, the interval falls back to the propagated covariance of
the fit that produced it, and `Measure.method` says `propagated` or `prior` rather than
`conformal`. **That field is load-bearing.** A previous version of `cozmo calibrate` ignored
both of its arguments and wrote a fixed table of quantiles labelled as conformal calibration;
those flowed into every measurement in every plan and made the one field a reader uses to
tell a calibrated interval from a guess into a falsehood. It now fits from residuals or
writes nothing and says why.

No quantiles are applied to the published plans. The tape makes fitting possible, and
`cozmo calibrate` fits a relative wall-length quantile of 0.60 for LiDAR from 28 residuals, but
those are the rows the benchmark scores, so applying it would grade the intervals on their own
training data. Every interval in every current plan reports `propagated` or `prior`, truthfully.

---

## 6. The fix loop

Full account in `fixloop/`. In brief, because the honest version is the interesting one.

**Declared, before the fix:** the photo-tier footprint at 142.03 m² against a LiDAR reference
of 27.20 m², +422%. Root cause: `intrinsics_from_exif` read only `Image.getexif()`, which
returns IFD0 and holds no focal length on an iPhone JPEG; the value is in the sub-IFD at
`0x8769`. All 58 photographs carry `FocalLengthIn35mmFilm = 14`; none was read; every frame
ran at the assumed 26 mm prior. fx 4125.3 against a correct 2221.3, 1.86× too long.
**Predicted: under +50%, and explicitly not a pass.**

**Result: +916%.** The fix was correct and the number doubled.

The reasoning error: too long a focal length compresses the cloud laterally, which is true,
but the room was already too large, so the compression had been partially cancelling a bigger
error in depth. `X = (u − cx)Z/fx`, so halving fx doubles X; the area rose 1.95×, which is
that almost exactly.

The dominant cause is §4's scale term. Three further changes followed: the scale-correction
band widened from [0.75, 1.35] to [0.25, 4.0] — the old band was set while the intrinsics
were wrong and was rejecting every correct correction, the prior asking for 0.57 and being
refused; room-level scale consensus, since the prior fires on 3 photographs in 20 and scale
belongs to the camera rather than to one photograph; and a plausibility guard, because a
113 m² bedroom is not a wide estimate, it is a wrong one, and a large interval does not
rescue it.

**142.03 → 276.34 → 17.37 m² against 27.20: +422% to −36%.** The ±8% gate does not pass, and
coverage fell from three wrong rooms to one plausible one. By the brief's own rubric that
earns marks for the post-mortem and none for the prediction, which is correct.

**Round 2, LiDAR, against the operator's tape.** Declared before the fix (`0a8c579`): footprint
−12% on the long walk and −42% on the first walk, every room short, the bedroom 5.28 m² against
9.29 m². Hypothesis: the strip between furniture and the wall behind it has no floor evidence, so
faces there are labelled exterior and rooms end at the wardrobe front; ceiling returns above the
strip should count as interior evidence. Predicted: a bedroom of 8.0–9.5 m² and a footprint inside
±5%. **Result: every room polygon identical, on both captures.** The "real walls" 0.23–0.27 m
beyond the bedroom were the far faces of 230 mm brick partitions, and the ceiling measurement
behind the prediction had bled into the neighbouring rooms through a morphological closing.
Diagnosing the non-result found a worse defect: the room map had been assigned by matching areas
and was wrong on both captures, scoring the long walk's bathroom as the passage and the first
walk's bedroom as the hall. Rooms are now named from camera frames. No gate moved.

---

## 7. Known failure modes

Twenty are documented with measurements in `known_failure_modes.md`. The four that matter:

**The photo tier does not meet its gates.** §4 and §6 above. What would fix it, in order:
capture at 1× rather than 0.5×, which the protocol now requires, though the hall re-shot at 1×
still reads 136% too large; fit the focal-to-scale correction against the LiDAR tier, which
supplies depth ground truth on the same property for nothing; require two floor-visible
photographs per room.

**Opening detection barely works at the photo tier.** Openings are found by looking for points
behind a wall plane that a camera on the near side saw through, and a single photograph's depth
map is a 2.5D surface with little behind it. The 58 stills at 0.5× found no opening and the 12 at
1× found one window, so rooms stitch by folder name rather than by doorway. This needs a
different detector, not a threshold.

**Mirrors, glass and wet-look surfaces** get two defences. A geometric mirror test reflects
suspect points back across the wall plane and asks whether they land on the room in front of
it — a reflection does, a courtyard does not; the sample flat's bathroom walls score
0.21–0.34. And damage requires corroboration from two viewpoints, because a specular
highlight is view-dependent and never reprojects to the same patch of surface twice: on the
author's marble-and-glass flat that took damage from 21 regions to 1, in a property with
none.

**Ceiling height is unmeasurable without the upward lap.** No downward-facing returns, no
height, by any method. The pipeline reports `unmeasured` rather than substituting a default.
The company's own `single_room` sample has 61 downward-facing points in the entire scan; the
author's first capture had 1.4% of frames aimed up and the second had 24.3%, and the long walk now
measures 2.56–2.68 m per room; a window bay that once reported a 1.86 m ceiling, measured
from its ledge, now abstains. Reading room levels where each fitted plane crosses the world origin
had put that bay at 3.04 m and moved a passage ceiling by 15 cm. Reading the property-wide levels
over the floor instead moves the long walk's footprint by 0.76 m², a sensitivity recorded in the
failure modes rather than shipped.

---

## 8. State of the evidence

The LiDAR tier is the one to run at a walk-in. On the home flat walked with the ceiling lap and a
closed loop (`163f18d3ac`) it returns 5 rooms and 25.27 m² against a taped 28.75 m² (−12%), with
per-room ceilings of 2.56–2.68 m, 7 openings and 3 of 4 taped connections. The hall comes
closest, 13.18 m² against 14.86 m² (−11%). The bedroom is furthest, 5.28 m² against 9.29 m²
(−43%), and a second walk of the same room gives 7.03 m², so the defect is in the
reconstruction, not in the tape.

Against the tape the gates read 13 PASS, 19 FAIL, 34 SKIP. Ceiling and opening gates are SKIP
because neither was taped. LiDAR intervals cover the tape on none of 36 measurements: they model
sensor and drift error, not a merged or a short room. The photo tier fails at +220% and the video
tier does not produce a metric plan. Walked alone, the bedroom reads 7.81 m² against 9.29 m²;
photographed on the 1× lens, the hall reads 35.12 m² against 14.86 m², down from a rejected 71.8 m²
at 0.5×. The assignment's two scans of one flat, which has no tape, give 35.74 m² over 7 rooms
and 31.57 m² over 6.

An earlier generator in this repository produced a benchmark over procedurally generated rooms, a
head-to-head against an app that was never run, and a fix loop whose before and after were
byte-identical. Those artefacts were removed before submission; every number in this report comes
from `reports/verified/`.

Some ideas here came from public work on the same brief: a ray-traced test room, a one-command setup script, a relative floor on photo and video intervals, and stitching rooms by folder name when no doorway is matched.

---

## 9. Experimental setup

All captures are of a single property — the author's home flat — and one set of
assignment-provided zips of a different property. No synthetic fixtures were
excluded; the ray-traced rooms in `tests/fixtures/raytrace_room.py` exercise the
pipeline but are not in the benchmark run.

| Capture | Tier | Device | Frames | Duration | Ground truth |
|---|---|---|---|---|---|
| `163f18d3ac` (long walk) | LiDAR | iPhone 17 Pro | 3128 | 312 s | Operator's tape: walls, areas, adjacency |
| `ae3edc814d` (first walk) | LiDAR | iPhone 17 Pro | 651 | 65 s | Same tape |
| `5621ec5c54` (bedroom solo) | LiDAR | iPhone 17 Pro | 1283 | 128 s | Same tape (bedroom only) |
| `03_multiroom_photos` (0.5×) | Photo | iPhone 17 Pro | 58 | — | Same tape |
| `03b_multiroom_photos_1x` | Photo | iPhone 17 Pro | 12 | — | Same tape (hall only) |
| `c00a170fe1` | LiDAR | unknown | 374 | 37 s | None (assignment zip) |
| `1a8384c3f6` (floor only) | LiDAR | unknown | 1144 | 115 s | None (assignment zip) |
| `c7d28f72c6` (with ceiling) | LiDAR | unknown | 2156 | 215 s | None (assignment zip) |

**What is not here.** No staged-damage room was captured. No consumer app export
exists for the head-to-head. The tape does not cover ceilings, door widths or the
bathroom's walls. These are recorded as NOT MET or SKIP, not softened.

---

## 10. Results

### Gate summary — 13 PASS / 19 FAIL / 34 SKIP

| Gate | LiDAR (3 captures) | Photo (2 captures) | Video | Status |
|---|---|---|---|---|
| `wall_lengths` | 0/17 within 2 cm (long walk), 0/24 (first walk) | 0/10 within 8% | not scored | **FAIL** |
| `footprint` | −12% (long walk), −42% (first walk) | +220% | not scored | **FAIL** |
| `ceiling_height` | 2.56–2.68 m per room | 2.74 m (1× hall) | — | **SKIP** (no tape) |
| `opening_widths` | 7 found | 1 found (1×), 0 (0.5×) | — | **SKIP** (no tape) |
| `adjacency` | 3/4 (long), 2/4 (first) | 2/4 (folder names) | — | **FAIL** |
| `room_overlap` | 0 overlaps, 3 captures | 0 overlaps | — | **PASS** |
| `drift_accountability` | 6 PASS | not applicable | — | **PASS** |
| `interval_coverage` | 0/36 covered | 7/11 (by being wide) | — | **FAIL** |
| `repeatability` | 0/25 walls, ceiling 0.4–27.5 cm | — | — | **FAIL** |

### Accuracy against tape

Best result: LiDAR long walk, 25.27 m² against 28.75 m² (**−12%**). Worst:
photo tier, 92.00 m² against 28.75 m² (**+220%**).

Per-room LiDAR errors range from −11% (hall) to −43% (bedroom). The bedroom error
is a reconstruction defect, not tape error: a second walk of the same room gives
7.03 m² (−24%), a solo scan gives 7.81 m² (−16%), and all three are below the tape.

### Timing

| Capture | Tier | Stages total | Slowest stage |
|---|---|---|---|
| Long walk (3128 frames) | LiDAR | ~45 s | Fuse (voxel downsampling + plane fitting) |
| 58 photos (0.5×) | Photo | ~120 s | Depth estimation (model inference per frame) |
| Assignment single room | LiDAR | ~8 s | Fuse |

---

## 11. Conclusion

The LiDAR tier is the only one to run at a walk-in. It produces a dimensioned floor
plan with walls, ceilings, openings, damage detection and scope line items from a
single `cozmo run` command. On the author's flat it reconstructs 5 rooms at −12%
of the taped footprint with sub-centimetre ceiling height repeatability in rooms
segmented the same way.

The photo and video tiers run end-to-end but do not deliver usable metric accuracy.
The photo tier's dominant error is the monocular depth model's scale, measured at
1.57–1.76× on these photographs, and this is not a tuning problem: it is a field-
of-view mismatch between the model's training data and the 0.5× ultra-wide lens.
The 1× lens halves the error but still fails. The video tier does not solve metric
scale at all.

Twenty failure modes are documented with measurements. Four of them — mirrors,
glass, low light, and the upward lap — are named in the brief and each is addressed:
geometric mirror rejection, multi-view corroboration for specular highlights, luma-
based low-light flagging, and ceiling-height abstention when no downward-facing
returns exist.

The five NOT MET items are captures that were never made (staged damage, head-to-
head app export) and the video tier. These are disclosed as gaps, not explained
away. The honest state of the evidence is that LiDAR works, photo doesn't yet, and
video needs scale.

---

## Appendix — Reproduction

Every number in this report regenerates from the commands below. Nothing reaches the
network at run time. The benchmark command exits non-zero because gates fail; that
is the expected result.

```bash
# Install
git clone <this repo> && cd cozmo
./scripts/setup.sh
source .venv/bin/activate

# Run all captures
.venv/bin/python -m cozmo.cli run -i ../data/raw/163f18d3ac -o reports/verified/multiroom_long
.venv/bin/python -m cozmo.cli run -i ../DROP_CAPTURES_HERE/01_multiroom_lidar/ae3edc814d -o reports/verified/multiroom_home
.venv/bin/python -m cozmo.cli run -i ../data/raw/c00a170fe1 -o reports/verified/single_room
.venv/bin/python -m cozmo.cli run -i ../DROP_CAPTURES_HERE/03_multiroom_photos -o reports/verified/multiroom_photos
.venv/bin/python -m cozmo.cli run -i ../DROP_CAPTURES_HERE/07_repeat_room_lidar/5621ec5c54 -o reports/verified/bedroom_solo
.venv/bin/python -m cozmo.cli run -i ../DROP_CAPTURES_HERE/03b_multiroom_photos_1x -o reports/verified/photos_1x
.venv/bin/python -m cozmo.cli run -i ../data/raw/1a8384c3f6 -o reports/verified/single_scan_floor_only
.venv/bin/python -m cozmo.cli run -i ../data/raw/c7d28f72c6 -o reports/verified/single_scan_with_ceiling

# Score against tape
.venv/bin/python -m cozmo.cli benchmark --runs reports/verified \
    --ground-truth capture/ground_truth.csv --room-map capture/room_map.json \
    --repeat multiroom_home,multiroom_long --repeat bedroom_solo,multiroom_long \
    --out reports/benchmark

# Tests
PYTHONPATH=. .venv/bin/pytest -v
```

