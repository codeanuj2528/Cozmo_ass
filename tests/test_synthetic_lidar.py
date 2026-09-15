"""The ray-traced box is the only LiDAR input whose metres we actually know.

A 0.1 cm error on this fixture is a unit test, not a benchmark win. The test
asserts we recover the room we drew, and that skipping the upward lap leaves
the ceiling unmeasured rather than filled with a prior.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cozmo.config import PipelineConfig
from cozmo.io import load_capture
from cozmo.pipeline import reconstruct
from tests.fixtures.raytrace_room import (
    CEILING_HEIGHT,
    DOOR,
    ROOM_DEPTH,
    ROOM_WIDTH,
    WINDOW,
    write_capture,
)

TRUTH_AREA = ROOM_WIDTH * ROOM_DEPTH


def _run(root: Path):
    source = load_capture(root)
    # The fixture is 16 frames of a closed box. Default voxels are fine; skip
    # damage so the smoke test does not load a detector.
    config = PipelineConfig(detect_damage=False, build_scope=False, max_keyframes=16)
    return reconstruct(source, config=config)


def test_raytraced_room_recovers_known_box(tmp_path: Path):
    root = write_capture(tmp_path / "synthetic_room", drop_ceiling=False)
    result = _run(root)
    plan = result.plan
    assert len(plan.rooms) == 1
    room = plan.rooms[0]
    assert room.floor_area.value == pytest.approx(TRUTH_AREA, rel=0.12)
    assert room.ceiling_height is not None
    assert room.ceiling_height.value == pytest.approx(CEILING_HEIGHT, abs=0.15)
    assert room.ceiling_height.lo <= CEILING_HEIGHT <= room.ceiling_height.hi, "ceiling interval misses the truth"
    lengths = sorted(w.length.value for w in room.walls if w.length.value > 1.0)
    assert any(abs(L - ROOM_WIDTH) < 0.35 for L in lengths)
    assert any(abs(L - ROOM_DEPTH) < 0.35 for L in lengths)

    # Both openings are found, classified, and measured inside the brief's 2 cm gate.
    widths = {opening.type.value: opening.width for opening in room.openings}
    assert set(widths) == {"door", "window"}
    assert widths["door"].value == pytest.approx(DOOR["u1"] - DOOR["u0"], abs=0.02)
    assert widths["window"].value == pytest.approx(WINDOW["u1"] - WINDOW["u0"], abs=0.02)
    for name, truth in (("door", DOOR["u1"] - DOOR["u0"]), ("window", WINDOW["u1"] - WINDOW["u0"])):
        assert widths[name].lo <= truth <= widths[name].hi, f"{name} interval misses the truth"


def test_raytraced_room_without_up_lap_leaves_ceiling_unmeasured(tmp_path: Path):
    root = write_capture(tmp_path / "synthetic_no_ceiling", drop_ceiling=True)
    result = _run(root)
    room = result.plan.rooms[0]
    assert room.ceiling_height is None
    assert room.floor_area.value == pytest.approx(TRUTH_AREA, rel=0.15)
