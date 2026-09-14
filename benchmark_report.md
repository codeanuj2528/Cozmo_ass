# Benchmark report

13 Sep 2026. Every number is regenerable; the commands are at the end. Ground truth is the
operator's tape (`capture/ground_truth.csv`, `tool=tape`), recorded in whole or half feet. It
covers wall lengths, floor areas and adjacency for the home flat. It does not cover ceilings,
doors or bathroom walls, so those gates report SKIP.

## Gates

| status | count |
|---|---|
| PASS | 13 |
| FAIL | 19 |
| SKIP | 34 |

Full table: `reports/verified/gates/gate_table.txt`.

PASS: drift accountability on the six LiDAR runs; room overlap on the two home walks, the bedroom
scan, the assignment's two flat scans and the 0.5× photo set; interval coverage on the 1× hall photos, whose intervals are metres
wide. FAIL: walls, footprint and interval coverage on the two home walks, the bedroom scan and the
0.5× photo set; adjacency on the two home walks and the 0.5× photo set; walls and footprint on the
1× hall photos; both repeatability pairs. SKIP: ceiling height and opening widths on every capture
of the flat (no tape), every other gate on the assignment's three zips (a different property, no tape), adjacency on
the bedroom scan and the 1× hall photos, room overlap on the 1× hall photos, and drift on both photo
sets (not applicable).

## Which reconstructed room is which

Named from RGB frames taken by the camera standing deepest inside each room
(`capture/room_identity/`), never from area, because area is one of the scored quantities. An
earlier map named rooms by matching areas and was wrong on both captures: it scored the long
walk's bathroom as the passage and the home walk's bedroom as the hall. Every per-room figure
published before 13 Sep is superseded.

## LiDAR against tape

Long walk `163f18d3ac`, which followed the protocol: ceiling lap done, loop closed.

| Room | Tape | LiDAR | Error | LiDAR, feet | Tape, feet |
|---|---|---|---|---|---|
| Hall | 14.86 m² | 13.18 m² | −11% | sides 9.6, 13.5, 9.8, 15.1 | 16 × 10; one LiDAR wall meets its neighbours at 78° |
| Bedroom | 9.29 m² | 5.28 m² | −43% | 8.5 × 6.8 | 10 × 10 |
| Bathroom | 2.04 m² | 2.63 m² | +29% | — | area only; this room also holds the upper passage |
| Passage | 2.55 m² | 1.99 m² | −22% | 8.4 × 2.6 | 11 × 2.5, width matched, length short |
| Window bay | not taped | 2.19 m² | — | 8.1 × 3.8 | — |
| **Footprint** | **28.75 m²** | **25.27 m²** | **−12%, FAIL** | | the taped rooms alone sum to 23.08 m², −20% |

LiDAR feet are each room's extent along its longest wall, except the hall: the plan draws it out of
square, so it is given side by side, and one long side is 2.5 ft short of the tape.

Adjacency 3/4. Found: passage–bedroom, hall–bathroom, passage–bathroom. Missed: hall–passage,
because the upper passage is merged into the bathroom. One edge beyond the tape, hall to the
window bay, which does open off the hall. Walls 0/17 within 2 cm.

Home first walk `ae3edc814d`: 1.4% of frames aimed at the ceiling, and the hall barely covered.

| Room | Tape | LiDAR | Error |
|---|---|---|---|
| Bedroom | 9.29 m² | 7.03 m² | −24% |
| Hall | 14.86 m² | 6.04 m² | −59% |
| Passage | 2.55 m² | 3.50 m² | +37%, merged with the bathroom |
| **Footprint** | **28.75 m²** | **16.57 m²** | **−42%, FAIL** |

Adjacency 2/4. Walls 0/24.

## The bedroom on its own

`5621ec5c54`, 13 Sep: the bedroom walked alone with Stray Scanner, 128 s, with the ceiling lap
(17.7% of frames look more than 20° up). Rooms are named from camera frames
(`capture/room_identity/5621ec5c54.jpg`). The tape is in whole feet, so each taped side carries
about ±15 cm.

| | Tape | Bedroom scan | Long walk | First walk |
|---|---|---|---|---|
| Area | 9.29 m² (100 sq ft) | 7.81 m² (84 sq ft), −16% | 5.28 m² (57 sq ft), −43% | 7.03 m² (76 sq ft), −24% |
| Size | 10 × 10 ft | 12.4 × 7.2 ft | 8.5 × 6.8 ft | 9.5 × 8.8 ft |
| Ceiling | not taped | 2.606 m | 2.635 m | 2.632 m |

The scan is outside the tape's precision on both sides, one long and one short. The plan also holds a
3.70 m² strip of passage where the walk began and ended, so its footprint row reads 11.51 m² against
the bedroom's 9.29 m² (+24%). Against the long walk's bedroom 0/5 walls agree (worst 120.7 cm) and the
ceilings are 2.9 cm apart: FAIL.

## Repeatability

The two walks are the same flat at the same tier.

| Room | Long walk | First walk | Walls apart | Ceiling apart |
|---|---|---|---|---|
| Bedroom | 2.58 × 2.08 m | 2.89 × 2.68 m | 0.31 / 0.61 m | 0.4 cm |
| Hall | 4.72 × 3.88 m, out of square | 2.88 × 2.59 m | 1.84 / 1.29 m | 0.8 cm |
| Passage | 2.55 × 0.80 m | 2.73 × 2.30 m | segmented differently | 27.5 cm |

Gate: 0/25 walls agree, ceiling spread 27.5 cm, FAIL. The brief asks which failure this is.
Ceiling height repeats to 4 mm in the bedroom and 8 mm in the hall, where both walks segment the
room the same way, and fails in the passage, where they do not: the first walk's passage room
also holds the bathroom and reports 2.41 m, the height of the strongest overhead surface in the
long walk's bathroom, while the long walk reads 2.68 m over the passage itself. Walls are
unrepeatable rather than repeatable-but-biased: the same bedroom differs by 0.3–0.6 m between
walks, so the long walk's short bedroom is a reconstruction defect and not a tape error.

## Interval coverage

LiDAR intervals cover the tape on 0 of 16 measurements on the long walk (mean half-width 11.9 cm),
0 of 15 on the first walk (9.6 cm) and 0 of 5 on the bedroom scan (12.4 cm). The intervals carry sensor noise, residual drift and plane
roughness. They do not carry segmentation error, and segmentation error — a merged room, a short
bedroom — runs to tens of centimetres. No quantiles were fitted to close the gap: with three walks
of one flat, the rows used to fit would be the rows scored. The photo tier covers 7 of 11 at a
mean half-width of 6.9 m, which is coverage by being uninformative.

## Photo tier

58 stills in four folders from an iPhone 17 Pro, all on its 2.22 mm ultra-wide (0.5×): 54 at the
14 mm equivalent, 4 digitally cropped.

| Room | Tape | Photo | Note |
|---|---|---|---|
| Hall | 14.86 m² | rejected at 71.8 m² | above the 60 m² plausibility bound |
| Bedroom | 9.29 m² | 44.20 m² | no floor in frame; scale borrowed |
| Passage | 2.55 m² | 28.34 m² | scale from 2 of 8 photos |
| Bathroom | 2.04 m² | 19.45 m² | no floor in frame; scale borrowed |
| **Footprint** | **28.75 m²** | **92.00 m², +220%, FAIL** | gate ±8% |

It was 60.87 m² over two rooms before `2937d35`. Every photographed room passes through the LiDAR
geometry, and the level changes in that commit other than the 2.20 m bound re-cut it: restoring
the old 1.6 m bound alone still gives 92.00 m². It fails its gate either way.

Adjacency 2/4, and both edges come from folder names, not detection. Walls 0/10 within 8%.
Against LiDAR depth of the same flat the depth model over-predicts on 0.5× frames by 1.57×; the
camera height implied by the detected floor gives 1.76×.

### The hall again, on the 1× lens

`photos_1x`, 13 Sep: 12 stills of the hall on the 24 mm main camera. The pipeline keeps 8 and
registers 7.

| | Tape | 0.5× set | 1× set |
|---|---|---|---|
| Area | 14.86 m² (160 sq ft) | rejected at 71.8 m² | 35.12 m² (378 sq ft), +136% |
| Size | 16 × 10 ft | — | 32.1 × 19.5 ft extent, two corners at 61° |
| Ceiling | not taped (LiDAR 2.60 m) | — | 2.74 m |

The 1× lens roughly halves the area the hall reconstructs to and brings it inside the plausibility
bound, but it still fails. The ceiling is within 6% of LiDAR while the floor is 2.4 times too large,
which points at the room's extent rather than its scale. Scale came from 4 of 8 photographs that
showed enough floor. Interval coverage passes 5/5 only because the intervals are about 7 m wide.

## Video tier

Metric scale is not solved. The whole-flat walkthrough produces one room of about 371 m², and
the assignment zip's own `rgb.mp4` without its poses gives 339.61 m² for a room LiDAR puts at
17.87 m². Do not choose this tier at a walk-in.

## The assignment's three samples

The zips that came with the brief, run unchanged. They are a different property with no tape, so
every accuracy gate on them is SKIP. Drift accountability passes on all three, and room overlap on
the two scans with more than one room.

| Zip | Capture | Walk | Rooms | Area | Ceilings |
|---|---|---|---|---|---|
| `single_room.zip` | `c00a170fe1` | 37 s, no upward frames | 1 | 17.87 m² (192 sq ft) | unmeasured |
| `single_scan_floor_only.zip` | `1a8384c3f6` | 115 s, no upward frames | 7 | 35.74 m² (385 sq ft) | unmeasured |
| `single_scan_with_ceiling.zip` | `c7d28f72c6` | 215 s, 16.6% of frames look up | 6 | 31.57 m² (340 sq ft) | 2.845–2.980 m in 4 of 6 rooms |

The two flat scans cover the same space and disagree by 13% on area and by one room; without tape
there is no telling which is closer. Both plans close the gaps between rooms by translating them,
by up to 1.73 m on the floor-only scan and 1.34 m on the other, and each reports one damage region
nobody has checked: a 0.99 m crack and a 0.03 m² water stain.

`single_room.zip` published 17.36 m² until `5091f2a`, which merges a short step the cell complex
leaves in the middle of a straight wall back into the wall.

## Head-to-head

No consumer-app export exists, so the Part 3 head-to-head is not done.

## Regenerate

```bash
.venv/bin/python -m cozmo.cli run -i ../data/raw/163f18d3ac -o reports/verified/multiroom_long
.venv/bin/python -m cozmo.cli run -i ../DROP_CAPTURES_HERE/01_multiroom_lidar/ae3edc814d -o reports/verified/multiroom_home
.venv/bin/python -m cozmo.cli run -i ../data/raw/c00a170fe1 -o reports/verified/single_room
.venv/bin/python -m cozmo.cli run -i ../DROP_CAPTURES_HERE/03_multiroom_photos -o reports/verified/multiroom_photos
.venv/bin/python -m cozmo.cli run -i ../DROP_CAPTURES_HERE/07_repeat_room_lidar/5621ec5c54 -o reports/verified/bedroom_solo
.venv/bin/python -m cozmo.cli run -i ../DROP_CAPTURES_HERE/03b_multiroom_photos_1x -o reports/verified/photos_1x
.venv/bin/python -m cozmo.cli run -i ../data/raw/1a8384c3f6 -o reports/verified/single_scan_floor_only
.venv/bin/python -m cozmo.cli run -i ../data/raw/c7d28f72c6 -o reports/verified/single_scan_with_ceiling
.venv/bin/python -m cozmo.cli benchmark --runs reports/verified \
    --ground-truth capture/ground_truth.csv --room-map capture/room_map.json \
    --repeat multiroom_home,multiroom_long --repeat bedroom_solo,multiroom_long \
    --out reports/benchmark
```

The benchmark command exits non-zero because gates fail. That is the expected result.
