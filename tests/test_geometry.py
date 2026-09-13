"""Tests for geometric algorithms, polygon cleanup and wall fitting."""

from __future__ import annotations

import numpy as np
import pytest
from shapely.geometry import Polygon as ShapelyPolygon

from cozmo.util.polygons import (
    _drop_short_edges,
    _drop_collinear,
    _collapse_jogs,
    clean_polygon,
    rotation_2d,
    rotation_about_up,
)


# ---------------------------------------------------------------------------
# rotation_about_up
# ---------------------------------------------------------------------------

def test_rotation_about_up():
    angle = np.pi / 4.0
    rot = rotation_about_up(angle)
    assert rot.shape == (3, 3)
    assert np.allclose(rot @ rot.T, np.eye(3))
    # Vector pointing along X should rotate in XZ plane
    v = np.array([1.0, 0.0, 0.0])
    v_rot = v @ rot.T
    assert v_rot[1] == 0.0


def test_rotation_about_up_identity():
    """Zero angle should be the identity rotation."""
    rot = rotation_about_up(0.0)
    assert np.allclose(rot, np.eye(3), atol=1e-12)


def test_rotation_about_up_full_turn():
    """A full 2π rotation should also be the identity."""
    rot = rotation_about_up(2 * np.pi)
    assert np.allclose(rot, np.eye(3), atol=1e-10)


def test_rotation_about_up_preserves_y():
    """Rotation about the up axis should not change the Y component."""
    rot = rotation_about_up(np.pi / 3.0)
    v = np.array([3.0, 7.5, -2.0])
    v_rot = rot @ v
    assert v_rot[1] == pytest.approx(7.5, abs=1e-12)


# ---------------------------------------------------------------------------
# rotation_2d
# ---------------------------------------------------------------------------

def test_rotation_2d_90_degrees():
    """90° should rotate [1,0] to [0,1]."""
    rot = rotation_2d(np.pi / 2)
    v = np.array([1.0, 0.0])
    v_rot = rot @ v
    assert np.allclose(v_rot, [0.0, 1.0], atol=1e-12)


def test_rotation_2d_is_orthogonal():
    for angle in [0.0, np.pi / 7, np.pi, -0.3]:
        rot = rotation_2d(angle)
        assert np.allclose(rot @ rot.T, np.eye(2), atol=1e-12)


# ---------------------------------------------------------------------------
# _drop_short_edges
# ---------------------------------------------------------------------------

def test_drop_short_edges_removes_slivers():
    """Two vertices within min_edge should be collapsed to one."""
    ring = np.array([
        [0.0, 0.0],
        [0.001, 0.001],   # sliver next to origin
        [5.0, 0.0],
        [5.0, 4.0],
        [0.0, 4.0],
    ])
    cleaned = _drop_short_edges(ring, min_edge_m=0.04)
    assert len(cleaned) == 4, "the sliver vertex should be dropped"


def test_drop_short_edges_preserves_rectangle():
    """A clean rectangle should be unchanged."""
    ring = np.array([[0, 0], [4, 0], [4, 3], [0, 3]], dtype=float)
    cleaned = _drop_short_edges(ring, min_edge_m=0.04)
    assert len(cleaned) == 4


# ---------------------------------------------------------------------------
# _drop_collinear
# ---------------------------------------------------------------------------

def test_drop_collinear_removes_midpoints():
    """A point on a straight edge should be dropped."""
    ring = np.array([
        [0.0, 0.0],
        [2.0, 0.0],  # midpoint of a straight bottom edge
        [4.0, 0.0],
        [4.0, 3.0],
        [0.0, 3.0],
    ])
    cleaned = _drop_collinear(ring, tolerance_rad=np.deg2rad(4.0))
    assert len(cleaned) == 4, "the midpoint on the straight bottom edge should be dropped"


def test_drop_collinear_keeps_corners():
    """A right-angle corner should not be dropped."""
    ring = np.array([[0, 0], [4, 0], [4, 3], [0, 3]], dtype=float)
    cleaned = _drop_collinear(ring, tolerance_rad=np.deg2rad(4.0))
    assert len(cleaned) == 4


# ---------------------------------------------------------------------------
# _collapse_jogs
# ---------------------------------------------------------------------------

def test_collapse_jogs_removes_staircase():
    """A small perpendicular step in the middle of a long wall should be collapsed."""
    ring = np.array([
        [0.0, 0.0],
        [2.5, 0.0],
        [2.5, 0.3],   # jog outward
        [5.0, 0.3],   # continue wall
        [5.0, 4.0],
        [0.0, 4.0],
    ])
    cleaned = _collapse_jogs(ring, max_jog_m=0.55)
    assert len(cleaned) <= 5, "the jog vertices should be collapsed"


# ---------------------------------------------------------------------------
# clean_polygon (integration)
# ---------------------------------------------------------------------------

def test_clean_polygon_preserves_area():
    """Cleaning a polygon should not change its area by more than 6%."""
    # A rectangle with one sliver and one collinear midpoint
    ring = [
        (0.0, 0.0),
        (0.01, 0.005),   # sliver
        (2.0, 0.0),
        (4.0, 0.0),      # collinear
        (4.0, 3.0),
        (0.0, 3.0),
    ]
    poly = ShapelyPolygon(ring)
    cleaned = clean_polygon(poly)
    assert abs(cleaned.area - poly.area) / poly.area < 0.06


def test_clean_polygon_reduces_vertex_count():
    """Cleaning should produce fewer vertices than the raw input."""
    ring = [
        (0.0, 0.0),
        (1.0, 0.0),
        (2.0, 0.0),
        (3.0, 0.0),
        (4.0, 0.0),
        (4.0, 1.0),
        (4.0, 2.0),
        (4.0, 3.0),
        (0.0, 3.0),
    ]
    poly = ShapelyPolygon(ring)
    cleaned = clean_polygon(poly)
    n_cleaned = len(cleaned.exterior.coords) - 1  # shapely closes the ring
    assert n_cleaned < len(ring)


def test_clean_polygon_empty_returns_empty():
    """An empty polygon should come back empty."""
    poly = ShapelyPolygon()
    cleaned = clean_polygon(poly)
    assert cleaned.is_empty


# ---------------------------------------------------------------------------
# Polygon area (Shoelace formula sanity)
# ---------------------------------------------------------------------------

def test_rectangle_area_matches_shapely():
    """4×3 rectangle should have area 12."""
    poly = ShapelyPolygon([(0, 0), (4, 0), (4, 3), (0, 3)])
    assert poly.area == pytest.approx(12.0)


def test_triangle_area():
    """A right triangle with legs 3 and 4 should have area 6."""
    poly = ShapelyPolygon([(0, 0), (3, 0), (0, 4)])
    assert poly.area == pytest.approx(6.0)
