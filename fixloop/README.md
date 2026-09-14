# Fix loop

Three rounds. In each, the declaration was committed before the fix.

| | Round 1 | Round 2 | Round 3 |
|---|---|---|---|
| Tier | photo | LiDAR | LiDAR |
| Gate | whole-property footprint | whole-property footprint | whole-property footprint, long walk |
| Reference | the LiDAR reconstruction, 27.20 m²; tape did not exist yet | the operator's tape, 28.75 m² | the operator's tape, 28.75 m² |
| Before | 142.03 m² | 25.27 m² long walk, 16.57 m² first walk | 31.26 m² (+8.7%) |
| Declared root cause | focal length read from the wrong EXIF IFD | furniture bounding rooms short of their walls | a room running past where its space ends: a stairwell, a corridor seen from its doorway, a walled space nobody saw into, a room never walked |
| Predicted | under +50%, and not a pass | bedroom 8.0–9.5 m², footprint inside ±5% | 28.90–29.40 m², a pass, with every taped room unchanged; the effect had already been seen on saved intermediates |
| After | 17.37 m² | identical to before | 29.13 m² (+1.3%) |
| Gate | FAIL | FAIL | PASS, because an untaped room left the plan |
| Root cause right? | real, but not the dominant cause | no | yes, by the declaration's own tests; the fix also removes 0.26–1.05 m² of seen floor per corrected room |
| Declaration, fix | `aa7be32`, `db4cfa9` | `0a8c579`, `11cebf3` | `949d24b`, `02be79e` |
| Files | `FIX_DECLARATION.md`, `POSTMORTEM.md`, `before/`, `after/` | `round2/`: declaration, post-mortem, runs, manifests, gate tables | `round3/`: declaration, evidence, runs, manifests, gate tables, post-mortem |

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
