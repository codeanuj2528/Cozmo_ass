# Fix loop round 4: declaration

Committed before the fix. The commit that adds this file contains no code of the fix; the commit that adds
the fix comes after it in `git log`.

What was measured before this was written, and how, so the predictions below can be judged for what they are:

- the photo and video tiers' before runs (`before/`);
- VGGT-1B, MoGe-2 and Depth Anything V2 run outside the pipeline, on the same photo inputs and on LiDAR frames of
  the same walks, and ARKit's gravity against gravity estimated from the stills' camera axes (`evidence/`);
- tests of the wiring with stand-ins for the two models, which run the reconstruction core on a ray-cast room.

The fixed photo and video tiers had not been run on these inputs. Two evidence scripts,
`evidence/cache_vggt_views.py` and `evidence/evaluate_moge_fov.py`, call the model wrapper in
`src/cozmo/recon/multiview.py`, which arrives with the fix; run them at the fix's commit.

## 1. The worst gate, with its failing number

**The photo-tier whole-property stitch: footprint within ±8%.** The home flat's photographs, where this gate
failed at +238%, are not on the machine this round was done on, so the gate is measured on photo inputs made
from the assignment's three Stray exports (`scripts/make_tier_inputs.py`): 2–8 stills per room, chosen by
rule from each walk's own colour stream where the camera stood inside a LiDAR room, turned upright and tagged
with their focal length as an iPhone still is. The reference is the LiDAR plan of the same walk
(`reports/verified/`), not tape (`src/cozmo/bench/tiers.py`).

Before, at `f7d043f` (`before/`, `scripts/run_tier_benchmark.sh fixloop/round4/before`):

| Export | Photo rooms | LiDAR, those rooms | Photo plan | Footprint | Walls within 8% | Rooms overlapping |
|---|---|---|---|---|---|---|
| `single_room` | 3 | 20.34 m² | 50.52 m² | **+148.4%** | 1 of 24 | no |
| `single_scan_floor_only` | 6 | 50.82 m² | 77.53 m² (5 rooms) | **+52.5%** | 1 of 41 | 3.0% of the smallest room |
| `single_scan_with_ceiling` | 6 | 44.53 m² | 82.30 m² (5 rooms) | **+84.8%** | 4 of 23 | 12.4% |

Per room the error runs from −0% to +408%, and two folders give no room at all.

The video tier is worse and is not the declared gate. At `597400a`, which gave its input the rotation tag the
Camera app writes, its footprints are **+194.6%, +197.3% and +108.9%** against a ±5% gate, and no plan lines up
with the LiDAR walls well enough for its walls to be scored (17%, 46% and 49% of wall cells within 10 cm). It is
broken by the same cause and by a second one, joining a whole walk; its numbers after the fix are reported
without a prediction of a pass.

## 2. Root cause, and the evidence for it

**Hypothesis: the photo tier builds each still on its own.** A monocular metric depth model gives every still
its own depth and its own scale, a camera-height correction adjusts that scale from whichever stills show
enough floor, and the stills are then registered to each other by occupancy correlation and ICP. Both halves
fail on these inputs.

**Scale.** Depth Anything V2 Metric Indoor over-predicts depth on upright iPhone frames. Against LiDAR depth on
117 frames of the three walks, the median ratio is 1.280 (small) and 1.333 (large), with one frame's ratio
between 1.09 and 1.67 (small) and 1.18 and 1.52 (large) from the 10th to the 90th percentile
(`evidence/depth_scale.json`). The correction meant to absorb that fires on 1–4 stills of 6–8 per room and gives
room scales from 0.574 to 1.296 across the three exports (`before/*_photo/plan.json`, `quality.warnings`). A room
37% too large in each direction is 89% too large in area: +150%, +199%, +350%, +401% and +408% rooms are in the
table's plans.

**Geometry.** A still's depth map is a surface seen from one place. Registered to the next still by overlap it
has little of, a room comes out as views that do not agree: the plans in `before/` draw rectangles whose walls
miss the LiDAR walls by a median 42%, 50% and 28%, and three rooms overlap the room beside them.

**What a joint reconstruction gives on the same stills** (`evidence/`):

| | Measured on the 15 photo rooms and 106 stills |
|---|---|
| VGGT-1B depth against LiDAR, one scale per room | absrel 0.019–0.069 |
| One still's scale against its room's | spread 2.1–8.3% |
| Relative rotation between stills, against ARKit | median 1.9–4.4° in 10 rooms; in the other 5, 17 of their 38 stills are posed 41–168° wrong |
| Room scale from Depth Anything V2 small / large | median +37.6% / +35.1% |
| Room scale from MoGe-2 ViT-L given the stills' field of view | median −4.5%, range −7.4% to +2.6% |
| Room scale from MoGe-2 ViT-L left to guess its field of view | median −7.3% |
| MoGe-2 ViT-L depth against LiDAR, the 117 frames | ratio 0.967 given the field of view (0.88–1.04), 0.932 left to guess it |
| Room scale from a 1.40 m camera height over VGGT's floor | median −4.0%, range −33.7% to +99.4% |
| VGGT-1B's horizontal field of view, against EXIF's 48.5° | 41.2–48.1° per room; back-projecting with EXIF's focal length instead leaves as much residual against LiDAR (median over rooms 17.7 cm, against 16.9 cm) |
| Gravity as the mean of the stills' down axes, against ARKit | 1.9–28.9° per room, because the phone is tipped 7–37° down on average |
| Gravity as the direction every still's x axis is perpendicular to | 0.8–4.9° per room |

**The wrongly posed stills.** 17 of the 106 stills are posed 41–168° wrong against ARKit, in 5 rooms: one of
8 in `single_room` `room_01`, all 8 in its `room_02` (stills from 1.4 s of the walk), 3 of 8 in
`single_scan_floor_only` `room_01`, 3 of 8 in `single_scan_with_ceiling` `room_01` and 2 of 6 in its `room_04`.
Five checks that need no ground truth were measured against them, and none separates them from the 89 stills
posed right (`evidence/pose_checks.json`, `evidence/order_check.json`):

| Check | Wrong stills | Right stills |
|---|---|---|
| Down axis against the others' | 5–38° | 6–36° in the same five rooms, from how far the phone was tipped |
| Share of its points lying in space another still saw through | 0–100% | 0–75% |
| Share of its points within 10% of another still's depth | 0–77% | 0–100% |
| Relative rotation against the essential matrix from SIFT matches | 2 of the 333 pairs of stills have the 30 inliers to check | |
| Pose moved when the stills are given in reverse order | 4–119°, 10 of 17 above 25° | 0.3–127°, 6 of 89 above 25° |

The down-axis numbers say the errors are turns about the vertical, which gravity cannot see. Reversing the order
comes nearest, but dropping what moves more than 25° would remove 6 right stills for 10 wrong ones, and doubles
the model's time. The fix therefore does not find them, and the rooms that hold them are predicted to be the
worst rooms after it.

The test of the hypothesis is that joint geometry with a camera-aware scale moves every room's area by the
square of its scale error and no more. If the fixed plans still miss rooms by tens of percent where MoGe-2's
scale for that room was within a few percent and no still was posed wrong, the cause is the room outline, not
scale or registration, and this declaration is wrong about what dominates.

## 3. The fix

- Per photo folder, VGGT-1B reconstructs the stills together: depth, pose and intrinsics for each, in one frame
  (`src/cozmo/recon/multiview.py`).
- MoGe-2 ViT-L, given each still's field of view from EXIF, sets the room's metric scale: the median over stills
  of MoGe-2 depth over VGGT depth (`src/cozmo/recon/metric_scale.py`).
- Up is the direction every still's x axis is perpendicular to, the down axes breaking ties. The reconstruction
  core's floor fit refines it within 6°, as it does at LiDAR.
- A still whose down axis is more than 75° from up, turned sideways or upside down, is left out and the room
  reconstructed again. It finds none of the wrongly posed stills above, and is there for the stills it does find.
- The scaled, posed depth maps go to the same reconstruction core as before. Stitching, the plausibility guard and
  the output contract do not change. The monocular path stays behind `--no-multiview`, for the ablation and for a
  machine without the models.
- The video tier (`src/cozmo/recon/sequence.py`) keeps the sharpest frame of each second of the clip and
  reconstructs runs of 8 keyframes that share 3 with the run before. Each run is joined to the one before by a
  similarity fitted to the depth of the frames they share. A clip carries no focal length, so MoGe-2, run on every
  third keyframe, is given the field of view VGGT-1B estimated: on the 6 photo rooms with no wrongly posed still
  that sets the scale within −1.8% to +7.2% (median +1.9%), against −10.7% to +2.1% (median −5.9%) when MoGe-2
  guesses it (`evidence/moge_fov_partial.json`, 10 of the 15 rooms: the run was stopped). The core runs with
  drift correction.

Weights are fetched by `scripts/fetch_weights.sh`, pinned to a revision and checked by sha256: VGGT-1B
(CC BY-NC 4.0, research use) and MoGe-2 ViT-L (MIT).

## 4. Predictions

Written before the fixed tiers were run on these inputs. Scale alone, with each room's MoGe-2 scale error from
`evidence/multiview.json` squared and weighted by the room's LiDAR area, would move the three footprints by
**−2.9%, −11.3% and −8.4%**; over the rooms with no wrongly posed still, by −5.5%, −10.2% and −8.9%.

The photo tier, the declared gate:

1. Every export's footprint error falls from +148.4%, +52.5% and +84.8% to between −20% and +10%, and is negative
   in at least two of the three.
2. **The ±8% gate fails on `single_scan_floor_only` and on `single_scan_with_ceiling`.** On `single_room` it can
   pass only by chance: `room_02`, 7.02 m² of its 20.34 m², has every still posed wrong. At most one pass in
   three, most likely none.
3. Every folder gives a room: 15 of 15, against 13 before.
4. No room overlaps another by more than 2% of the smaller, in any export (3.0% and 12.4% before).
5. Walls within 8% rise from 1 of 24, 1 of 41 and 4 of 23 to at least a third of the scored walls in each export,
   still short of the 85% gate.
6. The largest per-room area errors in each export are in its rooms with a wrongly posed still. A room with none,
   whose MoGe-2 scale was within 5% (`single_room` `room_03`, `single_scan_floor_only` `room_05` and `room_06`,
   `single_scan_with_ceiling` `room_06`), comes within ±15% of its LiDAR area.

The video tier, not the declared gate:

7. Footprint error falls from +194.6%, +197.3% and +108.9% to within ±30% in all three, and the ±5% gate fails in
   all three.
8. At least two of the three plans line up with their LiDAR walls well enough for wall lengths to be scored (half
   the wall cells within 10 cm), where none did before.

Runtime on the 16 GB M5 this was built on: the photo tier under 6 minutes per export, the video tier under 20.

## 5. What would show this wrong

- A room with no wrongly posed still, whose MoGe-2 scale was within 5%, but whose area misses by more than 15%:
  the outline dominates.
- Footprint errors of the sign opposite to the scale-alone figures in §4: something other than scale dominates.
- Rooms still overlapping, or adjacency no better: registration inside a room was not the cause of those.
