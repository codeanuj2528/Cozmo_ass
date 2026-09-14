# Fix loop, round 2: what happened

Declaration: `FIX_DECLARATION.md`, committed first (0a8c579). This is the account of running it.

## The fix moved nothing

| capture | before | after | room polygons |
|---|---|---|---|
| `163f18d3ac` long walk | 25.275 m², 5 rooms | 25.275 m², 5 rooms | **identical** |
| `ae3edc814d` home walk | 16.567 m², 3 rooms | 16.567 m², 3 rooms | **identical** |

Predicted: bedroom 5.28 → 8.0–9.5 m², footprint inside ±5%. Actual: no change to any room, to
the millimetre. The prediction was badly wrong. By the brief's rubric that earns nothing for the
prediction.

The code did run. The ceiling raster populated 37,367 cells (29,270 after the wall mask), and
it raised the evidence on faces that were already interior. It changed the label of none.

## Why the hypothesis was wrong

**The "real walls further out" were the other side of the same wall.** Beyond three bedroom
edges the declaration cited a parallel plane 0.27, 0.27 and 0.50 m out. Measured properly they
sit at 0.23–0.27 m, and the strongest of them carries the highest support weight in the entire
capture. 230 mm is a standard Indian nine-inch brick wall. Those planes are the far faces of the
bedroom's own partitions, seen from the rooms next door. The bedroom polygon was on the inner
face of its walls all along.

**The ceiling measurement that anchored the prediction was contaminated.** "9.10 m² of ceiling
observed above the bedroom against a tape of 9.29" came from a connected component of ceiling
returns after a morphological closing, and the closing bridged into the neighbouring rooms. The
faces that would have had to flip for the prediction to hold have ceiling evidence of 0.02–0.15
against a threshold of 0.22 — wall thickness and outdoors, correctly exterior. The agreement
with the tape was a coincidence I built a prediction on.

**Truncation was not the cause either.** The arrangement used 26 lines of a 44-line cap, with
27 runs available.

## What diagnosing it found, which matters more than the fix

**The room maps were wrong on both captures.** `capture/room_map.json` had been assigned by
matching reconstructed areas to taped areas — which the scorer's own docstring calls circular,
since area is one of the things being scored. Identified instead from RGB frames taken by the
camera standing deepest inside each room (`capture/room_identity/`):

| capture | room | committed map said | the camera shows | evidence |
|---|---|---|---|---|
| `163f18d3ac` | room_03 | passage | **bathroom** | shampoo niches, geyser, shower mixer |
| `163f18d3ac` | room_05 | bathroom | **passage** | corridor, "Nice Day" doormat, bedroom door |
| `ae3edc814d` | room_01 | hall | **bedroom** | bed, headboard, wardrobe |
| `ae3edc814d` | room_02 | bedroom | **hall** | mandir, wall clock, shoe rack, front door |

On the long walk the swap turned a bathroom at +29% and a passage at −22% into "+3%" and "−2%".
Every per-room figure previously in `benchmark_report.md` was scored against the wrong room.

**The tape is recorded to the foot.** Hall 16 × 10 ft, bedroom 10 × 10 ft, passage 11 × 2.5 ft.
The long-walk hall reconstructs at 15.8 × 9.6 ft — the tape to its own precision. A 2 cm
wall-length gate cannot be adjudicated by a tape rounded to the nearest 15 cm; that is reported
beside the gates rather than used to soften them.

**The long-walk bedroom is genuinely short.** The same bedroom, same tier, reconstructs at
2.89 × 2.68 m on the home walk and 2.58 × 2.08 m on the long walk. That is a repeatability failure
on its own terms, and it rules out the tape as the explanation: the sensor itself disagrees with
itself by 0.3–0.6 m. The defect is real and remains unfixed.

**Bathroom and passage merge.** On both captures one reconstructed room holds the bathroom and
part of the passage (fill ratios 0.50 and 0.56), so neither is measured on its own.

## What was shipped

Ceiling returns as interior evidence (`geometry/occupancy.py`, `geometry/cellcomplex.py`, with
`tests/test_ceiling_evidence.py`). It is principled — a strip whose floor is hidden and whose
ceiling is seen is floor — and it moved no output on either benchmark capture. It stays in, and
it is not claimed to have moved a gate.

The per-capture room map, built from camera evidence rather than area, ships in the next commit
together with the rescored benchmark.

## Scoring this section honestly

Root cause: wrong. Fix: shipped. Gate movement: none. Prediction: badly wrong. That is
post-mortem marks at most, and the round-1 photo-tier loop remains the one with movement on it.

## Correction, 13 Sep 2026

The hall sentence under "The tape is recorded to the foot" picked the longest side and one short
side. The plan draws the long-walk hall with sides of 9.6, 13.5, 9.8 and 15.1 ft plus a 0.7 ft jog,
one wall meeting its neighbours at 78°, and the `before/` and `after/` plans hold that same polygon.
It is 13.18 m² against the taped 14.86 m² (−11%), with one long side 2.5 ft short. Tape precision is
also not reported beside the gates: the benchmark scores each gate as written and nothing more.
