"""Stage 8 domain unit tests: lifecycle eligibility and UNDO_OPEN target (PROJECT-SPEC §21-§24).

These tests exercise the pure domain helpers only; the resource-locked behaviour is
covered by the PostgreSQL integration/concurrency suites.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from app.domain.booking import lifecycle_actions, undo_open_target

MSK = timezone(timedelta(hours=3))
SHIFT_START = datetime(2026, 10, 4, 16, 0, tzinfo=MSK)
SHIFT_END = datetime(2026, 10, 5, 2, 0, tzinfo=MSK)
STARTS = datetime(2026, 10, 4, 20, 0, tzinfo=MSK)
ENDS = datetime(2026, 10, 4, 22, 0, tzinfo=MSK)


def _actions(status, now, *, undo_target=None):
    return lifecycle_actions(
        status=status,
        starts_at=STARTS,
        ends_at=ENDS,
        shift_starts_at=SHIFT_START,
        now=now,
        undo_target=undo_target,
    )


# --- WAITING (§21) ----------------------------------------------------------


def test_wait_offered_only_inside_planned_interval() -> None:
    assert "wait" in _actions("NEW", STARTS)
    assert "wait" in _actions("NEW", ENDS - timedelta(minutes=5))
    # Before the plan start and after plan end the transition is not offered.
    assert "wait" not in _actions("NEW", STARTS - timedelta(minutes=5))
    assert "wait" not in _actions("NEW", ENDS)


def test_wait_not_offered_from_waiting_or_open() -> None:
    assert "wait" not in _actions("WAITING", STARTS)
    assert "wait" not in _actions("OPEN", STARTS)


# --- OPEN (§22) -------------------------------------------------------------


def test_open_offered_from_new_and_waiting_within_shift() -> None:
    # Early OPEN: shift started, plan not started yet.
    early = SHIFT_START + timedelta(minutes=10)
    assert "open" in _actions("NEW", early)
    assert "open" in _actions("WAITING", early)
    assert "open" in _actions("NEW", STARTS)
    assert "open" in _actions("NEW", ENDS - timedelta(minutes=5))


def test_open_not_offered_before_shift_start() -> None:
    before = SHIFT_START - timedelta(minutes=1)
    assert "open" not in _actions("NEW", before)
    assert "open" not in _actions("WAITING", before)


def test_open_not_offered_after_plan_end() -> None:
    # §22: ``shift_starts_at <= operation_now < ends_at``; overdue plan cannot OPEN.
    assert "open" not in _actions("NEW", ENDS)
    assert "open" not in _actions("WAITING", ENDS + timedelta(minutes=1))


def test_open_not_offered_from_open() -> None:
    assert "open" not in _actions("OPEN", STARTS)


# --- CANCEL (§25) -----------------------------------------------------------


def test_cancel_offered_for_unopened_bookings_only() -> None:
    assert "cancel" in _actions("NEW", STARTS)
    assert "cancel" in _actions("WAITING", STARTS)
    # Overdue unopened bookings can still be canceled (§20.4).
    assert "cancel" in _actions("NEW", ENDS + timedelta(hours=1))
    assert "cancel" in _actions("WAITING", ENDS + timedelta(hours=1))
    assert "cancel" not in _actions("OPEN", STARTS)
    assert "cancel" not in _actions("CLOSED", STARTS)
    assert "cancel" not in _actions("CANCELED", STARTS)


# --- CLOSE (§24) ------------------------------------------------------------


def test_close_always_offered_for_open_including_overdue() -> None:
    assert "close" in _actions("OPEN", STARTS)
    assert "close" in _actions("OPEN", ENDS + timedelta(hours=3))
    assert "close" not in _actions("NEW", STARTS)
    assert "close" not in _actions("CLOSED", STARTS)


# --- UNDO_OPEN (§23) --------------------------------------------------------


def test_undo_open_offered_only_before_plan_end_with_target() -> None:
    assert "undo-open" in _actions("OPEN", STARTS, undo_target="NEW")
    assert "undo-open" in _actions("OPEN", ENDS - timedelta(minutes=5), undo_target="WAITING")
    # §23: ``operation_now < booking.ends_at`` — no undo after plan end.
    assert "undo-open" not in _actions("OPEN", ENDS, undo_target="NEW")
    assert "undo-open" not in _actions("OPEN", ENDS + timedelta(minutes=1), undo_target="NEW")
    # No reversible OPEN event -> no undo.
    assert "undo-open" not in _actions("OPEN", STARTS, undo_target=None)


def test_undo_open_not_offered_from_non_open() -> None:
    assert "undo-open" not in _actions("NEW", STARTS, undo_target="NEW")
    assert "undo-open" not in _actions("WAITING", STARTS, undo_target="WAITING")


# --- undo_open_target (§23) -------------------------------------------------


def test_undo_target_new_when_opened_from_new() -> None:
    events = [
        ("BOOKING_CREATED", {}),
        ("BOOKING_OPENED", {"previous_status": "NEW"}),
    ]
    assert undo_open_target(events) == "NEW"


def test_undo_target_waiting_when_opened_from_waiting() -> None:
    events = [
        ("BOOKING_CREATED", {}),
        ("WAITING_SET", {}),
        ("BOOKING_OPENED", {"previous_status": "WAITING"}),
    ]
    assert undo_open_target(events) == "WAITING"


def test_undo_target_none_without_open_event() -> None:
    assert undo_open_target([("BOOKING_CREATED", {})]) is None
    assert undo_open_target([]) is None


def test_walk_in_open_defaults_to_new() -> None:
    # Stage 7 WALK_IN create+open writes BOOKING_OPENED without previous_status.
    events = [("BOOKING_CREATED", {}), ("BOOKING_OPENED", {"table_ids": [1]})]
    assert undo_open_target(events) == "NEW"


def test_text_only_edit_preserves_undo_target() -> None:
    events = [
        ("BOOKING_OPENED", {"previous_status": "WAITING"}),
        ("BOOKING_EDITED", {"changed_fields": ["guest_name", "guest_comment"]}),
    ]
    assert undo_open_target(events) == "WAITING"


def test_party_size_edit_blocks_undo() -> None:
    events = [
        ("BOOKING_OPENED", {"previous_status": "NEW"}),
        ("BOOKING_EDITED", {"changed_fields": ["guest_name", "party_size"]}),
    ]
    assert undo_open_target(events) is None


def test_state_mutation_after_open_blocks_undo() -> None:
    for kind in ("TIME_CHANGED", "BOOKING_RESCHEDULED", "TABLE_ADDED", "TABLE_REMOVED"):
        events = [("BOOKING_OPENED", {"previous_status": "NEW"}), (kind, {})]
        assert undo_open_target(events) is None, kind


def test_edit_before_last_open_does_not_block() -> None:
    # A text edit that happened *before* the OPEN is irrelevant to the last OPEN.
    events = [
        ("BOOKING_EDITED", {"changed_fields": ["party_size"]}),
        ("BOOKING_OPENED", {"previous_status": "NEW"}),
    ]
    assert undo_open_target(events) == "NEW"


def test_latest_open_wins_after_undo_cycle() -> None:
    # NEW -> OPEN -> UNDO -> WAITING -> OPEN: the last OPEN decides the target.
    events = [
        ("BOOKING_OPENED", {"previous_status": "NEW"}),
        ("OPEN_UNDONE", {"restored_status": "NEW"}),
        ("WAITING_SET", {}),
        ("BOOKING_OPENED", {"previous_status": "WAITING"}),
    ]
    assert undo_open_target(events) == "WAITING"


def test_unknown_edit_shape_blocks_undo() -> None:
    # A BOOKING_EDITED without a usable changed_fields list is treated conservatively.
    events = [("BOOKING_OPENED", {"previous_status": "NEW"}), ("BOOKING_EDITED", {})]
    assert undo_open_target(events) is None
    events = [
        ("BOOKING_OPENED", {"previous_status": "NEW"}),
        ("BOOKING_EDITED", {"changed_fields": "guest_name"}),
    ]
    assert undo_open_target(events) is None


@pytest.mark.parametrize("status", ["CLOSED", "CANCELED"])
def test_terminal_statuses_have_no_lifecycle_actions(status: str) -> None:
    assert _actions(status, STARTS) == []
    assert _actions(status, ENDS + timedelta(hours=1)) == []
