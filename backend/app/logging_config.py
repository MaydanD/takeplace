"""Structured logging with a PII redaction baseline.

PROJECT-SPEC §39.7 / §42.2 forbid writing raw phone numbers, guest comments,
passwords, raw session tokens, VK tokens and raw client IPs to application logs.
Stage 1 establishes the mechanism now so later stages cannot accidentally leak
them: a processor walks every event and redacts values whose *key* looks
sensitive, plus anything matching phone / token shapes.
"""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import MutableMapping
from typing import Any

import structlog

REDACTED = "[redacted]"

# Substrings that mark a field as sensitive regardless of the surrounding event.
_SENSITIVE_KEY_MARKERS = (
    "password",
    "passwd",
    "secret",
    "token",
    "authorization",
    "cookie",
    "api_key",
    "apikey",
    "hmac",
    "phone",
    "comment",
    "note",
    "session",
    "encryption_key",
    "access_token",
)

# A value shaped like a phone number or a long opaque secret.
_PHONE_RE = re.compile(r"(?<!\w)\+?\d[\d\s().-]{7,}\d(?!\w)")
_LONG_OPAQUE_RE = re.compile(r"^[A-Za-z0-9_\-]{32,}$")

_REDACT_KEYS = {"request_ip", "client_ip", "remote_addr", "ip"}


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    if lowered in _REDACT_KEYS:
        return True
    return any(marker in lowered for marker in _SENSITIVE_KEY_MARKERS)


def _redact_value(value: Any) -> Any:
    if isinstance(value, str):
        if _PHONE_RE.search(value):
            return _PHONE_RE.sub(REDACTED, value)
        if _LONG_OPAQUE_RE.match(value):
            return REDACTED
        return value
    if isinstance(value, dict):
        return _redact_mapping(value)
    if isinstance(value, (list, tuple)):
        return type(value)(_redact_value(item) for item in value)
    return value


def _redact_mapping(mapping: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    for key in list(mapping.keys()):
        if _is_sensitive_key(key):
            mapping[key] = REDACTED
        else:
            mapping[key] = _redact_value(mapping[key])
    return mapping


def redact_processor(
    _logger: Any, _method_name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """structlog processor that scrubs sensitive fields from every event."""
    return _redact_mapping(event_dict)


def configure_logging(level: str, *, json_output: bool) -> None:
    """Configure stdlib + structlog logging.

    Args:
        level: one of DEBUG/INFO/WARNING/ERROR.
        json_output: emit JSON lines (production) instead of a console renderer.
    """
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level.upper())

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        redact_processor,
    ]

    renderer: Any
    if json_output:
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=sys.stdout.isatty())

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level.upper())),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger
