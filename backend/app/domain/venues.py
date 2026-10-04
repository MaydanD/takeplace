"""Venue slug rules (PROJECT-SPEC §6.1).

Slugs are lowercase, limited to ``a-z``, ``0-9`` and ``-``, between 2 and 40
characters, and globally unique. A fixed list of route-like slugs is reserved so
a venue can never shadow an application path.
"""

from __future__ import annotations

import re

SLUG_MIN_LENGTH = 2
SLUG_MAX_LENGTH = 40
SLUG_PATTERN = re.compile(r"^[a-z0-9-]{2,40}$")

RESERVED_SLUGS: frozenset[str] = frozenset(
    {
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
)


class InvalidSlugError(ValueError):
    """Raised when a venue slug violates the naming rules."""

    def __init__(self, slug: str, reason: str) -> None:
        super().__init__(f"invalid venue slug {slug!r}: {reason}")
        self.slug = slug
        self.reason = reason


def validate_slug(slug: str) -> str:
    """Return ``slug`` if it is valid, raising ``InvalidSlugError`` otherwise."""
    if not SLUG_PATTERN.match(slug):
        raise InvalidSlugError(
            slug,
            f"must match {SLUG_PATTERN.pattern} "
            f"({SLUG_MIN_LENGTH}-{SLUG_MAX_LENGTH} chars, lowercase a-z 0-9 -)",
        )
    if slug in RESERVED_SLUGS:
        raise InvalidSlugError(slug, "is a reserved application path")
    return slug
