# Part 4: Fix declaration

Written and committed **before** the fix. The commit that follows this one is the fix; the
ordering is in `git log` and is the point.

## 1. The single worst-performing gate, with the failing number

**Photo-tier whole-property footprint.**

| | value |
|---|---|
| Gate | footprint within ±8% |
| Reference | LiDAR reconstruction of the same flat, 27.20 m² (laser ground truth not yet recorded) |
| Photo tier reports | **142.03 m²** |
| Error | **+422%** |
| Status | **FAIL**, by a factor of five |

Everything else at that tier fails with it: ceiling heights come out at 4.46 m, 4.07 m and
1.92 m against a LiDAR-measured 2.49–2.68 m; the bedroom is not recovered at all from eight
photographs; zero openings are detected, so the doorway-matching stitch has nothing to match
and reports zero adjacency.

I am reporting against the LiDAR reconstruction rather than the laser because the laser
measurements do not exist yet. That is a weaker reference and it is disclosed as one. It is
adequate here only because the error is 422%, which is far outside any plausible
disagreement between the LiDAR tier and a tape.

## 2. Root-cause hypothesis, and the evidence

**Hypothesis.** The photo tier never reads the focal length that is present in every one of
the 58 photographs, so it runs every frame at an assumed focal length 1.86× too long, and
the resulting back-projection is not a scaled version of the room but a distorted one.

**Evidence.** `intrinsics_from_exif` (`src/cozmo/recon/monocular.py:110`) calls
`Image.getexif()` and looks for `FocalLengthIn35mmFilm` in the result. On an iPhone JPEG
that call returns IFD0, which holds eleven tags and no focal length:

```
IFD0 tags: DateTime, ExifOffset, GPSInfo, HostComputer, Make, Model, Orientation,
           ResolutionUnit, Software, XResolution, YResolution
f35 from IFD0    : None
f35 from sub-IFD : 14
```

The focal length lives in the EXIF sub-IFD, tag `0x8769`, which is what `ExifOffset` above
points at. All 58 photographs carry it. The lookup therefore always fails and always falls
back to the assumed iPhone main-camera value of 26 mm, while these photographs were taken on
the 0.5x ultra-wide at a 14 mm equivalent:

```
current code -> fx = 4125.3   source = assumed_iphone_main_camera
correct      -> fx = 2221.3
ratio        -> 1.86x too long
```

**Why that produces 422% and not 1.86².** Back-projection computes `X = (u - cx) Z / fx`
with `Z` coming from the depth model and unaffected by `fx`. Too long a focal length
therefore compresses the cloud laterally while leaving depth alone. That is an anisotropic
distortion, not a similarity, so the floor of the room is no longer planar in the
back-projected cloud. `estimate_gravity_and_height` then fits a floor to a surface that is
not flat, recovers a wrong camera height, and the camera-height scale correction is computed
from that wrong height. The error compounds through gravity, scale and registration rather
than appearing as a single clean factor, which is also why the three rooms come out
inconsistent with each other (4.46 m, 4.07 m, 1.92 m) instead of uniformly wrong.

## 3. The fix, and the number I predict after it

**Fix.** Read the EXIF sub-IFD before falling back. Concretely, in `intrinsics_from_exif`:
resolve `FocalLengthIn35mmFilm` from `getexif().get_ifd(0x8769)`, fall back to IFD0, and only
then to the device prior. Record which of the three was used in the returned provenance
string so a reader can tell a measured focal length from an assumed one.

**Predicted number.** Footprint error against the LiDAR reference falls from **+422% to under
+50%**, and per-room ceiling heights come back inside 2.2–3.0 m.

**I do not predict the ±8% gate will pass, and I am saying so before running it.** The depth
model's own mean absolute relative error against LiDAR on this property is 0.28, with a
per-frame scale factor spanning 0.71 to 1.48. Correct intrinsics remove a systematic
distortion; they do nothing about that spread. A tier whose depth is 28% out cannot deliver
an 8% footprint without something further — most plausibly averaging over more photographs
per room, or a second scale cue. Predicting a pass here would be the optimistic answer rather
than the honest one, and the brief scores a badly wrong prediction in either direction.

**Secondary prediction:** openings will remain at or near zero. That failure has a different
cause, described in the known failure modes: opening detection looks for points behind a
wall plane, and a single photograph's depth map is a 2.5D surface with nothing behind it.
Correct intrinsics do not change that, and I expect the stitch to remain unsolved at the
photo tier.

## 4. Regeneration

The annotated tag `fixloop-before` points at the declaration commit `aa7be32`, an ancestor
of `HEAD`. `git diff fixloop-before..HEAD` is everything committed since then, not the fix; the
fix alone is `git diff aa7be32..db4cfa9`.

Commit ids in this section were corrected on 14 Sep 2026. The ids first written here, `d15c21b`
and `80c44f3`, belonged to a history that was later rewritten and are not reachable from any
branch or tag, so they do not exist in a fresh clone. `aa7be32` and `db4cfa9` carry the identical
patches.

Photos live **outside** this repo: `../DROP_CAPTURES_HERE/03_multiroom_photos/`.
Without that folder the before/after JSON already committed is the evidence.

```bash
# the fix alone
git diff aa7be32..db4cfa9

# before — needs the photo folders next to the repo
git checkout aa7be32
.venv/bin/python -m cozmo.cli run \
  --input ../DROP_CAPTURES_HERE/03_multiroom_photos \
  --out fixloop/before

# after
git checkout db4cfa9   # or main
.venv/bin/python -m cozmo.cli run \
  --input ../DROP_CAPTURES_HERE/03_multiroom_photos \
  --out fixloop/after

# accuracy gates SKIP until capture/ground_truth.csv has laser rows
.venv/bin/python -m cozmo.cli benchmark \
  --runs fixloop --ground-truth capture/ground_truth.csv --out fixloop/gates
```

`fixloop/before/` is committed alongside this declaration, produced by the code as it stands
at this commit. Compare footprints in the two `plan.json` files (142.03 m² → 17.37 m²),
not a laser gate. The ±8% gate still fails. Do not run `cozmo fixloop` for this story —
that CLI is the wall-snap ablation, not the EXIF fix.
