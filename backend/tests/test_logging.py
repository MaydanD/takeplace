"""Log redaction tests (PROJECT-SPEC §39.7, §42.2)."""

from __future__ import annotations

from app.logging_config import REDACTED, redact_processor


def _redact(event: dict[str, object]) -> dict[str, object]:
    return redact_processor(None, "info", dict(event))


def test_phone_key_is_redacted() -> None:
    result = _redact({"guest_phone_raw": "+79991234567"})
    assert result["guest_phone_raw"] == REDACTED


def test_guest_comment_key_is_redacted() -> None:
    result = _redact({"guest_comment": "у окна"})
    assert result["guest_comment"] == REDACTED


def test_session_token_key_is_redacted() -> None:
    result = _redact({"session_token": "x" * 40})
    assert result["session_token"] == REDACTED


def test_password_key_is_redacted() -> None:
    result = _redact({"password": "hunter2"})
    assert result["password"] == REDACTED


def test_request_ip_key_is_redacted() -> None:
    result = _redact({"request_ip": "203.0.113.7"})
    assert result["request_ip"] == REDACTED


def test_phone_like_value_is_redacted_even_under_neutral_key() -> None:
    result = _redact({"message": "call +7 999 123-45-67 now"})
    assert "+7 999" not in str(result["message"])
    assert REDACTED in str(result["message"])


def test_long_opaque_value_is_redacted() -> None:
    result = _redact({"value": "A" * 48})
    assert result["value"] == REDACTED


def test_nested_mapping_is_redacted() -> None:
    result = _redact({"booking": {"guest_phone_raw": "+79991234567", "id": 5}})
    assert result["booking"] == {"guest_phone_raw": REDACTED, "id": 5}


def test_benign_values_are_preserved() -> None:
    result = _redact({"booking_id": 42, "status": "NEW", "count": 3})
    assert result == {"booking_id": 42, "status": "NEW", "count": 3}
