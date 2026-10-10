"""Log redaction tests (PROJECT-SPEC §39.7, §42.2)."""

from __future__ import annotations

import logging

import structlog
from app.logging_config import REDACTED, configure_logging, redact_processor


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


def test_sensitive_looking_numeric_counters_stay_readable() -> None:
    # Keys named after what they count must not hide the metric they carry: a
    # number can never be a token or a phone number, and §60 needs the retention
    # numbers to be observable in production logs.
    result = _redact({"cleared_ip_hmacs": 3, "deleted_sessions": 1})
    assert result == {"cleared_ip_hmacs": 3, "deleted_sessions": 1}


def test_sensitive_key_with_a_string_value_still_redacts() -> None:
    result = _redact({"request_ip_hmac": "a" * 64, "session_token": "x" * 40})
    assert result == {"request_ip_hmac": REDACTED, "session_token": REDACTED}


# --- audit FIX-06: guest_name and the phone/date boundary -------------------


def test_guest_name_is_redacted() -> None:
    assert _redact({"guest_name": "Иван Иванов"})["guest_name"] == REDACTED


def test_technical_name_fields_are_not_redacted() -> None:
    # A generic ``name`` marker would hide useful fields; only the exact
    # personal-data key is protected.
    result = _redact(
        {"venue_name": "Пивная №1", "hall_name": "Зал", "event_type": "BOOKING_CREATED"}
    )
    assert result == {
        "venue_name": "Пивная №1",
        "hall_name": "Зал",
        "event_type": "BOOKING_CREATED",
    }


def test_ordinary_dates_are_not_redacted_by_the_phone_rule() -> None:
    result = _redact(
        {
            "business_date": "2026-10-05",
            "message": "shift 2026-10-05 closed",
            "created_at": "05.10.2026",
        }
    )
    assert result["business_date"] == "2026-10-05"
    assert result["message"] == "shift 2026-10-05 closed"
    assert result["created_at"] == "05.10.2026"


def test_short_numeric_identifiers_are_preserved() -> None:
    assert _redact({"message": "booking 20261005 ok"})["message"] == "booking 20261005 ok"


def test_real_phone_numbers_are_still_redacted_under_neutral_keys() -> None:
    for value in ("+7 999 123-45-67", "8 (999) 123-45-67", "+1 555 123 4567"):
        redacted = _redact({"message": f"call {value} now"})["message"]
        assert REDACTED in redacted, value
        assert value not in redacted, value


# --- end-to-end emission through the real logging pipeline ------------------
#
# These assert on logs that were actually *captured*, so a policy regression
# cannot pass just because nothing was recorded (the Stage 12 ``caplog`` pitfall).


def test_configured_pipeline_emits_capturable_records(caplog) -> None:
    configure_logging("INFO", json_output=True)
    caplog.set_level(logging.INFO)
    logger = structlog.get_logger("test.caplog")
    logger.info("worker_job_sent", outbox_id=7)
    assert caplog.records, "no records were captured; the redaction check would be vacuous"
    assert "worker_job_sent" in caplog.text


def test_secret_and_pii_never_reach_the_captured_records(caplog) -> None:
    configure_logging("INFO", json_output=True)
    caplog.set_level(logging.INFO)
    logger = structlog.get_logger("test.caplog")
    logger.info(
        "vk_request",
        access_token="vk1.a." + "S" * 60,
        guest_phone_raw="+79991234567",
        guest_comment="у окна",
    )
    assert caplog.records, "no records were captured; the redaction check would be vacuous"
    text = caplog.text
    assert "+79991234567" not in text
    assert "у окна" not in text
    assert "S" * 40 not in text
    assert REDACTED in text


def test_guest_name_never_reaches_the_captured_records(caplog) -> None:
    configure_logging("INFO", json_output=True)
    caplog.set_level(logging.INFO)
    logger = structlog.get_logger("test.caplog.name")
    logger.info("booking_created", guest_name="Иван Иванов", venue_name="Пивная №1")
    assert caplog.records, "no records were captured; the redaction check would be vacuous"
    text = caplog.text
    assert "Иван Иванов" not in text
    assert "Пивная №1" in text  # non-personal field stays readable
    assert REDACTED in text
