# Fix loop, round 3: what happened

Declaration: `FIX_DECLARATION.md`, committed first (`949d24b`). The fix is `02be79e`, and `after/`
and `gates/after/` were produced by it. `--no-refine-rooms` (`20d41e0`) turns the fix off for the
ablation.

## The gate moved, for the reason the declaration gave

| capture | gate | before | after |
|---|---|---|---|
| long walk `163f18d3ac` | footprint, ±5% | 31.26 m², +8.7%, FAIL | **29.13 m², +1.3%, PASS** |
| long walk | adjacency | 2/6, 4 phantom, FAIL | 2/5, 3 phantom, FAIL |
| bedroom walk `5621ec5c54` | footprint, ±5% | 13.58 m², +46.1%, FAIL | 10.88 m², +17.2%, FAIL |
| home walk `ae3edc814d` | every gate | — | unchanged |

Over these six captures the gate table goes from 12 PASS, 11 FAIL, 25 SKIP to 13 PASS, 10 FAIL,
25 SKIP.

The long walk passes because `room_07`, 2.12 m², which no keyframe stands in and the room map does
not name, left the plan. Every taped room is identical before and after: hall 13.61, bedroom 5.64,
bathroom 3.02 and passage 2.27 m². No taped quantity became more accurate. It is a pass on the
footprint as the gate defines it, and nothing more.

## Prediction by prediction

| quantity | predicted | actual | |
|---|---|---|---|
| long walk footprint | 28.90–29.40 m², PASS | 29.13 m², PASS | inside |
| long walk rooms | 6, the other six within 0.05 m² | 6, the other six identical | inside |
| long walk adjacency | 2/5, 3 phantom, FAIL | 2/5, 3 phantom, FAIL | inside |
| home walk | every room within 0.05 m², FAIL | identical, FAIL | inside |
| bedroom walk | 10.60–11.20 m², bedroom within 0.05 m², FAIL | 10.88 m², bedroom 8.90 m² identical, FAIL | inside |
| `interval_coverage`, `wall_lengths` | stay FAIL | unchanged on every capture | inside |
| `c00a170fe1` | 20.60–21.20 m²; corridor 4.30–4.70; room_04 1.50–1.90 | 20.91; 4.51; 1.71 | inside |
| `1a8384c3f6` | 38.60–39.10 m²; stair hall 7.80–8.20 | 38.86; 8.01 | inside |
| `c7d28f72c6` | 41.00–41.50 m²; stair hall 8.10–8.50; bathroom 5.30–5.60 | 41.24; 8.32; 5.46 | inside |

Every number landed inside its range. That is less than it looks: §0 of the declaration records
that the corrections had been run on saved intermediates and their numbers read before the ranges
were written. What the full runs show is that the pipeline reproduces that experiment to the
centimetre, not that the effect was foreseen.

The declaration named three ways to be shown wrong. None happened:

* The corridor's walled side ends 2.71 m from its mouth, and the corrected room ends 2.77 m from
  it, 6 cm further, inside the 0.2 m allowed.
* Every below-floor return deeper than 0.30 m inside the two stair halls lies in the removed part:
  5,446 of 5,446 on the floor-only scan and 5,708 of 5,708 on the with-ceiling scan.
* No taped room moved.

## What the prediction did not cover

**Floor that was seen is removed too.** The removed parts, classified on a 5 cm raster of the scan:

| room | removed | floor seen | below the floor | raised surface | nothing seen |
|---|---|---|---|---|---|
| `1a8384c3f6` stair hall | 3.39 m² | 0.75 m² | 1.32 m² | 0.40 m² | 1.46 m² |
| `c7d28f72c6` stair hall | 4.57 m² | 1.05 m² | 1.72 m² | 0.65 m² | 1.60 m² |
| `c00a170fe1` room_04, walled space | 1.67 m² | 0.49 m² | — | 0.07 m² | 1.29 m² |
| `c7d28f72c6` bathroom, walled space | 1.51 m² | 0.26 m² | — | 0.21 m² | 1.40 m² |

Drawn over the scans, the seen floor goes three ways. The rectangle around an irregular drop covers
landing along its edges. A strip narrower than 30 cm left between a removed rectangle and a wall is
opened away, which is what happens beside the walled space. A piece of landing left separate from
the rest of the room by the rectangle is dropped, because a room is one polygon.

Two other hole shapes were tried afterwards on the saved intermediates. Neither is in the fix.

| hole | floor-only hall: seen floor removed | its below-floor area removed | with-ceiling hall: seen floor removed | its below-floor area removed | outline |
|---|---|---|---|---|---|
| rectangle, as shipped | 0.75 m² | 1.32 m² | 1.05 m² | 1.72 m² | 8 and 10 vertices |
| rectangle shrunk off seen floor | 0.30 m² | 1.01 m² | 0.76 m² | 1.57 m² | 10 and 10 |
| seen floor cut out of the rectangle | 0.30 m² | 0.80 m² | 0.17 m² | 1.11 m² | 32 and 45 |

Each gives back landing only by leaving part of the well in the hall, and the second draws a ragged
outline. The rectangle stays, and the cost is written into `geometry/refine.py` and
`known_failure_modes.md` §21.

**The bedroom walk's passage strip now carries a 0.90 m door.** Before the fix that plan had no
opening at all. Drawn over the scan it sits in the bedroom doorway
(`evidence/after_bedroom_walk_plan_on_scan.png`), where the walk entered.

**Room ids moved on both whole-flat samples.** Ids follow area, and the stair halls are now the
second-largest rooms: `room_01` became `room_02` on both, and the 9.56 and 9.11 m² rooms became
`room_01`.

**The photo and video tiers go through the same builder,** and the declaration made no prediction
for them. Regenerated at `3dc8471`, the rules misfired on monocular geometry:

| run | before | with the fix on | what fired |
|---|---|---|---|
| 1× hall photos | 35.12 m² | 2.11 m² | open end, 33.01 m² cut |
| 0.5× photos | 97.19 m², 3 rooms | 49.86 m², 4 rooms, **a room overlap** | bedroom: open end 99.88 m², "stairwells" 6.27 m²; hall: open end 33.65 m², "stairwell" 16.74 m² |
| walkthrough video | 339.61 m² | 123.18 m² | one room: open end 194.61 m²; "stairwells" 21.64 m² |

Two of those moved towards the tape, for reasons that have nothing to do with it, and the 0.5× set
gained a room overlap, which the brief makes an automatic failure. A monocular depth map has partial
walls and no trustworthy returns below the floor, so it cannot show that floor is absent. `8b53ef4`
runs the step on the LiDAR tier only, with a test that the same capture refines as LiDAR and does
not as photo. Regenerated at `8b53ef4`, both photo plans are the same as before the round, room for
room: 97.19 m² with the hall still rejected at 71.8 m², and the 1× hall at 35.12 m².

## What stays wrong, as declared

* The single-room scan's corridor is 4.51 m² against the 2.20 m² the with-ceiling scan walked. Its
  unwalled side was never measured from the mouth.
* The floor-only scan's living room is 5.35 m² against 10.77 m² from the single-room scan, cut by
  diagonal wall segments from curtains.
* Both stair halls keep the flight going up and the unseen band beside the well.

## Scoring this round honestly

Root cause: right for the cases it named, by the three tests the declaration set. Fix: shipped.
Gate: one FAIL to PASS, and it passed because an untaped room left the plan, not because a taped
room got closer to the tape. Prediction: every number inside its range, from ranges written after
the effect had been seen on saved data. Cost: between 0.26 and 1.05 m² of seen floor removed from
each room it corrected.
