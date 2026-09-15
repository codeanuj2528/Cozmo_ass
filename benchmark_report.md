# Benchmark report

15 Sep 2026, after fix loop round 3, every plan regenerated at `a92927c`: opening extents measured
without the one-cell dilation margin, slits in room outlines filled where the scan saw floor, and the
ray-traced room scored against its exact dimensions. Every number regenerates with
`scripts/regenerate_verified.sh`.
Ground truth is the operator's tape (`capture/ground_truth.csv`, `tool=tape`), recorded in whole or
half feet. It covers wall lengths, floor areas and adjacency for the home flat. It does not cover
ceilings, doors or bathroom walls, or anything on the assignment's flat, so those gates report SKIP.

## Gates

| status | against the tape | ray-traced room, exact truth | table total |
|---|---|---|---|
| PASS | 15 | 11 | 26 |
| FAIL | 18 | 0 | 18 |
| SKIP | 33 | 5 | 38 |

Full table: `reports/verified/gates/gate_table.txt`. The ray-traced room (`capture/ground_truth_synthetic.csv`)
is the only input with exact ceiling and opening truth. On it walls are within 0.6 cm, the ceiling
within 0.1 cm and both openings within 1.0 cm, with intervals covering all 6 values; without its
upward lap the ceiling is reported unmeasured and stays SKIP. It is noiseless and unfurnished, so these
rows show that the measurement is unbiased, not what a phone delivers in a real room. The rest of this
section is about the tape.

PASS: drift accountability on the six LiDAR runs; room overlap on the two home walks, the bedroom
scan, the assignment's three scans and the 0.5× photo set; footprint on the long walk, inside ±5%
because its per-room errors offset and, since fix loop round 3, a room never walked into is no
longer in it; interval coverage on the 1× hall photos, whose intervals are metres wide. FAIL: walls
and interval coverage on the two home walks, the bedroom scan and the 0.5× photo set; footprint on
the first walk, the bedroom scan and the 0.5× photo set; adjacency on the two home walks and the 0.5×
photo set; walls and footprint on the 1× hall photos; both repeatability pairs. SKIP: ceiling height and opening
widths on all eight scored runs (no tape); walls, footprint, interval coverage and adjacency on the
assignment's three scans (a different property, no tape); adjacency on the bedroom scan and the 1×
hall photos; room overlap on the 1× hall photos; drift on both photo sets (not applicable).

## Which reconstructed room is which

Named from RGB frames taken by the camera standing inside each room (`capture/room_identity/`),
never from area, because area is one of the scored quantities. An earlier map named rooms by matching
areas and was wrong on both captures. Room ids changed with the code of 14 Sep, so the map was rebuilt
from new frames, and each id's evidence is in `capture/room_map.json`. Two rooms of the long walk
are left out of it on purpose: a 0.9 m strip of the bedroom that the segmentation split off
(`room_06`), so the bedroom tape is not scored twice, and a window bay off the hall. A third, a space
no keyframe stands in, was removed from the plan by fix loop round 3.

## LiDAR against tape

Long walk `163f18d3ac`, which followed the protocol: ceiling lap done, loop closed, 312 s.

| Room | Tape | LiDAR | Error | LiDAR extent | Tape |
|---|---|---|---|---|---|
| Hall | 14.86 m² | 13.61 m² | −8% | 16.2 × 9.6 ft | 16 × 10 ft |
| Bedroom | 9.29 m² | 5.64 m² | −39% | 10.0 × 6.5 ft | 10 × 10 ft; a further 2.18 m² strip of it is a separate room |
| Bathroom | 2.04 m² | 3.02 m² | +48% | 8.5 × 6.5 ft | area only; this room also holds part of the passage |
| Passage | 2.55 m² | 2.27 m² | −11% | 8.5 × 4.0 ft | 11 × 2.5 ft |
| Window bay | not taped | 2.41 m² | — | 8.3 × 3.9 ft | — |
| Bedroom strip | not taped | 2.18 m² | — | 8.3 × 3.0 ft | — |
| **Footprint** | **28.75 m²** | **29.13 m²** | **+1%, PASS** | | the four taped rooms alone sum to 24.54 m², −15% |

Extents are the sides of the smallest rectangle around each room. The footprint passes because a
2.12 m² room that no keyframe stands in left the plan in fix loop round 3; before that it read
31.26 m², +9%, and no taped room changed. It passes with the bedroom 39% short and the bathroom 48%
long. Adjacency 2/5: found hall–bathroom and passage–bathroom; missed hall–passage and
passage–bedroom, because the walk crosses from the passage into the split-off strip of the bedroom,
which the map leaves unnamed; the three other edges lead to the two unnamed rooms, and the hall does
open onto its window bay. Walls 0/15 within 2 cm, worst 134.4 cm, 3 unpaired. 7 openings.

Home first walk `ae3edc814d`: 73 s, 1.4% of frames aimed at the ceiling.

| Room | Tape | LiDAR | Error | LiDAR extent |
|---|---|---|---|---|
| Hall | 14.86 m² | 13.90 m² | −7% | 16.1 × 9.5 ft |
| Bedroom | 9.29 m² | 7.58 m² | −18% | 9.5 × 8.6 ft |
| Bathroom | 2.04 m² | 2.08 m² | +2% | 8.4 × 2.8 ft |
| Passage | 2.55 m² | 1.93 m² | −24% | 7.3 × 3.1 ft |
| **Footprint** | **28.75 m²** | **25.49 m²** | **−11%, FAIL** | |

Adjacency 3/4: missed hall–passage, nothing invented. Walls 0/14 within 2 cm, worst 262.1 cm,
2 unpaired. Before 14 Sep this walk's hall came out at 6.04 m² (−59%): an unwalked face that no wall
faces was dropped, and that took out the middle of the hall. Faces with floor evidence that join walked
floor across a boundary with no wall behind it are now kept.

## The bedroom on its own

`5621ec5c54`, 13 Sep: the bedroom walked alone with Stray Scanner, 128 s, with the ceiling lap
(17.7% of frames look more than 20° up). The tape is in whole feet, so each taped side carries
about ±15 cm.

| | Tape | Bedroom scan | Long walk | First walk |
|---|---|---|---|---|
| Area | 9.29 m² (100 sq ft) | 8.90 m² (96 sq ft), −4% | 5.64 m² (61 sq ft), −39% | 7.58 m² (82 sq ft), −18% |
| Size | 10 × 10 ft | 12.4 × 8.7 ft | 10.0 × 6.5 ft | 9.5 × 8.6 ft |
| Ceiling | not taped | 2.625 m | 2.625 m | 2.617 m |

The scan's area is within the tape's precision; its shape is not, 12.4 ft long against 10 ft. The plan
also holds a 1.98 m² strip of passage where the walk began and ended (4.68 m² until fix loop round 3
cut it off where its walls end), so its footprint row reads 10.88 m² against the bedroom's 9.29 m²
(+17%). Against the long walk's bedroom 1 of 6 walls agrees
within 1 cm (worst 123.7 cm) and the ceilings are under 0.05 cm apart: FAIL.

## Repeatability

The two walks are the same flat at the same tier.

| Room | Long walk | First walk | Ceiling apart |
|---|---|---|---|
| Hall | 4.93 × 2.92 m | 4.90 × 2.88 m | 0.4 cm |
| Bedroom | 3.06 × 1.97 m, plus a 0.90 m strip | 2.91 × 2.61 m | 0.8 cm |
| Bathroom | 2.58 × 1.99 m, with part of the passage | 2.57 × 0.86 m | 13.1 cm |
| Passage | 2.58 × 1.21 m | 2.22 × 0.95 m | first walk unmeasured |

Gate: 0/27 walls agree, worst 267.3 cm, ceiling spread 13.1 cm, FAIL. The brief asks which failure
this is. The hall now comes out the same size on both walks to within 4 cm and its ceiling repeats to
4 mm, but the gate pairs walls one by one and the two walks draw the hall with 4 and 7 wall segments,
so no pair lands within 1 cm. The ceiling fails in the bathroom, which the two walks segment
differently: the long walk's bathroom room also holds part of the passage and reads 2.563 m, the first
walk's 2.432 m. The bedroom differs by 0.15–0.64 m between walks, so its shortfall is a reconstruction
defect and not a tape error.

## Interval coverage

LiDAR intervals cover the tape on 0 of 16 measurements on the long walk (mean half-width 12.4 cm),
2 of 16 on the first walk (12.3 cm) and 1 of 5 on the bedroom scan (13.9 cm). The intervals carry
sensor noise, residual drift and plane roughness. They do not carry segmentation error, and
segmentation error — a merged room, a split bedroom — runs to tens of centimetres. No quantiles were
fitted to close the gap: with three walks of one flat, the rows used to fit would be the rows scored.
The photo tier covers 7 of 11 on the 0.5× set and 5 of 5 on the 1× hall at mean half-widths of
7.26 m, which is coverage by being uninformative.

## Photo tier

58 stills in four folders from an iPhone 17 Pro, all on its 2.22 mm ultra-wide (0.5×): 54 at the
14 mm equivalent, 4 digitally cropped.

| Room | Tape | Photo | Note |
|---|---|---|---|
| Hall | 14.86 m² | rejected at 71.8 m² | above the 60 m² plausibility bound |
| Bedroom | 9.29 m² | 49.40 m² | no floor in frame; property scale 0.613 borrowed |
| Passage | 2.55 m² | 28.34 m² | scale 0.647 from 2 of 8 photos |
| Bathroom | 2.04 m² | 19.45 m² | no floor in frame; property scale borrowed |
| **Footprint** | **28.75 m²** | **97.19 m², +238%, FAIL** | gate ±8% |

It read 92.00 m² before 14 Sep; that day's segmentation changes grew the bedroom from 44.20 to
49.40 m². It fails its gate either way.

Adjacency 2/4, and both edges found come from folder names, not detection; bathroom–hall and
hall–passage are missed. Walls 0/10 within 8%, worst 444.2 cm. Against LiDAR depth of the same flat
the depth model over-predicts on 0.5× frames by 1.57×; the camera height implied by the detected
floor gives 1.76×.

### The hall again, on the 1× lens

`photos_1x`, 13 Sep: 12 stills of the hall on the 24 mm main camera. The pipeline keeps 8 and
registers 7.

| | Tape | 0.5× set | 1× set |
|---|---|---|---|
| Area | 14.86 m² (160 sq ft) | rejected at 71.8 m² | 35.12 m² (378 sq ft), +136% |
| Ceiling | not taped (LiDAR 2.60 m) | — | 2.74 m |

The 1× lens roughly halves the area the hall reconstructs to and brings it inside the plausibility
bound, but it still fails. The ceiling is within 6% of LiDAR while the floor is 2.4 times too large,
which points at the room's extent rather than its scale. Scale came from 4 of 8 photographs that
showed enough floor. Interval coverage passes 5/5 only because the intervals are about 7 m wide.

## Video tier

Metric scale is not solved. The whole-flat walkthrough produces one room of about 371 m², and the
assignment zip's own `rgb.mp4` without its poses gives 339.61 m² for a walk LiDAR puts at 20.91 m².
Do not choose this tier at a walk-in.

## The assignment's three samples

The zips that came with the brief, run unchanged; unzipped afresh and rerun after fix loop round 3,
they give the same plans room for room. They are a different property with no tape, so every
accuracy gate on them is SKIP. Drift accountability and room overlap pass on all three.

| Zip | Capture | Walk | Rooms | Area | Ceilings | Openings | Loop closures kept |
|---|---|---|---|---|---|---|---|
| `single_room.zip` | `c00a170fe1` | 37 s, no upward frames | 4 | 20.91 m² (225 sq ft) | unmeasured | 1 | 0 of 16 |
| `single_scan_floor_only.zip` | `1a8384c3f6` | 115 s, no upward frames | 8 | 38.91 m² (419 sq ft) | unmeasured | 3 | 1 of 19 |
| `single_scan_with_ceiling.zip` | `c7d28f72c6` | 215 s, 16.6% of frames look up | 7 | 41.58 m² (448 sq ft) | 2.27–3.08 m in all 7 rooms | 5 | 21 of 41 |

`single_room.zip` covers a living room (10.77 m²), its bathroom (3.93 m²) and the lobby between them
(1.71 m²), named here from its video frames, and stands at the mouth of a corridor the plan draws at
4.51 m². It was published as one 17.87 m² room before 14 Sep: its living room and bathroom had been
merged, and all 16 loop closures that passed ICP were slides, asking for 56–88 cm after under 7 m of
walking, which moved keyframes by up to 58 cm. Until fix loop round 3 it read 26.90 m²: the corridor
ran on across the passage beyond it and into a bathroom, 8.82 m², and the lobby held 1.67 m² of a
walled space none of the three scans saw into.

The two whole-flat scans cover the same space. Their areas are 7% apart and they differ by one room.
Aligned on their walls, 63% of the with-ceiling scan's wall points lie within 5 cm of the floor-only
scan's walls and 81% within 10 cm, and their room footprints overlap at an intersection-over-union of
0.61. Round 3 took the stairwell out of both stair halls, 3.39 and 4.57 m², and 0.75 and 1.05 m² of
that was floor the scan had seen (`known_failure_modes.md` §21). The floor-only scan's living room is
still cut short at 5.35 m² by diagonal wall segments from its curtains. Before 14 Sep they read
35.74 m² over 7 rooms and 31.57 m² over 6, rooms had been moved by up to 1.73 m to close the gaps
between them, and each reported a damage region that was the edge of furniture in front of a wall.
Rooms now stay where they were measured, and each plan lists the connections it draws more than
0.30 m apart. No damage is reported on any of the three, and none is visible in the sampled video
frames.

## Head-to-head

No consumer-app export exists, so the Part 3 head-to-head is not done.

## Regenerate

```bash
./scripts/regenerate_verified.sh
```

It runs every capture into `reports/verified/` and scores them into `reports/verified/gates/`. The
raw captures are not in git; `COZMO_RAW` and `COZMO_DROP` point at them. The benchmark command inside
it exits non-zero because gates fail, and the script treats that as the result it is.
