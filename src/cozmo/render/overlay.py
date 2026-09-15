"""The plan drawn over the scan evidence it was built from.

A plan can be internally consistent and still sit in the wrong place. This image puts every room
outline and opening over what the sensor measured in the same frame: floor returns in grey,
ceiling returns in pale blue, vertical returns in dark grey, returns below the floor in tan, the
walk in red, and the doorway bridges the layout closed in magenta. It is how the plans in
`reports/verified/` were checked by eye, and it is written next to every LiDAR plan.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

COLOURS = [(200, 60, 20), (20, 150, 20), (160, 30, 160), (0, 120, 230), (30, 30, 200),
           (140, 110, 0), (90, 60, 180), (0, 170, 170), (200, 0, 120), (60, 140, 90)]
OPENING_COLOURS = {"door": (0, 170, 0), "window": (0, 140, 255), "pass_through": (230, 110, 0)}


def save_scan_overlay(plan, artifacts, out_path: Path, scale: int = 3) -> bool:
    occ = getattr(artifacts, "occupancy", None)
    if occ is None:
        return False
    grid = occ.grid
    h, w = grid.shape
    img = np.full((h, w, 3), 255, np.uint8)
    if occ.ceiling_hits is not None:
        img[occ.ceiling_hits] = (240, 228, 215)
    img[occ.floor_hits] = (200, 200, 200)
    if occ.drop_hits is not None:
        img[occ.drop_hits] = (160, 200, 230)
    if occ.wall_point_hits is not None:
        img[occ.wall_point_hits] = (70, 70, 70)
    # Rows run along +z, which a plan draws upwards: flip so this reads the same way as plan.png.
    img = cv2.resize(img[::-1], (w * scale, h * scale), interpolation=cv2.INTER_NEAREST)

    def px(xz) -> np.ndarray:
        rc = grid.to_cell_float(np.asarray(xz, float))
        return np.round(np.stack([rc[:, 1] * scale + scale / 2, (h - 1 - rc[:, 0]) * scale + scale / 2], 1)).astype(np.int32)

    layout = getattr(artifacts, "layout", None)
    if layout is not None:
        for bridge in layout.bridges:
            if bridge.accepted:
                p, q = bridge.endpoints()
                a, b = px([p, q])
                cv2.line(img, tuple(int(v) for v in a), tuple(int(v) for v in b), (200, 0, 200), 2, cv2.LINE_AA)
    if len(occ.camera_track):
        track = px(grid.to_world(occ.camera_track))
        cv2.polylines(img, [track.reshape(-1, 1, 2)], False, (0, 0, 230), 1, cv2.LINE_AA)
    for i, room in enumerate(plan.rooms):
        colour = COLOURS[i % len(COLOURS)]
        ring = px(room.polygon)
        cv2.polylines(img, [ring.reshape(-1, 1, 2)], True, colour, 2, cv2.LINE_AA)
        walls = {wall.wall_id: wall for wall in room.walls}
        for opening in room.openings:
            wall = walls.get(opening.wall_id)
            if wall is None:
                continue
            start, end = np.asarray(wall.start, float), np.asarray(wall.end, float)
            length = float(np.linalg.norm(end - start))
            along = (end - start) / max(length, 1e-9)
            centre, half = opening.offset_along_wall.value, opening.width.value / 2
            a, b = px([start + along * max(centre - half, 0.0), start + along * min(centre + half, length)])
            cv2.line(img, tuple(int(v) for v in a), tuple(int(v) for v in b), OPENING_COLOURS[opening.type.value], 5, cv2.LINE_AA)
        centre = ring.mean(axis=0).astype(int)
        cv2.putText(img, f"{room.room_id} {room.floor_area.value:.2f} m2", (int(centre[0]) - 45, int(centre[1])),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, colour, 2, cv2.LINE_AA)
    title = f"{plan.capture_id} {plan.tier.value}: plan over its own scan"
    cv2.putText(img, title, (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 2, cv2.LINE_AA)
    cv2.putText(img, "grey floor, blue ceiling, dark walls, red walk, magenta doorway bridges; green door, orange window",
                (8, 44), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (60, 60, 60), 1, cv2.LINE_AA)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    return bool(cv2.imwrite(str(out_path), img))
