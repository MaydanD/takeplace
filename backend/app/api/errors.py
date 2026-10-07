"""Machine-readable API errors (PROJECT-SPEC §36).

The frontend branches on the ``code`` field, never on the human-readable
``detail``. The ``detail`` is safe for display and must never contain secrets.
"""

from __future__ import annotations

from typing import cast

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class ApiError(Exception):
    """An expected error with a stable machine-readable code.

    ``extra`` carries optional, tenant-scoped structured context (for example
    the affected bookings of a blocked schedule/archive/capacity change, §36).
    """

    def __init__(
        self, status_code: int, code: str, detail: str, extra: dict[str, object] | None = None
    ) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.code = code
        self.detail = detail
        self.extra = extra or {}


async def api_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    # Registered only for ApiError; the cast keeps the Starlette handler
    # signature without a runtime assertion.
    error = cast(ApiError, exc)
    headers: dict[str, str] = {}
    retry_after = getattr(error, "retry_after_seconds", None)
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    content: dict[str, object] = {"code": error.code, "detail": error.detail}
    if error.extra:
        content.update(error.extra)
    return JSONResponse(status_code=error.status_code, content=content, headers=headers)


def unauthenticated() -> ApiError:
    return ApiError(401, "UNAUTHENTICATED", "authentication required")


def invalid_credentials() -> ApiError:
    # Deliberately identical for unknown login and wrong password (§39.5).
    return ApiError(401, "INVALID_CREDENTIALS", "invalid login or password")


def rate_limited(retry_after_seconds: int) -> ApiError:
    error = ApiError(429, "RATE_LIMITED", "too many requests")
    error.retry_after_seconds = retry_after_seconds  # type: ignore[attr-defined]
    return error


def schedule_invalid(detail: str) -> ApiError:
    """A schedule row violates the grid / consistency rules (§5.2, §5.3)."""
    return ApiError(422, "SCHEDULE_INVALID", detail)


def schedule_overlap(detail: str) -> ApiError:
    """Adjacent shifts overlap, so the change is rejected (§5.4)."""
    return ApiError(409, "SCHEDULE_OVERLAP", detail)


def not_found(code: str, detail: str) -> ApiError:
    return ApiError(404, code, detail)


def layout_invalid(detail: str) -> ApiError:
    """A hall/table/static-element payload violates the geometry contract."""
    return ApiError(422, "LAYOUT_INVALID", detail)


def hall_archive_blocked(detail: str) -> ApiError:
    """A hall still has non-archived tables (§29.5)."""
    return ApiError(409, "HALL_ARCHIVE_BLOCKED", detail)


def table_archive_blocked(detail: str) -> ApiError:
    """A table still has live/future occupancy (§29.3)."""
    return ApiError(409, "TABLE_ARCHIVE_BLOCKED", detail)


def table_number_taken(detail: str) -> ApiError:
    """A non-archived table already uses this number in the hall (§6.5)."""
    return ApiError(409, "TABLE_NUMBER_TAKEN", detail)


def booking_rule_violation(detail: str) -> ApiError:
    """A booking payload violates the interval/grid/shift/horizon rules (§18)."""
    return ApiError(422, "BOOKING_RULE_VIOLATION", detail)


def booking_conflict(detail: str, conflicting_booking_ids: list[int]) -> ApiError:
    """Another active occupancy overlaps the requested interval (§12, §36)."""
    return ApiError(
        409, "BOOKING_CONFLICT", detail, {"conflicting_booking_ids": conflicting_booking_ids}
    )


def booking_stale(detail: str) -> ApiError:
    """``expected_version`` does not match the stored booking version (§33)."""
    return ApiError(409, "BOOKING_STALE", detail)


def layout_stale(detail: str, *, expected_revision: int, layout_revision: int) -> ApiError:
    """``expected_revision`` does not match the stored hall layout revision (§31)."""
    return ApiError(
        409,
        "LAYOUT_STALE",
        detail,
        {"expected_revision": expected_revision, "layout_revision": layout_revision},
    )


def booking_invalid_state(detail: str) -> ApiError:
    """The booking is not in a state that allows the requested operation (§9)."""
    return ApiError(409, "BOOKING_INVALID_STATE", detail)


def table_not_bookable(detail: str) -> ApiError:
    """A selected table is archived or switched off (§29.2)."""
    return ApiError(409, "TABLE_NOT_BOOKABLE", detail)


def hall_not_bookable(detail: str) -> ApiError:
    """A selected table's hall is switched off (§29.1)."""
    return ApiError(409, "HALL_NOT_BOOKABLE", detail)


def idempotency_key_reused(detail: str) -> ApiError:
    """The same Idempotency-Key was sent with a different payload (§18.2)."""
    return ApiError(409, "IDEMPOTENCY_KEY_REUSED", detail)


def schedule_change_requires_confirmation(
    detail: str, affected: list[dict[str, object]]
) -> ApiError:
    """A schedule change would strand future bookings; explicit confirmation (§5.5)."""
    return ApiError(
        409, "SCHEDULE_CHANGE_REQUIRES_CONFIRMATION", detail, {"affected_bookings": affected}
    )


def capacity_change_blocked(detail: str, affected: list[dict[str, object]]) -> ApiError:
    """A capacity decrease would break an existing future booking (§29.4)."""
    return ApiError(409, "CAPACITY_CHANGE_BLOCKED", detail, {"affected_bookings": affected})


def service_unavailable(detail: str) -> ApiError:
    """A bounded retry budget was exhausted (lock timeout, §32.5)."""
    return ApiError(503, "SERVICE_UNAVAILABLE", detail)


def online_booking_disabled() -> ApiError:
    """The venue kill switch is off (§32.2, §54.11a)."""
    return ApiError(409, "ONLINE_BOOKING_DISABLED", "online booking is disabled for this venue")


def public_rate_limited(retry_after_seconds: int) -> ApiError:
    """Public create hit the soft create rate limit (§40)."""
    error = ApiError(429, "RATE_LIMITED", "too many public booking requests")
    error.retry_after_seconds = retry_after_seconds  # type: ignore[attr-defined]
    return error


def public_availability_rate_limited(retry_after_seconds: int) -> ApiError:
    """Public availability read hit the soft read rate limit (§40)."""
    error = ApiError(429, "RATE_LIMITED", "too many public availability requests")
    error.retry_after_seconds = retry_after_seconds  # type: ignore[attr-defined]
    return error


def captcha_required() -> ApiError:
    """CAPTCHA is enabled and the request did not pass it (§50)."""
    return ApiError(400, "CAPTCHA_REQUIRED", "captcha verification is required")


def honeypot_filled() -> ApiError:
    """The hidden honeypot field was filled (§40, §50)."""
    return ApiError(400, "HONEYPOT_FILLED", "invalid submission")


async def safe_validation_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    """Never echo submitted secrets/PII in request-validation responses."""
    error = cast(RequestValidationError, exc)
    return JSONResponse(
        status_code=422,
        content={
            "detail": [
                {"type": item["type"], "loc": item["loc"], "msg": item["msg"]}
                for item in error.errors()
            ]
        },
    )
