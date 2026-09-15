# Fix loop round 5: post-mortem

Declaration `1f134bb`, fix `21c9c8d`. The after runs are at `21c9c8d` (`after/`, each `run_manifest.json` says
so). `COZMO_TIERS=video scripts/run_tier_benchmark.sh fixloop/round5/after` regenerates them; this round ran
`single_room`; its `single_scan_floor_only` run was stopped before it finished, to submit.

## 1. The declared gate

The video-tier footprint within ±5%, against the LiDAR plan of the same walk.

| Walk | Before (after round 4) | After | Walls registered on LiDAR | Seconds |
|---|---|---|---|---|
| `single_room` | +196.3% | **+68.6%** | no: 27% of wall cells within 10 cm, from 17% | 289 |
| `single_scan_floor_only` | −11.7% | not measured: stopped before it finished | | |
| `single_scan_with_ceiling` | not measured | not run | | |

`single_room`, the largest error of any gate at any tier, is a third of what it was; its interval now covers the
LiDAR footprint, where it did not. The gate still fails.

## 2. The predictions, one by one

| # | Prediction | Outcome |
|---|---|---|
| 1 | Consecutive metric runs agree in scale within 0.85–1.15 | **Wrong.** `single_room` 0.872–1.705 over 6 links; not measured on the other walk |
| 2 | `single_room` within ±40%; `single_scan_floor_only` within ±20% | **Wrong** on `single_room`: +68.6%, from +196.3%. Not measured on `single_scan_floor_only` |
| 3 | The ±5% gate still fails on both | **Right** on `single_room`; not measured on the other walk |
| 4 | At least one plan lines up with its LiDAR walls, half the wall cells within 10 cm | **Wrong** on `single_room`: 27% of wall cells within 10 cm; not measured on the other walk |
| 5 | `single_scan_floor_only` on LiDAR depth and ARKit poses, as a video, writes a plan instead of raising | **Right.** 48.86 m², −3.9% (`evidence/video_oracle_after.txt`) |

## 3. Was the root cause right?

In part. The scale was compounding along the chain: with each run put in metres on its own, `single_room` went
from +196.3% to +68.6%. VGGT-1B's units differ between its calls on that walk by a factor of 1.65 (1.745–2.885
metres per unit across the seven runs), and the similarity links had been carrying every link's error in that
factor into the runs after it.

It is not the whole cause, by the declaration's own second test. Once every run is metric, the scale a similarity
would still apply between consecutive runs lies between 0.872 and 1.705 on `single_room`: two runs, each scaled
by MoGe-2 on its own two or three keyframes, still disagree by up to 70% about the size of the frames they share.
Either VGGT-1B's depth of those frames differs that much between two calls, which one scale per run cannot fix, or
a median of two or three MoGe-2 ratios is too few to set a run's scale, which more metric keyframes would.

## 4. What to do next

Scale every keyframe, not every third: MoGe-2 on each keyframe makes a run's scale the median of eight ratios,
and a keyframe two runs share gets the same metric depth in both. If consecutive runs still disagree by more than
15% after that, the depth itself differs between calls, and the walk should be joined on longer overlaps, four or
five shared keyframes, or on larger runs on a machine with more memory than 16 GB.

The crash is fixed. The core publishes a length a hair below zero as zero, and writes the floor-only walk's plan it
had refused.
