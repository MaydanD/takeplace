"""Transactional outbox and VK integration models (PROJECT-SPEC §6.11, §6.12, §38).

These models mirror the CHECK/FK/index invariants declared in migration
``20261005_0007``. The database is the last arbiter (§44): a malformed status, a
SKIPPED row without a reason, or a cross-tenant reference is rejected even for
direct SQL, not only through the service layer.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.domain.outbox import OUTBOX_STATUSES, SKIP_REASONS

_STATUS_LIST = ", ".join(f"'{value}'" for value in OUTBOX_STATUSES)
_SKIP_REASON_LIST = ", ".join(f"'{value}'" for value in SKIP_REASONS)


class VenueVKIntegration(Base):
    """One VK configuration per venue (§6.12).

    ``venue_id`` is both the primary key and a FK to ``venues(id)`` with
    ``ON DELETE CASCADE``, so a venue has at most one integration and a
    cross-tenant reference is impossible by construction. The access token is
    stored encrypted at rest and is never returned to the frontend (§38.5).
    """

    __tablename__ = "venue_vk_integrations"
    __table_args__ = (
        CheckConstraint(
            "NOT enabled OR (community_id IS NOT NULL AND peer_id IS NOT NULL "
            "AND encrypted_access_token IS NOT NULL AND encryption_key_version IS NOT NULL)",
            name="enabled_requires_full_config",
        ),
    )

    venue_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("venues.id", ondelete="CASCADE"), primary_key=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    community_id: Mapped[int | None] = mapped_column(BigInteger)
    peer_id: Mapped[int | None] = mapped_column(BigInteger)
    encrypted_access_token: Mapped[str | None] = mapped_column(Text)
    encryption_key_version: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )


class NotificationOutbox(Base):
    """A DB-backed notification job, created atomically with its booking (§6.11, §38.2).

    The worker claims rows with ``FOR UPDATE SKIP LOCKED`` and performs the VK HTTP
    call *outside* any database transaction (§38.3). ``provider_dedup_id`` is
    generated once and preserved across retries so VK can deduplicate a resend
    (§38.4).
    """

    __tablename__ = "notification_outbox"
    __table_args__ = (
        UniqueConstraint("type", "dedup_key", name="uq_notification_outbox_type_dedup_key"),
        CheckConstraint(f"status IN ({_STATUS_LIST})", name="status_allowed"),
        CheckConstraint(
            "(status = 'SKIPPED') = (skipped_at IS NOT NULL)",
            name="skipped_requires_timestamp",
        ),
        CheckConstraint(
            "status = 'SKIPPED' OR skip_reason IS NULL", name="skip_reason_only_when_skipped"
        ),
        CheckConstraint(
            "status <> 'SKIPPED' OR skip_reason IS NOT NULL", name="skipped_requires_reason"
        ),
        CheckConstraint(
            f"skip_reason IS NULL OR skip_reason IN ({_SKIP_REASON_LIST})",
            name="skip_reason_allowed",
        ),
        CheckConstraint("jsonb_typeof(payload) = 'object'", name="payload_object"),
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        ForeignKeyConstraint(
            ["venue_id"],
            ["venues.id"],
            name="fk_notification_outbox_venue_id_venues",
            ondelete="CASCADE",
        ),
        # Null only the session id on session cleanup (§6.11, PostgreSQL 15+).
        ForeignKeyConstraint(
            ["acknowledged_by_session_id", "venue_id"],
            ["admin_sessions.id", "admin_sessions.venue_id"],
            name="fk_notification_outbox_ack_session_id_venue_id_admin_sessions",
            ondelete="SET NULL (acknowledged_by_session_id)",
        ),
        Index(
            "ix_notification_outbox_next_attempt_at",
            "next_attempt_at",
            postgresql_where=text("status IN ('PENDING', 'RETRY')"),
        ),
        Index(
            "ix_notification_outbox_locked_until",
            "locked_until",
            postgresql_where=text("status = 'PROCESSING'"),
        ),
        Index(
            "ix_notification_outbox_acknowledged_at",
            "acknowledged_at",
            postgresql_where=text("status = 'DEAD'"),
        ),
        Index(
            "ix_notification_outbox_expires_at",
            "expires_at",
            postgresql_where=text("status IN ('PENDING', 'RETRY', 'PROCESSING')"),
        ),
        Index("ix_notification_outbox_venue_id", "venue_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    venue_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    dedup_key: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'PENDING'"))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    next_attempt_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    provider_dedup_id: Mapped[str | None] = mapped_column(Text)
    last_error: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    skipped_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    skip_reason: Mapped[str | None] = mapped_column(Text)
    acknowledged_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    acknowledged_by_session_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    sent_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))


class WorkerHeartbeatRow(Base):
    """Single-row worker liveness marker (PROJECT-SPEC §47).

    The worker upserts ``id=1`` each cycle and ``/health/ops`` reads its age, so a
    dead worker is visible to monitoring without the API sharing process state.
    """

    __tablename__ = "worker_heartbeat"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    last_seen_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
