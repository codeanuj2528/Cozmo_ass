# Fix loop

Five rounds. In each, the declaration was committed before the fix.

| | Round 1 | Round 2 | Round 3 | Round 4 | Round 5 |
|---|---|---|---|---|---|
| Tier | photo | LiDAR | LiDAR | photo (video reported alongside) | video |
| Gate | whole-property footprint | whole-property footprint | whole-property footprint, long walk | whole-property footprint, ±8%, on photo inputs made from the assignment's three walks | footprint, ±5%, on video inputs made from the assignment's walks |
| Reference | the LiDAR reconstruction, 27.20 m²; tape did not exist yet | the operator's tape, 28.75 m² | the operator's tape, 28.75 m² | the LiDAR plan of each walk | the LiDAR plan of each walk |
| Before | 142.03 m² | 25.27 m² long walk, 16.57 m² first walk | 31.26 m² (+8.7%) | +148.4%, +52.5%, +84.8% | +196.3% (`single_room`), −11.7% (`single_scan_floor_only`) |
| Declared root cause | focal length read from the wrong EXIF IFD | furniture bounding rooms short of their walls | a room running past where its space ends: a stairwell, a corridor seen from its doorway, a walled space nobody saw into, a room never walked | each still built on its own: a scale per still, and registration by overlap the stills barely have | the scale of the similarities joining runs of keyframes compounds along the walk |
| Predicted | under +50%, and not a pass | bedroom 8.0–9.5 m², footprint inside ±5% | 28.90–29.40 m², a pass, with every taped room unchanged; the effect had already been seen on saved intermediates | −20% to +10% on all three; a FAIL on two and a pass on `single_room` only by chance | `single_room` within ±40%, `single_scan_floor_only` within ±20%, both still failing |
| After | 17.37 m² | identical to before | 29.13 m² (+1.3%) | +4.3%, +8.8%, +120.1% | +68.6% on `single_room`; the other walks not rerun |
| Gate | FAIL | FAIL | PASS, because an untaped room left the plan | PASS on `single_room`, by room errors that cancel; FAIL on the other two | FAIL on `single_room` |
| Root cause right? | real, but not the dominant cause | no | yes, by the declaration's own tests; the fix also removes 0.26–1.05 m² of seen floor per corrected room | real and fixed (scale within −8% to +2%, no overlapping rooms), but not the dominant cause: the room outline is, by the declaration's own test, and the core draws rooms too large even from LiDAR depth at the still frames | in part: the error fell by two thirds, but consecutive runs still disagree in scale by up to 1.7× once each is metric, the declaration's own test for a second cause |
| Declaration, fix | `aa7be32`, `db4cfa9` | `0a8c579`, `11cebf3` | `949d24b`, `02be79e` | `5c55395`, `e52ca4d` | `1f134bb`, `21c9c8d` |
| Files | `FIX_DECLARATION.md`, `POSTMORTEM.md`, `before/`, `after/` | `round2/`: declaration, post-mortem, runs, manifests, gate tables | `round3/`: declaration, evidence, runs, manifests, gate tables, post-mortem | `round4/`: declaration, evidence, runs, manifests, tier tables, post-mortem | `round5/`: declaration, evidence, runs, manifests, tier table, post-mortem |

Against today's tape, round 1's photo-tier result reads 142.03 to 17.37 m² against 28.75 m²,
+394% to −40%.

Rounds 1 and 2 moved no gate to PASS. Diagnosing round 2's non-result found that the room map
scoring every per-room number had been assigned by area and was wrong on both captures; the fix is
`ecf5d76`. Round 3 moved one: the long walk's footprint, by removing a 2.12 m² room that no keyframe
stands in and the tape does not name. No taped room changed in that round.

`run_c00a170fe1/`, `run_1a8384c3f6/` and `run_c7d28f72c6/` belong to no round. They hold
`cozmo fixloop` output on the assignment's three captures, and that command is the wall-snapping
ablation: `before_run.json` is the plan with snapping off and `after_run.json` with it on, both
from the code of 14 Sep. They are not the before and after of a fix.
