"""``venues`` model (PROJECT-SPEC §6.1)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Identity, Text, text
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.domain.venues import RESERVED_SLUGS

_RESERVED_SLUG_LIST = ", ".join(f"'{slug}'" for slug in sorted(RESERVED_SLUGS))


class Venue(Base):
    """A tenant. One venue == one independent tenant (PROJECT-SPEC §1.1)."""

    __tablename__ = "venues"
    __table_args__ = (
        # The DB is the last arbiter: these invariants hold even for direct SQL.
        CheckConstraint("slug ~ '^[a-z0-9-]{2,40}$'", name="slug_format"),
        CheckConstraint(f"slug NOT IN ({_RESERVED_SLUG_LIST})", name="slug_not_reserved"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    slug: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    address: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(Text)
    timezone: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    online_booking_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
