# One-capture run results

15 Sep 2026, regenerated on the code of 14 Sep. The inputs, the reason for each and the three commands
are in `SELECTION.md`.

| Tier | Input | Rooms | Area, 90% interval | Openings | Ceiling | Runtime | Verdict |
|---|---|---|---|---|---|---|---|
| LiDAR | `c00a170fe1` | 4 | **26.90 m²** [25.28, 28.51] | 1 window, 0.50 m [0.46, 0.54] | unmeasured | 14 s | a living room, its bathroom and the lobby between them, plus 8.82 m² of a space the walk entered briefly |
| Video | the same walk's `rgb.mp4`, no poses or depth | 2 | **339.61 m²** [135.84, 543.38] | 0 | 4.00 / 4.41 m, on a wrong scale | 61 s | scale failed, about 13 times LiDAR |
| Photo | bathroom, 7 stills | 0 | **0.00 m²** | 0 | n/a | 14 s | room rejected as `no_room` |

LiDAR and video are the same walk. Photo is a different room, the home bathroom: there are no stills of
`c00a170fe1`.

The LiDAR plan was one 17.87 m² room until 14 Sep. Its living room and bathroom, scanned about 40°
off the world axes, had been merged by a strip-width test that measured an axis-aligned bounding box,
and 16 loop closures that were slides rather than revisits had moved its keyframes by up to 58 cm.
Both are fixed; `known_failure_modes.md` §5 and `docs/design.md` §3 have the details.

Video warnings, not hidden: iPhone main-camera intrinsics assumed; consensus scale 1.945 from 27 of
120 keyframes that resolved a floor plane (range 0.775–3.43); registration broke at keyframe 92; 5
keyframes failed to register and were skipped, and 116 of 120 registered.

Photo warning: `bathroom: reconstruction rejected as physically implausible (no_room)`.

There is no tape for this capture, so its accuracy gates report SKIP.
