# Fix loop round 5: declaration

Committed before the fix. The commit that adds this file contains no code of the fix; the commit that adds the
fix comes after it in `git log`.

What was measured before this was written: the video tier's runs after round 4 (`fixloop/round4/after/*_video`,
at `e52ca4d`), and the reconstruction core run on LiDAR depth and ARKit poses of one keyframe per second of each
walk, labelled a video capture (`evidence/video_oracle.txt`). The fixed video tier had not been run.

## 1. The worst gate, with its failing number

**The video-tier footprint within ±5%**, against the LiDAR plan of the same walk, on the video inputs made from
the assignment's walks (`scripts/make_tier_inputs.py`). At `e52ca4d`, after round 4:

| Walk | LiDAR plan | Video plan | Footprint | Walls |
|---|---|---|---|---|
| `single_room` | 23.47 m² | 69.55 m² | **+196.3%** | not registered: 17% of wall cells within 10 cm |
| `single_scan_floor_only` | 50.82 m² | 44.88 m² | **−11.7%** | not registered: 35% of wall cells within 10 cm |
| `single_scan_with_ceiling` | 49.88 m² | not measured: its run was stopped before it finished | | |

Before round 4 the first two read +194.6% and +197.3%. `single_room` did not move, and at +196.3% it is the largest
error of any gate at any tier.

## 2. Root cause, and the evidence for it

**What the video tier hands the core is wrong, not the core.** On LiDAR depth and ARKit poses of one keyframe per
second, the same core, told the frames are a video, gives `single_room` 23.07 m² (−1.7%) and
`single_scan_with_ceiling` 48.28 m² (−3.2%) (`evidence/video_oracle.txt`). On `single_scan_floor_only` it raised
instead of writing a plan: a prior-only interval was built around a value of −0.01 below its own lower bound of
zero, which the output contract refuses. At the photo tier round 4 found the opposite: there the core's outline was
the error. Here it is the geometry, and that crash.

**Hypothesis: the scale compounds along the chain of runs.** The walk is reconstructed in runs of eight
keyframes, each in VGGT-1B's own frame and units. Run k is joined to run k−1 by a similarity fitted to the depth
both give for the three keyframes they share, those similarities are multiplied back to the first run, and one
MoGe-2 scale is then applied to the whole chain. A link's scale is only as good as two runs' agreement on three
frames, and its error multiplies into every run after it. Each plan records the product: across the walk the model's
units span a factor of 1.146–2.811 over 6 links on `single_room` and 0.903–1.948 over 22 on
`single_scan_floor_only`. The walk with the widest span in the fewest links is the one that did not move in round 4.
Those numbers mix a real change of VGGT's units between calls, which the links are there to absorb, with link error,
which they cannot show apart; the predictions below say how the fix separates them.

## 3. The fix

- MoGe-2's depth, already computed on every third keyframe, scales each run on its own: the median, over the run's
  keyframes that have it, of MoGe-2 depth over VGGT depth.
- Runs are joined in metres by a rigid transform, rotation and translation fitted to the frames they share. The
  scale a similarity would have used between two metric runs is recorded as a check, not applied.
- A run with no usable metric keyframe is joined by a similarity, as before.
- A length computed a hair below zero, within its own interval of it, is published as zero rather than refused.
- Keyframes, runs of eight sharing three, gravity, and the core with drift correction do not change.

## 4. Predictions

1. On every walk run, the scale between consecutive metric runs, recorded and not applied, lies within 0.85–1.15.
2. `single_room` falls from +196.3% to within ±40%. `single_scan_floor_only` stays within ±20% (from −11.7%).
3. The ±5% gate still fails on both.
4. At least one of them lines up with its LiDAR walls, half the wall cells within 10 cm, where neither did.
5. `single_scan_floor_only` run on LiDAR depth and ARKit poses as a video writes a plan instead of raising.

## 5. What would show this wrong

- The runs agree once metric (prediction 1) and `single_room` stays beyond ±50%: the chain's rotations or
  translations are wrong, not its scale.
- Consecutive metric runs disagree by more than 15% in scale: VGGT's depth is not consistent within a run, and one
  scale per run cannot fix that.
