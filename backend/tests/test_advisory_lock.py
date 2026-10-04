"""Advisory-lock key vectors (PROJECT-SPEC §32.3, §54).

The schedule guard's correctness depends on every caller deriving the *same*
key, so the algorithm is pinned with fixed vectors. ``venue_id`` must never be
reduced to ``int4``: the vector with ``2**63 - 1`` proves large ids survive.
"""

from __future__ import annotations

from app.db.locks import LAYOUT_NAMESPACE, SCHEDULE_NAMESPACE, advisory_key


def test_schedule_key_vectors() -> None:
    assert advisory_key("schedule", 1) == -96805519464533641
    assert advisory_key("schedule", 2) == -856532074855973278
    assert advisory_key("schedule", 123456) == 1740832087693327240


def test_layout_key_vector() -> None:
    assert advisory_key("layout", 1) == -5530287696282033675


def test_large_venue_id_is_not_truncated() -> None:
    # 2**63 - 1 fits in bigint; int4 truncation would collapse distinct ids.
    assert advisory_key("schedule", 2**63 - 1) == 8260724857304804455
    assert advisory_key("schedule", 2**63 - 1) != advisory_key("schedule", 1)


def test_namespaces_are_separated() -> None:
    assert advisory_key(SCHEDULE_NAMESPACE, 7) != advisory_key(LAYOUT_NAMESPACE, 7)


def test_key_is_stable_and_signed_64_bit() -> None:
    key = advisory_key(SCHEDULE_NAMESPACE, 42)
    assert key == advisory_key(SCHEDULE_NAMESPACE, 42)
    assert -(2**63) <= key <= 2**63 - 1
