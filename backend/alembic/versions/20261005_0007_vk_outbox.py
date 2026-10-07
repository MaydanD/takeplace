"""Stage 12: VK transactional outbox and per-venue VK integration (§6.11, §6.12, §38).

Revision ID: 20261005_0007
Revises: 20261004_0006

Three tables (including the durable worker heartbeat):

* ``venue_vk_integrations`` — one VK configuration per venue (PK ``venue_id`` and
  FK ``venue_id -> venues(id) ON DELETE CASCADE``), so a cross-tenant reference is
  structurally impossible (§6.12). The CHECK guarantees an enabled integration has
  the full set of required fields.
* ``notification_outbox`` — the transactional outbox (§6.11). A booking mutation
  inserts its outbox row in the *same* transaction, so a rollback leaves no
  notification behind (§38.2). The worker claims rows with ``FOR UPDATE SKIP
  LOCKED`` and never holds a transaction across the VK HTTP call (§38.3).

The ``acknowledged_by_session_id`` composite FK uses PostgreSQL's
``ON DELETE SET NULL (column_list)`` form so deleting a session nulls only the
session id and keeps the row (and its ``venue_id NOT NULL``) alive — a plain
``ON DELETE SET NULL`` would try to null ``venue_id`` too (§6.11).
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20261005_0007"
down_revision = "20261004_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "venue_vk_integrations",
        sa.Column("venue_id", sa.BigInteger(), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("community_id", sa.BigInteger(), nullable=True),
        sa.Column("peer_id", sa.BigInteger(), nullable=True),
        sa.Column("encrypted_access_token", sa.Text(), nullable=True),
        sa.Column("encryption_key_version", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["venue_id"],
            ["venues.id"],
            name="fk_venue_vk_integrations_venue_id_venues",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "NOT enabled OR (community_id IS NOT NULL AND peer_id IS NOT NULL "
            "AND encrypted_access_token IS NOT NULL AND encryption_key_version IS NOT NULL)",
            name="enabled_requires_full_config",
        ),
    )

    op.create_table(
        "notification_outbox",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("venue_id", sa.BigInteger(), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("dedup_key", sa.Text(), nullable=False),
        sa.Column(
            "payload", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'PENDING'")),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider_dedup_id", sa.Text(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("skipped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("skip_reason", sa.Text(), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_by_session_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("type", "dedup_key", name="uq_notification_outbox_type_dedup_key"),
        sa.CheckConstraint(
            "status IN ('PENDING','PROCESSING','RETRY','SENT','DEAD','SKIPPED')",
            name="status_allowed",
        ),
        sa.CheckConstraint(
            "(status = 'SKIPPED') = (skipped_at IS NOT NULL)",
            name="skipped_requires_timestamp",
        ),
        sa.CheckConstraint(
            "status = 'SKIPPED' OR skip_reason IS NULL",
            name="skip_reason_only_when_skipped",
        ),
        sa.CheckConstraint(
            "status <> 'SKIPPED' OR skip_reason IS NOT NULL",
            name="skipped_requires_reason",
        ),
        sa.CheckConstraint(
            "skip_reason IS NULL OR skip_reason IN ('EXPIRED','BOOKING_INACTIVE','BOOKING_ANONYMIZED','INTEGRATION_DISABLED')",
            name="skip_reason_allowed",
        ),
        sa.CheckConstraint("jsonb_typeof(payload) = 'object'", name="payload_object"),
        sa.CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        sa.ForeignKeyConstraint(
            ["venue_id"],
            ["venues.id"],
            name="fk_notification_outbox_venue_id_venues",
            ondelete="CASCADE",
        ),
        # Null only the session id; keep the row and its venue_id NOT NULL (§6.11).
        sa.ForeignKeyConstraint(
            ["acknowledged_by_session_id", "venue_id"],
            ["admin_sessions.id", "admin_sessions.venue_id"],
            name="fk_notification_outbox_ack_session_id_venue_id_admin_sessions",
            ondelete="SET NULL (acknowledged_by_session_id)",
        ),
    )

    # Worker polling index: due pending/retry rows, ordered by next_attempt_at (§43).
    op.create_index(
        "ix_notification_outbox_next_attempt_at",
        "notification_outbox",
        ["next_attempt_at"],
        postgresql_where=sa.text("status IN ('PENDING', 'RETRY')"),
    )
    # Stuck PROCESSING lease reclaim (§38.3).
    op.create_index(
        "ix_notification_outbox_locked_until",
        "notification_outbox",
        ["locked_until"],
        postgresql_where=sa.text("status = 'PROCESSING'"),
    )
    # Active operational alert: unacknowledged DEAD rows (§47, §49).
    op.create_index(
        "ix_notification_outbox_acknowledged_at",
        "notification_outbox",
        ["acknowledged_at"],
        postgresql_where=sa.text("status = 'DEAD'"),
    )
    op.create_index(
        "ix_notification_outbox_expires_at",
        "notification_outbox",
        ["expires_at"],
        postgresql_where=sa.text("status IN ('PENDING', 'RETRY', 'PROCESSING')"),
    )
    op.create_index("ix_notification_outbox_venue_id", "notification_outbox", ["venue_id"])

    # Worker heartbeat (§47): a single fixed row the worker upserts each cycle and
    # ``/health/ops`` reads to detect a dead worker across processes.
    op.create_table(
        "worker_heartbeat",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("worker_heartbeat")
    op.drop_index("ix_notification_outbox_venue_id", table_name="notification_outbox")
    op.drop_index("ix_notification_outbox_expires_at", table_name="notification_outbox")
    op.drop_index("ix_notification_outbox_acknowledged_at", table_name="notification_outbox")
    op.drop_index("ix_notification_outbox_locked_until", table_name="notification_outbox")
    op.drop_index("ix_notification_outbox_next_attempt_at", table_name="notification_outbox")
    op.drop_table("notification_outbox")
    op.drop_table("venue_vk_integrations")
