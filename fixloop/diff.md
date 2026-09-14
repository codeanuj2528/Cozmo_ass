# Fix loop: before → after

Declaration (written first, committed first): [FIX_DECLARATION.md](FIX_DECLARATION.md)

This fix loop shipped in three rounds. Round 1 targeted the photo tier's worst gate (footprint
+422%). Round 2 targeted the LiDAR tier's footprint against tape (−12%). Rounds 1 and 2 are shown
below with their own before/after. Round 3 targeted the LiDAR footprint on the long walk (+8.7%);
its code change is `git diff 949d24b..02be79e`, and its declaration, runs and post-mortem are in
`round3/`.

| Round | Target | Declared gate | Command |
|---|---|---|---|
| 1 (photo EXIF) | Photo footprint +422% | `aa7be32` → `db4cfa9` | See §1 below |
| 2 (ceiling evidence) | LiDAR footprint −12% | `0a8c579` → `11cebf3` | See §2 below |
| 3 (room refinement) | LiDAR footprint +8.7%, long walk | `949d24b` → `02be79e` | `round3/FIX_DECLARATION.md` §4 |

All before/after plans are committed in `fixloop/before/`, `fixloop/after/`,
`fixloop/round2/before/`, `fixloop/round2/after/`, `fixloop/round3/before/` and
`fixloop/round3/after/`.

---

## Round 1 — the photo-tier EXIF fix

**Declared gate:** Photo-tier footprint, 142.03 m² against a 27.20 m² LiDAR reference
(+422%). Declaration committed at `aa7be32`, fix at `db4cfa9`.

### Root cause

`intrinsics_from_exif` in `src/cozmo/recon/monocular.py` read only `Image.getexif()`
(IFD0), which holds no focal length on an iPhone JPEG. The value lives in the EXIF
sub-IFD at tag `0x8769`. All 58 photographs carry `FocalLengthIn35mmFilm = 14`;
none was read; every frame ran at an assumed 26 mm prior.

```
current code → fx = 4125.3   source = assumed_iphone_main_camera
correct      → fx = 2221.3   source = exif_sub_ifd_35mm_equivalent
ratio        → 1.86× too long
```

### Code diff

**Four changes shipped**, all in `db4cfa9` (`git show db4cfa9`). The excerpts below are cut
from that commit's diff; `...` marks omitted lines.

#### 1. EXIF sub-IFD read (`src/cozmo/recon/monocular.py`)

```diff
+EXIF_SUB_IFD = 0x8769
 ...
+def _focal_from_exif(path: Optional[Path]) -> tuple[float | None, str]:
 ...
+        with Image.open(path) as image:
+            base = image.getexif()
+            if not base:
+                return None, "no_exif"
+            for ifd, label in ((base.get_ifd(EXIF_SUB_IFD), "exif_sub_ifd"), (base, "exif_ifd0")):
+                if not ifd:
+                    continue
+                tags = {TAGS.get(k, k): v for k, v in ifd.items()}
+                value = tags.get("FocalLengthIn35mmFilm")
+                if value:
+                    return float(value), f"{label}_35mm_equivalent"
 ...
 def intrinsics_from_exif(path: Optional[Path], width: int, height: int) -> tuple[np.ndarray, str]:
 ...
-            with Image.open(path) as image:
-                exif = image.getexif()
-                if exif:
-                    tags = {TAGS.get(k, k): v for k, v in exif.items()}
-                    value = tags.get("FocalLengthIn35mmFilm")
-                    if value:
-                        equivalent_mm = float(value)
 ...
-    source = "exif_35mm_equivalent"
+    equivalent_mm, source = _focal_from_exif(path)
```

#### 2. Scale-correction band widened (`src/cozmo/recon/monocular.py`)

```diff
-SCALE_CORRECTION_MIN = 0.75
-SCALE_CORRECTION_MAX = 1.35
+SCALE_CORRECTION_MIN = 0.25
+SCALE_CORRECTION_MAX = 4.0
+PLAUSIBLE_CAMERA_HEIGHT_M = (0.6, 2.4)
 ...
-    if backbone_is_metric and not (SCALE_CORRECTION_MIN <= factor <= SCALE_CORRECTION_MAX):
+    if not (SCALE_CORRECTION_MIN <= factor <= SCALE_CORRECTION_MAX):
```

The old band was set while intrinsics were wrong and was rejecting every correct
correction: the prior was asking for 0.57 and being refused.

#### 3. Room-level scale consensus (`src/cozmo/pipeline/photo.py`)

```diff
+    confident = [
+        e["geometry"].scale.factor
+        for e in pending
+        if e["geometry"].floor_found and e["geometry"].scale.source == "camera_height_correction"
+    ]
+    room_scale = float(np.median(confident)) if confident else None
 ...
+        if room_scale is not None and geometry.scale.source != "camera_height_correction":
+            # Re-scale an image that could not recover its own scale onto the room's.
+            depth = depth * (room_scale / max(geometry.scale.factor, 1e-6))
```

The camera-height prior fires on only 3 photographs in 20. Scale belongs to the camera
rather than to one photograph.

#### 4. Plausibility guard (`src/cozmo/pipeline/photo.py`)

```diff
+PLAUSIBLE_CEILING_M = (1.80, 4.20)
+PLAUSIBLE_ROOM_AREA_M2 = (1.0, 60.0)
+PLAUSIBLE_ROOM_SPAN_M = 14.0
 ...
+        reason = _implausible(largest)
+        if reason:
+            warnings.append(
+                f"{name}: reconstruction rejected as physically implausible ({reason}); "
+                f"reported as not reconstructed rather than published"
+            )
```

A 113 m² bedroom is not a wide estimate, it is a wrong one.

### Before / after

| Stage | Footprint | Rooms | Ceilings |
|---|---|---|---|
| Before (declaration commit) | 142.03 m² (+422%) | 3 | 4.46 / 4.07 / 1.92 m |
| After EXIF fix alone | 276.34 m² (+916%) | 3 | prediction was wrong |
| After all four changes | **17.37 m² (−36%)** | 1 | two rooms rejected |

**The prediction was wrong.** Too long a focal length had been partially cancelling a
larger depth-model error. Removing it exposed the full scale term. `X = (u - cx) Z / fx`;
halving `fx` doubles `X`; area rose 1.95×.

**±8% gate: still FAIL.** Coverage fell from three wrong rooms to one plausible one. That
is the correct trade: the brief says confident garbage on thin input caps the total score.

Full post-mortem: [POSTMORTEM.md](POSTMORTEM.md)

---

## Round 2 — LiDAR ceiling evidence

**Declared gate:** LiDAR footprint, 25.27 m² against a taped 28.75 m² (−12%) on the long
walk, 16.57 m² (−42%) on the first walk. Declaration committed at `0a8c579`, fix at
`11cebf3`.

### Root cause hypothesis

A face of the cell complex is labelled interior only on direct floor evidence — floor
returns, carved free space, or camera track. The strip between furniture and the wall
behind it has none: the floor is under the wardrobe, and nobody walks there. The ceiling
above that strip is not hidden by anything but is not used.

Evidence from `163f18d3ac`:

| Room | Tape | Ceiling observed above | Plan reports |
|---|---|---|---|
| Bedroom | 9.29 m² | **9.10 m²** | 5.28 m² |
| Hall | 14.86 m² | **14.80 m²** | 13.18 m² |

### Code diff

Shipped in `11cebf3` (`git show 11cebf3`); the excerpts are cut from its diff.

#### Occupancy: ceiling evidence channel (`src/cozmo/geometry/occupancy.py`)

```diff
+CEILING_EVIDENCE_MIN_HEIGHT_M = 1.95
+CEILING_EVIDENCE_NORMAL = 0.90
 ...
+    ceiling_hits = np.zeros(grid.shape, dtype=bool)
+    ceiling_seen = (cloud.normals[:, 1] < -CEILING_EVIDENCE_NORMAL) & (
+        height > CEILING_EVIDENCE_MIN_HEIGHT_M
+    )
+    if ceiling_y is not None:
+        ceiling_seen &= height < (ceiling_y - floor_y) + 0.15
+    if ceiling_seen.any():
+        cells = grid.to_cell(points_xz[ceiling_seen])
+        keep = grid.inside(cells)
+        ceiling_hits[cells[keep, 0], cells[keep, 1]] = True
```

#### Cell complex: use ceiling evidence (`src/cozmo/geometry/cellcomplex.py`)

```diff
     evidence_mask = occ.floor_hits | (occ.free_mask & occ.observed)
+    if occ.ceiling_hits is not None:
+        evidence_mask = evidence_mask | (occ.ceiling_hits & ~(occ.wall_weight > 0))
```

### Before / after

**Result: every room polygon identical, on both captures.**

The "real walls" 0.23–0.27 m beyond the bedroom were the far faces of 230 mm brick
partitions, and the ceiling measurement had bled into neighbouring rooms through a
morphological closing. The hypothesis was refuted by measurement.

| Capture | Before | After | Moved? |
|---|---|---|---|
| Long walk, footprint | 25.27 m² (−12%) | 25.27 m² (−12%) | No |
| First walk, footprint | 16.57 m² (−42%) | 16.57 m² (−42%) | No |

**Gate: did not move.** The prediction was wrong, but diagnosing *why* it didn't move found
a worse defect: the room map had been assigned by matching areas and was wrong on both
captures (scoring the long walk's bathroom as the passage and the first walk's bedroom as
the hall). Rooms are now named from camera frames. Every per-room figure published before
13 Sep is superseded.

Full post-mortem: [round2/POSTMORTEM.md](round2/POSTMORTEM.md)

---

## Summary

| Round | Declared gate | Predicted outcome | Actual outcome | Gate moved? |
|---|---|---|---|---|
| 1 (photo EXIF) | +422% → under +50% | Under +50% | **−36%** (but only 1 room) | FAIL → FAIL |
| 2 (ceiling evidence) | −12% → inside ±5% | PASS | **Identical** | FAIL → FAIL |

Both predictions were wrong. Neither gate moved to PASS. The post-mortems explain why,
with measurements, and that honesty is the point of this section.
