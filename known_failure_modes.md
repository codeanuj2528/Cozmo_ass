# Known failure modes

Every entry here was observed on real data, not imagined. Where a number appears, it was
measured on the benchmark captures and can be reproduced with the command given.

## 1. The photo tier does not meet its accuracy gates

**Status: fails, and is reported as failing.**

On the benchmark property the photo tier reports one room of four at a footprint 36% below
the LiDAR reconstruction. It does not reach the ±8% gate.

The cause is measured, not guessed. Depth Anything V2 Metric Indoor over-predicts depth on
these photographs by a factor established two independent ways:

| method | factor |
|---|---|
| Camera height implied by the detected floor, against a true ~1.45 m | 1.76 |
| Median ratio of LiDAR depth to predicted depth, 8 frames, same property | 1.57 |

The photographs are 0.5x ultra-wide (14 mm equivalent, 88° horizontal). The model is trained
on normal-field-of-view indoor imagery. A metric monocular model infers depth from apparent
size, which needs an assumed focal length, so an out-of-distribution field of view shifts its
metric scale proportionally.

**What we do about it.** A plausibility guard drops any reconstruction outside 1–60 m² or
1.8–4.2 m of ceiling and records why, so the tier reports fewer rooms rather than absurd
ones. The capture protocol now specifies the 1x lens.

**What would fix it** is in `fixloop/POSTMORTEM.md`: capture at 1x, and fit the
focal-to-scale correction against the LiDAR tier, which supplies depth ground truth on the
same property for free.

## 2. Opening detection barely works at the photo tier

**Status: structural, not a tuning problem.**

Openings are found by looking for points *behind* a wall plane that a camera on the near side
saw through. A single photograph's depth map is a 2.5D surface with little behind it. The 58
stills at 0.5× detect no opening at all and the 12 of the hall at 1× detect one window, so the
photo tier cannot join rooms at a doorway; it joins them by folder name, and the plan says so.

The LiDAR tier finds 8 openings on the long walk of the same flat.

Fixing this needs a different detector for the photo tier — appearance-based door detection,
or a learned layout estimator — not a threshold change.

## 3. Ceiling height is unmeasurable without the upward lap

If the operator never points the phone at the ceiling there are no downward-facing surface
returns and the height cannot be computed by any method. The company's own `single_room`
sample has 61 downward-facing points in the entire scan.

The pipeline reports `ceiling unmeasured` rather than substituting a default. On the first
capture of the benchmark property, 1.4% of frames were aimed up and the result was poor; on
the second, 24.3% were, and per-room heights came out at 2.56–2.67 m, with the
window bay, whose only upward surface is a ledge, reporting none.

## 4. Mirrors, glass and wet-look surfaces

**Handled, with residual risk.**

A mirror returns depth at the reflected distance, so the surface reads as empty and the
reflection reads as structure behind the wall — indistinguishable from a window to a
see-through test. Two defences:

- **Geometric mirror test.** Suspect points are reflected back across the wall plane; if they
  land on the room actually in front of it, the opening is a reflection. The sample flat's
  bathroom walls score 0.21–0.34 on this test.
- **Multi-view damage requirement.** A specular highlight is view-dependent and never
  reprojects to the same patch of surface twice. Requiring two viewpoints took damage on the
  author's marble-and-glass flat from 21 regions to 1, in a property with no damage in it.

Residual risk: a large mirror facing a blank wall could still pass both tests. A floor-length
mirror is the worst case and is untested.

## 5. A false loop closure can fold the map

Loop closure on a wrongly matched pair folds the map. Guards: a candidate must be a genuine
revisit (path walked at least 6× the distance closed), ICP must reach 0.55 fitness and
0.035 m RMSE, and the pose graph uses a soft-L1 loss so one surviving false closure cannot
dominate.

Every plan reports its loop closures, pose residuals and largest correction. On `163f18d3ac`
(the 107 m long walk) the four-way ablation, regenerated at this commit:

| variant | rooms | footprint | loop closures |
|---|---|---|---|
| drift off, snap off | 5 | 20.25 m² | 0 |
| drift off, snap on | 4 | 16.62 m² | 0 |
| drift on, snap off | 5 | 25.77 m² | 110 |
| drift on, snap on | 5 | 25.27 m² | 110 |

On this capture correction makes the plan better, not worse: with snapping on, the walk without
it loses a room and a third of its footprint. The last row is the published plan.

Snapping moves only walls within 6° of the building frame and leaves the rest where they were
measured. The long walk's hall keeps one wall meeting its neighbours at 78°, the bedroom walked
alone one at 79°, and the 1× photo hall two corners at 61°, so those rooms are drawn out of square.

## 6. A room the operator did not walk into is not reported

A face of the floor plan is interior only on direct evidence, and an unwalked face that no wall
faces is dropped. This is deliberate — it is what stops the reconstruction leaking through a glass
balcony door and reporting the courtyard as a room, which it did before the rule existed (195 m²
for a 44 m² flat). Faces with interior evidence that join a walked face across a boundary with no
wall behind it are kept, so the middle of a large room survives: without that the first walk's
hall came out at 6.04 m² against a taped 14.86 m², and with it at 13.90 m². The cost runs the other
way too. A space entered for a moment is drawn out to its walls however little of its floor was
seen: the assignment's single-room scan reports an 8.82 m² room beside the living room that the
walk entered only briefly, and 7.87 m² of that plan's 26.90 m² has no floor seen within 10 cm and
no walking within 30 cm.

## 7. Damage detection without model weights

The classical detector runs: colour-anomaly for stains, black-hat ridge with tiling-pattern
and straight-edge rejection for cracks. It is discriminative on synthetic walls (clean →
nothing; stain → one stain, no crack; wandering crack → one crack, no stain; tile grid →
nothing; a long ruler-straight line → nothing) but it has no open-vocabulary capability and will
miss classes it was not written for. It also misses a crack that runs dead straight for more than
about 150 px. The plan records which detector produced each finding.

## 8. Ultra-wide lens distortion is not modelled

Intrinsics are pinhole. At 0.5x the iPhone's residual barrel distortion after in-camera
correction is not zero, and nothing here compensates for it. Another reason the protocol now
specifies 1x.

## 9. Scale uncertainty does not reach the ceiling-height interval

`sigma_height` is composed from plane-fit terms only. That is correct for the LiDAR tier,
where scale is measured by the sensor, and wrong for the photo tier, where the dominant
uncertainty is scale. Photo-tier ceiling intervals are therefore narrower than they should
be. Identified but not fixed before the deadline.

## 10. Gates without ground truth are not evaluated

The operator's tape covers walls, floor areas and adjacency on the home flat. It covers no
ceilings, doors or bathroom walls, so 14 of 33 gate rows report `SKIP`. None is reported as
passing.

## 11. Room identity has to come from what the camera saw

Room ids are assigned per reconstruction in order of area, so `room_03` means a different room in
each capture. The first room map named rooms by matching their areas to the taped areas, which is
circular when area is being scored, and it was wrong on both captures: the long walk's bathroom was
scored as the passage and the first walk's bedroom as the hall. Rooms are now named from frames
taken by the camera standing deepest inside each room (`capture/room_identity/`), and the map is
keyed by capture.

## 12. Bathroom and passage merge on the long walk

On the long walk one reconstructed room still holds the bathroom and part of the passage in front
of it, so the bathroom reads 3.02 m² against a taped 2.04 m² (+48%) and the passage 2.27 m² against
2.55 m² (−11%). On the first walk the two are now separate rooms: the bathroom reads 2.08 m² (+2%)
and the passage 1.93 m² (−24%).

## 13. The same bedroom differs by 0.3–1.2 m between walks

The long walk reconstructs the bedroom at 2.58 × 2.08 m and the first walk at 2.89 × 2.68 m, against
a taped 10 × 10 ft. The planes just outside the long walk's bedroom are the far faces of 230 mm brick
partitions, not hidden walls, so the loss is not furniture standing in front of the walls. The
cause is not yet found. Walked on its own (§19), its walls sit up to 1.21 m from the long walk's. The
bedroom ceiling repeats to 4 mm between the two home walks, and the solo walk reads it 2.9 cm lower.

## 14. LiDAR intervals do not cover the tape

0 of 16, 0 of 15 and 0 of 5 measurements fall inside their intervals, on the long walk, the first
walk and the solo bedroom walk. The interval model includes sensor
noise, plane roughness and residual drift, and excludes segmentation error, which on this flat is
tens of centimetres. No quantiles were fitted to widen them: with three walks of one flat, the rows
used to fit would be the rows scored.

## 15. A tape in whole feet cannot adjudicate a 2 cm gate

The operator recorded 16 × 10 ft, 10 × 10 ft and 11 × 2.5 ft. A reading rounded to the foot carries
±15 cm, so the wall-length gate at 2 cm is unscorable against it in either direction. The gate is
scored as written and is not softened for the tape's precision.

## 16. Damage on a damage-free flat

No LiDAR run reports damage now. Before the surface, straight-edge and same-patch tests (§7 and
`docs/design.md` §7), the first walk reported a 0.08 m² water stain and later a crack on the rim
of a toilet seat, the assignment's floor-only scan reported the lower edge of a picture frame as a
crack, and its two flat scans a vanity front and the edge of a fridge. All were false. Neither
flat is known to have damage, so these runs show the detector staying silent, not that it finds
real damage.

## 17. Segmentation moves with the height band

Measured on the code at `4c7f3e2`, before the drift, segmentation and damage changes of 14 Sep, and
not re-measured since. The whole-property floor and ceiling bound the height bands for wall voting (to 6 cm below the
ceiling) and occupancy (to 12 cm below it). Reading those two levels over the centre of the floor
instead of at the world origin raises the long walk's property ceiling by 2.8 cm, well inside both
margins. It should change nothing. It changes the footprint from 25.27 to 24.51 m²; the bathroom
shrinks from 2.63 to 2.15 m² while the window bay grows from 2.19 to 2.35 m², so the two swap room
ids; and three water stains appear on a flat that has none. Turning off the ceiling evidence added
in fix loop round 2 leaves that result unchanged room for room, so the sensitivity lies in wall
voting and occupancy, not in that change.

The property levels are therefore still read at the origin, and the published plans are the ones
that reading produces. Neither version is closer to the tape overall: the bathroom improves from
+29% to +5% and the hall worsens from −11% to −15%. A segmentation that a 3 cm band edge can re-cut
is not stable enough to be judged by a tape recorded to the foot.

## 18. The 1× hall is nearly the right height and far too large

Twelve stills of the hall on the 1× lens reconstruct a 2.74 m ceiling, within 6% of LiDAR's 2.60 m,
over a 35.12 m² floor against 14.86 m² taped. A scale error would move both together. One photograph
of eight failed to register, and the hall has a glossy tiled floor that mirrors the windows and the
lights, the wet-look case in §4. Which of the two inflates the outline is not yet measured.

## 19. A room walked on its own keeps the passage it was entered from

The bedroom-only walk began and ended at the doorway, outside the room, so the plan holds a 4.68 m²
strip of passage beside the 8.90 m² bedroom, and the footprint row compares 13.58 m² with the
bedroom's 9.29 m². The strip is 5.58 m long against a taped passage of 3.35 m. The protocol asks for
both still periods just inside the doorway.

## 20. The assignment's flat, scanned twice, disagrees with itself

`single_scan_floor_only.zip` and `single_scan_with_ceiling.zip` cover the same space. They
reconstruct as 8 rooms and 42.26 m² and as 7 rooms and 47.31 m², 11% apart. Neither has tape, so
neither can be called right. Aligned on their walls, 63% of the with-ceiling scan's wall points lie
within 5 cm of the floor-only scan's walls and 81% within 10 cm, and the two room footprints
overlap at an intersection-over-union of 0.65.

Rooms are drawn where they were measured. Some floor between rooms is not in any room, so rooms the
operator walked between stand apart, and `quality.warnings` names every declared connection a plan
draws more than 0.30 m apart: six on each scan, at 0.31–1.11 m on the floor-only scan and
0.40–2.06 m on the with-ceiling scan. An earlier version closed those gaps by moving whole rooms, by
up to 1.73 m, which made the plans look connected and put rooms where they were not measured.
