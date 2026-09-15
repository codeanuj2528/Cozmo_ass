#!/usr/bin/env python
"""Photo- and video-tier inputs made from one LiDAR capture, with the LiDAR plan as their reference.

The brief asks for the same rooms captured at all three tiers. A Stray Scanner export carries the
colour stream the LiDAR tier ignores: a 1920x1440 walkthrough clip from the phone's main camera. This
script turns one export into

  <out>/video/walkthrough.mp4  the clip itself, frames untouched, carrying the rotation tag the iPhone Camera
                             app writes, as the video-tier capture
  <out>/photo/<room>/*.jpg   2-8 stills per room, taken from that clip where the camera stood inside the
                             room, turned upright and tagged with their focal length as an iPhone still is
  <out>/reference.json       the LiDAR plan's rooms, areas, walls and adjacency, which the photo and video
                             plans are scored against
  <out>/stills.jpg           every chosen still, one row per room, for checking by eye

The reference is a reconstruction, not tape. It says whether the thinner tiers agree with the LiDAR tier
on the same walk; it cannot say whether any tier is right.

Stills taken from a video are harder input than stills from the camera app: they are frames of a moving
phone, with motion blur and rolling shutter, and nobody framed them. They are chosen by rule, for
sharpness and for spread across the room, so a set cannot be hand-picked to flatter a tier. Rooms under
2.5 m2 get no folder: a lobby or a closet is not photographed as a room.

    .venv/bin/python scripts/make_tier_inputs.py --capture ../data/raw/c7d28f72c6 --out data/tier_inputs/single_scan_with_ceiling
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path

import cv2
import numpy as np
from PIL import Image
from shapely.geometry import Point, Polygon

from cozmo.config import PipelineConfig
from cozmo.io import load_capture
from cozmo.pipeline import reconstruct
from cozmo.util.orientation import quarter_turns_upright, rotate_quarter

MIN_ROOM_AREA_M2 = 2.5
SHARPNESS_WIDTH = 480
# Only the sharper part of a room's frames is eligible. A photographer does not keep a blurred shot.
SHARP_QUANTILE = 0.40
FILM_WIDTH_MM = 36.0
EXIF_SUB_IFD = 0x8769
EXIF_FOCAL_35MM = 0xA405
EXIF_ORIENTATION = 0x0112


def stills_for_area(area_m2: float) -> int:
    """How many stills a person would take of a room this size, within the brief's 2-8."""
    if area_m2 >= 6.0:
        return 8
    if area_m2 >= 3.5:
        return 6
    return 4


def focal_35mm(fx_px: float, width: int, height: int) -> int:
    """The 35 mm equivalent focal length an iPhone records: defined on the long edge, stored as an integer."""
    return int(round(fx_px * FILM_WIDTH_MM / max(width, height)))


def save_still(path: Path, rgb: np.ndarray, equivalent_mm: int) -> None:
    exif = Image.Exif()
    exif[EXIF_ORIENTATION] = 1
    exif.get_ifd(EXIF_SUB_IFD)[EXIF_FOCAL_35MM] = int(equivalent_mm)
    Image.fromarray(rgb).save(path, quality=92, exif=exif)


def video_turns(poses: np.ndarray) -> tuple[int, float]:
    """The quarter turn most of a walk was held at, and the share of frames held that way."""
    turns = [quarter_turns_upright(pose[:3, :3]) for pose in poses]
    values, counts = np.unique(turns, return_counts=True)
    return int(values[np.argmax(counts)]), float(counts.max() / max(len(turns), 1))


def write_tagged_video(source: Path, target: Path, turns: int) -> str:
    """The clip as the Camera app stores a video: the same frames, with a display rotation to show them upright.

    Nothing is re-encoded. ffmpeg's display rotation is anticlockwise, so `turns` clockwise quarter turns is
    -90 * turns degrees, and OpenCV applies the tag when it decodes, as a player does. Without ffmpeg the clip is
    linked untagged and the plan's input is sideways, which the returned note says.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink() or target.exists():
        target.unlink()
    turns %= 4
    if turns == 0:
        target.symlink_to(Path(source).resolve())
        return "untagged: stored upright"
    if shutil.which("ffmpeg") is None:
        target.symlink_to(Path(source).resolve())
        return "untagged: ffmpeg not found, so the clip is sideways"
    degrees = ((-90 * turns + 180) % 360) - 180
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-display_rotation", str(degrees), "-i", str(source), "-c", "copy",
                    str(target)], check=True)
    return f"display rotation {degrees} degrees, frames not re-encoded"


def sharpness(bgr: np.ndarray) -> float:
    height = max(int(round(SHARPNESS_WIDTH * bgr.shape[0] / bgr.shape[1])), 8)
    grey = cv2.cvtColor(cv2.resize(bgr, (SHARPNESS_WIDTH, height), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(grey, cv2.CV_64F).var())


def decode(video: Path, wanted: set[int], keep_images: bool) -> dict[int, np.ndarray | float]:
    """One forward pass: the sharpness of each wanted frame, or the frame itself."""
    capture = cv2.VideoCapture(str(video))
    out: dict[int, np.ndarray | float] = {}
    last = max(wanted) if wanted else -1
    index = 0
    try:
        while index <= last:
            if not capture.grab():
                break
            if index in wanted:
                ok, bgr = capture.retrieve()
                if ok:
                    out[index] = bgr if keep_images else sharpness(bgr)
            index += 1
    finally:
        capture.release()
    return out


def choose_spread(candidates: list[dict], count: int) -> list[dict]:
    """Greedy farthest-point choice over viewing direction and position, starting from the sharpest frame."""
    if len(candidates) <= count:
        return list(candidates)
    threshold = float(np.quantile([c["sharpness"] for c in candidates], SHARP_QUANTILE))
    pool = [c for c in candidates if c["sharpness"] >= threshold]
    if len(pool) < count:
        pool = sorted(candidates, key=lambda c: -c["sharpness"])[: max(count, len(pool))]
    chosen = [max(pool, key=lambda c: c["sharpness"])]
    while len(chosen) < count:
        best, best_score = None, -1.0
        for c in pool:
            if c in chosen:
                continue
            score = min(
                abs((c["azimuth"] - k["azimuth"] + np.pi) % (2 * np.pi) - np.pi)
                + 0.5 * float(np.hypot(c["xz"][0] - k["xz"][0], c["xz"][1] - k["xz"][1]))
                for k in chosen
            )
            if score > best_score:
                best, best_score = c, score
        if best is None:
            break
        chosen.append(best)
    return sorted(chosen, key=lambda c: c["frame"])


def write_video_capture(video: Path, poses: np.ndarray, video_dir: Path, name: str) -> dict:
    video_dir.mkdir(parents=True, exist_ok=True)
    for stale in video_dir.glob("*.mp4"):
        stale.unlink()
    turns, share = video_turns(poses)
    note = write_tagged_video(video, video_dir / "walkthrough.mp4", turns)
    (video_dir / "capture.json").write_text(json.dumps({"tier": "video", "capture_id": f"{name}_video"}, indent=2))
    return {"file": "video/walkthrough.mp4", "rotation_turns": turns, "frames_held_that_way": share, "tag": note}


def retag_video(out_dir: Path) -> dict:
    """Rewrite only the video capture of an existing set, from the export its reference.json names."""
    reference_path = out_dir / "reference.json"
    reference = json.loads(reference_path.read_text())
    source = load_capture(Path(reference["source"]))
    reference["video"] = write_video_capture(Path(reference["source"]) / "rgb.mp4", source.poses(), out_dir / "video",
                                             reference["capture"])
    reference_path.write_text(json.dumps(reference, indent=2))
    return reference


def _git_commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        return "unknown"


def contact_sheet(rows: list[tuple[str, list[np.ndarray]]], path: Path, thumb_width: int = 240) -> None:
    lines = []
    for label, images in rows:
        thumbs = []
        for rgb in images:
            height = int(round(thumb_width * rgb.shape[0] / rgb.shape[1]))
            thumb = cv2.resize(rgb, (thumb_width, height), interpolation=cv2.INTER_AREA)
            canvas = np.full((thumb_width, thumb_width, 3), 255, np.uint8)
            if height > thumb_width:
                height = thumb_width
                thumb = cv2.resize(rgb, (int(round(thumb_width * rgb.shape[1] / rgb.shape[0])), thumb_width))
            top = (thumb_width - thumb.shape[0]) // 2
            left = (thumb_width - thumb.shape[1]) // 2
            canvas[top:top + thumb.shape[0], left:left + thumb.shape[1]] = thumb
            thumbs.append(canvas)
        strip = np.full((thumb_width, 160 + thumb_width * 8, 3), 255, np.uint8)
        cv2.putText(strip, label, (6, thumb_width // 2), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 1, cv2.LINE_AA)
        for i, thumb in enumerate(thumbs[:8]):
            strip[:, 160 + i * thumb_width:160 + (i + 1) * thumb_width] = thumb
        lines.append(strip)
    if lines:
        cv2.imwrite(str(path), cv2.cvtColor(np.vstack(lines), cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 85])


def make_tier_inputs(capture_dir: Path, out_dir: Path, name: str, min_room_area_m2: float = MIN_ROOM_AREA_M2) -> dict:
    source = load_capture(capture_dir)
    root = Path(source.meta.root)
    video = root / "rgb.mp4"
    if not video.exists():
        raise FileNotFoundError(f"{root} has no rgb.mp4 to take the video and photo tiers from")

    result = reconstruct(source, config=PipelineConfig(detect_damage=False))
    plan, artifacts = result.plan, result.artifacts
    poses = source.poses()
    frame_numbers = source.frame_indices()
    keyframes = list(artifacts.keyframes)
    cameras_xz = np.asarray(artifacts.cameras)[:, [0, 2]]
    world_rotation = np.asarray(artifacts.world_rotation)
    k_rgb = np.asarray(source.k_rgb)
    width, height = source.rgb_size

    rooms = [room for room in plan.rooms if room.floor_area.value >= min_room_area_m2]
    candidates: dict[str, list[dict]] = {}
    for room in rooms:
        outline = Polygon(room.polygon)
        found = []
        for slot, key in enumerate(keyframes):
            if not outline.contains(Point(float(cameras_xz[slot, 0]), float(cameras_xz[slot, 1]))):
                continue
            rotation = poses[key][:3, :3]
            forward = world_rotation @ rotation[:, 2]
            found.append({
                "frame": int(frame_numbers[key]),
                "xz": [float(cameras_xz[slot, 0]), float(cameras_xz[slot, 1])],
                "azimuth": float(np.arctan2(forward[2], forward[0])),
                "turns": quarter_turns_upright(rotation),
            })
        candidates[room.room_id] = found

    wanted = {c["frame"] for found in candidates.values() for c in found}
    scores = decode(video, wanted, keep_images=False)
    for found in candidates.values():
        for c in found:
            c["sharpness"] = float(scores.get(c["frame"], 0.0))

    chosen: dict[str, list[dict]] = {}
    for room in rooms:
        picks = choose_spread(candidates[room.room_id], stills_for_area(room.floor_area.value))
        if len(picks) >= 2:
            chosen[room.room_id] = picks

    images = decode(video, {c["frame"] for picks in chosen.values() for c in picks}, keep_images=True)
    photo_dir = out_dir / "photo"
    photo_dir.mkdir(parents=True, exist_ok=True)
    equivalent = focal_35mm(float(k_rgb[0, 0]), width, height)
    sheet_rows = []
    for room_id, picks in chosen.items():
        folder = photo_dir / room_id
        folder.mkdir(parents=True, exist_ok=True)
        upright_images = []
        for c in picks:
            bgr = images.get(c["frame"])
            if bgr is None:
                continue
            # Turned as the Camera app would have stored it; see util/orientation.py.
            rgb = rotate_quarter(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB), c["turns"])
            save_still(folder / f"IMG_{c['frame']:06d}.jpg", rgb, equivalent)
            upright_images.append(rgb)
        sheet_rows.append((f"{room_id} {len(upright_images)} stills", upright_images))
    (photo_dir / "capture.json").write_text(json.dumps({"tier": "photo", "capture_id": f"{name}_photo"}, indent=2))
    contact_sheet(sheet_rows, out_dir / "stills.jpg")

    video_entry = write_video_capture(video, poses, out_dir / "video", name)

    included = set(chosen)
    reference = {
        "capture": name,
        "source": str(root),
        "reference_kind": "LiDAR reconstruction of the same walk, not tape",
        "code_commit": _git_commit(),
        "lidar_footprint_m2": plan.total_floor_area.value,
        "photo_rooms_footprint_m2": float(sum(r.floor_area.value for r in plan.rooms if r.room_id in included)),
        "min_room_area_m2": min_room_area_m2,
        "focal_35mm": equivalent,
        "video": video_entry,
        "rooms": [
            {
                "room_id": room.room_id,
                "photo_folder": room.room_id if room.room_id in included else None,
                "floor_area_m2": room.floor_area.value,
                "perimeter_m": room.perimeter.value,
                "ceiling_height_m": room.ceiling_height.value if room.ceiling_height is not None else None,
                "wall_lengths_m": [w.length.value for w in room.walls],
                "polygon": [list(p) for p in room.polygon],
                "stills": chosen.get(room.room_id, []),
            }
            for room in plan.rooms
        ],
        "adjacency": [
            {"room_a": a.room_a, "room_b": a.room_b, "through_opening": bool(a.opening_a and a.opening_b)}
            for a in plan.adjacency
        ],
    }
    (out_dir / "reference.json").write_text(json.dumps(reference, indent=2))
    return reference


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--capture", type=Path, help="Stray Scanner export (not needed with --video-only)")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--name", default=None, help="capture name used in capture ids; defaults to the out folder name")
    parser.add_argument("--min-room-area", type=float, default=MIN_ROOM_AREA_M2)
    parser.add_argument("--video-only", action="store_true", help="rewrite only the video capture of an existing set")
    args = parser.parse_args()
    if args.video_only:
        video = retag_video(args.out)["video"]
        print(f"{args.out.name}: video held at {video['rotation_turns']} quarter turns for "
              f"{video['frames_held_that_way']:.0%} of frames; {video['tag']}")
        return
    reference = make_tier_inputs(args.capture, args.out, args.name or args.out.name, args.min_room_area)
    folders = [r for r in reference["rooms"] if r["photo_folder"]]
    print(f"{reference['capture']}: LiDAR {len(reference['rooms'])} rooms {reference['lidar_footprint_m2']:.2f} m2; "
          f"photo folders {len(folders)} ({reference['photo_rooms_footprint_m2']:.2f} m2), "
          f"{sum(len(r['stills']) for r in folders)} stills at {reference['focal_35mm']} mm equivalent")


if __name__ == "__main__":
    main()
