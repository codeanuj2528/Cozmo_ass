# Fix loop, round 2: declaration

Committed **before** the fix. The next commit is the fix; `git log` shows the order.

Round 1 (`fixloop/FIX_DECLARATION.md`) was the photo tier and its prediction was wrong.
This round is the LiDAR tier, and unlike round 1 it is scored against the operator's tape.

## 1. Worst gate, with the failing number

**LiDAR footprint, against tape.** Tape: hall 16×10 ft, passage 2.5×11 ft, bedroom 10×10 ft,
bathroom 22 sq ft — **28.75 m²** (`capture/ground_truth.csv`, `tool=tape`).

| capture | reported | error vs tape | gate ±5% |
|---|---|---|---|
| `163f18d3ac` long walk | 25.27 m² | −12% | **FAIL** |
| `ae3edc814d` home walk | 16.57 m² | −42% | **FAIL** |

Per room on the long walk: hall 13.18 vs 14.86, passage 2.63 vs 2.55, bathroom 1.99 vs 2.04,
**bedroom 5.28 vs 9.29 (−43%)**. Every room is short. None is long. A systematic error.

## 2. Root cause, and the evidence

**Hypothesis.** A face of the cell complex is labelled interior only on direct floor evidence
— floor returns, carved free space, or camera track (`geometry/cellcomplex.py`, the
`evidence_mask`). The strip between a piece of furniture standing against a wall and the wall
itself has none of those: the floor is under the wardrobe or the bed, and nobody walks there.
So that strip is labelled exterior and the room is bounded by the furniture front instead of
the wall. **The ceiling above the strip is not hidden by anything**, and it is not used.

**Evidence** (`163f18d3ac`, ceiling returns rasterised above each reconstructed room):

| room | tape | ceiling observed above it | plan reports |
|---|---|---|---|
| bedroom | 9.29 m² | **9.10 m²** | 5.28 m² |
| hall | 14.86 m² | **14.80 m²** | 13.18 m² |

The bedroom's floor plan is a clean rectangle (fill 0.99) of 2.58 × 2.08 m against a taped
3.05 × 3.05 m. Three of its walls have a second, parallel wall plane 0.27 m, 0.27 m and 0.50 m
further out, reaching 2.50 m — the real walls, present in the line arrangement, never used as
the boundary. The operator's bedroom photographs show a full-wall wardrobe and a padded
headboard, both large vertical planes standing in front of the walls.

## 3. The fix, and the predicted numbers

**Fix.** Rasterise downward-facing returns at ceiling height as their own evidence channel in
`geometry/occupancy.py`, and count them as interior evidence when labelling faces in
`geometry/cellcomplex.py`. Room separation is unchanged: rooms are still split where the
boundary between faces has built wall behind it, so ceiling continuity through a doorway
cannot merge two rooms.

**Predictions, stated before running:**

| quantity | before | predicted after |
|---|---|---|
| Long walk, bedroom | 5.28 m² | **8.0–9.5 m²** (the outer planes imply about 2.85 × 2.85) |
| Long walk, hall | 13.18 m² | 13.8–14.8 m² |
| Long walk, footprint | 25.27 m² (−12%) | **27.3–30.2 m², inside ±5% — gate moves to PASS** |
| Home walk, footprint | 16.57 m² (−42%) | improves but **stays FAIL** |
| Wall lengths, ≤2 cm on ≥85% | FAIL | **stays FAIL** |

The home walk is predicted not to pass, and that is part of the hypothesis rather than a
hedge: on that capture only 1.4% of frames were aimed at the ceiling, against 24.3% on the
long walk. If ceiling evidence is the mechanism, a capture without ceiling returns cannot
benefit from it. If the home walk passes anyway, the mechanism is not what this says.

Wall lengths stay FAIL because 2 cm is tighter than the outer planes' own placement; moving a
boundary from a wardrobe front to a wall recovers tens of centimetres, not the last two.

Unmapped `room_04` (2.19 m², ceiling 1.86 m — a soffit or loft, not in the tape) is reported
separately in the after table so it cannot quietly carry the footprint across the line.

## 4. Regeneration

```bash
git checkout <this commit>
cozmo run -i ../data/raw/163f18d3ac -o fixloop/round2/before/163f18d3ac
cozmo run -i ../DROP_CAPTURES_HERE/01_multiroom_lidar/ae3edc814d -o fixloop/round2/before/ae3edc814d

git checkout <the fix commit>
cozmo run -i ../data/raw/163f18d3ac -o fixloop/round2/after/163f18d3ac
cozmo run -i ../DROP_CAPTURES_HERE/01_multiroom_lidar/ae3edc814d -o fixloop/round2/after/ae3edc814d

cozmo benchmark --runs fixloop/round2/after --ground-truth capture/ground_truth.csv \
    --room-map capture/room_map.json --out fixloop/round2/gates
```

`fixloop/round2/before/` is committed with this declaration, produced by commit `5091f2a`.
