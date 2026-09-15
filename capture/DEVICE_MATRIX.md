# Device matrix

Which tier runs on which hardware, and what each tier honestly delivers.

## Tier availability by device

| Device | Photo | Video | LiDAR | Notes |
|---|---|---|---|---|
| iPhone 15 / 16 / 17 (non-Pro) | Yes | Yes | No | No depth sensor. Photo and video tiers are the whole capability. |
| iPhone 15 / 16 / 17 Pro, Pro Max | Yes | Yes | Yes | LiDAR at 256x192, ARKit poses and per-frame intrinsics. |
| iPad Pro (2020 onward) | Yes | Yes | Yes | Same sensor family. Not tested by us; treated as untested rather than supported. |
| iPhone 14 and older | Out of contract | Out of contract | Out of contract | Brief specifies iPhone 15 or newer. |

## Accuracy each tier delivers

Measured on one iPhone 17 Pro against the operator's tape, which is recorded in whole feet and
covers walls, floor areas and adjacency but no ceilings or doors. Wall length and floor area are
the median and 90th-percentile absolute error from `scripts/accuracy_table.py`, relative error in
brackets; the other rows are gate measurements from `reports/verified/gates/gate_table.txt`. Both
were regenerated on 15 Sep, after fix loop round 3.

| Quantity | Photo | Video | LiDAR | Gate |
|---|---|---|---|---|
| Wall length | median 2.20 m (81%), p90 3.82 m (144%), n = 12 | not taped | median 0.37 m (14%), p90 1.15 m (38%), n = 28 | photo 8%, video 3%, LiDAR 2 cm |
| Room floor area | median 23.02 m² (642%), p90 35.81 m² (962%), n = 4 | not taped | median 0.97 m² (11%), p90 2.10 m² (41%), n = 9 | no separate gate |
| Ceiling height | not taped | not taped | not taped | 1.5 cm per room |
| Ceiling height spread across repeats | no repeat capture | no repeat capture | 0.0 cm for the bedroom walked alone against the long walk; 13.1 cm across the two flat walks | 1 cm |
| Opening width | not taped | not taped | not taped | 2 cm on 85% of openings |
| Repeatability per wall | no repeat capture | no repeat capture | 1/6 walls, worst 123.7 cm (bedroom); 0/27, worst 267.3 cm (flat walks) | 1 cm or 0.5% |
| Whole-property footprint | +238% (58 stills at 0.5×); the hall alone at 1× +136% | not taped | +1% (protocol walk), −11% (walk without the ceiling lap) | photo 8%, LiDAR 5% |

Every cell comes from one of those two outputs. `not taped` means the ground truth that would score
it does not exist, and `no repeat capture` means that tier was captured once.

The only exact truth for ceilings and openings is the ray-traced room (`reports/verified/synthetic_room`,
scored against `capture/ground_truth_synthetic.csv`): walls within 0.6 cm, ceiling within 0.1 cm, the
door 1.0 cm narrow and the window exact. It is noiseless and has no furniture, so it shows the LiDAR
measurement is unbiased, not what a phone delivers in a real room. It is kept out of the table above.

## What limits each tier

**LiDAR.** Depth is 256x192 over the full field of view, so a wall at 3 m is sampled every
2.3 cm. Range is honest to about 5 m and degrades past that; ARKit's own confidence channel
is used to weight rather than to threshold. Absolute scale comes from the sensor and needs
no recovery. What the tape exposes is segmentation, not sensor noise: a bedroom split in two, a
bathroom that holds part of the passage.

**Video.** No depth and no poses. Both are estimated, so scale is the binding constraint,
not geometry. On video inputs made from the assignment's walks the single-room footprint reads +68.6%
against its LiDAR plan after fix loop round 5, and the floor-only walk −11.7% after round 4: VGGT-1B reconstructs runs of keyframes and MoGe-2
puts each run in metres, and consecutive runs still disagree in scale by up to 1.7×.

**Photo.** No depth, no poses, and no continuity between frames. The table above is the home
flat before fix loop round 4, whose photographs have not been rebuilt since: the hall shot on the
1× lens came out 136% too large, and the whole flat on 0.5× 238% too large. Since round 4, VGGT-1B
reconstructs a room's stills together and MoGe-2 sets the scale, within −8% to +2% of LiDAR depth
on the assignment's walks. On photo inputs made from those walks the whole-property footprint reads
+4.3%, +8.8% and +120.1% against their LiDAR plans: the room outline, taken from the floor the
stills saw, is now the limit.
