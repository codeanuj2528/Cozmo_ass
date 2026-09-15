# Verified runs

Regenerate with the commands at the end of `benchmark_report.md`. Room names per capture are in
`capture/room_map.json`, taken from camera frames rather than from area.

| Folder | Capture | Tier | Rooms | Footprint | Ceilings, by room id | Openings | Against tape |
|---|---|---|---|---|---|---|---|
| `multiroom_long` | `163f18d3ac`, home, protocol followed | LiDAR | 6 | 29.13 m² | 2.599 / 2.625 / 2.563 / unmeasured / 2.668 / 2.626 m | 7 | 28.75 m², +1% |
| `multiroom_home` | `ae3edc814d`, home, ceiling lap skipped | LiDAR | 4 | 25.49 m² | 2.603 / 2.617 / 2.432 / unmeasured m | 6 | 28.75 m², −11% |
| `single_room` | `c00a170fe1`, assignment zip | LiDAR | 5 | 23.47 m² | unmeasured | 5 doors, 1 window | not taped |
| `bedroom_solo` | `5621ec5c54`, the bedroom alone | LiDAR | 2 | 10.88 m² | 2.625 m / unmeasured | 1 | bedroom 8.90 m² against 9.29 m², −4% |
| `photos_1x` | the hall, 12 stills at 1× | photo | 1 | 35.12 m² | 2.743 m | 1 | 14.86 m², +136% |
| `single_scan_floor_only` | `1a8384c3f6`, assignment zip | LiDAR | 6 | 50.82 m² | all unmeasured | 9 doors, 4 windows, 1 pass-through | not taped |
| `single_scan_with_ceiling` | `c7d28f72c6`, assignment zip | LiDAR | 9 | 49.88 m² | 3.064 / 3.082 / 3.065 / 2.343 / 2.406 / 2.275 / 2.461 / 2.462 / 2.403 m | 8 doors, 4 windows, 1 pass-through | not taped |
| `multiroom_photos` | home, 58 stills | photo | 3 | 97.19 m² | — | 1 | 28.75 m², +238% |
| `synthetic_room` | ray-traced 3.60 × 2.80 m box, `tests/fixtures/raytrace_room.py` | LiDAR | 1 | 10.08 m² | 2.499 m | 2 | exact: 10.08 m², 2.50 m, door 0.85 m, window 1.10 m |
| `synthetic_no_ceiling` | the same box without its upward lap | LiDAR | 1 | 10.08 m² | unmeasured | 2 | exact, as above |
| `one_room/` | one capture at three tiers | all | see `one_room/RESULTS.md` | | | | |
| `gates/` | the scored gate table for the folders above | | | | | | |
| `repeatability/` | the with-ceiling scan against the floor-only scan, and against the single-room scan, registered on their walls (`cozmo repeat`) | LiDAR | | | | | 0 of 52 and 1 of 52 walls within 1 cm or 0.5% |

The three assignment scans and the two ray-traced rooms were rebuilt at `0fd7baa`, after the 15 Sep
changes to the canonical frame and to how LiDAR rooms are found. The home-flat and photo folders are
still the plans of `a92927c`; their raw captures were not on the machine where those changes were made
(`known_failure_modes.md` §23).

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
