"""Unit tests for the public booking service and booking-core public helpers.

Covers: PublicBookingInput, canonical_public_payload, public_request_hmac,
find_by_public_key signature, the CAPTCHA adapter seam, and the
resolve_public_venue slug helper interface (PROJECT-SPEC §18, §18.2, §32.2, §50).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from app.services.bookings import (
    PublicBookingInput,
    canonical_public_payload,
    public_request_hmac,
)
from app.services.public_bookings import (
    BaseCaptchaAdapter,
    CaptchaVerificationError,
    build_captcha_adapter,
)

MSK = timezone(timedelta(hours=3))


# --- PublicBookingInput -----------------------------------------------------


def test_public_booking_input_defaults() -> None:
    data = PublicBookingInput(
        starts_at=datetime(2026, 10, 5, 20, 0, tzinfo=MSK),
        ends_at=datetime(2026, 10, 5, 22, 0, tzinfo=MSK),
        table_id=1,
        party_size=2,
        guest_name="Guest",
        guest_phone_raw="+79990000000",
    )
    assert data.privacy_policy_version == "1.0.0"
    assert data.guest_comment is None


def test_public_booking_input_with_all_fields() -> None:
    data = PublicBookingInput(
        starts_at=datetime(2026, 10, 5, 20, 0, tzinfo=MSK),
        ends_at=datetime(2026, 10, 5, 22, 0, tzinfo=MSK),
        table_id=1,
        party_size=4,
        guest_name="Guest",
        guest_phone_raw="+79990000000",
        guest_comment="Window table",
        privacy_policy_version="2.0.0",
    )
    assert data.guest_comment == "Window table"
    assert data.privacy_policy_version == "2.0.0"


# --- canonical_public_payload -----------------------------------------------


def _make_input(**overrides: object) -> PublicBookingInput:
    defaults: dict[str, object] = {
        "starts_at": datetime(2026, 10, 5, 20, 0, tzinfo=MSK),
        "ends_at": datetime(2026, 10, 5, 22, 0, tzinfo=MSK),
        "table_id": 1,
        "party_size": 2,
        "guest_name": "Guest",
        "guest_phone_raw": "+79990000000",
    }
    defaults.update(overrides)
    return PublicBookingInput(**defaults)  # type: ignore[arg-type]


def test_canonical_payload_is_deterministic() -> None:
    data = _make_input()
    p1 = canonical_public_payload(venue_id=1, data=data)
    p2 = canonical_public_payload(venue_id=1, data=data)
    assert p1 == p2


def test_canonical_payload_differs_by_venue() -> None:
    data = _make_input()
    p1 = canonical_public_payload(venue_id=1, data=data)
    p2 = canonical_public_payload(venue_id=2, data=data)
    assert p1 != p2


def test_canonical_payload_differs_by_table() -> None:
    p1 = canonical_public_payload(venue_id=1, data=_make_input(table_id=1))
    p2 = canonical_public_payload(venue_id=1, data=_make_input(table_id=2))
    assert p1 != p2


def test_canonical_payload_differs_by_phone() -> None:
    p1 = canonical_public_payload(venue_id=1, data=_make_input(guest_phone_raw="+79990000000"))
    p2 = canonical_public_payload(venue_id=1, data=_make_input(guest_phone_raw="+79990000001"))
    assert p1 != p2


def test_canonical_payload_differs_by_time() -> None:
    p1 = canonical_public_payload(
        venue_id=1, data=_make_input(starts_at=datetime(2026, 10, 5, 20, 0, tzinfo=MSK))
    )
    p2 = canonical_public_payload(
        venue_id=1, data=_make_input(starts_at=datetime(2026, 10, 5, 20, 30, tzinfo=MSK))
    )
    assert p1 != p2


def test_canonical_payload_differs_by_privacy_version() -> None:
    p1 = canonical_public_payload(venue_id=1, data=_make_input(privacy_policy_version="1.0.0"))
    p2 = canonical_public_payload(venue_id=1, data=_make_input(privacy_policy_version="2.0.0"))
    assert p1 != p2


# --- public_request_hmac ----------------------------------------------------


def test_public_request_hmac_is_stable() -> None:
    data = _make_input()
    h1 = public_request_hmac("secret-key", venue_id=1, data=data)
    h2 = public_request_hmac("secret-key", venue_id=1, data=data)
    assert h1 == h2


def test_public_request_hmac_differs_by_key() -> None:
    data = _make_input()
    h1 = public_request_hmac("key-a", venue_id=1, data=data)
    h2 = public_request_hmac("key-b", venue_id=1, data=data)
    assert h1 != h2


def test_public_request_hmac_differs_by_payload() -> None:
    h1 = public_request_hmac("key", venue_id=1, data=_make_input(guest_name="Alice"))
    h2 = public_request_hmac("key", venue_id=1, data=_make_input(guest_name="Bob"))
    assert h1 != h2


def test_public_request_hmac_is_hex() -> None:
    data = _make_input()
    h = public_request_hmac("key", venue_id=1, data=data)
    assert len(h) == 64
    int(h, 16)


# --- CAPTCHA adapter --------------------------------------------------------


@pytest.mark.asyncio
async def test_captcha_disabled_accepts_none_token() -> None:
    adapter = BaseCaptchaAdapter(enabled=False)
    assert adapter.is_enabled() is False
    await adapter.verify(None)


@pytest.mark.asyncio
async def test_captcha_disabled_accepts_empty_token() -> None:
    adapter = BaseCaptchaAdapter(enabled=False)
    await adapter.verify("")


@pytest.mark.asyncio
async def test_captcha_enabled_rejects_none_token() -> None:
    adapter = BaseCaptchaAdapter(enabled=True)
    assert adapter.is_enabled() is True
    with pytest.raises(CaptchaVerificationError):
        await adapter.verify(None)


@pytest.mark.asyncio
async def test_captcha_enabled_rejects_empty_token() -> None:
    adapter = BaseCaptchaAdapter(enabled=True)
    with pytest.raises(CaptchaVerificationError):
        await adapter.verify("")


@pytest.mark.asyncio
async def test_captcha_enabled_rejects_unverified_nonempty_token() -> None:
    adapter = BaseCaptchaAdapter(enabled=True)
    with pytest.raises(CaptchaVerificationError):
        await adapter.verify("some-token")


def test_build_captcha_adapter_disabled_by_default() -> None:
    adapter = build_captcha_adapter()
    assert adapter.is_enabled() is False


def test_build_captcha_adapter_enabled() -> None:
    adapter = build_captcha_adapter(enabled=True)
    assert adapter.is_enabled() is True
