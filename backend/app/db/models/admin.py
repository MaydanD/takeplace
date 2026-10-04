"""Admin account and session models (PROJECT-SPEC §6.2, §6.3)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Identity,
    Index,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AdminAccount(Base):
    """Exactly one shared admin account per venue (PROJECT-SPEC §6.2)."""

    __tablename__ = "admin_accounts"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    venue_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("venues.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    # Globally unique: the login screen does not require choosing a venue first.
    login: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )


class AdminSession(Base):
    """Server-side admin session (PROJECT-SPEC §6.3).

    Only the SHA-256 hash of the session token is stored; the raw token lives
    solely in the client cookie.
    """

    __tablename__ = "admin_sessions"
    __table_args__ = (
        # Composite target for later tenant-safe FKs such as
        # booking_events(admin_session_id, venue_id) (PROJECT-SPEC §43, §6.10).
        UniqueConstraint("id", "venue_id"),
        CheckConstraint("expires_at > created_at", name="expires_after_created"),
        Index("ix_admin_sessions_expires_at", "expires_at"),
        Index("ix_admin_sessions_venue_id", "venue_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    venue_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("venues.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
