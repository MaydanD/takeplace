"""Domain errors raised by the service layer.

These are transport-agnostic: the API layer maps them to HTTP + machine-readable
codes, and the CLI turns them into operator-facing messages.
"""

from __future__ import annotations


class ServiceError(Exception):
    """Base class for expected, user-actionable service failures."""


class SlugTakenError(ServiceError):
    def __init__(self, slug: str) -> None:
        super().__init__(f"venue slug {slug!r} is already taken")
        self.slug = slug


class LoginTakenError(ServiceError):
    def __init__(self, login: str) -> None:
        super().__init__(f"admin login {login!r} is already taken")
        self.login = login


class VenueNotFoundError(ServiceError):
    def __init__(self, identifier: str | int) -> None:
        super().__init__(f"venue {identifier!r} was not found")
        self.identifier = identifier


class AdminNotFoundError(ServiceError):
    def __init__(self, venue_id: int) -> None:
        super().__init__(f"venue {venue_id} has no admin account")
        self.venue_id = venue_id


class UnsupportedTimezoneServiceError(ServiceError):
    def __init__(self, message: str) -> None:
        super().__init__(message)
