# One-room run: which inputs, and why

Unbiased means: pick the capture closest to one room, not the capture that makes the
pipeline look good. Rejected inputs are listed so the choice can be checked.

## Selected

| Tier | Path | Why this one |
|---|---|---|
| LiDAR | `data/raw/c00a170fe1` (`single_room.zip`) | The assignment named it a single room: 37 s, 1,715 frames, the shortest Stray export and the only one not walked through a whole flat. It is not strictly one room. The walk covers a living room, its bathroom and the lobby between them, and the plan reports four rooms. |
| Video | that same folder's `rgb.mp4` (42 MB), copied as a *video-only* folder so the loader cannot see `odometry.csv` and cheat into the LiDAR tier | Same walk, no depth, no poses. This is the fair video of that capture. |
| Photo | `03_multiroom_photos/bathroom/` — **7 stills** | Brief says 2–8 stills per room. Bathroom is the only home folder inside that band. |

LiDAR + video are the **same walk**. Photo is a **different room**, the home bathroom. That is disclosed: there are no 2–8 stills of `c00a170fe1`, and inventing them by dumping video frames would not be a photo-tier capture.

## Rejected, and why

| Input | Why not |
|---|---|
| `01_multiroom_lidar` / `163f18d3ac` | Multi-room walks. Using them for a one-room plan would hide rooms. |
| Home `IMG_1582.mp4` (3.4 GB) | Whole-flat walkthrough. Not one room. |
| `bedroom/` 23 photos, `hall/` 12, `passage/` 16 | Over the brief's 2–8 stills. Picking the folder with the most pictures would flatter the photo tier. |
| `1a8384c3f6`, `c7d28f72c6` | Same property scanned as a flat, not a room. |

## Commands

The video and photo inputs are links into the raw captures, which are not in git. Recreate them
first:

```bash
mkdir -p reports/verified/one_room/inputs/video_single reports/verified/one_room/inputs/photos_bathroom
ln -s ../../../../../../data/raw/c00a170fe1/rgb.mp4 reports/verified/one_room/inputs/video_single/room.mp4
ln -s ../../../../../../DROP_CAPTURES_HERE/03_multiroom_photos/bathroom reports/verified/one_room/inputs/photos_bathroom/bathroom
.venv/bin/python -m cozmo.cli run -i ../data/raw/c00a170fe1 -o reports/verified/one_room/lidar
.venv/bin/python -m cozmo.cli run -i reports/verified/one_room/inputs/video_single -o reports/verified/one_room/video
.venv/bin/python -m cozmo.cli run -i reports/verified/one_room/inputs/photos_bathroom -o reports/verified/one_room/photo
```
