"""Layout geometry contract: table shapes and static elements (PROJECT-SPEC §6.4-6.5, §30.3).

The same geometry model is used by the admin canvas, the JSON/CLI import and,
later, the public canvas (§30.1). It is intentionally the spec's field set only —
position (``x``/``y``), size (``width``/``height``), ``rotation``, ``z_index``,
``shape`` — with no extra layout properties.

Static elements are a Pydantic **discriminated union** limited to the types the
spec names (wall / stage / bar / text / zone). Arbitrary HTML, JS and raw SVG are
impossible by construction: the models forbid unknown fields and expose no
markup or script properties (§30.3).
"""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, field_validator

# Table shapes the renderer understands. The spec leaves ``shape`` free text, so
# this is our deterministic contract, pinned by a DB CHECK (§44 "enum-like").
VALID_TABLE_SHAPES: tuple[str, ...] = ("rect", "circle")

# Static-element limits (§30.3: a cap on element count and total JSON size).
MAX_STATIC_ELEMENTS = 200
MAX_STATIC_ELEMENTS_JSON_BYTES = 65_536

# Canvas and geometry ranges. Table/canvas defaults are the same for the CLI
# onboarding default hall.
MIN_CANVAS_SIZE = 1
DEFAULT_CANVAS_WIDTH = 1200
DEFAULT_CANVAS_HEIGHT = 800
DEFAULT_HALL_NAME = "Основной зал"

ROTATION_MIN = 0
ROTATION_MAX = 360

STATIC_ELEMENT_TYPES: tuple[str, ...] = ("wall", "stage", "bar", "text", "zone")


class LayoutValidationError(ValueError):
    """A layout payload violates the geometry contract (surfaces as 422)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class _StaticBase(BaseModel):
    # ``extra="forbid"`` rejects anything outside the contract, which is how we
    # guarantee no HTML/JS/SVG markup can be smuggled into static_elements.
    model_config = ConfigDict(extra="forbid")

    x: float = Field(ge=0)
    y: float = Field(ge=0)
    rotation: float = Field(default=0, ge=ROTATION_MIN, le=ROTATION_MAX)
    z_index: int = 0


class WallElement(_StaticBase):
    type: Literal["wall"]
    width: float = Field(gt=0)
    height: float = Field(gt=0)


class StageElement(_StaticBase):
    type: Literal["stage"]
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    label: str | None = Field(default=None, max_length=100)


class BarElement(_StaticBase):
    type: Literal["bar"]
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    label: str | None = Field(default=None, max_length=100)


class ZoneElement(_StaticBase):
    type: Literal["zone"]
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    label: str | None = Field(default=None, max_length=100)


class TextElement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["text"]
    x: float = Field(ge=0)
    y: float = Field(ge=0)
    text: str = Field(min_length=1, max_length=200)
    font_size: float = Field(default=14, gt=0, le=200)
    rotation: float = Field(default=0, ge=ROTATION_MIN, le=ROTATION_MAX)
    z_index: int = 0


StaticElement: TypeAlias = Annotated[
    WallElement | StageElement | BarElement | ZoneElement | TextElement,
    Field(discriminator="type"),
]

_STATIC_ELEMENT_ADAPTER: TypeAdapter[StaticElement] = TypeAdapter(StaticElement)


def _enforce_static_limits(parsed: list[StaticElement]) -> list[dict[str, Any]]:
    """Enforce the §30.3 element-count and total-JSON-size limits."""
    if len(parsed) > MAX_STATIC_ELEMENTS:
        raise LayoutValidationError(
            f"too many static elements: {len(parsed)} > {MAX_STATIC_ELEMENTS}"
        )
    as_dicts = [element.model_dump(mode="json") for element in parsed]
    encoded = json.dumps(as_dicts, ensure_ascii=False).encode()
    if len(encoded) > MAX_STATIC_ELEMENTS_JSON_BYTES:
        raise LayoutValidationError(
            f"static elements exceed {MAX_STATIC_ELEMENTS_JSON_BYTES} bytes of JSON"
        )
    return as_dicts


def parse_static_elements(raw: list[Any]) -> list[dict[str, Any]]:
    """Validate a list of raw static-element dicts and return JSON dicts."""
    parsed: list[StaticElement] = []
    for index, item in enumerate(raw):
        try:
            parsed.append(_STATIC_ELEMENT_ADAPTER.validate_python(item))
        except ValidationError as exc:
            raise LayoutValidationError(
                f"static_elements[{index}] is invalid: {exc.errors()[0]['msg']}"
            ) from exc
    return _enforce_static_limits(parsed)


def validate_canvas(width: int, height: int) -> None:
    """Validate hall canvas dimensions."""
    if width < MIN_CANVAS_SIZE or height < MIN_CANVAS_SIZE:
        raise LayoutValidationError(
            f"canvas size must be at least {MIN_CANVAS_SIZE}x{MIN_CANVAS_SIZE}"
        )


def validate_table_geometry(
    *,
    shape: str,
    x: float,
    y: float,
    width: float,
    height: float,
    rotation: float,
    capacity: int,
) -> None:
    """Validate table geometry against the contract (mirrors the DB CHECKs).

    The DB remains the last arbiter; this gives a clear 422 before the write.
    """
    if shape not in VALID_TABLE_SHAPES:
        raise LayoutValidationError(
            f"shape must be one of {list(VALID_TABLE_SHAPES)}, got {shape!r}"
        )
    if capacity <= 0:
        raise LayoutValidationError("capacity must be greater than 0")
    if width <= 0 or height <= 0:
        raise LayoutValidationError("width and height must be greater than 0")
    if x < 0 or y < 0:
        raise LayoutValidationError("x and y must not be negative")
    if not ROTATION_MIN <= rotation <= ROTATION_MAX:
        raise LayoutValidationError(f"rotation must be between {ROTATION_MIN} and {ROTATION_MAX}")


# --- JSON / CLI import contract (§63 Stage 4) -------------------------------
#
# A layout file lists halls; each hall lists its tables and static elements.
# Unknown fields are rejected so a typo cannot silently drop a field. Import is
# an upsert (hall by name, table by number), never a destructive replace.


class TableSpec(BaseModel):
    """One table in an imported layout."""

    model_config = ConfigDict(extra="forbid")

    number: str = Field(min_length=1, max_length=50)
    capacity: int = Field(gt=0, le=100_000)
    shape: str
    x: float = Field(ge=0)
    y: float = Field(ge=0)
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    rotation: float = Field(default=0, ge=ROTATION_MIN, le=ROTATION_MAX)
    z_index: int = 0
    is_bookable: bool = True

    @field_validator("shape")
    @classmethod
    def _shape_allowed(cls, value: str) -> str:
        if value not in VALID_TABLE_SHAPES:
            raise ValueError(f"shape must be one of {list(VALID_TABLE_SHAPES)}")
        return value

    def geometry(self) -> dict[str, float]:
        return {
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
            "rotation": self.rotation,
        }


class HallSpec(BaseModel):
    """One hall (canvas) in an imported layout."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    canvas_width: int = Field(ge=MIN_CANVAS_SIZE, le=1_000_000)
    canvas_height: int = Field(ge=MIN_CANVAS_SIZE, le=1_000_000)
    is_bookable: bool = True
    static_elements: list[StaticElement] = Field(default_factory=list)
    tables: list[TableSpec] = Field(default_factory=list)

    def static_elements_json(self) -> list[dict[str, Any]]:
        return _enforce_static_limits(self.static_elements)


class LayoutImport(BaseModel):
    """A complete layout file: one or more halls, each with tables/elements."""

    model_config = ConfigDict(extra="forbid")

    halls: list[HallSpec] = Field(min_length=1, max_length=50)


def validate_layout_import(raw: Any) -> LayoutImport:
    """Validate a raw layout payload, raising ``LayoutValidationError``.

    The whole payload is validated before the service writes anything, so a bad
    file can never leave a partial layout behind.
    """
    try:
        layout = LayoutImport.model_validate(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        location = ".".join(str(part) for part in first["loc"]) or "<root>"
        raise LayoutValidationError(f"{location}: {first['msg']}") from exc

    seen_halls: set[str] = set()
    for hall in layout.halls:
        if hall.name in seen_halls:
            raise LayoutValidationError(f"duplicate hall name {hall.name!r} in layout")
        seen_halls.add(hall.name)
        hall.static_elements_json()  # enforce count/size limits up front
        seen_numbers: set[str] = set()
        for table in hall.tables:
            validate_table_geometry(
                shape=table.shape,
                x=table.x,
                y=table.y,
                width=table.width,
                height=table.height,
                rotation=table.rotation,
                capacity=table.capacity,
            )
            if table.number in seen_numbers:
                raise LayoutValidationError(
                    f"duplicate table number {table.number!r} in hall {hall.name!r}"
                )
            seen_numbers.add(table.number)
    return layout
