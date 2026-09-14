# Fix loop

Two rounds. In each, the declaration was committed before the fix.

| | Round 1 | Round 2 |
|---|---|---|
| Tier | photo | LiDAR |
| Gate | whole-property footprint | whole-property footprint |
| Reference | the LiDAR reconstruction, 27.20 m²; tape did not exist yet | the operator's tape, 28.75 m² |
| Before | 142.03 m² | 25.27 m² long walk, 16.57 m² first walk |
| Declared root cause | focal length read from the wrong EXIF IFD | furniture bounding rooms short of their walls |
| Predicted | under +50%, and not a pass | bedroom 8.0–9.5 m², footprint inside ±5% |
| After | 17.37 m² | identical to before |
| Gate | FAIL | FAIL |
| Root cause right? | real, but not the dominant cause | no |
| Declaration, fix | `aa7be32`, `db4cfa9` | `0a8c579`, `11cebf3` |
| Files | `FIX_DECLARATION.md`, `POSTMORTEM.md`, `before/`, `after/` | `round2/`: declaration, post-mortem, runs, manifests, gate tables |

Against today's tape, round 1's photo-tier result reads 142.03 to 17.37 m² against 28.75 m²,
+394% to −40%.

Neither round moved a gate to PASS. Diagnosing round 2's non-result found that the room map
scoring every per-room number had been assigned by area and was wrong on both captures; the fix is
`ecf5d76`.
