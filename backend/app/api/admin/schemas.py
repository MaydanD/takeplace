"""Admin API schemas.

Transport types are generated from these models for the frontend.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.db.models import AdminAccount, Venue


class AdminSummary(BaseModel):
    """The authenticated admin account. Never contains the password hash."""

    id: int
    login: str
    is_active: bool

    @classmethod
    def from_model(cls, admin: AdminAccount) -> AdminSummary:
        return cls(id=admin.id, login=admin.login, is_active=admin.is_active)


class VenueSummary(BaseModel):
    """Public identity of the tenant the session belongs to."""

    id: int
    slug: str
    name: str
    address: str | None
    phone: str | None
    timezone: str
    is_active: bool
    online_booking_enabled: bool

    @classmethod
    def from_model(cls, venue: Venue) -> VenueSummary:
        return cls(
            id=venue.id,
            slug=venue.slug,
            name=venue.name,
            address=venue.address,
            phone=venue.phone,
            timezone=venue.timezone,
            is_active=venue.is_active,
            online_booking_enabled=venue.online_booking_enabled,
        )


class MeResponse(BaseModel):
    admin: AdminSummary
    venue: VenueSummary


class LoginRequest(BaseModel):
    login: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=1024)


class StatusResponse(BaseModel):
    status: str


class SettingsUpdate(BaseModel):
    """Editable venue settings.

    ``slug``, ``timezone`` and ``is_active`` are intentionally absent: slug and
    timezone are not runtime-editable in v1, and suspension is a CLI operation.
    """

    name: str | None = Field(default=None, min_length=1, max_length=200)
    address: str | None = Field(default=None, max_length=300)
    phone: str | None = Field(default=None, max_length=50)
    online_booking_enabled: bool | None = None
