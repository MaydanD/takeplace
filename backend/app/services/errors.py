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


class ScheduleConflictError(ServiceError):
    """Adjacent shifts overlap, so the schedule change is rejected (§5.4)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


class HallNotFoundError(ServiceError):
    """A hall is not visible to this tenant (wrong venue -> 404, §7.1)."""

    def __init__(self, hall_id: int) -> None:
        super().__init__(f"hall {hall_id} was not found")
        self.hall_id = hall_id


class TableNotFoundError(ServiceError):
    """A table is not visible to this tenant (wrong venue -> 404, §7.1)."""

    def __init__(self, table_id: int) -> None:
        super().__init__(f"table {table_id} was not found")
        self.table_id = table_id


class HallArchiveBlockedError(ServiceError):
    """A hall cannot be archived while it still has non-archived tables (§29.5)."""

    def __init__(self, active_tables: int) -> None:
        super().__init__(
            f"hall cannot be archived while it has {active_tables} non-archived table(s)"
        )
        self.active_tables = active_tables


class TableArchiveBlockedError(ServiceError):
    """A table cannot be archived while it has live/future occupancy (§29.3)."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"table cannot be archived: {reason}")
        self.reason = reason


class TableNumberTakenError(ServiceError):
    """A non-archived table with this number already exists in the hall (§6.5)."""

    def __init__(self, number: str) -> None:
        super().__init__(f"table number {number!r} is already used in this hall")
        self.number = number


class BookingNotFoundError(ServiceError):
    """A booking is not visible to this tenant (wrong venue -> 404, §7.1)."""

    def __init__(self, booking_id: int) -> None:
        super().__init__(f"booking {booking_id} was not found")
        self.booking_id = booking_id


class BookingRuleViolationError(ServiceError):
    """A booking payload violates a domain rule (interval/grid/shift/horizon, §18)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class BookingConflictError(ServiceError):
    """Another active occupancy overlaps the requested table intervals (§12)."""

    def __init__(self, conflicting_booking_ids: list[int] | None = None) -> None:
        super().__init__("the requested tables are already reserved for that interval")
        self.conflicting_booking_ids = list(conflicting_booking_ids or [])


class BookingStaleError(ServiceError):
    """``expected_version`` does not match the stored booking version (§33)."""

    def __init__(self, booking_id: int) -> None:
        super().__init__(f"booking {booking_id} was modified by another operation")
        self.booking_id = booking_id


class BookingInvalidStateError(ServiceError):
    """The requested transition is not allowed from the booking's status (§9)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


class TableNotBookableError(ServiceError):
    """A selected table is archived or switched off for booking (§29.2)."""

    def __init__(self, table_id: int) -> None:
        super().__init__(f"table {table_id} is not bookable")
        self.table_id = table_id


class HallNotBookableError(ServiceError):
    """A selected table's hall is switched off for booking (§29.1)."""

    def __init__(self, hall_id: int) -> None:
        super().__init__(f"hall {hall_id} is not bookable")
        self.hall_id = hall_id


class IdempotencyKeyReusedError(ServiceError):
    """The same key was sent with a different payload (§18.2, §19)."""

    def __init__(self) -> None:
        super().__init__("this Idempotency-Key was already used with a different payload")


class ScheduleChangeRequiresConfirmationError(ServiceError):
    """A schedule change would leave future bookings outside the new schedule (§5.5)."""

    def __init__(self, affected: list[dict[str, object]]) -> None:
        super().__init__(
            f"the change affects {len(affected)} future booking(s); explicit confirmation required"
        )
        self.affected = affected


class ServiceUnavailableError(ServiceError):
    """A bounded retry budget was exhausted on lock contention/timeout (§32.5)."""

    def __init__(self, message: str = "the operation could not acquire its locks in time") -> None:
        super().__init__(message)


class CapacityChangeBlockedError(ServiceError):
    """A capacity decrease would break an existing future booking's capacity (§29.4)."""

    def __init__(self, affected: list[dict[str, object]]) -> None:
        super().__init__(f"the capacity change would break {len(affected)} existing booking(s)")
        self.affected = affected
