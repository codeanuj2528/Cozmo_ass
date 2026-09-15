"""Does a wrongly posed still move when the same stills are given to VGGT-1B in reverse order?

The two reconstructions of a room are in frames of their own. The rotation between the frames is the chordal mean of
R_forward_i R_reverse_i^T over the stills; each still's instability is how far its reverse pose, carried into the
forward frame, is from its forward pose. Compared with the stills pose_checks.json calls wrong against ARKit.
"""
import json
import sys
from pathlib import Path

import numpy as np

FORWARD, REVERSE, CHECKS = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
wrong = {(r["capture"], r["room"], r["frame"]): r["wrong"] for r in json.loads(CHECKS.read_text())}


def angle(r):
    return float(np.degrees(np.arccos(np.clip((np.trace(r) - 1.0) / 2.0, -1.0, 1.0))))


def chordal_mean(rotations):
    u, _, vt = np.linalg.svd(np.sum(rotations, axis=0))
    d = np.diag([1.0, 1.0, np.sign(np.linalg.det(u @ vt))])
    return u @ d @ vt


rows = []
for path in sorted(FORWARD.glob("*.npz")):
    other = REVERSE / path.name
    if not other.exists():
        continue
    a, b = np.load(path), np.load(other)
    capture, room = path.stem.split("__")
    n = len(a["frames"])
    align = chordal_mean([a["rotation_wc"][i] @ b["rotation_wc"][i].T for i in range(n)])
    # Refit without the stills furthest out, so one unstable still does not set the alignment for the rest.
    shift = np.array([angle(a["rotation_wc"][i].T @ align @ b["rotation_wc"][i]) for i in range(n)])
    keep = shift <= np.median(shift) + 10.0
    if keep.sum() >= 2:
        align = chordal_mean([a["rotation_wc"][i] @ b["rotation_wc"][i].T for i in range(n) if keep[i]])
    shift = np.array([angle(a["rotation_wc"][i].T @ align @ b["rotation_wc"][i]) for i in range(n)])
    print(f"== {capture} {room}")
    for i in range(n):
        key = (capture, room, int(a["frames"][i]))
        rows.append({"capture": capture, "room": room, "frame": key[2], "wrong": wrong.get(key), "shift_deg": float(shift[i])})
        print(f"  still {key[2]:5d} {'WRONG' if wrong.get(key) else 'ok   '}  moved {shift[i]:6.1f} deg between orders")

out = Path(sys.argv[4])
out.write_text(json.dumps(rows, indent=2))
print("wrong:", sorted(round(r["shift_deg"], 1) for r in rows if r["wrong"]))
print("right:", sorted(round(r["shift_deg"], 1) for r in rows if not r["wrong"]))
