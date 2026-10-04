"""Venue slug rules (PROJECT-SPEC §6.1)."""

from __future__ import annotations

import pytest
from app.domain.venues import RESERVED_SLUGS, InvalidSlugError, validate_slug

_SPEC_RESERVED = {
    "api",
    "admin",
    "assets",
    "static",
    "login",
    "logout",
    "health",
    "robots.txt",
    "favicon.ico",
    "privacy",
    "terms",
    "booking",
    "bookings",
    "settings",
}


def test_reserved_slugs_cover_the_spec_list() -> None:
    assert _SPEC_RESERVED <= RESERVED_SLUGS


@pytest.mark.parametrize(
    "slug",
    ["ab", "dragon", "dragon-2", "a1-b2-c3", "-dragon", "x" * 40],
)
def test_valid_slugs_are_returned(slug: str) -> None:
    # Hyphens are allowed anywhere by the spec regex, including at the edges.
    assert validate_slug(slug) == slug


@pytest.mark.parametrize(
    "slug",
    [
        "a",  # too short
        "x" * 41,  # too long
        "Dragon",  # uppercase
        "dragon_2",  # underscore
        "dragon 2",  # space
        "dragon.2",  # dot
    ],
)
def test_clearly_invalid_slugs_raise(slug: str) -> None:
    with pytest.raises(InvalidSlugError):
        validate_slug(slug)


@pytest.mark.parametrize("slug", sorted(_SPEC_RESERVED))
def test_reserved_slugs_are_rejected(slug: str) -> None:
    with pytest.raises(InvalidSlugError):
        validate_slug(slug)
