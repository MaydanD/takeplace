"""Unit tests for the layout geometry contract (PROJECT-SPEC §6.4-6.5, §30.3)."""

from __future__ import annotations

from typing import Any

import pytest
from app.domain.layout import (
    MAX_STATIC_ELEMENTS,
    LayoutValidationError,
    parse_static_elements,
    validate_canvas,
    validate_layout_import,
    validate_table_geometry,
)


def test_validate_canvas_accepts_positive_sizes() -> None:
    validate_canvas(1200, 800)


@pytest.mark.parametrize("width,height", [(0, 800), (1200, 0), (-1, 800)])
def test_validate_canvas_rejects_non_positive(width: int, height: int) -> None:
    with pytest.raises(LayoutValidationError):
        validate_canvas(width, height)


def _geometry(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "shape": "rect",
        "x": 10.0,
        "y": 10.0,
        "width": 80.0,
        "height": 80.0,
        "rotation": 0.0,
        "capacity": 4,
    }
    base.update(overrides)
    return base


def test_valid_table_geometry() -> None:
    validate_table_geometry(**_geometry())  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "overrides",
    [
        {"shape": "triangle"},
        {"capacity": 0},
        {"width": 0},
        {"height": -5},
        {"x": -1},
        {"y": -1},
        {"rotation": 361},
        {"rotation": -1},
    ],
)
def test_invalid_table_geometry_is_rejected(overrides: dict[str, object]) -> None:
    with pytest.raises(LayoutValidationError):
        validate_table_geometry(**_geometry(**overrides))  # type: ignore[arg-type]


def test_static_elements_each_type_round_trips() -> None:
    raw = [
        {"type": "wall", "x": 0, "y": 0, "width": 100, "height": 10},
        {"type": "stage", "x": 10, "y": 10, "width": 100, "height": 50, "label": "Сцена"},
        {"type": "bar", "x": 10, "y": 10, "width": 100, "height": 50, "label": "Бар"},
        {"type": "zone", "x": 10, "y": 10, "width": 100, "height": 50, "label": "VIP"},
        {"type": "text", "x": 5, "y": 5, "text": "Вход", "font_size": 18},
    ]
    parsed = parse_static_elements(raw)
    assert [element["type"] for element in parsed] == ["wall", "stage", "bar", "zone", "text"]
    assert parsed[4]["font_size"] == 18


def test_static_element_unknown_field_is_rejected() -> None:
    # No arbitrary HTML/JS/SVG markup can be smuggled in (§30.3).
    with pytest.raises(LayoutValidationError):
        parse_static_elements(
            [{"type": "wall", "x": 0, "y": 0, "width": 10, "height": 10, "html": "<b>x</b>"}]
        )


def test_unknown_static_element_type_is_rejected() -> None:
    with pytest.raises(LayoutValidationError):
        parse_static_elements([{"type": "hologram", "x": 0, "y": 0}])


def test_sized_element_requires_dimensions() -> None:
    with pytest.raises(LayoutValidationError):
        parse_static_elements([{"type": "wall", "x": 0, "y": 0}])


def test_static_element_count_limit() -> None:
    too_many = [{"type": "text", "x": 0, "y": 0, "text": "x"}] * (MAX_STATIC_ELEMENTS + 1)
    with pytest.raises(LayoutValidationError):
        parse_static_elements(too_many)


def _layout(**hall_overrides: object) -> dict[str, Any]:
    hall: dict[str, Any] = {
        "name": "Main",
        "canvas_width": 800,
        "canvas_height": 600,
        "tables": [],
        "static_elements": [],
    }
    hall.update(hall_overrides)
    return {"halls": [hall]}


def test_validate_layout_import_accepts_valid_payload() -> None:
    layout = validate_layout_import(
        _layout(
            tables=[
                {
                    "number": "1",
                    "capacity": 4,
                    "shape": "rect",
                    "x": 0,
                    "y": 0,
                    "width": 60,
                    "height": 60,
                }
            ]
        )
    )
    assert layout.halls[0].tables[0].number == "1"


def test_validate_layout_import_rejects_duplicate_hall_names() -> None:
    payload = {"halls": [_layout()["halls"][0], _layout()["halls"][0]]}
    with pytest.raises(LayoutValidationError, match="duplicate hall name"):
        validate_layout_import(payload)


def test_validate_layout_import_rejects_duplicate_table_numbers() -> None:
    table = {
        "number": "1",
        "capacity": 4,
        "shape": "rect",
        "x": 0,
        "y": 0,
        "width": 60,
        "height": 60,
    }
    with pytest.raises(LayoutValidationError, match="duplicate table number"):
        validate_layout_import(_layout(tables=[table, dict(table)]))


def test_validate_layout_import_rejects_unknown_field() -> None:
    with pytest.raises(LayoutValidationError):
        validate_layout_import({"halls": [dict(_layout()["halls"][0], venue_id=5)]})


def test_validate_layout_import_rejects_empty_halls() -> None:
    with pytest.raises(LayoutValidationError):
        validate_layout_import({"halls": []})


def test_validate_layout_import_rejects_invalid_table_geometry() -> None:
    with pytest.raises(LayoutValidationError):
        validate_layout_import(
            _layout(
                tables=[
                    {
                        "number": "1",
                        "capacity": 0,
                        "shape": "rect",
                        "x": 0,
                        "y": 0,
                        "width": 60,
                        "height": 60,
                    }
                ]
            )
        )
