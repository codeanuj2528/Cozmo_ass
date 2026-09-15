"""Shared pipeline data structures used by all tier-specific reconstructors.

Kept separate so that ``pipeline/__init__.py`` can re-export them without
circular imports through the tier modules.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from cozmo.geometry.cellcomplex import CellComplex
from cozmo.geometry.fusion import FusedCloud
from cozmo.geometry.layout import Layout
from cozmo.geometry.levels import LevelEstimate
from cozmo.geometry.occupancy import OccupancyMaps
from cozmo.geometry.walls import WallSegment
from cozmo.schema import PropertyPlan


@dataclass
class PipelineArtifacts:
    """Intermediate state kept for rendering, ablation and debugging.

    Every field is populated after a successful ``reconstruct`` call.  The
    artefacts intentionally carry the *rotated* cloud and cameras so that
    downstream consumers (the renderer, the benchmark harness) can work in the
    canonical frame without repeating the transform.
    """

    cloud: FusedCloud
    cameras: np.ndarray
    occupancy: OccupancyMaps
    complex: CellComplex | None
    walls: list[WallSegment]
    levels: LevelEstimate
    world_rotation: np.ndarray
    keyframes: list[int] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)
    # The evidence layout, when it built the rooms: barriers, bridges and doorways, for drawing.
    layout: Layout | None = None


@dataclass
class PipelineResult:
    """Wrapper returned by every tier's ``reconstruct`` / ``build_*_plan`` call."""

    plan: PropertyPlan
    artifacts: PipelineArtifacts
