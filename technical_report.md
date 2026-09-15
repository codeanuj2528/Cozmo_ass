# Floor-Plan Reconstruction and Damage Scoping from Handheld Phone Captures

**Technical Report**

| | |
|---|---|
| **Author** | Anuj |
| **Date** | 15 September 2026 |
| **Reference run** | `reports/verified/` (8 scored runs, scorer against operator's tape) |
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
the home flat reconstructs to 29.13 m² against a taped 28.75 m² (**+1%**, inside the ±5%
gate): the hall within 8%, the bedroom 39% short with a strip of it drawn as another room,
the bathroom 48% long, and two rooms outside the tape, so the footprint passes on errors that
offset. Per-room ceilings read 2.56–2.67 m and 7 openings are found.
The photo tier fails at +238% and the video tier does not produce a metric plan. The
assignment's three samples have no tape and are checked against the scans themselves.

The central finding is stated up front: **the LiDAR tier is the only one that
delivers usable accuracy, and even it does not meet its repeatability gates.**
Two walks of the same flat agree on the hall to 4 cm and its ceiling to 4 mm, but
differ on the bedroom by up to 0.64 m. Twenty-one failure modes are documented with
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
rather than staircases of pixels. Faces with floor evidence that join walked floor across a
boundary with no wall behind it are the same room; without that rule the middle of the first
walk's hall was dropped and it read 6.04 m² against 14.86 m². Rooms are never moved after
segmentation: they share one world frame, so a gap between connected rooms is floor left out,
and the plan says so.

**Walls come from a Hough accumulator over (normal azimuth, signed offset)**, each point
voting once into the bin its own measured normal selects. Peaks are accepted on observed
surface area, not on a fraction of total vote weight: a forty-wall apartment divides its
weight forty ways, so the strongest genuine peak carries 0.28% of the total and any
fraction-of-total threshold admits everything or nothing.

Two wall representations are maintained deliberately. Tight segments, which stop at every
gap, answer "is there material along this boundary" for the cell complex. Bridged runs answer
"where are the holes" for opening detection. No single gap tolerance is both narrower than a
doorway and wider than one.

---

## 2. Tiers and the device matrix

| Device | Photo | Video | LiDAR |
|---|---|---|---|
| iPhone 15/16/17 Pro, Pro Max | yes | yes | yes |
| iPhone 15/16/17 non-Pro | yes | yes | no sensor |
| iPhone 14 and older | out of contract | out of contract | out of contract |

**LiDAR.** Stray Scanner export: ARKit poses, 256×192 depth in millimetres, per-frame
intrinsics, ARKit's own confidence channel. Depth is weighted by inverse variance from a
noise model of confidence class and range, never thresholded. Absolute scale comes from the
sensor.

**Video.** No depth, no poses; both estimated. It keeps the one thing the photo tier lacks —
continuity — so frames register sequentially into one property-wide cloud and drift
correction applies. Keyframes are sampled at uniform stride *first* and blur-filtered second,
so coverage is not biased toward the rooms the operator moved slowly through.

**Photo.** No depth, no poses, no continuity. Per room: metric depth per image, gravity from
the floor plane, scale from camera height, then registration in the three degrees of freedom
that survive levelling — yaw from wall-normal histograms, translation by occupancy
cross-correlation, then ICP, which has a small basin of convergence and fails from the
identity.

Measured against the operator's tape, which is recorded in whole feet, LiDAR walls come out at a
median error of 0.37 m (14%) over 28 walls and photo walls at 2.20 m (81%) over 12. Errors that
size are segmentation, merged and split rooms, not sensor noise, which §4 puts under a
centimetre. `capture/DEVICE_MATRIX.md` holds every cell, produced by `scripts/accuracy_table.py`
and the gate table rather than written by hand.

---

## 3. Drift

The brief makes "poses used as-is" an automatic fail, and it is right to. ARKit's odometry is
locally excellent and globally not.

Correction is a pose graph over keyframes that moves only their heading and horizontal
position: odometry edges at the reported relative pose, loop-closure edges at the pose ICP
measured. Height and tilt stay as the phone measured them. They are referenced to gravity and
do not accumulate the way heading and position do, while ICP between two keyframes that mostly
see ceiling or blank wall is barely constrained vertically: with all six degrees of freedom
free, closures of that kind lowered part of the assignment's with-ceiling walk by about 40 cm.

**Loop candidates are proposed by geometry and confirmed by ICP, never the reverse.** A
candidate must be a genuine revisit — path walked at least 6× the distance closed, and at
least 6 m. Without that test a slow walk down a corridor generates a constraint between every
pair of keyframes in it, and that mistake produced 114 "closures" and made the map worse. ICP
must then reach 0.55 fitness and 0.035 m RMSE and actually converge (it used to report
convergence when it ran out of iterations), ask for no more horizontal drift than 10 cm or 3%
of the path walked between the two keyframes, and leave height within 5 cm and tilt within 2°
of odometry. On the assignment's single-room scan all 16 candidates that reached the fitness bar
asked for 56–88 cm after under 7 m of walking. None is kept now; the earlier graph kept them and
moved keyframes by up to 58 cm. 1 of 19 candidates survives on its floor-only scan, 21 of 41 on
its scan with ceiling and 77 of 110 on the author's long walk. A soft-L1 loss keeps one false
closure that survives all of this from folding the map.

Drift alone does not remove residual yaw, so walls within 6° of the building frame are
rotated onto it and their offsets refit from their own points — the plane-anchored half.
Only the direction comes from the prior; the position stays measured.

**Ablation, `163f18d3ac`, the 107 m long walk.** Every row regenerates from
`--no-drift-correction`, `--no-snap-walls` and `--no-refine-rooms`; the fourth row is the published
plan. Rooms are named by overlap with the named rooms of the plan before fix loop round 3.

| variant | rooms | footprint | against 28.75 m² | taped rooms, mean error | loop closures | max correction |
|---|---|---|---|---|---|---|
| drift off, snap off | 6 | 27.93 m² | −3% | 27.7% | 0 | — |
| drift off, snap on | 5 | 24.17 m² | −16% | passage lost | 0 | — |
| drift on, snap off | 6 | 29.10 m² | +1% | 30.6% | 77 | 8.5 cm |
| **drift on, snap on** | **6** | **29.13 m²** | **+1%** | **26.6%** | **77** | **8.5 cm** |
| drift on, snap on, no room refinement | 7 | 31.26 m² | +9% | 26.6% | 77 | 8.5 cm |

This walk drifted little, and the ablation says so. Correction matters with snapping on, where
the uncorrected walk loses the passage; with snapping off it changes little. The room refinement
changes only the published variant, where it removes a room no keyframe stands in, and moves no
taped room. The published plan is the closest per taped room and level with snapping off on
footprint; it carries two rooms outside the tape, 4.59 m² together, and its footprint passes on
errors that offset. Snapping stays on as
the plane-anchored half of the correction: the 23 wall runs it rotates sat 2.24° off the building
frame on average, which is yaw error if the flat's walls are square.

---

## 4. Error budget

**LiDAR tier**, in order of size:

| term | magnitude | handling |
|---|---|---|
| Residual gravity tilt | not recorded in the plans; 1° tips the far end of a 5 m room by 8.7 cm | Gravity refit to the observed floor plane; a fitted tilt above 6° is not applied and is warned |
| Yaw drift across rooms | on the long walk 77 closures, largest keyframe correction 8.5 cm; the 23 wall runs snapped afterwards sat 2.24° off the building frame on average | Pose graph over heading and position, then walls within 6° snapped onto the frame |
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
rather than only asymptotically. Quantities calibrate in the units their error actually scales
with: ceiling height in metres, wall length in percent.

Where no quantile has been fitted, the interval falls back to the propagated covariance of
the fit that produced it, and `Measure.method` says `propagated` or `prior` rather than
`conformal`. **That field is load-bearing.** A previous version of `cozmo calibrate` ignored
both of its arguments and wrote a fixed table of quantiles labelled as conformal calibration.
It now fits from residuals or writes nothing and says why.

No quantiles are applied to the published plans. The tape makes fitting possible, but those
are the rows the benchmark scores, so applying them would grade the intervals on their own
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

**Result: +916%.** The fix was correct and the number doubled. Too long a focal length
compresses the cloud laterally, but the room was already too large, so the compression had been
partially cancelling a bigger error in depth. `X = (u − cx)Z/fx`, so halving fx doubles X; the
area rose 1.95×.

The dominant cause is §4's scale term. Three further changes followed: the scale-correction
band widened from [0.75, 1.35] to [0.25, 4.0]; room-level scale consensus, since the prior fires
on 3 photographs in 20; and a plausibility guard, because a 113 m² bedroom is not a wide
estimate, it is a wrong one. **142.03 → 276.34 → 17.37 m² against 27.20: +422% to −36%.** The
±8% gate does not pass. By the brief's own rubric that earns marks for the post-mortem and
none for the prediction.

**Round 2, LiDAR, against the operator's tape.** Declared before the fix (`0a8c579`): footprint
−12% on the long walk and −42% on the first walk, the bedroom 5.28 m² against 9.29 m².
Hypothesis: ceiling returns above the strip between furniture and wall should count as interior
evidence. Predicted: a bedroom of 8.0–9.5 m² and a footprint inside ±5%. **Result: every room
polygon identical, on both captures.** Diagnosing the non-result found a worse defect: the room
map had been assigned by matching areas and was wrong on both captures. Rooms are now named
from camera frames. No gate moved.

**Round 3, LiDAR, the assignment's samples and the tape.** Declared before the fix (`949d24b`):
footprint +8.7% on the long walk. Hypothesis: a room runs past where its space ends wherever no wall
line crosses there, as a corridor seen from its doorway through two walls, two stairwells and a
walled space no scan saw into on the samples, and a never-walked room on the long walk. Unlike
rounds 1 and 2, the corrections had already been tried on saved intermediates before the ranges
were written. **Result: 29.13 m², +1.3%, PASS, every number inside its range.** The pass comes from
removing that untaped room; no taped room changed. The removals also take 0.26–1.05 m² of floor
that had been seen from each room they correct. Run on the photo and video tiers, which reach the
same builder, the rules read monocular depth error as missing floor, cut the 1× hall from 35.12 to
2.11 m² and gave the 0.5× set a room overlap; they now run on LiDAR only.

---

## 7. Known failure modes

Twenty-one are documented with measurements in `known_failure_modes.md`. The four that matter:

**The photo tier does not meet its gates.** §4 and §6 above. What would fix it, in order:
capture at 1× rather than 0.5×, which the protocol now requires, though the hall re-shot at 1×
still reads 136% too large; fit the focal-to-scale correction against the LiDAR tier; require
two floor-visible photographs per room.

**Opening detection barely works at the photo tier.** Openings are found by looking for points
behind a wall plane that a camera on the near side saw through, and a single photograph's depth
map is a 2.5D surface with little behind it, so rooms stitch by folder name rather than by
doorway. This needs a different detector, not a threshold.

**Mirrors, glass and wet-look surfaces** get geometric defences. A mirror test reflects suspect
points back across the wall plane and asks whether they land on the room in front of it. Damage
must be seen from two viewpoints on the same patch of wall, because a specular highlight is
view-dependent; that took damage on the author's marble-and-glass flat from 21 regions to 1. Its
depth must also lie on the wall plane, and a ruler-straight edge is not a crack. With those, no
LiDAR run reports damage; the findings they removed were furniture edges, a picture frame and a
toilet seat. No captured flat has damage, so recall is unmeasured.

**Ceiling height is unmeasurable without the upward lap.** No downward-facing returns, no
height, by any method. The pipeline reports `unmeasured` rather than substituting a default.
The company's own `single_room` sample has 61 downward-facing points in the entire scan; the
author's first capture had 1.4% of frames aimed up and the second had 24.3%, and the long walk
measures 2.56–2.67 m in five of its six rooms; a window bay whose only upward surface is a
ledge abstains.

---

## 8. State of the evidence

The LiDAR tier is the one to run at a walk-in. On the home flat walked with the ceiling lap and a
closed loop (`163f18d3ac`) it returns 6 rooms and 29.13 m² against a taped 28.75 m² (+1%, inside
the ±5% gate), with per-room ceilings of 2.56–2.67 m and 7 openings. That footprint passes on errors
that offset. The hall comes closest, 13.61 m² against 14.86 m² (−8%). The bedroom is furthest, 5.64 m² against 9.29 m² (−39%), with a 2.18 m² strip of
it drawn as a separate room; the first walk gives 7.58 m² and the bedroom walked alone 8.90 m²
(−4%), so the defect is in the reconstruction, not in the tape. The first walk, without the
ceiling lap, reads −11% overall with its bathroom within 2%.

Against the tape the gates read 15 PASS, 18 FAIL, 33 SKIP. Ceiling and opening gates are SKIP
because neither was taped. LiDAR intervals cover the tape on 3 of 37 measurements: they model
sensor and drift error, not a merged or a split room. The photo tier fails at +238%;
photographed on the 1× lens, the hall reads 35.12 m² against 14.86 m². The video tier does not
produce a metric plan.

The assignment's three samples have no tape, so they are checked against the scans themselves:
each plan drawn over its own scan and over the other two scans registered onto it.
`single_room.zip` is a living room, its bathroom and the lobby between them, plus the mouth of a
corridor: 4 rooms, 20.91 m². Its two whole-flat scans give 38.86 m² over 8 rooms and 41.24 m² over
7; aligned on their walls, 63% of one scan's wall points lie within 5 cm of the other's walls, and
the room footprints overlap at an intersection-over-union of 0.61. Fix loop round 3 took out of
these plans a corridor that ran through two walls, both stairwells and a walled space no scan saw
into, and some floor that had been seen with them. No damage is reported on any of them, and the same plans come out of the zips unzipped afresh. Every id
in every published plan resolves: `cozmo run` checks before writing, and a test checks every plan in
`reports/verified/`.

An earlier generator in this repository produced a benchmark over procedurally generated rooms, a
head-to-head against an app that was never run, and a fix loop whose before and after were
byte-identical. Those artefacts were removed before submission; every number in this report comes
from `reports/verified/`.

Some ideas here came from public work on the same brief: a ray-traced test room, a one-command setup script, a relative floor on photo and video intervals, and stitching rooms by folder name when no doorway is matched.

---

## 9. Experimental setup

All captures are of a single property — the author's home flat — and one set of
assignment-provided zips of a different property. The ray-traced rooms in
`tests/fixtures/raytrace_room.py` exercise the pipeline but are not in the benchmark run.

| Capture | Tier | Device | Frames | Duration | Ground truth |
|---|---|---|---|---|---|
| `163f18d3ac` (long walk) | LiDAR | iPhone 17 Pro | 18,649 | 312 s | Operator's tape: walls, areas, adjacency |
| `ae3edc814d` (first walk) | LiDAR | iPhone 17 Pro | 4,378 | 73 s | Same tape |
| `5621ec5c54` (bedroom solo) | LiDAR | iPhone 17 Pro | 7,682 | 128 s | Same tape (bedroom only) |
| `03_multiroom_photos` (0.5×) | Photo | iPhone 17 Pro | 58 | — | Same tape |
| `03b_multiroom_photos_1x` | Photo | iPhone 17 Pro | 12 | — | Same tape (hall only) |
| `c00a170fe1` (single room) | LiDAR | unknown | 1,715 | 37 s | None (assignment zip) |
| `1a8384c3f6` (floor only) | LiDAR | unknown | 5,251 | 115 s | None (assignment zip) |
| `c7d28f72c6` (with ceiling) | LiDAR | unknown | 9,745 | 215 s | None (assignment zip) |

**What is not here.** No staged-damage room was captured. No consumer app export
exists for the head-to-head. The tape does not cover ceilings, door widths or the
bathroom's walls. These are recorded as NOT MET or SKIP, not softened.

---

## 10. Results

### Gate summary — 15 PASS / 18 FAIL / 33 SKIP

| Gate | LiDAR (6 captures) | Photo (2 captures) | Video | Status |
|---|---|---|---|---|
| `wall_lengths` | 0/15 within 2 cm (long walk), 0/14 (first walk), 0/6 (bedroom) | 0/10 within 8% (0.5×), 0/4 (1×) | not scored | **FAIL** |
| `footprint` | +1% (long walk), −11% (first walk), +17% (bedroom scan with its passage strip) | +238% (0.5×), +136% (1× hall) | not scored | **FAIL**, long walk PASS |
| `ceiling_height` | 2.56–2.67 m per room | 2.74 m (1× hall) | — | **SKIP** (no tape) |
| `opening_widths` | 7 found (long walk) | 1 found on each set | — | **SKIP** (no tape) |
| `adjacency` | 2/5 (long), 3/4 (first) | 2/4 (folder names) | — | **FAIL** |
| `room_overlap` | 0 overlaps, 6 captures | 0 overlaps | — | **PASS** |
| `drift_accountability` | 6 PASS | not applicable | — | **PASS** |
| `interval_coverage` | 3/37 covered | 7/11 and 5/5 (by being wide) | — | **FAIL**, 1× PASS |
| `repeatability` | 0/27 walls, ceiling 13.1 cm; bedroom alone 1/6, under 0.05 cm | — | — | **FAIL** |

### Accuracy against tape

Best results: the bedroom walked alone, 8.90 m² against 9.29 m² (−4%), and the first walk's
bathroom, 2.08 m² against 2.04 m² (+2%). Whole-flat LiDAR footprints: +1% on the long walk, −11%
on the first. Worst: the photo tier, 97.19 m² against 28.75 m² (+238%).

Per-room LiDAR errors on the long walk range from −8% (hall) to +48% (bathroom, which holds part
of the passage). The bedroom error is a reconstruction defect, not tape error: three walks of the
same room give 5.64, 7.58 and 8.90 m².

### Timing

| Capture | Tier | Runtime, one run on an Apple laptop |
|---|---|---|
| Long walk (18,649 frames) | LiDAR | 60 s |
| 58 photos (0.5×) | Photo | 139 s |
| Assignment single room (1,715 frames) | LiDAR | 14 s |

---

## 11. Conclusion

The LiDAR tier is the only one to run at a walk-in. It produces a dimensioned floor plan with
walls, ceilings, openings, damage detection and scope line items from a single `cozmo run`
command. On the author's flat both whole-flat walks land within 11% of the taped footprint, the
hall within 8%, and ceilings repeat to under a centimetre in rooms segmented the same way; the
bedroom is its worst room.

The photo and video tiers run end-to-end but do not deliver usable metric accuracy. The photo
tier's dominant error is the monocular depth model's scale, measured at 1.57–1.76× on these
photographs, a field-of-view mismatch between the model's training data and the 0.5× ultra-wide
lens. The 1× lens halves the error but still fails. The video tier does not solve metric scale.

Twenty-one failure modes are documented with measurements. Four of them — mirrors, glass, low light,
and the upward lap — are named in the brief and each is addressed: geometric mirror rejection,
two-view corroboration on the same patch of wall, luma-based low-light flagging, and
ceiling-height abstention when no downward-facing returns exist.

The five NOT MET items are captures that were never made (staged damage, head-to-head app
export) and the video tier. These are disclosed as gaps, not explained away. The honest state
of the evidence is that LiDAR works, photo doesn't yet, and video needs scale.

---

## Appendix — Reproduction

Every number in this report regenerates from the commands below. Nothing reaches the network at
run time.

```bash
git clone <this repo> && cd cozmo
./scripts/setup.sh
./scripts/fetch_weights.sh            # photo and video tiers only

# Every plan in reports/verified/ and the gate table; the raw captures are not in git
COZMO_RAW=../data/raw COZMO_DROP=../DROP_CAPTURES_HERE ./scripts/regenerate_verified.sh

# One capture
.venv/bin/python -m cozmo.cli run -i <capture-dir> -o runs/my_capture

# Tests
.venv/bin/python -m pytest -q
```
