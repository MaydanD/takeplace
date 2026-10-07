"""Stage 13: remediate the outbox skip-reason CHECK and add retention indexes.

Revision ID: 20261006_0008
Revises: 20261005_0007

Stage 12 added the ``INTEGRATION_DISABLED`` skip reason by editing revision
``20261005_0007`` in place. That revision id is now published history, so any
database created before the edit carries a ``notification_outbox`` CHECK that
rejects ``INTEGRATION_DISABLED`` while Alembic still reports it at head. This
forward migration does not rely on ``0007`` running again: it drops (if present)
and recreates the CHECK with the canonical §6.11/§44.1 value list, so the
constraint reaches the correct state from either revision of ``0007``.

It also adds the two retention-scan indexes used by the Stage 13 maintenance job
(§40 request-IP HMAC cleanup, §42.2 anonymization). Index names match the ORM
metadata so ``alembic check`` stays green.
"""

from alembic import op
from sqlalchemy import text

revision = "20261006_0008"
down_revision = "20261005_0007"
branch_labels = None
depends_on = None

_CANONICAL_SKIP_REASONS = "'EXPIRED','BOOKING_INACTIVE','BOOKING_ANONYMIZED','INTEGRATION_DISABLED'"
_CHECK_NAME = "ck_notification_outbox_skip_reason_allowed"


def upgrade() -> None:
    # --- 0007 remediation: guarantee the canonical skip_reason CHECK ---------
    op.execute(f"ALTER TABLE notification_outbox DROP CONSTRAINT IF EXISTS {_CHECK_NAME}")
    op.execute(
        "ALTER TABLE notification_outbox "
        f"ADD CONSTRAINT {_CHECK_NAME} "
        f"CHECK (skip_reason IS NULL OR skip_reason IN ({_CANONICAL_SKIP_REASONS}))"
    )

    # --- retention-scan indexes (§40, §42.2) ---------------------------------
    op.create_index(
        "ix_bookings_request_ip_hmac_expires_at",
        "bookings",
        ["request_ip_hmac_expires_at"],
        postgresql_where=text("request_ip_hmac IS NOT NULL"),
    )
    op.create_index(
        "ix_bookings_venue_id_anonymization_due",
        "bookings",
        ["venue_id"],
        postgresql_where=text("anonymized_at IS NULL AND status IN ('CLOSED', 'CANCELED')"),
    )


def downgrade() -> None:
    # The 0007 in-place edit is not restorable to its earlier (broken) form; the
    # canonical value list is the only correct state, so this re-asserts it. The
    # indexes are safe to drop.
    op.drop_index("ix_bookings_venue_id_anonymization_due", table_name="bookings")
    op.drop_index("ix_bookings_request_ip_hmac_expires_at", table_name="bookings")
    op.execute(f"ALTER TABLE notification_outbox DROP CONSTRAINT IF EXISTS {_CHECK_NAME}")
    op.execute(
        "ALTER TABLE notification_outbox "
        f"ADD CONSTRAINT {_CHECK_NAME} "
        f"CHECK (skip_reason IS NULL OR skip_reason IN ({_CANONICAL_SKIP_REASONS}))"
    )
