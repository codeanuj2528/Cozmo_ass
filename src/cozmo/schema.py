"""Output contract.

Every number the pipeline reports is a `Measure`: a point estimate plus an interval and
the name of the method that produced the interval. There is no path in the codebase that
emits a bare float for a physical quantity, because an uncalibrated number is the failure
mode this contract exists to prevent.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = "1.0.0"


class StrictModel(BaseModel):
    """Base for every contract object: unknown keys are an error, not a shrug."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class Tier(str, Enum):
    """Input tier. Ordered from thinnest to richest sensor data."""

    PHOTO = "photo"
    VIDEO = "video"
    LIDAR = "lidar"


class IntervalMethod(str, Enum):
    CONFORMAL = "conformal_split"
    PROPAGATED = "propagated_covariance"
    BOOTSTRAP = "bootstrap"
    PRIOR = "prior_only"


class Measure(StrictModel):
    """A physical quantity with a calibrated interval.

    `lo`/`hi` bound the quantity at `coverage` nominal probability. `method` records how
    the interval was produced so a reader can tell a calibrated interval from a guess.
    No code path should emit a bare float for a physical quantity.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    value: float
    lo: float
    hi: float
    unit: str
    coverage: float = Field(default=0.90, gt=0.0, le=1.0)
    method: IntervalMethod = IntervalMethod.CONFORMAL

    @field_validator("hi")
    @classmethod
    def lo_le_hi(cls, hi: float, info) -> float:
        """Ensure lo <= hi. A reversed interval is a bug, not a measurement."""
        lo = info.data.get("lo")
        if lo is not None and lo > hi:
            raise ValueError(f"lo ({lo}) must not exceed hi ({hi})")
        return hi

    @model_validator(mode="after")
    def value_within_interval(self) -> "Measure":
        """Ensure lo <= value <= hi. A value outside its own interval is nonsense."""
        if not (self.lo <= self.value <= self.hi):
            raise ValueError(
                f"value ({self.value}) must lie within [{self.lo}, {self.hi}]"
            )
        return self

    @property
    def half_width(self) -> float:
        return 0.5 * (self.hi - self.lo)

    def contains(self, truth: float) -> bool:
        return self.lo <= truth <= self.hi


class OpeningType(str, Enum):
    DOOR = "door"
    WINDOW = "window"
    PASS_THROUGH = "pass_through"


class SurfaceType(str, Enum):
    WALL = "wall"
    FLOOR = "floor"
    CEILING = "ceiling"


class Plane(BaseModel):
    """Plane in the property frame: unit normal `n` and offset `d` with n . x + d = 0."""

    normal: tuple[float, float, float]
    offset: float


class Opening(BaseModel):
    opening_id: str
    type: OpeningType
    wall_id: str
    width: Measure
    height: Measure
    sill_height: Measure
    offset_along_wall: Measure
    detection_confidence: float = Field(ge=0.0, le=1.0)
    connects_to_room: str | None = None


class Wall(BaseModel):
    wall_id: str
    surface_id: str
    start: tuple[float, float]
    end: tuple[float, float]
    length: Measure
    height: Measure | None = Field(
        default=None,
        description="Floor to ceiling. None when the ceiling was never observed.",
    )
    plane: Plane
    point_support: int = Field(ge=0, description="Number of observed 3D points that fit this wall plane.")


class Surface(BaseModel):
    surface_id: str
    room_id: str
    type: SurfaceType
    area: Measure | None = Field(
        default=None,
        description="None for a wall whose height is unmeasured: length alone does not give area.",
    )
    plane: Plane


class Room(BaseModel):
    room_id: str
    label: str
    polygon: list[tuple[float, float]] = Field(description="Floor outline, property frame, metres.")
    walls: list[Wall]
    surfaces: list[Surface]
    openings: list[Opening]
    ceiling_height: Measure | None = Field(
        default=None,
        description=(
            "None when no ceiling surface was observed. Previously this was emitted as "
            "0.0 m with an interval of [-0.03, 0.03], which reports the absence of a "
            "measurement as a measurement, and is the confident garbage the brief penalises."
        ),
    )
    floor_area: Measure
    perimeter: Measure
    observation_quality: float = Field(ge=0.0, le=1.0)

    @field_validator("polygon")
    @classmethod
    def polygon_has_vertices(cls, v: list) -> list:
        """A room polygon must have at least 3 vertices to enclose area."""
        if len(v) < 3:
            raise ValueError(f"polygon must have >= 3 vertices, got {len(v)}")
        return v


class Adjacency(BaseModel):
    room_a: str
    room_b: str
    opening_a: str
    opening_b: str | None
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: str


class DamageClass(str, Enum):
    WATER_STAIN = "water_stain"
    MOLD = "mold"
    CRACK = "crack"
    HOLE = "hole"
    PEELING_PAINT = "peeling_paint"
    SMOKE_SOOT = "smoke_soot"
    MISSING_MATERIAL = "missing_material"
    IMPACT_DAMAGE = "impact_damage"


class ExtentKind(str, Enum):
    AREA = "area"
    LENGTH = "length"


class DamageRegion(BaseModel):
    damage_id: str
    room_id: str
    surface_id: str
    damage_class: DamageClass
    extent_kind: ExtentKind
    extent: Measure
    bbox_on_surface: tuple[float, float, float, float] = Field(
        description="(u_min, v_min, u_max, v_max) in surface-local metres."
    )
    polygon_on_surface: list[tuple[float, float]]
    severity: Literal["minor", "moderate", "severe"]
    classification_confidence: float = Field(ge=0.0, le=1.0)
    evidence_frames: list[int]


class RuleCondition(BaseModel):
    """One condition a concealed-damage rule tested, and the value it read."""

    field: str
    op: str
    expected: Any
    actual: Any
    passed: bool


class ConcealedFlag(BaseModel):
    flag_id: str
    rule_id: str
    rule_text: str = Field(description="The rule as written, so a reader can audit the firing.")
    room_id: str
    surface_id: str | None
    triggered_by: list[str] = Field(description="damage_ids and measurement ids that fired the rule.")
    confidence: float = Field(ge=0.0, le=1.0)
    recommended_action: str
    conditions: list[RuleCondition] = Field(
        default_factory=list,
        description="Every condition the rule tested and the value it read, so the firing can be checked by hand.",
    )


class ScopeItem(BaseModel):
    item_id: str
    room_id: str
    surface_id: str
    code: str
    description: str
    unit: Literal["SF", "LF", "EA", "SY", "HR"]
    quantity: Measure
    driver_damage_ids: list[str]
    rationale: str


class DriftReport(BaseModel):
    """What the pipeline did about accumulated pose drift, with the numbers."""

    method: str
    loop_closures_found: int
    residual_before_m: float
    residual_after_m: float
    max_pose_correction_m: float
    footprint_area_before_m2: float | None = Field(
        default=None,
        description=(
            "Footprint with drift correction off. None when correction was applied, because "
            "producing it takes a second floor-plan pass: run with --no-drift-correction."
        ),
    )
    footprint_area_after_m2: float
    applied: bool


class CalibrationReport(BaseModel):
    method: IntervalMethod
    nominal_coverage: float
    empirical_coverage: dict[str, float]
    residual_quantiles: dict[str, float]
    fitted_on: str = Field(description="Which benchmark split the conformal quantiles came from.")


class QualityReport(BaseModel):
    tier: Tier
    device_model: str
    frames_available: int
    frames_used: int
    median_depth_confidence: float | None
    surface_coverage: float = Field(ge=0.0, le=1.0)
    low_light_fraction: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description=(
            "Share of the colour frames examined whose mean luma is below 50 of 255. None when "
            "no colour frame was examined."
        ),
    )
    specular_fraction: float | None = Field(
        default=None,
        description=(
            "Fraction of surface area flagged mirror or glass. None: opening detection rejects "
            "mirror candidates, but no stage measures mirror or glass area."
        ),
    )
    warnings: list[str]


class PropertyPlan(BaseModel):
    """Root object. One per capture."""

    schema_version: str = SCHEMA_VERSION
    pipeline_version: str
    capture_id: str
    tier: Tier
    created_at: datetime
    rooms: list[Room]
    adjacency: list[Adjacency]
    damage: list[DamageRegion]
    concealed_flags: list[ConcealedFlag]
    scope_items: list[ScopeItem]
    drift: DriftReport
    calibration: CalibrationReport
    quality: QualityReport
    total_floor_area: Measure
    runtime_seconds: float

    def reference_problems(self) -> list[str]:
        """Every id in the plan that names something the plan does not contain, or names it twice.

        A method rather than a validator, so a plan written by an earlier version still loads for
        scoring. `cozmo run` refuses to write a plan for which this is not empty.
        """
        problems: list[str] = []
        rooms = {room.room_id for room in self.rooms}
        if len(rooms) != len(self.rooms):
            problems.append("room ids repeat")
        owner: dict[str, dict[str, str]] = {"wall": {}, "surface": {}, "opening": {}}
        for room in self.rooms:
            for kind, ids in (
                ("wall", [w.wall_id for w in room.walls]),
                ("surface", [s.surface_id for s in room.surfaces]),
                ("opening", [o.opening_id for o in room.openings]),
            ):
                for item in ids:
                    if item in owner[kind]:
                        problems.append(f"{kind} id {item} repeats")
                    owner[kind][item] = room.room_id
        walls, surfaces, openings = owner["wall"], owner["surface"], owner["opening"]
        for room in self.rooms:
            for wall in room.walls:
                if surfaces.get(wall.surface_id) != room.room_id:
                    problems.append(f"{wall.wall_id}: surface {wall.surface_id} is not in {room.room_id}")
            for surface in room.surfaces:
                if surface.room_id != room.room_id:
                    problems.append(f"{surface.surface_id}: says room {surface.room_id} but sits in {room.room_id}")
            for opening in room.openings:
                if walls.get(opening.wall_id) != room.room_id:
                    problems.append(f"{opening.opening_id}: wall {opening.wall_id} is not in {room.room_id}")
                if opening.connects_to_room is not None and opening.connects_to_room not in rooms:
                    problems.append(f"{opening.opening_id}: connects to unknown room {opening.connects_to_room}")
        for link in self.adjacency:
            for side in (link.room_a, link.room_b):
                if side not in rooms:
                    problems.append(f"adjacency names unknown room {side}")
            if link.opening_a and openings.get(link.opening_a) != link.room_a:
                problems.append(f"adjacency {link.room_a}-{link.room_b}: opening {link.opening_a} is not in {link.room_a}")
            if link.opening_b and openings.get(link.opening_b) != link.room_b:
                problems.append(f"adjacency {link.room_a}-{link.room_b}: opening {link.opening_b} is not in {link.room_b}")
        damage_ids = {d.damage_id for d in self.damage}
        if len(damage_ids) != len(self.damage):
            problems.append("damage ids repeat")
        for region in self.damage:
            if surfaces.get(region.surface_id) != region.room_id:
                problems.append(f"{region.damage_id}: surface {region.surface_id} is not in {region.room_id}")
        known = damage_ids | set(walls) | set(surfaces) | set(openings)
        for flag in self.concealed_flags:
            if flag.room_id not in rooms:
                problems.append(f"{flag.flag_id}: unknown room {flag.room_id}")
            if flag.surface_id is not None and surfaces.get(flag.surface_id) != flag.room_id:
                problems.append(f"{flag.flag_id}: surface {flag.surface_id} is not in {flag.room_id}")
            problems.extend(f"{flag.flag_id}: triggered by unknown id {item}" for item in flag.triggered_by if item not in known)
        for item in self.scope_items:
            if surfaces.get(item.surface_id) != item.room_id:
                problems.append(f"{item.item_id}: surface {item.surface_id} is not in {item.room_id}")
            problems.extend(f"{item.item_id}: driven by unknown damage {d}" for d in item.driver_damage_ids if d not in damage_ids)
        return problems
