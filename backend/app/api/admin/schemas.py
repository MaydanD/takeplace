"""Admin API schemas.

Transport types are generated from these models for the frontend.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, SecretStr

from app.db.models import AdminAccount, BookingEvent, Hall, Table, Venue
from app.domain.layout import StaticElement
from app.services.bookings import BookingView
from app.services.outbox_worker import DeadJobSummary
from app.services.vk_integration import VKIntegrationSummary as VKIntegrationSummaryData


class VKIntegrationSummary(BaseModel):
    """Frontend-safe VK configuration state (secret-free summary)."""

    enabled: bool
    community_id: int | None = Field(default=None, gt=0)
    peer_id: int | None = Field(default=None, gt=0)
    has_token: bool

    @classmethod
    def from_summary(cls, summary: VKIntegrationSummaryData) -> VKIntegrationSummary:
        return cls(
            enabled=summary.enabled,
            community_id=summary.community_id,
            peer_id=summary.peer_id,
            has_token=summary.has_token,
        )


class VKIntegrationUpdate(BaseModel):
    """Admin update payload for venue VK configuration."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    community_id: int | None = Field(default=None, gt=0)
    peer_id: int | None = Field(default=None, gt=0)
    access_token: SecretStr | None = Field(default=None, min_length=1, max_length=8192)


class OutboxDeadJob(BaseModel):
    """Secret-free outbox delivery failure summary."""

    id: int
    type: str
    status: Literal["DEAD", "RETRY"]
    attempts: int
    created_at: AwareDatetime
    expires_at: AwareDatetime
    last_error: str | None
    acknowledged_at: AwareDatetime | None

    @classmethod
    def from_summary(cls, summary: DeadJobSummary) -> OutboxDeadJob:
        return cls.model_validate(summary, from_attributes=True)


class OutboxDeadList(BaseModel):
    """Tenant-scoped DEAD jobs and unacknowledged count."""

    jobs: list[OutboxDeadJob]
    unacknowledged: int
    next_cursor: int | None = None


# Local schedule times are wall-clock ``HH:MM`` values in the venue timezone, on
# the 5-minute grid (§5.2). They are never ambiguous instants: the business date
# supplies the calendar day and the shift may cross midnight.
_TIME_PATTERN = r"^([01]\d|2[0-3]):[0-5]\d$"


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


class ScheduleDay(BaseModel):
    """One weekday rule. ``is_open=false`` means the day is closed."""

    weekday: int = Field(ge=0, le=6)
    is_open: bool
    open_time: str | None = Field(default=None, pattern=_TIME_PATTERN)
    close_time: str | None = Field(default=None, pattern=_TIME_PATTERN)


class WeeklyScheduleResponse(BaseModel):
    timezone: str
    weekdays: list[ScheduleDay]


class WeeklyScheduleUpdate(BaseModel):
    """Full replacement of the weekly schedule (all seven weekdays)."""

    weekdays: list[ScheduleDay] = Field(min_length=1, max_length=7)


class ScheduleExceptionEntry(BaseModel):
    date: date
    is_closed: bool
    open_time: str | None
    close_time: str | None


class ScheduleExceptionsResponse(BaseModel):
    timezone: str
    exceptions: list[ScheduleExceptionEntry]


class ScheduleExceptionUpdate(BaseModel):
    """A date-specific override; it fully replaces the weekly rule (§5.3)."""

    is_closed: bool
    open_time: str | None = Field(default=None, pattern=_TIME_PATTERN)
    close_time: str | None = Field(default=None, pattern=_TIME_PATTERN)


class BusinessDayResponse(BaseModel):
    """Computed schedule state for one business date (PROJECT-SPEC §5.1, §5.6)."""

    business_date: date
    timezone: str
    is_open: bool
    is_open_now: bool
    current_business_date: date
    shift_start: datetime | None
    shift_end: datetime | None


class TableSummary(BaseModel):
    """Table geometry and state (PROJECT-SPEC §6.5)."""

    id: int
    hall_id: int
    hall_name: str | None = None
    number: str
    capacity: int
    is_bookable: bool
    archived_at: datetime | None
    x: float
    y: float
    width: float
    height: float
    rotation: float
    shape: str
    z_index: int

    @classmethod
    def from_model(cls, table: Table, *, hall_name: str | None = None) -> TableSummary:
        return cls(
            id=table.id,
            hall_id=table.hall_id,
            hall_name=hall_name,
            number=table.number,
            capacity=table.capacity,
            is_bookable=table.is_bookable,
            archived_at=table.archived_at,
            x=float(table.x),
            y=float(table.y),
            width=float(table.width),
            height=float(table.height),
            rotation=float(table.rotation),
            shape=table.shape,
            z_index=table.z_index,
        )


class HallSummary(BaseModel):
    """Hall metadata (canvas size, revision, archive state)."""

    id: int
    name: str
    is_bookable: bool
    canvas_width: int
    canvas_height: int
    layout_revision: int
    archived_at: datetime | None
    table_count: int

    @classmethod
    def from_model(cls, hall: Hall, *, table_count: int) -> HallSummary:
        return cls(
            id=hall.id,
            name=hall.name,
            is_bookable=hall.is_bookable,
            canvas_width=hall.canvas_width,
            canvas_height=hall.canvas_height,
            layout_revision=hall.layout_revision,
            archived_at=hall.archived_at,
            table_count=table_count,
        )


class HallDetail(BaseModel):
    """A hall with its tables and static elements (read-only canvas source)."""

    id: int
    name: str
    is_bookable: bool
    canvas_width: int
    canvas_height: int
    layout_revision: int
    archived_at: datetime | None
    static_elements: list[StaticElement]
    tables: list[TableSummary]

    @classmethod
    def from_model(cls, hall: Hall, tables: list[Table]) -> HallDetail:
        return cls(
            id=hall.id,
            name=hall.name,
            is_bookable=hall.is_bookable,
            canvas_width=hall.canvas_width,
            canvas_height=hall.canvas_height,
            layout_revision=hall.layout_revision,
            archived_at=hall.archived_at,
            static_elements=hall.static_elements,
            tables=[TableSummary.from_model(t) for t in tables],
        )


class HallsResponse(BaseModel):
    halls: list[HallSummary]


class TablesResponse(BaseModel):
    tables: list[TableSummary]


class HallCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    canvas_width: int = Field(default=1200, ge=1, le=1_000_000)
    canvas_height: int = Field(default=800, ge=1, le=1_000_000)
    is_bookable: bool = True


class HallUpdate(BaseModel):
    """Editable hall fields. ``is_bookable`` does not bump the layout revision."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    is_bookable: bool | None = None
    canvas_width: int | None = Field(default=None, ge=1, le=1_000_000)
    canvas_height: int | None = Field(default=None, ge=1, le=1_000_000)


class TableUpdate(BaseModel):
    """Operational table toggle. Geometry is owned by layout-save, not here (§35)."""

    is_bookable: bool


class LayoutSaveTable(BaseModel):
    """One table row in a layout-save payload (§31, editor-owned fields only)."""

    model_config = ConfigDict(extra="forbid")

    id: int | None = None
    number: str = Field(min_length=1, max_length=50)
    capacity: int = Field(gt=0, le=100_000)
    shape: str = Field(min_length=1, max_length=50)
    x: float = Field(ge=0)
    y: float = Field(ge=0)
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    rotation: float = Field(default=0, ge=0, le=360)
    z_index: int = 0


class LayoutSaveRequest(BaseModel):
    """Full-state editor layout-save for one hall (§31).

    ``is_bookable`` is deliberately absent on both levels: it is an operational
    flag changed via separate mutations and never bumps ``layout_revision``.
    """

    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
    canvas_width: int = Field(ge=1, le=1_000_000)
    canvas_height: int = Field(ge=1, le=1_000_000)
    tables: list[LayoutSaveTable] = Field(default_factory=list, max_length=1000)
    static_elements: list[StaticElement] = Field(default_factory=list)


# --- bookings (Stage 5) -----------------------------------------------------


def _tables_from_occupancies(table_ids: list[int]) -> list[int]:
    return sorted(table_ids)


class BookingCreate(BaseModel):
    """Admin manual create (§19). ``ONLINE`` is reserved for the public flow (§8).

    Times are absolute ISO-8601 instants; the backend derives the business date
    and the shift snapshot from the canonical schedule resolver.
    """

    model_config = ConfigDict(extra="forbid")
    open_immediately: bool = False
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    table_ids: list[int] = Field(min_length=1, max_length=50)
    party_size: int = Field(ge=1, le=1000)
    source: Literal["PHONE", "VK", "WALK_IN", "OTHER"]
    guest_name: str = Field(min_length=1, max_length=100)
    guest_phone_raw: str | None = Field(default=None, max_length=50)
    guest_comment: str | None = Field(default=None, max_length=1000)


class BookingGuestEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)
    guest_name: str | None = Field(default=None, min_length=1, max_length=100)
    guest_phone_raw: str | None = Field(default=None, max_length=50)
    guest_comment: str | None = Field(default=None, max_length=1000)
    party_size: int | None = Field(default=None, ge=1, le=1000)


class BookingLifecycle(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)


class BookingCancel(BaseModel):
    """Cancel an unopened booking (§9, §11)."""

    expected_version: int = Field(ge=1)
    reason: Literal[
        "GUEST_CANCELED",
        "NO_SHOW",
        "DUPLICATE",
        "UNREACHABLE",
        "RESCHEDULED",
        "GUEST_LATE",
        "CREATION_ERROR",
        "TERMS_REFUSED",
        "INVALID_DATA",
        "MOVED_ELSEWHERE",
        "NO_TABLES",
        "VENUE_CLOSED",
        "ENTRY_REFUSED",
        "OTHER",
    ]
    note: str | None = Field(default=None, max_length=500)


class BookingChangeTime(BaseModel):
    """Move a NEW/WAITING booking, or change only the end of any booking (§26).

    Omitting ``starts_at`` (or sending the booking's current start) performs an
    end-only change: allowed for NEW/WAITING and for OPEN before its plan end.
    """

    expected_version: int = Field(ge=1)
    starts_at: AwareDatetime | None = None
    ends_at: AwareDatetime


class BookingTableAdd(BaseModel):
    """Add one or more tables to a NEW/WAITING/OPEN booking (§20)."""

    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)
    table_ids: list[int] = Field(min_length=1, max_length=50)


class BookingTableReplace(BaseModel):
    """Atomically replace/reseat a set of tables, `remove old + add new` (§20.6).

    A single-table replace sends ``from_table_ids=[old]``/``to_table_ids=[new]``;
    a full reseat sends the whole current and target sets.
    """

    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)
    from_table_ids: list[int] = Field(default_factory=list, max_length=50)
    to_table_ids: list[int] = Field(default_factory=list, max_length=50)


class BookingSummary(BaseModel):
    """Booking representation for the admin book (no idempotency keys/HMACs)."""

    id: int
    number: int
    venue_id: int
    business_date: date
    status: str
    source: str
    party_size: int
    guest_name: str | None
    guest_phone_raw: str | None
    guest_phone_normalized: str | None
    guest_comment: str | None
    shift_starts_at: datetime
    shift_ends_at: datetime
    starts_at: datetime
    ends_at: datetime
    table_ids: list[int]
    live_table_ids: list[int]
    available_actions: list[str]
    is_overdue: bool
    is_previous_shift: bool
    evaluated_at: datetime | None
    opened_at: datetime | None
    waiting_at: datetime | None
    closed_at: datetime | None
    cancellation_note: str | None
    can_investigate_network: bool
    version: int
    canceled_at: datetime | None
    cancellation_reason: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_view(cls, view: BookingView) -> BookingSummary:
        booking = view.booking
        return cls(
            id=booking.id,
            number=booking.number,
            venue_id=booking.venue_id,
            business_date=booking.business_date,
            status=booking.status,
            source=booking.source,
            party_size=booking.party_size,
            guest_name=booking.guest_name,
            guest_phone_raw=booking.guest_phone_raw,
            guest_phone_normalized=booking.guest_phone_normalized,
            guest_comment=booking.guest_comment,
            shift_starts_at=booking.shift_starts_at,
            shift_ends_at=booking.shift_ends_at,
            starts_at=booking.starts_at,
            ends_at=booking.ends_at,
            table_ids=_tables_from_occupancies(view.table_ids),
            live_table_ids=view.live_table_ids,
            available_actions=view.available_actions,
            is_overdue=view.is_overdue,
            is_previous_shift=view.is_previous_shift,
            evaluated_at=view.evaluated_at,
            opened_at=booking.opened_at,
            waiting_at=booking.waiting_at,
            closed_at=booking.closed_at,
            cancellation_note=booking.cancellation_note,
            can_investigate_network=view.can_investigate_network,
            version=booking.version,
            canceled_at=booking.canceled_at,
            cancellation_reason=booking.cancellation_reason,
            created_at=booking.created_at,
            updated_at=booking.updated_at,
        )


class BookingListResponse(BaseModel):
    items: list[BookingSummary]
    next_cursor: int | None = None


class BookingEventSummary(BaseModel):
    """One append-only history row; payload holds no PII (§6.10)."""

    id: int
    event_type: str
    actor_type: str
    payload: dict[str, object]
    created_at: datetime

    @classmethod
    def from_model(cls, event: BookingEvent) -> BookingEventSummary:
        return cls(
            id=event.id,
            event_type=event.event_type,
            actor_type=event.actor_type,
            payload=event.payload,
            created_at=event.created_at,
        )


class BookingHistoryResponse(BaseModel):
    events: list[BookingEventSummary]
