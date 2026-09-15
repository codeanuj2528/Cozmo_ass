"""A length computed a hair below zero is published as zero; one far below zero is still refused.

The video tier's core met a value of -0.01 m on the assignment's floor-only walk, and the whole plan was lost to
the output contract's check that a value lies within its own interval (fix loop round 5).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from cozmo.schema import Tier
from cozmo.uncertainty.calibration import IntervalBook


def test_a_length_a_hair_below_zero_is_published_as_zero():
    measure = IntervalBook().measure("opening_width", -0.01, Tier.VIDEO, "m")
    assert measure.value == 0.0
    assert measure.lo == 0.0 and measure.hi == pytest.approx(0.60)


def test_a_length_further_below_zero_than_its_interval_is_still_a_defect():
    with pytest.raises(ValidationError):
        IntervalBook().measure("opening_width", -5.0, Tier.VIDEO, "m")


def test_a_positive_length_is_untouched():
    measure = IntervalBook().measure("opening_width", 0.84, Tier.LIDAR, "m")
    assert measure.value == 0.84 and measure.lo == pytest.approx(0.80)
