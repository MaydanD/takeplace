"""Unit tests that VK secrets never leak into logs or errors (PROJECT-SPEC §38.5, §39.7)."""

from __future__ import annotations

import structlog
from app.integrations.vk.client import VKDeliveryError, VKErrorClass
from app.logging_config import REDACTED, redact_processor
from app.worker import _safe_error

_SECRET_TOKEN = "vk1.a." + "S" * 60


def _redact(event: dict) -> dict:
    return dict(redact_processor(None, "info", dict(event)))


def test_access_token_key_is_redacted() -> None:
    event = _redact({"event": "send", "access_token": _SECRET_TOKEN})
    assert event["access_token"] == REDACTED
    assert _SECRET_TOKEN not in str(event)


def test_raw_token_shaped_value_is_redacted_even_without_a_sensitive_key() -> None:
    # A long opaque value is scrubbed regardless of the surrounding key name.
    event = _redact({"event": "send", "detail": "S" * 40})
    assert event["detail"] == REDACTED


def test_token_like_field_names_are_redacted() -> None:
    for key in ("token", "encrypted_access_token", "authorization", "apikey"):
        event = _redact({"event": "x", key: "some-value"})
        assert event[key] == REDACTED, key


def test_safe_error_reporting_never_contains_the_token() -> None:
    exc = VKDeliveryError("network", error_class=VKErrorClass.RETRYABLE)
    assert _SECRET_TOKEN not in _safe_error(exc)


def test_safe_error_reports_vk_code_and_class_only() -> None:
    exc = VKDeliveryError(
        "VK error 100 (permanent)", error_class=VKErrorClass.PERMANENT, vk_error_code=100
    )
    assert _safe_error(exc) == "vk:100:permanent"


def test_structlog_bound_logger_output_scrubs_token(capsys) -> None:
    from app.logging_config import configure_logging

    configure_logging("INFO", json_output=True)
    logger = structlog.get_logger("test.leak")
    logger.info("vk_request", access_token=_SECRET_TOKEN, peer_id=123)
    captured = capsys.readouterr().out
    assert _SECRET_TOKEN not in captured
    assert REDACTED in captured
