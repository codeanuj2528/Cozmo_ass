"""Opening classification.

The plan of the assignment's with-ceiling scan carried a 0.38 m pass-through: a floor-level gap
too narrow for a door fell through to the pass-through branch, which tested only its height.
"""

from __future__ import annotations

from cozmo.geometry.openings import DOOR_MIN_WIDTH_M, _classify
from cozmo.schema import OpeningType


def test_a_floor_gap_narrower_than_a_door_is_not_an_opening():
    assert _classify(width=0.38, height=2.0, sill=0.0, ceiling_y=2.6, floor_y=0.0) is None
    assert _classify(width=DOOR_MIN_WIDTH_M - 0.01, height=2.0, sill=0.0, ceiling_y=2.6, floor_y=0.0) is None


def test_doors_and_pass_throughs_still_classify():
    assert _classify(width=0.85, height=2.05, sill=0.0, ceiling_y=2.6, floor_y=0.0) == OpeningType.DOOR
    assert _classify(width=2.10, height=2.10, sill=0.0, ceiling_y=2.6, floor_y=0.0) == OpeningType.PASS_THROUGH


def test_a_raised_sill_makes_a_window():
    assert _classify(width=0.90, height=1.10, sill=0.85, ceiling_y=2.6, floor_y=0.0) == OpeningType.WINDOW
