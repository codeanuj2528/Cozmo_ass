# Design notes

Architecture and design rationale for the Cozmo pipeline. This document explains
*why* the code is structured the way it is, not *what* it does — the module
docstrings already serve that role and this document references them rather than
duplicating them.

## 1. Captures

A capture is a directory on disk. The tier is auto-detected by `cozmo/io/discover.py`
from what files the directory holds, never passed as a flag:

| Content | Detected tier | Loader |
|---|---|---|
| `confidence/`, `depth/`, `rgb/`, `camera_matrix.csv`, `odometry.csv` | LiDAR (Stray Scanner export) | `cozmo/io/stray.py` |
| `room_*/` subdirectories with `.jpg`, `.jpeg`, `.png` or `.heic` images | Photo (per-room folders) | `cozmo/io/photo.py` |
| A single video file (`.mp4`, `.mov`) | Video (walkthrough clip) | `cozmo/io/video.py` |

The tier is a fact about the capture, and a flag would let a photo capture be scored
against LiDAR gates by typo. The detection code handles iPhone file naming: `IMG_1582.MOV`
in capitals, case-insensitively, including `.HEIC` (the iPhone default that JPEG-only
readers miss).

Every capture gets a `capture_id` from its directory name and an `input_hash` computed
over the files it reads, so a reported number can always be tied to the bytes that
produced it.

## 2. Output contract

`cozmo/schema.py` defines the output contract. One rule runs through it: **every physical
quantity is a `Measure`, never a bare float.** A `Measure` is a point estimate plus an
interval (`lo`, `hi`), a `coverage` probability, and the `method` that produced the
interval.

The `method` field is load-bearing. It records whether the interval is `conformal_split`
(fitted from residuals against ground truth), `propagated_covariance` (the plane fit's own
uncertainty), `bootstrap`, or `prior_only` (a fallback with no data behind it). A previous
version labelled fixed-width priors as conformal; that made the one field a reader uses to
tell a calibrated interval from a guess into a falsehood.

The contract uses Pydantic `BaseModel` with `frozen=True` on `Measure` (immutable once
created) and `Field(ge=0.0, le=1.0)` on confidences to enforce constraints structurally.

Key models:
- `PropertyPlan` — root object, one per capture
- `Room` → `Wall` → `Opening`, `Surface` — the floor plan
- `DamageRegion` → `ConcealedFlag` → `ScopeItem` — the damage chain
- `DriftReport`, `CalibrationReport`, `QualityReport` — process evidence

## 3. LiDAR tier

The reference implementation. Every other tier is measured against this one.

### Pipeline stages

```
Stray frames → fuse (voxel + plane fitting) → gravity (floor plane)
  → walls (Hough over normals) → rotate to building frame
  → walls again (aligned grid) → cell complex → rooms → levels
  → openings → damage → concealed rules → scope → plan.json
```

Walls are extracted **twice**. The first pass finds the property's own axes; the cloud
is rotated onto them and walls are re-extracted. A raster aligned to the walls quantises
them along their own direction instead of across it. On the sample capture the property
sits 27° off the ARKit axes.

Levels are measured **twice** for the same reason at a different scale: once globally to
get a floor reference, then per room for per-room ceiling gates.

### Wall extraction (`cozmo/geometry/walls.py`)

Walls are found with a 2D Hough accumulator over (normal azimuth, signed offset), where
each point votes once into the bin its own measured normal selects. Sharper than a
classical line Hough, where every point smears a sinusoid across a cluttered accumulator.

Votes are weighted by the point's inverse variance, so a wall seen precisely from 2 m is
not outvoted by a noisy patch of returns at 5 m. Peaks are accepted on observed surface
area, not on a fraction of total vote weight.

Two wall representations are maintained deliberately:
- **Tight segments** (stop at every gap) → cell complex face labelling
- **Bridged runs** (span gaps narrower than a doorway) → opening detection

### Cell complex (`cozmo/geometry/cellcomplex.py`)

The floor plan is a cell complex, not a raster. Wall lines partition the floor; each face
is labelled interior/exterior from direct evidence (observed floor, carved free space,
camera track). A flood fill was tried first and leaked through a glass balcony door,
reporting 195 m² for a 44 m² flat. A face of an arrangement is bounded by lines on all
sides, so a labelling mistake cannot propagate.

### Drift correction (`cozmo/geometry/drift.py`)

ARKit's odometry is locally excellent and globally not: on the long walk, 107 m of
odometry over 312 s, the pose graph starts with a 0.709 m residual. Correction is a pose
graph over keyframes:

- **Odometry edges** at the reported relative pose
- **Loop-closure edges** at the pose ICP measured

Loop candidates are proposed by geometry and confirmed by ICP, never the reverse. A
candidate must be a genuine revisit — path walked at least 6× the distance closed, and at
least 6 m. Rotation and translation residuals are weighted by separate information terms,
and a soft-L1 loss keeps one false closure from folding the map.

### Levels (`cozmo/geometry/levels.py`)

Ceiling height is measured from downward-facing returns above a height threshold. A ceiling
must be strong (point support), cover ≥ 0.25 m², be above 2.20 m, and be observed by
multiple frames aimed upward. A window bay that reported 1.86 m ceiling (measured from its
ledge) now abstains. If no downward-facing returns exist, `ceiling_height` is `None`, not
a default — because an unchecked prior is exactly the "confident garbage" the brief
penalises.

## 4. Photo tier — 2–8 unposed stills, no depth, no poses

The photo tier's whole job is to manufacture the two things LiDAR is handed for free —
metric depth and a pose per frame — and then hand the result to the same reconstruction
core. Everything after that is literally the same code: same wall extraction, same cell
complex, same opening detection, same ceiling measurement.

Per room the sequence is:
1. **Predict depth** — Depth Anything V2 Metric Indoor, via `cozmo/recon/monocular.py`
2. **Level** — fit a floor plane, estimate camera height, compute gravity
3. **Scale** — camera-height scale correction from the floor distance
4. **Register** — yaw from wall-normal histograms, translation by occupancy
   cross-correlation, then ICP. That order matters: ICP has a small basin of convergence
5. **Fuse** — same as LiDAR tier from here on

### EXIF intrinsics

`intrinsics_from_exif` reads `FocalLengthIn35mmFilm` from the EXIF sub-IFD at `0x8769`,
not from IFD0 (which holds no focal length on an iPhone JPEG). All 58 benchmark
photographs carry it. The fix loop (Round 1) found and fixed this.

### Honest status

The photo tier does not meet its gates. Depth Anything V2 over-predicts depth on 0.5×
ultra-wide frames by 1.57–1.76×. This is a field-of-view mismatch, not a tuning problem.
The intervals reported are wide because that is what the measurement says.

## 5. Video tier — more views, same code

The video tier sits between the other two. Like the photo tier it has no depth and must
predict it. Unlike the photo tier it has continuity: consecutive frames overlap, so poses
chain, and the walk returns past places it has been.

Frame selection is critical. A phone swung through a doorway produces frames whose motion
blur destroys both depth prediction and registration. Frames are scored on sharpness
(Laplacian variance) before anything else, and blurred frames are dropped.

Frames are sampled at uniform stride first and blur-filtered second, so coverage is not
biased toward the rooms the operator moved slowly through.

The video tier does not currently produce a usable metric plan. This is disclosed as NOT
MET, not hidden.

## 6. Multi-room stitching

`cozmo/stitch/rooms.py` assembles per-room plans into a whole-property plan:

1. **Doorway matching** — if two rooms both see an opening, the opening connects them
2. **Folder-name fallback** — if no doorway matches (the photo tier), rooms are connected
   by folder naming convention (e.g., `room_01` and `room_02` are adjacent)
3. **Gap closing** — rooms connected by declared adjacency are translated to close their
   gap, by up to a configurable maximum

Room overlap is checked: if any room polygon overlaps another, it is an automatic failure
per the brief. The cell complex prevents this structurally in the LiDAR tier; the photo
tier checks it after stitching.

## 7. Damage, concealed flags and scope

### Detection (`cozmo/damage/detect.py`)

A classical detector for discolouration and cracks. No downloaded model, as the walk-in
test requires a cold run. Detections are only reported once they land on a surface: a
bounding box in an image is not a finding; a region of a named wall with an area in m² is.

Multi-view corroboration is required: a specular highlight is view-dependent and never
reprojects to the same patch of surface twice. On the author's marble-and-glass flat that
took damage from 21 false regions to 1.

### Rule engine (`cozmo/damage/rules.py`)

Concealed-damage flags are produced by a YAML rule engine that evaluates structured rules
without `eval()`. Every `ConcealedFlag` records `rule_id`, `rule_text` (the rule as
written), `triggered_by` (the damage and measurement IDs that fired it), and
`recommended_action`. The rules are auditable, not a model.

### Scope items (`cozmo/scope/generate.py`)

Maps damage regions and concealed flags to scope line items with units (SF, LF, EA, SY,
HR), quantity as a `Measure`, `driver_damage_ids`, and a `rationale` string showing the
arithmetic.

## 8. Intervals and calibration

Intervals are split conformal, fitted per (tier, quantity), with the finite-sample
correction — the quantile at ⌈(n+1)(1−α)⌉/n, which makes coverage hold at small n.
Distribution-free, because wall-length error is a mixture of noise, scale bias and gross
failure, and no single normal describes that.

Quantities calibrate in the units their error scales with:
- Ceiling height in metres (equally hard in a small or large room)
- Wall length in percent (error grows with the wall)

Where no quantile has been fitted, the interval falls back to propagated covariance, and
`Measure.method` says `propagated` or `prior`. That field is the reader's way to tell a
calibrated interval from a guess.

No quantiles are applied to the published plans: the tape measurements used to fit would
also be the rows scored, which is grading intervals on their training data.

## 9. Benchmark scoring

`cozmo/bench/gates.py` implements the gates as the brief states them:

- **Opening detection is scored**, not just measurement. Misses and phantoms both count.
- **Ceiling height has two gates**: absolute error (catches bias) and spread across repeats
  (catches noise).
- **SKIP is never a pass.** A gate that cannot be evaluated for lack of ground truth
  reports SKIP, not PASS.

Ground truth is loaded from `capture/ground_truth.csv` with room identity resolved from
camera frames (`capture/room_identity/`), never from area — because area is one of the
scored quantities.

## 10. Fixtures

Three fixture types for testing:

| Fixture | Source | Purpose |
|---|---|---|
| `generate_box_room` | `tests/fixtures/generate.py` | Synthetic point cloud (4 walls + floor + ceiling) |
| `generate_l_shaped_room` | `tests/fixtures/generate.py` | L-shaped room (two joined boxes) |
| `write_capture` (ray-traced) | `tests/fixtures/raytrace_room.py` | Full Stray Scanner capture with exact known dimensions |

The ray-traced fixture produces a capture whose answer is known analytically: 3.60 × 2.80 m
floor, 2.50 m ceiling, two openings. It exercises the entire pipeline from I/O to plan.json
without any real sensor data.

## 11. Layout

```
src/cozmo/
├── cli.py              CLI entry points (Typer + Rich)
├── config.py           PipelineConfig dataclass
├── schema.py           Output contract (Pydantic)
├── pipeline/
│   ├── common.py       PipelineArtifacts
│   ├── lidar.py        LiDAR tier orchestrator
│   ├── photo.py        Photo tier orchestrator
│   └── video.py        Video tier orchestrator
├── io/
│   ├── discover.py     Tier auto-detection
│   ├── stray.py        Stray Scanner loader
│   ├── photo.py        Photo folder loader
│   ├── video.py        Video file loader
│   ├── posed.py        Frame with depth + pose
│   └── base.py         Base capture interface
├── geometry/
│   ├── fusion.py       Voxel downsampling + plane fitting
│   ├── walls.py        Hough wall extraction
│   ├── cellcomplex.py  Cell complex floor plan
│   ├── levels.py       Floor/ceiling measurement
│   ├── openings.py     Opening detection
│   ├── drift.py        Pose graph drift correction
│   ├── icp.py          ICP registration
│   ├── occupancy.py    Occupancy grid
│   ├── assemble.py     Room assembly
│   └── planes.py       Plane fitting
├── recon/
│   ├── monocular.py    Depth Anything V2 inference
│   ├── backbone.py     Model loading
│   ├── register.py     Multi-frame registration
│   └── frames.py       Frame selection
├── damage/
│   ├── detect.py       Classical damage detector
│   ├── rules.py        YAML rule engine
│   └── rules.yaml      Concealed-damage rules
├── scope/
│   ├── generate.py     Scope line item generator
│   └── items.csv       Standard scope items
├── stitch/
│   ├── rooms.py        Multi-room assembly
│   └── graph.py        Adjacency graph
├── render/
│   ├── plan.py         Floor plan renderer
│   ├── drawing.py      Drawing primitives
│   └── backends.py     SVG/PNG output
├── uncertainty/
│   └── calibration.py  Split conformal calibration
├── util/
│   ├── polygons.py     Polygon geometry
│   ├── transforms.py   Coordinate transforms
│   ├── imaging.py      Image utilities
│   ├── raster.py       Rasterisation
│   └── numpy_compat.py NumPy version shims
└── bench/
    ├── gates.py        Benchmark gates
    └── groundtruth.py  Ground truth loading
```

## 12. Deliberately not here yet

- **VGGT / learned stereo**: would give the photo tier actual geometry instead of
  monocular depth. Needs a separate Python environment with PyTorch3D.
- **Appearance-based door detection**: the photo tier cannot find openings from geometry
  alone. A learned detector is the fix.
- **In-sample calibration**: conformal quantiles fitted and applied to different captures
  of the same property. Needs more properties.
- **Multi-floor plans**: the level estimation assumes a single floor.
- **GPU acceleration**: the pipeline runs CPU-only. Depth inference is the bottleneck.
