# Fix loop: before → after

Declaration (written first, committed first): [FIX_DECLARATION.md](FIX_DECLARATION.md)

This fix loop shipped in two rounds against two different tiers. Round 1 targeted the
photo tier's worst gate (footprint +422%). Round 2 targeted the LiDAR tier's footprint
against tape (−12%). Both are real, both are shown below with their own before/after.

| Round | Target | Declared gate | Command |
|---|---|---|---|
| 1 (photo EXIF) | Photo footprint +422% | `d15c21b` → `80c44f3` | See §1 below |
| 2 (ceiling evidence) | LiDAR footprint −12% | `88af4e3` → `20cb44a` | See §2 below |

All before/after plans are committed in `fixloop/before/`, `fixloop/after/`,
`fixloop/round2/before/`, `fixloop/round2/after/`.

---

## Round 1 — the photo-tier EXIF fix

**Declared gate:** Photo-tier footprint, 142.03 m² against a 27.20 m² LiDAR reference
(+422%). Declaration committed at `d15c21b`, fix at `80c44f3`.

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

**Four changes shipped:**

#### 1. EXIF sub-IFD read (`src/cozmo/recon/monocular.py`)

```diff
 def intrinsics_from_exif(img_path: Path) -> tuple[float, str]:
-    exif = Image.open(img_path).getexif()
-    f35 = exif.get(0xa405)  # FocalLengthIn35mmFilm
+    pil_img = Image.open(img_path)
+    exif = pil_img.getexif()
+    # iPhone puts FocalLengthIn35mmFilm in the sub-IFD, not IFD0.
+    sub_ifd = exif.get_ifd(0x8769)
+    f35 = sub_ifd.get(0xa405) or exif.get(0xa405)
     if f35 and f35 > 0:
-        return sensor_width_mm * img_w / (f35 * crop_factor), "exif_ifd0"
+        return sensor_width_mm * img_w / (f35 * crop_factor), "exif_sub_ifd_35mm_equivalent"
```

#### 2. Scale-correction band widened (`src/cozmo/recon/monocular.py`)

```diff
-SCALE_BAND = (0.75, 1.35)
+SCALE_BAND = (0.25, 4.0)
```

The old band was set while intrinsics were wrong and was rejecting every correct
correction: the prior was asking for 0.57 and being refused.

#### 3. Room-level scale consensus (`src/cozmo/pipeline/photo.py`)

```diff
+# Scale is a camera property, not per-frame. Median of the frames that see floor.
+if floor_scales:
+    consensus_scale = float(np.median(floor_scales))
+    for frame in room_frames:
+        frame.apply_scale(consensus_scale)
```

The camera-height prior fires on only 3 photographs in 20. Scale belongs to the camera
rather than to one photograph.

#### 4. Plausibility guard (`src/cozmo/pipeline/photo.py`)

```diff
+PLAUSIBLE_AREA_M2 = (1.0, 60.0)
+PLAUSIBLE_CEILING_M = (1.8, 4.2)
+
+if not (PLAUSIBLE_AREA_M2[0] <= room_area <= PLAUSIBLE_AREA_M2[1]):
+    log.warning("Room %s: area %.1f m² outside plausible range, dropping", room_id, room_area)
+    continue
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
walk, 16.57 m² (−42%) on the first walk. Declaration committed at `88af4e3`, fix at
`20cb44a`.

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

#### Occupancy: ceiling evidence channel (`src/cozmo/geometry/occupancy.py`)

```diff
+def rasterise_ceiling_evidence(
+    cloud: FusedCloud,
+    grid: OccupancyGrid,
+    ceiling_height_m: float,
+    height_band_m: float = 0.3,
+) -> np.ndarray:
+    """Rasterise downward-facing returns near ceiling height as interior evidence."""
+    mask = (cloud.normals[:, 1] < -0.7) & (
+        np.abs(cloud.points[:, 1] - ceiling_height_m) < height_band_m
+    )
+    return grid.rasterise(cloud.points[mask], channel="ceiling")
```

#### Cell complex: use ceiling evidence (`src/cozmo/geometry/cellcomplex.py`)

```diff
 def label_faces(self, evidence: OccupancyMaps) -> None:
     for face in self.faces:
-        interior = evidence.floor[face.mask].sum() > 0
-        interior |= evidence.freespace[face.mask].sum() > 0
-        interior |= evidence.camera_track[face.mask].sum() > 0
+        interior = evidence.floor[face.mask].sum() > 0
+        interior |= evidence.freespace[face.mask].sum() > 0
+        interior |= evidence.camera_track[face.mask].sum() > 0
+        interior |= evidence.ceiling[face.mask].sum() > 0
         face.label = "interior" if interior else "exterior"
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
