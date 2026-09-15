# Known failure modes

Every entry here was observed on real data, not imagined. Where a number appears, it was
measured on the benchmark captures and can be reproduced with the command given.

## 1. The photo tier does not meet its accuracy gates

**Status: fails, and is reported as failing.**

On the home flat, before fix loop round 4, the photo tier reported three rooms of four at 97.19 m²
against a taped 28.75 m² (+238%), and the hall alone on the 1× lens at 35.12 m² against 14.86 m²
(+136%). Those plans were built with Depth Anything V2 Metric Indoor on each still alone, which
over-predicted depth on those photographs by 1.57× against LiDAR. The photographs are not on the
machine round 4 was done on, so those plans have not been rebuilt.

Fix loop round 4 (`fixloop/round4/`) reconstructs each room's stills together with VGGT-1B and sets
the scale with MoGe-2. On photo inputs made from the assignment's three walks and scored against the
LiDAR plan of each walk, the whole-property footprint moved from +148.4%, +52.5% and +84.8% to
**+4.3% (a pass, by room errors that cancel), +8.8% and +120.1%**. Room scales are now within −8% to
+2% of LiDAR depth and no room overlaps another, but walls within 8% are 4/23, 4/31 and 4/30 and
single rooms miss by −40% to +355%.

**The cause is now the room outline, not the depth.** The same reconstruction core, run on LiDAR depth
and ARKit poses of exactly the still frames, makes rooms +17% to +694% too large
(`fixloop/round4/evidence/oracle_core.txt`): four to eight stills see floor through every doorway, and
the photo path takes a room to be the floor its stills saw.

**What we do about it.** A plausibility guard drops any reconstruction outside 1–60 m² or 1.8–4.2 m of
ceiling and records why, so the tier reports fewer rooms rather than absurd ones. Intervals stay wide.

**What would fix it** is in `fixloop/round4/POSTMORTEM.md` §4: take a photo room's outline from wall
planes fitted in each still and merged across stills, so floor seen through a doorway cannot extend it.

## 2. Opening detection barely works at the photo tier

**Status: structural, not a tuning problem.**

Openings are found by looking for points *behind* a wall plane that a camera on the near side
saw through. A single photograph's depth map is a 2.5D surface with little behind it. The 58
stills at 0.5× detect no opening at all and the 12 of the hall at 1× detect one window, so the
photo tier cannot join rooms at a doorway; it joins them by folder name, and the plan says so.

The LiDAR tier finds 7 openings on the long walk of the same flat.

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
revisit (path walked at least 6× the distance closed, and at least 6 m); ICP must reach 0.55
fitness and 0.035 m RMSE and actually converge; the correction it asks for must be no more
horizontal drift than 10 cm or 3% of the path walked between the two keyframes; it must leave
height within 5 cm and tilt within 2° of odometry, which is referenced to gravity; the pose graph
moves only heading and horizontal position; and a soft-L1 loss keeps one false closure that
survives all of that from dominating. On the assignment's single-room scan all 16 candidates that
reached the fitness bar asked for 56–88 cm after under 7 m of walking, and none is kept.

Every plan reports its loop closures, pose residuals and largest correction. On `163f18d3ac`
(the 107 m long walk) the ablation, regenerated after fix loop round 3, with rooms named by overlap
with the named rooms of the plan before that round:

| variant | rooms | footprint | hall | bedroom | bathroom | passage | loop closures |
|---|---|---|---|---|---|---|---|
| drift off, snap off | 6 | 27.93 m² (−3%) | −5% | −48% | +38% | −20% | 0 |
| drift off, snap on | 5 | 24.17 m² (−16%) | −10% | −45% | −18% | not found | 0 |
| drift on, snap off | 6 | 29.10 m² (+1%) | −7% | −48% | +48% | +19% | 77 |
| drift on, snap on | 6 | 29.13 m² (+1%) | −8% | −39% | +48% | −11% | 77 |
| drift on, snap on, no room refinement | 7 | 31.26 m² (+9%) | −8% | −39% | +48% | −11% | 77 |

The fourth row is the published plan. The room refinement of fix loop round 3 changes only that
variant, where it removes a 2.12 m² room no keyframe stands in, and no taped room moves. The published
plan is now within 1% on footprint, level with drift on and snap off, and still the closest per taped
room, a mean absolute error of 26.6% against 27.7% and 30.6% with snapping off. It carries two rooms
outside the tape, a window bay and a strip of the bedroom, 4.59 m² together, and its footprint passes
on errors that offset, the bedroom 39% short and the bathroom 48% long. With snapping on, the walk
without correction loses the passage. This walk drifted little, its largest correction is 8.5 cm, so
correction changes less here than it would on a walk that drifts more.

Snapping moves only walls within 6° of the building frame and leaves the rest where they were
measured, so a room can be drawn out of square: the 1× photo hall has two corners at 61°.

## 6. A room the operator did not walk into is not reported

A face of the floor plan is interior only on direct evidence, and an unwalked face that no wall
faces is dropped. This is deliberate — it is what stops the reconstruction leaking through a glass
balcony door and reporting the courtyard as a room, which it did before the rule existed (195 m²
for a 44 m² flat). Faces with interior evidence that join a walked face across a boundary with no
wall behind it are kept, so the middle of a large room survives: without that the first walk's
hall came out at 6.04 m² against a taped 14.86 m², and with it at 13.90 m². The cost runs the other
way too. A space entered for a moment is drawn out to whatever face the wall lines give it, however
little of its floor was seen. The assignment's single-room scan stood only at the mouth of a
corridor, all 25 of its keyframes there within 0.31 m of the mouth; the corridor's face ran 6.02 m,
across the passage beyond it and into a bathroom, and the plan drew an 8.82 m² room. Since fix loop
round 3 a room ends where neither of its sides has a wall for more than a door width, and that room
is 4.51 m². It is still about twice the 2.20 m² the with-ceiling scan walked, because its unwalled
side was never measured from the mouth.

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
ceilings, doors or bathroom walls, and nothing on the assignment's flat, so 33 of 66 gate rows
report `SKIP`. None is reported as passing.

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

## 13. The same bedroom differs between walks

The long walk reconstructs the bedroom at 3.06 × 1.97 m with a 0.9 m wide strip of it split off as a
separate room, the first walk at 2.91 × 2.61 m, and the bedroom walked alone at 3.78 × 2.64 m, against
a taped 10 × 10 ft (3.05 × 3.05 m). The planes just outside the long walk's bedroom are the far faces
of 230 mm brick partitions, not hidden walls, so the loss is not furniture standing in front of the
walls. The cause is not yet found. Against the long walk, 1 of 6 of the solo walk's walls agrees within
1 cm (worst 123.7 cm). The bedroom ceiling repeats to 0.8 cm between the two home walks and to under 0.05 cm
between the long walk and the solo walk.

## 14. LiDAR intervals do not cover the tape

0 of 16, 2 of 16 and 1 of 5 measurements fall inside their intervals, on the long walk, the first
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

The bedroom-only walk began and ended at the doorway, outside the room, so the plan holds a strip of
passage beside the 8.90 m² bedroom, and the footprint row compares the two together with the
bedroom's 9.29 m². The strip was 4.68 m² and 5.58 m long against a taped passage of 3.35 m. Since fix
loop round 3 it ends where its walls end, at 1.98 m², so the footprint row reads 10.88 m² (+17%), and
a 0.90 m door is now found in the bedroom doorway at its end. The protocol asks for both still
periods just inside the doorway.

## 20. The assignment's flat, scanned twice, disagrees with itself

`single_scan_floor_only.zip` and `single_scan_with_ceiling.zip` cover the same space. They
reconstruct as 8 rooms and 38.91 m² and as 7 rooms and 41.58 m², 7% apart; before the room
refinement of fix loop round 3 they read 42.26 and 47.31 m², 11% apart. Neither has tape, so
neither can be called right. Aligned on their walls, 63% of the with-ceiling scan's wall points lie
within 5 cm of the floor-only scan's walls and 81% within 10 cm. The two room footprints overlap at
an intersection-over-union of 0.61, down from 0.65: each scan's stair hall lost a different
rectangle to its stairwell (§21), and the floor-only scan's living room is still cut short by
diagonal wall segments from its curtains.

Rooms are drawn where they were measured. Some floor between rooms is not in any room, so rooms the
operator walked between stand apart, and `quality.warnings` names every declared connection a plan
draws more than 0.30 m apart: six on the floor-only scan, at 0.31–1.19 m, and seven on the
with-ceiling scan, at 0.40–2.06 m. Before fix loop round 3 there were six on each; taking floor out
of rooms can only widen those gaps. An earlier version closed those gaps by moving whole rooms, by
up to 1.73 m, which made the plans look connected and put rooms where they were not measured.

## 21. Removing a stairwell or a walled space takes some seen floor with it

`geometry/refine.py` (fix loop round 3) takes a stairwell and a walled space nobody saw into out of
a room as a rectangle on the room's own axis. A drop is not rectangular, so the rectangle covers
landing along its edges; a strip under 30 cm wide left between the rectangle and a wall is opened
away; and a piece of landing the rectangle separates from the rest of the room is dropped, because a
room is one polygon. Classified on a 5 cm raster of each scan:

| room | removed | floor seen in it | below the floor | nothing seen |
|---|---|---|---|---|
| floor-only scan, stair hall | 3.39 m² | 0.75 m² | 1.32 m² | 1.46 m² |
| with-ceiling scan, stair hall | 4.57 m² | 1.05 m² | 1.72 m² | 1.60 m² |
| single-room scan, room_04, walled space | 1.67 m² | 0.49 m² | — | 1.29 m² |
| with-ceiling scan, bathroom, walled space | 1.51 m² | 0.26 m² | — | 1.40 m² |

Shrinking the rectangle off seen floor gave back 0.45 and 0.29 m² of landing and left 0.31 and
0.15 m² of the well in the hall; cutting the seen floor out of it gave back more and left more of the
well, with outlines of 32 and 45 vertices. Neither shipped. The stair halls also keep the flight
going up, and only returns below the floor are treated as a stairwell.

The corrections run on the LiDAR tier only. The photo and video tiers reconstruct each room through
the same builder, and on their first regeneration with the corrections the rules read monocular
depth error as absent floor: the 1× hall photos went from 35.12 to 2.11 m² when the open-end rule
cut 33.01 m², the 0.5× bedroom lost 99.88 m² the same way and 6.27 m² to "stairwells" below a floor
that was never measured, and the 0.5× set gained a room overlap. A monocular depth map has partial
walls and no trustworthy returns below the floor, so it cannot show that floor is absent.

## 22. Slits in a room outline are filled only where the scan saw floor

Two wall lines a few centimetres apart leave a strip of cells between them, and the room's outline
runs round it. On the with-ceiling scan one such slit, 8 cm wide, reached 2.1 m into the 7.14 m²
room; the floor-only living room had a wedge a few centimetres wide at a corner. `geometry/refine.py`
closes each outline by 20 cm and fills a strip that closing adds only where floor returns cover at
least 70% of its cells and wall returns at most 30%, and never where another room lies. It filled
0.05 m² in the floor-only living room, 0.20 m² over two slits in the with-ceiling living room and
0.15 m² in the 7.14 m² room.

Three strips stay, because the scan saw wall in them: on the floor-only scan a 23 × 57 cm wedge in
the living room with wall returns in 67% of it and a 31 × 45 cm notch in room_05 with 65%, and on the
with-ceiling scan a 30 × 346 cm strip in the stair hall with 74%. Whether each is a real wall stub
has not been checked against the camera frames, and so they remain in the outlines as drawn. An
8 × 30 cm notch in the floor-only living room sits at the threshold and is not filled.

Filling a slit removes its sides, and an opening is attached to the room edge its centre falls on.
The with-ceiling living room had a window 0.68 m wide with a 1.69 m sill on a 0.70 m side of one
slit; that side now lies 0.35 m inside the room, so the plan no longer carries the window. It is not
known from the frames whether the window was real.

An opening is measured on the whole wall run it was found in, so it can be wider than the room edge
it is attached to when the room's outline stops short of its far jamb. The floor-only scan draws a
2.78 m pass-through on a 1.59 m edge of room_05. The width is reported as measured, not clipped to the edge.

## 23. The 15 Sep changes: the canonical frame, and rooms from evidence

Two changes on 15 Sep move every LiDAR plan.

The rotation meant to put the walls on the grid axes doubled their angle instead of removing it, so
the assignment's three scans reached the occupancy grid with their walls at 44.5°, 80.0° and 55.0°,
and every raster staircased them. `walls.canonical_rotation` fixes it, leaving 0.46°, 0.03° and 0.24°.

LiDAR rooms are now built by `geometry/layout.py` from wall barriers, doorways and the floor the scan
saw, instead of by labelling the faces of the wall-line arrangement. `--layout cellcomplex` keeps the
cell complex for the ablation, and the room refinement of fix loop round 3 (§21, §22) runs only with it.
On the two whole-flat scans, registered on their walls, the footprints are 49.88 and 50.82 m², 1.9%
apart, overlapping at an intersection-over-union of 0.735. The cell complex on the same corrected frame
gave 43.76 and 38.72 m², 11.5% apart, at 0.648; that was measured during development and is not kept
in `reports/verified/`. Wall by wall the two scans still do not repeat: 0 of 52 walls agree within
1 cm or 0.5%, with a median difference of 24.7 cm, and 5 of 9 rooms find a counterpart at an
intersection-over-union of at least 0.5 (`reports/verified/repeatability/`). They are not a controlled
repeat, since one never looks up and walks for 115 s against 215 s, so this shows the segmentation is
not stable to the centimetre without showing why.

None of this has been checked against tape. The home flat's raw captures were not on the machine where
these changes were made, so `multiroom_long`, `multiroom_home`, `bedroom_solo` and the photo runs in
`reports/verified/` are still the plans of `a92927c`, and every tape-scored number in this file,
`benchmark_report.md` and the technical report describes that code. Re-scored together with the new
assignment plans, the table still reads 15 PASS, 18 FAIL and 33 SKIP against the tape, and 11 PASS and
5 SKIP on the ray-traced rooms, which reconstruct to 3.599 × 2.800 m with a 2.499 m ceiling, the
0.85 m door at 0.84 m and the 1.10 m window at 1.10 m.

Defects of the evidence layout seen on the assignment's scans:

- A space seen but never walked into is left out and named in `quality.warnings`: 2.37 m² on the
  floor-only scan and 4.92 m² on the with-ceiling scan.
- Floor seen far along a corridor from its mouth stays in the room the walk reached: the single-room
  scan's corridor is 7.02 m², drawn from returns up to 5 m away.
- One doorway can be proposed on two wall faces a partition apart and reported twice: the floor-only
  scan joins room_01 and room_06 through two doorways side by side.
- A doorway whose head is not seen in the returns is given a 2.05 m height with a ±0.15 m interval.
- Dimension labels overlap on strips narrower than about 0.4 m, and a notch between rooms narrower
  than 0.4 m is drawn as solid wall.
