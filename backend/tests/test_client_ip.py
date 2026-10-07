"""Canonical client IP derivation (PROJECT-SPEC §39.5, §40)."""

from __future__ import annotations

import pytest
from app.security.client_ip import UNKNOWN_CLIENT_IP, canonical_client_ip


def test_ipv4_is_returned_canonically() -> None:
    assert canonical_client_ip("203.0.113.7") == "203.0.113.7"


def test_ipv4_leading_zeros_are_rejected_as_non_ip() -> None:
    # ``010.0.0.1`` is not a valid address; it must not become a second identity.
    assert canonical_client_ip("010.0.0.1") == UNKNOWN_CLIENT_IP


@pytest.mark.parametrize(
    "raw",
    ["::1", "0:0:0:0:0:0:0:1", "0000:0000:0000:0000:0000:0000:0000:0001"],
)
def test_ipv6_textual_variants_share_one_identity(raw: str) -> None:
    assert canonical_client_ip(raw) == "::1"


def test_ipv6_is_lowercased_and_compressed() -> None:
    assert canonical_client_ip("2001:0DB8:0000:0000:0000:0000:0000:0001") == "2001:db8::1"


def test_ipv6_zone_id_is_dropped() -> None:
    assert canonical_client_ip("fe80::1%eth0") == "fe80::1"


@pytest.mark.parametrize("raw", [None, "", "   ", "not-an-ip", "1.2.3.4.5", "300.1.1.1"])
def test_missing_or_invalid_addresses_collapse_to_unknown(raw: str | None) -> None:
    assert canonical_client_ip(raw) == UNKNOWN_CLIENT_IP


def test_surrounding_whitespace_is_tolerated() -> None:
    assert canonical_client_ip("  198.51.100.9  ") == "198.51.100.9"
