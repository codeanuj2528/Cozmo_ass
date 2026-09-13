# Capture protocol — one page

**Route 2 (stock tools).** Nothing to install from us. Follow this page literally; if
anything is ambiguous, tell us and we'll fix the page, not the capture.

**Before you start:** lights on · don't tidy up, furniture is expected · don't cover
mirrors or glass, just tell us they're there. Any **one** tier below is a complete
capture — pick on the day.

## The three rules that decide whether this works

1. **Sweep the phone upward at every corner** — tilt up until the ceiling fills the
   screen, hold 2 seconds, come down. Skip this and there is no ceiling height, only
   a wide interval.
2. **Move your feet between photos.** Never stand still and rotate: two photos from
   one spot contain no depth information and cannot be triangulated.
3. **Finish where you started**, and send **original files** — not via WhatsApp,
   Telegram or Slack, which strip the metadata the pipeline reads.

## What to install

| Tier | App | Where | Cost |
|---|---|---|---|
| LiDAR | **Stray Scanner** | iOS App Store | Free |
| Video | **Camera** (built in) | Already on the phone | Free |
| Photo | **Camera** (built in) | Already on the phone | Free |

Install time: under two minutes on any iPhone. No sign-in, no provisioning.

## How to capture

### Tier 1 — LiDAR (iPhone Pro only)

Open Stray Scanner. Tap the red record button. Then, **in each room in turn**:

1. Stand still just inside the doorway for 3 seconds. Do not move. This anchors the room.
2. **Lap one, walls.** Walk the perimeter ≈1.2 m from the walls, phone tilted down ≈15°.
3. **Lap two, ceiling.** Walk the same loop with the phone tilted **up** ≈30° so the
   ceiling fills the top of the screen. About 20 extra seconds per room. *Not optional.*
4. Walk through the doorway into the next room. Do not stop recording.

**Finish where you started** — walk back into the first room and stand still for 3 seconds.
Tap stop. The loop closure is what makes the map accurate over long walks.

Export: tap Export → select "Stray Scanner Format" → AirDrop or share the folder.

### Tier 2 — Video (any iPhone)

Open Camera. Switch to **video** mode. Record one continuous walk through the property,
following the same rules above. Use the **1× lens** (tap "1x"), not the ultra-wide.
Walk slowly — one step per second.

### Tier 3 — Photos (any iPhone)

Open Camera. Use the **1× lens** (not 0.5× ultra-wide).

**For each room**, take **4–8 photos**:
- Two from opposite corners
- One of each wall, straight on
- One aimed at the ceiling

Create a folder per room: `room_01/`, `room_02/`, etc. Name the folders by adjacency:
`room_01` and `room_02` share a doorway.

### Three failure modes that break real captures

| Problem | What happens | Fix |
|---|---|---|
| No upward sweep | Ceiling height is unmeasurable, only a prior | Do the ceiling lap |
| Photos from one spot | No parallax, depth is unusable | Move your feet |
| Files sent via WhatsApp | EXIF stripped, focal length unknown, pipeline uses wrong value | AirDrop or email original files |

## How to hand off

Send the raw export — the entire folder, not individual files. The pipeline reads
`camera_matrix.csv`, `odometry.csv`, per-frame depth and confidence maps. Missing any
of those degrades the output silently.

For full details, see `capture/PROTOCOL.md`.
