# Fix loop round 4: post-mortem

Declaration `5c55395`, fix `e52ca4d`. The after runs are at `e52ca4d` (`after/`, each `run_manifest.json`
says so): `scripts/run_tier_benchmark.sh fixloop/round4/after`.

## 1. The declared gate

The photo-tier whole-property footprint within ±8%, against the LiDAR plan of the same walk.

| Export | Before | Predicted | After | Gate |
|---|---|---|---|---|
| `single_room` | +148.4% | −20% to +10%; a pass only by chance | **+4.3%** | PASS |
| `single_scan_floor_only` | +52.5% | −20% to +10%; FAIL | **+8.8%** | FAIL |
| `single_scan_with_ceiling` | +84.8% | −20% to +10%; FAIL | **+120.1%** | FAIL |

One export moved to a pass, one moved close, and one got worse. The pass is the chance pass the declaration
allowed for: `single_room`'s rooms miss by −4.7%, −40.1% and +98.2%, and the errors cancel.

## 2. The predictions, one by one

| # | Prediction | Outcome |
|---|---|---|
| 1 | Every footprint between −20% and +10%, negative in at least two | **Wrong.** +4.3%, +8.8%, +120.1%: all positive, one far outside |
| 2 | The gate fails on `single_scan_floor_only` and `single_scan_with_ceiling`; `single_room` passes only by chance | **Right**, and the pass is by cancellation |
| 3 | Every folder gives a room, 15 of 15 | **Wrong.** 14 of 15. `single_scan_floor_only` `room_01`, with 3 stills posed wrong, came back at 65.2 m² and the plausibility guard rejected it |
| 4 | No room overlaps another by more than 2% of the smaller | **Right.** 0% in all three, from 0%, 3.0% and 12.4% |
| 5 | Walls within 8% rise to at least a third in each export | **Wrong.** 4/23, 4/31, 4/30, from 1/24, 1/41, 4/23 |
| 6 | The largest room errors are in rooms with a wrongly posed still; a clean room whose scale was within 5% comes within ±15% | **Wrong.** Of those four rooms, `single_scan_floor_only` `room_06` is −8.1%; `single_room` `room_03` is +98.2%, `single_scan_floor_only` `room_05` +152.6% and `single_scan_with_ceiling` `room_06` +88.6% |
| 7, 8 | Video | See §5 |
| — | Photo under 6 minutes per export | **Right.** 143, 260 and 303 s |

Per room, after (LiDAR area, plan area, error):

| Export | room_01 | room_02 | room_03 | room_04 | room_05 | room_06 |
|---|---|---|---|---|---|---|
| `single_room` | 9.12, 8.69, −4.7% | 7.02, 4.21, −40.1% | 4.20, 8.33, +98.2% | | | |
| `single_scan_floor_only` | 14.77, rejected | 10.55, 18.09, +71.4% | 9.02, 7.71, −14.6% | 7.36, 11.99, +62.8% | 5.68, 14.34, +152.6% | 3.44, 3.16, −8.1% |
| `single_scan_with_ceiling` | 12.36, 24.14, +95.2% | 10.16, 7.68, −24.4% | 8.33, 19.87, +138.6% | 5.19, 23.60, +354.7% | 4.85, 15.85, +227.0% | 3.64, 6.87, +88.6% |

## 3. Was the root cause right?

In part, and it was not the dominant cause. The declaration's own test for being wrong — a room with no wrongly
posed still, whose MoGe-2 scale was within 5%, missing by more than 15% — is met by three of the four rooms it
names.

What the fix did change, measured: room scales are within −7.8% to +2.1% of LiDAR depth, where each still's own
scale had given rooms from 0.574 to 1.296; no room overlaps another; gravity is within 0.8–4.9° of ARKit.

What it did not change is the outline. The reconstruction core was run on the stills of four clean rooms with
ideal geometry instead of the models' — LiDAR depth and ARKit poses of exactly those frames — and it makes the
same kind of room (`evidence/oracle_core.txt`):

| Room | LiDAR room | Core on LiDAR depth and ARKit poses of the still frames | Core on VGGT-1B's depth and poses |
|---|---|---|---|
| `single_scan_floor_only` `room_02` | 10.55 m² | 21.58 m², +105% | 21.09 m², +100% |
| `single_scan_floor_only` `room_05` | 5.68 m² | 17.39 m², +206% | 15.51 m², +173% |
| `single_room` `room_03` | 4.20 m² | 33.33 m², +694% | 5.86 m², +40% |
| `single_scan_with_ceiling` `room_02` | 10.16 m² | 11.84 m², +17% | 9.15 m², −10% |

So the models are no longer what makes a photo room wrong. Four to eight stills see floor through every doorway,
and the photo path takes a room to be the floor its stills saw, bounded by whatever wall lines the fused cloud
yields; nothing in a set of stills says where one room stops. Two things tried on the same inputs do not fix it:
the evidence layout the LiDAR tier uses (+17%, +105%, +128%, −17% on ideal geometry) and ignoring depth beyond 3 m
(two rooms of four smaller, still +46% and +114%; `evidence/depth_cap.txt`).

The stills are chosen by rule from a walk, which looks through doorways more than a photographer standing in a
room and aiming at its corners would. That makes these inputs harder than a protocol capture, and does not change
what the core does with them.

## 4. What to do next

Take a photo room's outline from its walls, not its floor: fit wall planes in each still, where one depth map sees
its own walls densely; merge the planes the stills share in the joint frame; close the room from those walls, so
floor seen through a doorway cannot extend it. The ideal-geometry runs above are that round's before, and they
separate the core from the models. The 17 stills VGGT-1B posed wrong stay open.

## 5. The video tier

Reported alongside the declared gate, with no prediction of a pass. Against the LiDAR plan of each walk:

| Walk | Before | After | Walls registered on LiDAR | Seconds |
|---|---|---|---|---|
| `single_room` | +194.6% | **+196.3%** | no: 17% of wall cells within 10 cm | 254 |
| `single_scan_floor_only` | +197.3% | **−11.7%** | no: 35% | 782 |
| `single_scan_with_ceiling` | +108.9% | not measured | — | — |

The `single_scan_with_ceiling` run was stopped twice, eight and twelve minutes in, when the session running it
ended, and was not repeated before submission.

| # | Prediction | Outcome |
|---|---|---|
| 7 | Footprint within ±30% on all three, the ±5% gate failing on all three | **Wrong** on `single_room` (+196.3%); right on `single_scan_floor_only` (−11.7%, a FAIL); not measured on the third |
| 8 | At least two plans line up with their LiDAR walls, half the wall cells within 10 cm | **Wrong.** Neither measured plan does |
| — | Video under 20 minutes | **Right** where measured: 254 and 782 s |

The walk that moved least moved least for a reason its plan records: the runs of keyframes are joined by
similarities, and the model's units across `single_room` span a factor of 1.146–2.811 over 6 links, against
0.903–1.948 over 22 on `single_scan_floor_only`. The core itself is not the problem at this tier: on LiDAR
depth and ARKit poses of one keyframe per second it gives −1.7% on `single_room` and −3.2% on
`single_scan_with_ceiling` (`fixloop/round5/evidence/video_oracle.txt`). Fix loop round 5 takes that up.
