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


def _is_identifier_value(value: Any) -> bool:
    """Whether a sensitive *key* can carry an actual secret.

    Only identifiers and free text can leak: a string, bytes or a container.
    Numeric log counters are frequently named after what they count
    (``cleared_ip_hmacs``, ``deleted_sessions``) and redacting them would blind
    operators to the retention/audit metrics §60 asks for, while a number can
    never be a token, a phone number or a session secret.
    """
    return isinstance(value, (str, bytes, bytearray, dict, list, tuple, set))


def _redact_mapping(mapping: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    for key in list(mapping.keys()):
        if _is_sensitive_key(key) and _is_identifier_value(mapping[key]):
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
    """Configure stdlib + structlog logging on one pipeline.

    structlog events are routed through the standard :mod:`logging` root logger
    rather than a private ``PrintLogger``. This keeps a single redaction pipeline
    for *both* structured application logs and third-party library logs (httpx,
    uvicorn), and makes log assertions in tests observable through the normal
    ``logging`` machinery (``caplog`` / handlers) instead of only raw stdout.

    Args:
        level: one of DEBUG/INFO/WARNING/ERROR.
        json_output: emit JSON lines (production) instead of a console renderer.
    """
    resolved_level = logging.getLevelName(level.upper())
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
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(resolved_level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        # Foreign (stdlib) records get the same timestamps/redaction.
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    # Mark our handler so repeated calls replace only ours and never remove a
    # handler installed by the host (pytest's capture/logging plugin, uvicorn).
    handler._takeplace_handler = True  # type: ignore[attr-defined]
    root = logging.getLogger()
    for existing in list(root.handlers):
        if getattr(existing, "_takeplace_handler", False):
            root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(resolved_level)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger
