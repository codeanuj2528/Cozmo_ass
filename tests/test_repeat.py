"""Registering two captures of one space on their walls (bench/repeat.py)."""

from __future__ import annotations

import numpy as np
import pytest

from cozmo.bench.repeat import register_walls


def _walls_of_a_flat(rng: np.random.Generator) -> np.ndarray:
    """Wall cells of an L-shaped flat with two partitions, 3 cm apart along each wall."""
    segments = [((0, 0), (6, 0)), ((6, 0), (6, 4)), ((6, 4), (3, 4)), ((3, 4), (3, 7)), ((3, 7), (0, 7)),
                ((0, 7), (0, 0)), ((2, 0), (2, 2.2)), ((3, 4), (4.5, 4)), ((0, 3.5), (1.4, 3.5))]
    points = []
    for (x0, z0), (x1, z1) in segments:
        n = int(np.hypot(x1 - x0, z1 - z0) / 0.03)
        t = np.linspace(0.0, 1.0, n)
        points.append(np.stack([x0 + (x1 - x0) * t, z0 + (z1 - z0) * t], axis=1))
    xy = np.vstack(points)
    return xy + rng.normal(0.0, 0.01, xy.shape)


@pytest.mark.parametrize("quarter_turns, angle_deg", [(0, 0.8), (1, -0.6), (2, 0.3), (3, 0.0)])
def test_two_captures_of_one_flat_register_to_a_centimetre(quarter_turns: int, angle_deg: float):
    rng = np.random.default_rng(quarter_turns)
    reference = _walls_of_a_flat(rng)
    theta = np.deg2rad(90.0 * quarter_turns + angle_deg)
    rotation = np.array([[np.cos(theta), -np.sin(theta)], [np.sin(theta), np.cos(theta)]])
    shift = np.array([2.37, -1.12])
    # The second capture saw most of the same walls, in its own frame.
    kept = rng.random(len(reference)) > 0.25
    moving = (reference[kept] - shift) @ rotation      # inverse of p -> R p + shift
    moving += rng.normal(0.0, 0.01, moving.shape)
    registration = register_walls(reference, moving)
    recovered = registration.apply(moving)
    assert np.median(np.linalg.norm(recovered - reference[kept], axis=1)) < 0.02
    assert registration.within_5cm > 0.9
