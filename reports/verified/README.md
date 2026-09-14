# Verified runs

Regenerate with the commands at the end of `benchmark_report.md`. Room names per capture are in
`capture/room_map.json`, taken from camera frames rather than from area.

| Folder | Capture | Tier | Rooms | Footprint | Ceilings, by room id | Openings | Against tape |
|---|---|---|---|---|---|---|---|
| `multiroom_long` | `163f18d3ac`, home, protocol followed | LiDAR | 6 | 29.13 m² | 2.599 / 2.625 / 2.563 / unmeasured / 2.668 / 2.626 m | 7 | 28.75 m², +1% |
| `multiroom_home` | `ae3edc814d`, home, ceiling lap skipped | LiDAR | 4 | 25.49 m² | 2.603 / 2.617 / 2.432 / unmeasured m | 6 | 28.75 m², −11% |
| `single_room` | `c00a170fe1`, assignment zip | LiDAR | 4 | 20.91 m² | unmeasured | 1 | not taped |
| `bedroom_solo` | `5621ec5c54`, the bedroom alone | LiDAR | 2 | 10.88 m² | 2.625 m / unmeasured | 1 | bedroom 8.90 m² against 9.29 m², −4% |
| `photos_1x` | the hall, 12 stills at 1× | photo | 1 | 35.12 m² | 2.743 m | 1 | 14.86 m², +136% |
| `single_scan_floor_only` | `1a8384c3f6`, assignment zip | LiDAR | 8 | 38.86 m² | all unmeasured | 3 | not taped |
| `single_scan_with_ceiling` | `c7d28f72c6`, assignment zip | LiDAR | 7 | 41.24 m² | 3.084 / 2.402 / 3.065 / 3.071 / 2.343 / 2.443 / 2.274 m | 6 | not taped |
| `multiroom_photos` | home, 58 stills | photo | 3 | 97.19 m² | — | 1 | 28.75 m², +238% |
| `one_room/` | one capture at three tiers | all | see `one_room/RESULTS.md` | | | | |
| `gates/` | the scored gate table for the folders above | | | | | | |

No LiDAR run reports damage. The three assignment plans are identical, room for room, to plans
built by unzipping `single_room.zip`, `single_scan_floor_only.zip` and
`single_scan_with_ceiling.zip` afresh and running `cozmo run` on each.

The long walk's fourth room is a window bay whose only upward surface is a ledge 0.52 m above the
floor. It reported a 1.860 m ceiling, and then 3.04 m once surfaces under 2.20 m were excluded,
both read where a fitted plane crosses the world origin. Read over its own floor it has no ceiling
to measure, and its plan says so. Its sixth room is a 0.9 m wide strip of the bedroom split off
`room_02`; it is not in the room map, so the bedroom is scored on `room_02` alone. A seventh room,
2.12 m² and never walked into, was removed by the room refinement of fix loop round 3
(`fixloop/round3/`).
