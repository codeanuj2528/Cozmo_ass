"""Damage detection and concealed-damage rules.

The previous version of this file imported `SemanticStager`, which does not exist, taking
the whole test suite down at collection alongside `test_recon.py`.

The tests here are written against the property that matters most for this part of the
system: a detector that reports the same thing regardless of what it was shown is not a
detector. Constant output was in fact what the damage stage produced -- the same
`water_stain 0.18 m2 conf 0.92` and `crack 1.15 m2 conf 0.88` for every room of a property
with no damage in it -- and nothing in the suite objected.
"""

from __future__ import annotations

import numpy as np

from cozmo.damage.rules import RuleEngine


def test_rule_engine_ships_with_auditable_rules():
    engine = RuleEngine()
    assert engine.rules, "the rule engine must ship with rules"
    for rule in engine.rules:
        assert rule.id
        assert rule.text and len(rule.text) > 20, f"{rule.id} has no auditable rule text"
        assert rule.predicate, f"{rule.id} has no predicate and would fire unconditionally"


def test_concealed_rules_do_not_fire_without_damage():
    """A rule that fires on an empty property is not evidence of anything."""
    assert RuleEngine().evaluate_damage([]) == []


# ---------------------------------------------------------------------------
# Image detectors. These exist because the damage stage previously returned two
# hardcoded regions per room regardless of what it was shown, and no test objected.
# ---------------------------------------------------------------------------


def _wall(seed: int = 0) -> np.ndarray:
    import numpy as _np

    rng = _np.random.default_rng(seed)
    return _np.clip(
        _np.full((400, 600, 3), 225, int) + rng.integers(-6, 6, (400, 600, 3)), 0, 255
    ).astype(_np.uint8)


def _draw_crack(image: np.ndarray, start, end, colour, thickness: int) -> None:
    """A crack between two points that wanders either side of the straight line, as real ones do."""
    import cv2

    wander = np.array([0, 5, -3, 7, 2, -6, -2, 4, 8, 1, -5, 3, 0], dtype=float)
    t = np.linspace(0.0, 1.0, len(wander))[:, None]
    a, b = np.array(start, dtype=float), np.array(end, dtype=float)
    normal = np.array([a[1] - b[1], b[0] - a[0]]) / np.linalg.norm(b - a)
    points = a + t * (b - a) + wander[:, None] * normal
    cv2.polylines(image, [np.round(points).astype(np.int32).reshape(-1, 1, 2)], False, colour, thickness)


def test_clean_wall_yields_no_damage():
    """The property this module most needs: silence when there is nothing there."""
    from cozmo.damage.detect import detect_cracks, detect_water_stains

    wall = _wall()
    assert detect_water_stains(wall) == []
    assert detect_cracks(wall) == []


def test_stain_is_found_and_is_not_called_a_crack():
    import cv2

    from cozmo.damage.detect import detect_cracks, detect_water_stains

    stained = _wall()
    cv2.ellipse(stained, (300, 180), (70, 45), 0, 0, 360, (150, 120, 70), -1)
    stained = cv2.GaussianBlur(stained, (31, 31), 0)

    stains = detect_water_stains(stained)
    assert len(stains) == 1
    assert stains[0].score > 0.3
    assert detect_cracks(stained) == []


def test_crack_is_found_and_is_not_called_a_stain():
    from cozmo.damage.detect import detect_cracks, detect_water_stains

    cracked = _wall()
    _draw_crack(cracked, (80, 300), (520, 320), (40, 40, 40), 2)

    assert len(detect_cracks(cracked)) == 1
    assert detect_water_stains(cracked) == []


def test_tile_grout_is_not_reported_as_cracks():
    """Grout lines are thin, dark, straight and long, which is also what a crack is.

    What separates them is that grout comes in a repeating parallel family and a crack
    generally does not. Without this the detector fired on every tiled bathroom wall in the
    sample property.
    """
    import cv2

    from cozmo.damage.detect import detect_cracks

    tiled = _wall()
    for x in range(60, 600, 90):
        cv2.line(tiled, (x, 0), (x, 400), (150, 150, 150), 2)
    for y in range(60, 400, 90):
        cv2.line(tiled, (0, y), (600, y), (150, 150, 150), 2)

    assert detect_cracks(tiled) == []


def test_a_crack_across_tiles_still_survives_the_grout_filter():
    """Rejecting the family must not reject the one feature that is not part of it."""
    import cv2

    from cozmo.damage.detect import detect_cracks

    tiled = _wall()
    for x in range(60, 600, 90):
        cv2.line(tiled, (x, 0), (x, 400), (150, 150, 150), 2)
    for y in range(60, 400, 90):
        cv2.line(tiled, (0, y), (600, y), (150, 150, 150), 2)
    _draw_crack(tiled, (90, 340), (500, 120), (30, 30, 30), 3)

    found = detect_cracks(tiled)
    # Where the crack crosses a grout line its ridge response dips, so it can come back in pieces.
    # Every piece must lie on the crack, and none may be a grout line running across the image.
    assert found, "the diagonal crack must survive while the grid is suppressed"
    assert all(80 <= d.bbox[0] and d.bbox[2] <= 510 and 110 <= d.bbox[1] and d.bbox[3] <= 350 for d in found)


def test_detector_never_claims_a_conformal_interval_it_has_not_fitted():
    """An uncalibrated extent must say so.

    The previous implementation stamped IntervalMethod.CONFORMAL on invented constants,
    which is the single field a reader uses to tell a calibrated interval from a guess.
    """
    from cozmo.damage.detect import build_damage_regions
    from cozmo.schema import IntervalMethod, Tier
    from cozmo.uncertainty.calibration import IntervalBook

    regions = build_damage_regions([], IntervalBook(), Tier.LIDAR, "classical")
    assert regions == []


def test_detection_in_front_of_a_wall_is_not_damage_to_it():
    """A cabinet front or a fridge edge a few centimetres off the wall is not the wall.

    On the assignment's scans the only findings that passed the two-view rule were a wooden
    vanity front 6-15 cm in front of the wall and the top edge of a fridge 9-10 cm off it.
    """
    from cozmo.damage.detect import ImageDetection, _WallView, project_detection
    from cozmo.schema import DamageClass

    k = np.array([[200.0, 0.0, 128.0], [0.0, 200.0, 96.0], [0.0, 0.0, 1.0]])
    wall = _WallView(start=np.array([-2.0, 2.0]), direction=np.array([1.0, 0.0]), normal_xz=np.array([0.0, -1.0]), length=4.0)
    detection = ImageDetection(
        frame_index=0, damage_class=DamageClass.WATER_STAIN, bbox=(800.0, 600.0, 1100.0, 840.0), score=0.8, detector="classical"
    )

    on_wall = np.full((192, 256), 2.0, dtype=np.float32)
    placed = project_detection(detection, on_wall, k, np.eye(4), (1920, 1440), [wall], {0: "room_01_s00"}, floor_y=-1.4)
    assert placed is not None and placed[0] == "room_01_s00"

    in_front = on_wall.copy()
    in_front[70:125, 95:160] = 1.90
    assert project_detection(detection, in_front, k, np.eye(4), (1920, 1440), [wall], {0: "room_01_s00"}, floor_y=-1.4) is None


def test_straight_edge_on_a_wall_is_not_a_crack():
    """The edge of a picture frame is thin, dark and long, and dead straight.

    On the assignment's floor-only scan the lower edge of a picture frame was reported as a
    crack seen from two frames. Its centreline stayed within 1.4 px of a straight line over
    471 px.
    """
    import cv2

    from cozmo.damage.detect import detect_cracks

    edged = _wall()
    cv2.line(edged, (80, 300), (520, 320), (40, 40, 40), 2)
    assert detect_cracks(edged) == []


def test_two_sightings_are_one_finding_only_on_the_same_patch_of_wall():
    """Seen twice has to mean the same place seen twice.

    On the first home walk the rim of a toilet lid and the edge of its seat, 15 cm apart on
    the wall, were joined into one crack corroborated by two frames.
    """
    from cozmo.damage.detect import ImageDetection, build_damage_regions
    from cozmo.schema import DamageClass, Tier
    from cozmo.uncertainty.calibration import IntervalBook

    def sighting(frame: int, u: float):
        uv = np.array([[u, 0.70], [u + 0.06, 0.76]])
        world = np.array([[u, 0.70, 2.0], [u + 0.06, 0.76, 2.0]])
        detection = ImageDetection(
            frame_index=frame, damage_class=DamageClass.CRACK, bbox=(0.0, 0.0, 10.0, 10.0), score=0.6, detector="classical"
        )
        return "room_03", "room_03_s01", uv, world, detection

    apart = build_damage_regions([sighting(4045, 2.13), sighting(4175, 2.33)], IntervalBook(), Tier.LIDAR, "classical")
    assert apart == []
    together = build_damage_regions([sighting(4045, 2.13), sighting(4175, 2.15)], IntervalBook(), Tier.LIDAR, "classical")
    assert len(together) == 1 and together[0].evidence_frames == [4045, 4175]
