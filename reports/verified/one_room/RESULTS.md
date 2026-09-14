# One-room run results

13 Sep 2026, regenerated at `2937d35`. The inputs, the reason for each and the three commands are in
`SELECTION.md`.

| Tier | Input | Rooms | Area, 90% interval | Openings | Ceiling | Runtime | Verdict |
|---|---|---|---|---|---|---|---|
| LiDAR | `c00a170fe1` | 1 | **17.87 m²** [16.80, 18.94] | 1 window, 0.76 m [0.72, 0.80] | unmeasured | 31 s | usable outline |
| Video | the same walk's `rgb.mp4`, no poses or depth | 2 | **339.61 m²** [135.84, 543.38] | 0 | 4.00 / 4.41 m, on a wrong scale | 58 s | scale failed, about 19 times LiDAR |
| Photo | bathroom, 7 stills | 0 | **0.00 m²** | 0 | n/a | 15 s | room rejected as `no_room` |

LiDAR and video are the same physical room. Photo is a different room, the home bathroom: there are
no stills of `c00a170fe1`.

The LiDAR outline was 17.36 m² until `5091f2a`, which merges a short step the cell complex leaves in
the middle of a straight wall back into the wall.

Video warnings, not hidden: iPhone main-camera intrinsics assumed; consensus scale 1.945 from 27 of
120 keyframes that resolved a floor plane (range 0.775–3.43); registration broke at keyframe 92; 5
keyframes failed to register and were skipped.

Photo warning: `bathroom: reconstruction rejected as physically implausible (no_room)`.

There is no tape for this room, so its accuracy gates report SKIP.
