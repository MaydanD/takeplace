"""Machine-readable API errors (PROJECT-SPEC §36).

The frontend branches on the ``code`` field, never on the human-readable
``detail``. The ``detail`` is safe for display and must never contain secrets.
"""

from __future__ import annotations

from typing import cast

from fastapi import Request
from fastapi.responses import JSONResponse


class ApiError(Exception):
    """An expected error with a stable machine-readable code."""

    def __init__(self, status_code: int, code: str, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.code = code
        self.detail = detail


async def api_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    # Registered only for ApiError; the cast keeps the Starlette handler
    # signature without a runtime assertion.
    error = cast(ApiError, exc)
    headers: dict[str, str] = {}
    retry_after = getattr(error, "retry_after_seconds", None)
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)
    return JSONResponse(
        status_code=error.status_code,
        content={"code": error.code, "detail": error.detail},
        headers=headers,
    )


def unauthenticated() -> ApiError:
    return ApiError(401, "UNAUTHENTICATED", "authentication required")


def invalid_credentials() -> ApiError:
    # Deliberately identical for unknown login and wrong password (§39.5).
    return ApiError(401, "INVALID_CREDENTIALS", "invalid login or password")


def rate_limited(retry_after_seconds: int) -> ApiError:
    error = ApiError(429, "RATE_LIMITED", "too many requests")
    error.retry_after_seconds = retry_after_seconds  # type: ignore[attr-defined]
    return error
