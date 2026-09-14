# Fix loop, round 3: declaration

Committed **before** the fix. The next commit is the fix; `git log` shows the order.

## 0. What was seen before this was written

Rounds 1 and 2 declared a hypothesis before any code was written against it. This round did not,
and that changes what the prediction below is worth.

The corrections were first tried by a script outside the repository, on intermediates saved from
the 14 Sep runs: each capture's occupancy rasters, wall candidates, wall segments and room
polygons. It post-processed the room polygons and printed these numbers, which I read:

| capture | before | in that experiment |
|---|---|---|
| long walk `163f18d3ac`, footprint | 31.26 m² | 29.14 m² |
| home walk `ae3edc814d`, footprint | 25.49 m² | 25.49 m² |
| bedroom walk `5621ec5c54`, footprint | 13.58 m² | 10.89 m² |
| `c00a170fe1` single room | 26.90 m² | 20.91 m² |
| `1a8384c3f6` floor only | 42.26 m² | 38.86 m² |
| `c7d28f72c6` with ceiling | 47.31 m² | 41.24 m² |

No taped room changed in it. So the prediction is not blind to those numbers. What has not been
run is the pipeline. In the fix, the new evidence rasters are built inside `build_occupancy` from
the fused cloud rather than recomputed from saved points, the corrections run inside the LiDAR
pipeline after `room_polygons`, and each room's mask, floor and ceiling levels, openings, walls,
intervals and the adjacency graph are computed downstream of them. This declaration commits to what
the full runs will show.

Four other rules were tried in the same experiment and are **not** in the fix:

| rule | why rejected |
|---|---|
| more wall lines, 44 to 60, 80 or 120 | the corridor did not change; the floor-only scan lost its bathroom as a room, and the two whole-flat scans' registered footprints fell from 0.65 to 0.44 IoU |
| cut room ends where under 5–20% of a slice has evidence | cut the long walk's taped passage from −11% to −30% |
| drop wall segments more than 12° off the building axes | home rooms' mean error 15.3% to 17.0%; the floor-only scan split into 9 rooms |
| count furniture tops as floor evidence | floor-only living room 5.35 to 6.49 m² with a new spike in its outline; home error moved 0.2 points |

## 1. The gate, with the failing number

**LiDAR footprint on the long walk `163f18d3ac`: 31.26 m² against 28.75 m² taped, +8.7%. Gate
±5%: FAIL.** From `fixloop/round3/gates/before/`, produced by `dd95678`.

The other taped LiDAR footprints: home walk 25.49 m² (−11.4%), FAIL; bedroom walk 13.58 m² against
9.29 m² (+46.1%), FAIL.

Why this gate. The defects this round is about are on the assignment's three samples, and no gate
can score them: there is no tape for those captures, so every accuracy gate on them is SKIP. The
long walk's footprint is the one taped gate the same mechanism reaches. `interval_coverage` (0/16,
2/16, 1/5) and `wall_lengths` (0/35 within 2 cm) are further from passing and have other causes.

Said plainly, so it cannot be misread later: **if the long walk's footprint moves to PASS, it will
be because a room the tape does not name was removed**, not because any taped room became more
accurate. That room is `room_07`, 2.12 m²; `capture/room_map.json` records that no keyframe stands
inside it and leaves it unmapped.

## 2. Root cause, and the evidence

**Hypothesis.** A room is a union of faces of the wall-line arrangement. Each face is labelled
interior from evidence averaged over the whole face, and then kept whole. A face is as large as the
wall lines around it allow, so where no wall line crosses the place a space ends, the face runs on
past it and the room inherits all of it: floor that is not there, and floor that belongs to the
next space. Three forms of it are visible on the assignment's scans.

**(a) A corridor seen from its doorway.** `c00a170fe1` room_02, 8.82 m², is 6.02 m long and 1.63 m
wide on its own axis. One side has a measured wall segment for the first 2.71 m from the mouth; the
other side has none; beyond 2.71 m neither side has any. All 25 keyframes inside it are within
0.31 m of the mouth. Registered onto the floor-only scan (82% of wall points within 5 cm, 97%
within 10 cm), the room crosses the passage and both of its walls and ends inside that scan's
bathroom `F06` (`evidence/single_room_rooms_on_floor_only_scan.png`). Frames 1576 and 1617 look
along a tiled corridor, and 1680 through a bathroom door off it (`evidence/single_room_frames.jpg`).
The with-ceiling scan, which walked this corridor, reports it as a 2.20 m² room.

**(b) A stairwell.** In `1a8384c3f6` room_01 (11.40 m²) and `c7d28f72c6` room_01 (12.89 m²) there
are 6,480 and 7,458 upward-facing returns below the floor plane, median −0.46 m, 5th percentile
−0.78 m and −0.88 m: the treads of a flight going down. Across all six LiDAR captures no other
room's 5th percentile is below −0.25 m; the bathrooms' below-floor returns lie between −0.10 and
−0.14 m. Frame 8579 of the with-ceiling scan looks over the railing into the well
(`evidence/with_ceiling_frames.jpg`); the red regions of `evidence/*_heights.png` are the well. The
face over it has carved free space and ceiling above it, which is why it was labelled floor.

**(c) A walled space nobody saw into.** Between the bathroom and the living room is a region with no
floor, furniture-top or ceiling return in any of the three scans. Its outline runs along wall
returns for 85% of its length inside `c00a170fe1` room_04 and 86% inside `c7d28f72c6` room_05. The
floor-only scan, which walked both neighbouring rooms, leaves it out of both.

**(d) The long walk's `room_07`.** Never walked; towards its far end neither of its sides has a wall
segment for more than a door width. In the experiment it was the only change on any home capture
(`evidence/long_walk_plan_on_scan.png`).

## 3. The fix, and the predicted numbers

**Fix.** `geometry/occupancy.py` gains four rasters from the fused cloud: upward-facing returns more
than 0.10 m below the floor, more than 0.30 m below it, upward-facing returns 0.07–2.10 m above it,
and near-vertical returns 0.3–2.0 m above it. A new `geometry/refine.py` runs on every room after
`room_polygons` and removes floor in three places only:

1. Over a stairwell: a connected region of below-floor returns of at least 0.25 m², at least
   0.10 m² of it deeper than 0.30 m, removed as a rectangle on the room's own axis. What is left is
   opened by 0.15 m, so no strip too narrow to stand in survives between the well and a wall.
2. At an end of a room where neither side has a wall segment for longer than a door, 1.60 m, which
   is the open-span width `_assign_rooms` already uses.
3. A region of at least 0.60 m² with no floor, furniture, ceiling or walk evidence in it, whose
   outline is at least 60% wall returns.

Nothing is grown and no wall is moved. Every removal is written into the plan's warnings, and a
`refine_rooms` switch in `PipelineConfig` turns the step off.

**Predictions, stated before running the pipeline:**

| quantity | before | predicted after |
|---|---|---|
| long walk footprint | 31.26 m² (+8.7%) | **28.90–29.40 m², inside ±5%: gate moves to PASS** |
| long walk rooms | 7 | 6: `room_07` gone, the other six each within 0.05 m² |
| long walk hall / bedroom / bathroom / passage | 13.61 / 5.64 / 3.02 / 2.27 m² | each within 0.05 m² of before |
| long walk adjacency | 2/6, 4 phantom | 2/5, 3 phantom (bathroom–room_07 gone); stays FAIL |
| home walk footprint and every room | 25.49 m² (−11.4%) | each within 0.05 m²; stays FAIL |
| bedroom walk footprint | 13.58 m² (+46.1%) | 10.60–11.20 m²; stays FAIL; bedroom 8.90 m² within 0.05 |
| `interval_coverage`, `wall_lengths` | FAIL on every taped capture | stay FAIL |
| `c00a170fe1` | 4 rooms, 26.90 m² | 4 rooms, 20.60–21.20 m²; corridor 4.30–4.70 m²; room_04 1.50–1.90 m² |
| `1a8384c3f6` | 8 rooms, 42.26 m² | 8 rooms, 38.60–39.10 m²; stair hall 7.80–8.20 m² |
| `c7d28f72c6` | 7 rooms, 47.31 m² | 7 rooms, 41.00–41.50 m²; stair hall 8.10–8.50 m²; room_05 5.30–5.60 m² |

What stays wrong, predicted so it is not discovered later and called a surprise:

* The corridor will still be about twice the with-ceiling scan's 2.20 m². Its unwalled side was
  never measured from the mouth, and this fix does not find it.
* The floor-only scan's living room stays at 5.35 m² against 10.77 m² from the single-room scan.
  Its outline is cut by diagonal wall segments from curtains, which none of the three corrections
  touches.
* The stair halls keep the flight going **up** and the unseen band beside the well. Only returns
  below the floor are treated as a stairwell.

What would show the hypothesis wrong: the corridor not ending within 0.2 m of where its wall segment
ends; the removed stairwell region not lying over the below-floor returns when drawn on the scan; or
any taped room moving by more than 0.05 m².

## 4. Regeneration

```bash
git checkout dd95678
for id in 163f18d3ac c00a170fe1 1a8384c3f6 c7d28f72c6; do
  cozmo run -i ../data/raw/$id -o fixloop/round3/before/$id
done
cozmo run -i ../DROP_CAPTURES_HERE/01_multiroom_lidar/ae3edc814d -o fixloop/round3/before/ae3edc814d
cozmo run -i ../DROP_CAPTURES_HERE/07_repeat_room_lidar/5621ec5c54 -o fixloop/round3/before/5621ec5c54
cozmo benchmark --runs fixloop/round3/before --ground-truth capture/ground_truth.csv \
    --room-map capture/room_map.json --out fixloop/round3/gates/before

git checkout <the fix commit>
# the same six runs into fixloop/round3/after/, and the benchmark into fixloop/round3/gates/after
```

`fixloop/round3/before/` and `fixloop/round3/gates/before/` are committed with this declaration,
produced by `dd95678`. The figures in `evidence/` were drawn from the 14 Sep runs, whose room
polygons `before/` reproduces exactly.
