"""Render a PropertyPlan as an architectural floor plan.

What a homeowner recognises from a consumer scanning app is a specific set of conventions:
walls drawn with thickness, doors shown as a gap with a swing arc, windows as a break in
the wall, every room labelled with its name and area, and a scale bar. Drawing the room
polygons as outlines would be technically complete and would not read as a floor plan.

Intervals are drawn, not just tabulated. A dimension is written as its value with the
interval underneath, so the reader sees at a glance which rooms the pipeline is confident
about. A plan that shows only point estimates invites exactly the confident-garbage reading
the brief penalises.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import numpy as np

from cozmo.render.drawing import Arc, Circle, Drawing, Line, Polygon, Style, Text
from cozmo.schema import OpeningType, PropertyPlan

WALL_THICKNESS_M = 0.11
PALETTE = {
    "paper": "#ffffff",
    "room_fill": "#f4f1ea",
    "room_fill_alt": "#eceadf",
    "wall": "#22222a",
    "opening": "#ffffff",
    "door_swing": "#8a8a96",
    "window": "#3d6fa8",
    "text": "#22222a",
    "muted": "#7a7a86",
    "damage": "#c0392b",
    "damage_fill": "#e8b4ae",
    "flag": "#d98324",
    "grid": "#dedcd4",
}


def build_plan_drawing(plan: PropertyPlan, show_intervals: bool = True) -> Drawing:
    drawing = Drawing()
    if not plan.rooms:
        drawing.add(Text((0.0, 0.0), "no rooms reconstructed", size=14, colour=PALETTE["muted"]))
        return drawing

    all_points = np.array([p for room in plan.rooms for p in room.polygon])
    drawing.set_extent(
        float(all_points[:, 0].min()), float(all_points[:, 1].min()),
        float(all_points[:, 0].max()), float(all_points[:, 1].max()),
    )

    # Walls are drawn as the solid between rooms, the way a scanning app draws them: one mass around
    # every room, closed across partitions, with the rooms laid over it. A stroke round each room on
    # its own leaves a white seam down the middle of every shared wall.
    mass, rooms_union = _wall_mass(plan)
    for part in _polygons(mass):
        drawing.add(Polygon([tuple(p) for p in part.exterior.coords], Style(fill=PALETTE["wall"], stroke=None)))
        for hole in part.interiors:
            drawing.add(Polygon([tuple(p) for p in hole.coords], Style(fill=PALETTE["paper"], stroke=None)))

    for i, room in enumerate(plan.rooms):
        fill = PALETTE["room_fill"] if i % 2 == 0 else PALETTE["room_fill_alt"]
        drawing.add(Polygon(list(room.polygon), Style(fill=fill, stroke=None)))

    walls_only = mass.difference(rooms_union) if mass is not None else None
    for room in plan.rooms:
        _draw_openings(drawing, room, walls_only)

    for room in plan.rooms:
        _draw_room_label(drawing, room, show_intervals)
        _draw_dimensions(drawing, room, show_intervals)

    _draw_damage(drawing, plan)
    _draw_adjacency(drawing, plan)
    return drawing


# Gaps between rooms up to twice this are drawn as wall: a 23 cm brick partition and the few
# centimetres either side of it that no return reached.
WALL_CLOSE_M = 0.20
OPENING_DEPTH_M = 0.45


def _polygons(geometry) -> list:
    if geometry is None or geometry.is_empty:
        return []
    return [g for g in getattr(geometry, "geoms", [geometry]) if g.geom_type == "Polygon" and not g.is_empty]


def _wall_mass(plan: PropertyPlan):
    from shapely.geometry import Polygon as ShapelyPolygon
    from shapely.ops import unary_union

    shapes = []
    for room in plan.rooms:
        if len(room.polygon) < 3:
            continue
        shape = ShapelyPolygon(room.polygon)
        shape = shape if shape.is_valid else shape.buffer(0)
        if not shape.is_empty:
            shapes.append(shape)
    if not shapes:
        return None, None
    mass = unary_union([s.buffer(WALL_THICKNESS_M, join_style=2) for s in shapes])
    mass = mass.buffer(WALL_CLOSE_M, join_style=2).buffer(-WALL_CLOSE_M, join_style=2)
    return mass, unary_union(shapes)


def _opening_span(wall, opening) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    start = np.array(wall.start, dtype=float)
    end = np.array(wall.end, dtype=float)
    length = float(np.linalg.norm(end - start))
    direction = (end - start) / max(length, 1e-9)
    centre_t = float(np.clip(opening.offset_along_wall.value, 0.0, length))
    half = 0.5 * opening.width.value
    a = start + direction * max(centre_t - half, 0.0)
    b = start + direction * min(centre_t + half, length)
    return a, b, direction


def _draw_openings(drawing: Drawing, room, walls_only=None) -> None:
    from shapely.geometry import Polygon as ShapelyPolygon

    walls = {w.wall_id: w for w in room.walls}
    anchor = _label_anchor(np.array(room.polygon))
    for opening in room.openings:
        wall = walls.get(opening.wall_id)
        if wall is None:
            continue
        a, b, direction = _opening_span(wall, opening)
        normal = np.array([-direction[1], direction[0]])
        if (anchor - 0.5 * (a + b)) @ normal < 0:
            normal = -normal

        # Cut the opening through the wall mass on the far side of this room's edge, as far as the
        # wall goes, which is how a wall break reads on a real plan.
        cut = ShapelyPolygon([tuple(a), tuple(b), tuple(b - normal * OPENING_DEPTH_M), tuple(a - normal * OPENING_DEPTH_M)])
        pieces = _polygons(cut.intersection(walls_only)) if walls_only is not None else [cut]
        for piece in pieces:
            drawing.add(Polygon([tuple(p) for p in piece.exterior.coords], Style(fill=PALETTE["opening"], stroke=None)))
        depth = max((float(np.max((np.asarray(p.exterior.coords) - a) @ -normal)) for p in pieces), default=WALL_THICKNESS_M)
        if opening.type is OpeningType.WINDOW:
            for f in (0.35, 0.65):
                offset = -normal * depth * f
                drawing.add(Line(tuple(a + offset), tuple(b + offset), Style(stroke=PALETTE["window"], width=0.022)))
        else:
            width = float(np.linalg.norm(b - a))
            # A doorway joins two rooms and is attached to both; its leaf is drawn once. A pass-through
            # has no leaf.
            other = getattr(opening, "connects_to_room", None)
            if opening.type is OpeningType.DOOR and width > 0.2 and not (other and other < room.room_id):
                hinge = a
                leaf_end = hinge + normal * width
                drawing.add(Line(tuple(hinge), tuple(leaf_end), Style(stroke=PALETTE["door_swing"], width=0.012)))
                start_deg = float(np.degrees(np.arctan2((b - hinge)[1], (b - hinge)[0])))
                # The swing is the quarter turn from the closed leaf to the open one. Taken as the smaller
                # and larger of the two angles, a pair either side of +-180 degrees drew nearly a full circle.
                sweep = (float(np.degrees(np.arctan2(normal[1], normal[0]))) - start_deg + 180.0) % 360.0 - 180.0
                if sweep < 0:
                    start_deg, sweep = start_deg + sweep, -sweep
                end_deg = start_deg + sweep
                drawing.add(
                    Arc(tuple(hinge), width, start_deg, end_deg,
                        Style(stroke=PALETTE["door_swing"], width=0.010, dash=(0.06, 0.05)))
                )


def _draw_room_label(drawing: Drawing, room, show_intervals: bool) -> None:
    ring = np.array(room.polygon)
    centre = _label_anchor(ring)
    name = room.label if room.label and room.label != "room" else room.room_id.replace("_", " ")
    drawing.add(Text(tuple(centre + np.array([0.0, 0.30])), name.title(), size=13, weight="bold"))
    drawing.add(
        Text(tuple(centre + np.array([0.0, 0.02])), f"{room.floor_area.value:.2f} m2", size=11)
    )
    if room.ceiling_height is not None and room.ceiling_height.value > 0:
        text = f"h {room.ceiling_height.value:.3f} m"
        if show_intervals:
            text += f"  [{room.ceiling_height.lo:.3f}, {room.ceiling_height.hi:.3f}]"
        drawing.add(Text(tuple(centre + np.array([0.0, -0.26])), text, size=9, colour=PALETTE["muted"]))
    else:
        drawing.add(
            Text(tuple(centre + np.array([0.0, -0.26])), "ceiling unmeasured", size=9, colour=PALETTE["flag"])
        )


def _label_anchor(ring: np.ndarray) -> np.ndarray:
    """A point inside the room to hang the label on.

    The centroid of a concave room can land outside it, which puts the label in the
    neighbour. Falling back to the widest interior span keeps it in the right room.
    """
    from shapely.geometry import Polygon as ShapelyPolygon

    poly = ShapelyPolygon(ring)
    if not poly.is_valid:
        poly = poly.buffer(0)
    if poly.is_empty:
        return ring.mean(axis=0)
    point = poly.centroid
    if not poly.contains(point):
        point = poly.representative_point()
    return np.array([point.x, point.y])


def _draw_dimensions(drawing: Drawing, room, show_intervals: bool) -> None:
    ring = np.array(room.polygon)
    centre = ring.mean(axis=0)
    for wall in room.walls:
        start, end = np.array(wall.start), np.array(wall.end)
        length = float(np.linalg.norm(end - start))
        if length < 0.55:
            continue
        midpoint = 0.5 * (start + end)
        direction = (end - start) / length
        normal = np.array([-direction[1], direction[0]])
        if (centre - midpoint) @ normal < 0:
            normal = -normal
        position = midpoint + normal * 0.20
        angle = float(np.degrees(np.arctan2(direction[1], direction[0])))
        if angle > 90 or angle < -90:
            angle += 180
        label = f"{wall.length.value:.2f}"
        if show_intervals and wall.length.half_width > 0:
            label += f" ±{wall.length.half_width * 100:.1f}cm"
        drawing.add(
            Text(tuple(position), label, size=8.5, colour=PALETTE["muted"], rotation_deg=angle)
        )


def _draw_damage(drawing: Drawing, plan: PropertyPlan) -> None:
    rooms = {r.room_id: r for r in plan.rooms}
    for damage in plan.damage:
        room = rooms.get(damage.room_id)
        if room is None:
            continue
        surface = next((s for s in room.surfaces if s.surface_id == damage.surface_id), None)
        anchor = _damage_anchor(room, surface, damage)
        radius = max(0.14, min(0.42, float(np.sqrt(max(damage.extent.value, 0.01) / np.pi))))
        drawing.add(
            Circle(tuple(anchor), radius,
                   Style(fill=PALETTE["damage_fill"], stroke=PALETTE["damage"], width=0.018, opacity=0.85))
        )
        drawing.add(
            Text(tuple(anchor + np.array([0.0, radius + 0.13])),
                 damage.damage_class.value.replace("_", " "), size=8, colour=PALETTE["damage"])
        )


def _damage_anchor(room, surface, damage) -> np.ndarray:
    ring = np.array(room.polygon)
    if surface is None:
        return _label_anchor(ring)
    wall = next((w for w in room.walls if w.surface_id == surface.surface_id), None)
    if wall is None:
        return _label_anchor(ring)
    start, end = np.array(wall.start), np.array(wall.end)
    length = float(np.linalg.norm(end - start))
    direction = (end - start) / max(length, 1e-9)
    normal = np.array([-direction[1], direction[0]])
    if (ring.mean(axis=0) - 0.5 * (start + end)) @ normal < 0:
        normal = -normal
    along = float(np.clip(0.5 * (damage.bbox_on_surface[0] + damage.bbox_on_surface[2]), 0.0, length))
    return start + direction * along + normal * 0.30


def _draw_adjacency(drawing: Drawing, plan: PropertyPlan) -> None:
    """A dashed line only for rooms connected without a drawn opening; a doorway already shows it."""
    centres = {r.room_id: _label_anchor(np.array(r.polygon)) for r in plan.rooms}
    for link in plan.adjacency:
        if link.opening_a and link.opening_b:
            continue
        a, b = centres.get(link.room_a), centres.get(link.room_b)
        if a is None or b is None:
            continue
        drawing.add(
            Line(tuple(a), tuple(b),
                 Style(stroke=PALETTE["muted"], width=0.008, dash=(0.10, 0.10), opacity=0.5))
        )


def render_svg(plan: PropertyPlan, artifacts: Optional[Any] = None) -> str:
    from cozmo.render.backends import to_svg
    drawing = build_plan_drawing(plan, show_intervals=True)
    title = f"Floor Plan - Capture {plan.capture_id}"
    subtitle = f"Tier: {plan.tier.value.upper()} | Total Floor Area: {plan.total_floor_area.value:.2f} m²"
    return to_svg(drawing, title, subtitle)


def save_plan_image(plan: PropertyPlan, artifacts: Optional[Any], out_path: Path) -> None:
    from cozmo.render.backends import to_png
    svg_content = render_svg(plan, artifacts)
    svg_file = out_path.with_suffix(".svg")
    svg_file.write_text(svg_content)
    if out_path.suffix.lower() == ".png":
        drawing = build_plan_drawing(plan, show_intervals=True)
        title = f"Floor Plan - Capture {plan.capture_id}"
        subtitle = f"Tier: {plan.tier.value.upper()} | Total Floor Area: {plan.total_floor_area.value:.2f} m²"
        to_png(drawing, out_path, title, subtitle)

