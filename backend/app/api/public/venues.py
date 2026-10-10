"""Public booking API (PROJECT-SPEC §16–§18, §32.2, §34, §40, §50).

This router is mounted at ``/api/public/v1`` and is intentionally separate from
the admin API: it does not require a session cookie and it never trusts a
client-supplied ``venue_id``. The only tenant anchor is the ``slug`` in the path,
which is resolved to exactly one ``Venue`` and used for every DB lookup.

Public contract rules (§34, §36):

* ``GET /venues/{slug}`` — only public venue/hall/table data;
* ``GET /venues/{slug}/availability`` — read-only snapshot, soft rate limited;
* ``POST /venues/{slug}/bookings`` — ONLINE create, client-generated UUID
  ``Idempotency-Key``, public HMAC idempotency, ``request_ip_hmac`` fingerprint,
  honeypot, CAPTCHA hook, soft rate limits, kill-switch gate, final DB exclusion.

Everything that mutates bookings is delegated to the existing booking-core
``create_public_booking`` (§32.2). This module only adds the public transport
guards, the abuse fingerprint and the routing/slug tenant isolation.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import (
    ApiError,
    booking_rule_violation,
    captcha_required,
    hall_not_bookable,
    honeypot_filled,
    idempotency_key_reused,
    not_found,
    online_booking_disabled,
    public_availability_rate_limited,
    public_rate_limited,
    service_unavailable,
    table_not_bookable,
)
from app.api.public.schemas import (
    PublicAvailabilityResponse,
    PublicAvailabilitySlot,
    PublicAvailabilityTable,
    PublicCreateRequest,
    PublicCreateResponse,
    PublicHall,
    PublicVenueResponse,
)
from app.db.models import Hall, Table
from app.db.session import get_db_session
from app.domain.schedule import current_business_date
from app.domain.timezone import load_timezone
from app.security.client_ip import canonical_client_ip
from app.security.tokens import hmac_sha256_hex
from app.services.bookings import create_public_booking
from app.services.errors import (
    BookingConflictError,
    BookingNotFoundError,
    BookingRuleViolationError,
    HallNotBookableError,
    IdempotencyKeyReusedError,
    OnlineBookingDisabledError,
    ServiceUnavailableError,
    TableNotBookableError,
    TableNotFoundError,
    VenueNotFoundError,
)
from app.services.public_availability import build_public_availability
from app.services.public_bookings import (
    CaptchaVerificationError,
    build_captcha_adapter,
    resolve_public_venue,
)
from app.services.public_rate_limit import (
    get_public_rate_limiters,
    public_create_fingerprint,
    public_get_fingerprint,
)
from app.services.schedule import load_schedule_table
from app.settings import Settings, get_settings

router = APIRouter(tags=["public-booking"])


# ---------------------------------------------------------------------------
# Error translation
# ---------------------------------------------------------------------------


@contextmanager
def _translating_errors() -> Iterator[None]:
    """Map booking service errors to stable API error codes (§36)."""
    try:
        yield
    except VenueNotFoundError as exc:
        raise not_found("VENUE_NOT_FOUND", str(exc)) from exc
    except BookingNotFoundError as exc:
        raise not_found("BOOKING_NOT_FOUND", str(exc)) from exc
    except TableNotFoundError as exc:
        raise not_found("TABLE_NOT_FOUND", str(exc)) from exc
    except BookingRuleViolationError as exc:
        raise booking_rule_violation(str(exc)) from exc
    except BookingConflictError as exc:
        raise ApiError(409, "BOOKING_CONFLICT", "the selected time is no longer available") from exc
    except TableNotBookableError as exc:
        raise table_not_bookable(str(exc)) from exc
    except HallNotBookableError as exc:
        raise hall_not_bookable(str(exc)) from exc
    except IdempotencyKeyReusedError as exc:
        raise idempotency_key_reused(str(exc)) from exc
    except OnlineBookingDisabledError as exc:
        raise online_booking_disabled() from exc
    except ServiceUnavailableError as exc:
        raise service_unavailable(str(exc)) from exc


def _slug_guard(slug: str) -> str:
    """Reject obviously malformed slugs before any DB work (cheap guard)."""
    if not slug or "/" in slug or "\\" in slug:
        raise not_found("VENUE_NOT_FOUND", "venue not found")
    return slug


def _parse_idempotency_key(raw: str) -> str:
    """Validate the presence and UUID shape of the public Idempotency-Key header."""
    import uuid

    if not raw:
        raise ApiError(400, "IDEMPOTENCY_KEY_MISSING", "Idempotency-Key header is required")
    try:
        uuid.UUID(raw)
    except ValueError as exc:
        raise ApiError(400, "IDEMPOTENCY_KEY_INVALID", "Idempotency-Key must be a UUID") from exc
    return raw


def _canonical_request_ip(request: Request) -> str:
    """The canonical client IP after the trusted-proxy policy (§39.5, §40).

    ``request.client`` is the address the ASGI server resolved after the
    ``FORWARDED_ALLOW_IPS`` policy, so a direct client cannot spoof it via
    ``X-Forwarded-For``. :func:`canonical_client_ip` collapses equivalent IPv6
    textual forms onto one identity and maps a missing/invalid address to the
    shared :data:`UNKNOWN_CLIENT_IP`, so every fingerprint below is derived from
    the exact same value.
    """
    return canonical_client_ip(request.client.host if request.client else None)


def _client_ip_hmac(settings: Settings, request: Request) -> str:
    """HMAC-SHA-256 of the canonical client IP under the abuse key (§40).

    Raw IP is **not** stored anywhere; this fingerprint is only ever used as a
    rate-limit key.
    """
    return hmac_sha256_hex(settings.abuse_hmac_key, _canonical_request_ip(request))


def _venue_ip_hmac(settings: Settings, *, venue_id: int, client_ip: str) -> str:
    """Tenant-scoped IP fingerprint persisted on a booking (§40).

    ``client_ip`` must already be canonical (:func:`_canonical_request_ip`); the
    ``venue_id`` prefix keeps the stored fingerprint scoped to one tenant so the
    ``same_network_as`` comparison can never span venues.
    """
    return hmac_sha256_hex(settings.abuse_hmac_key, f"{venue_id}:{client_ip}")


# ---------------------------------------------------------------------------
# GET /venues/{slug}
# ---------------------------------------------------------------------------


async def _load_public_halls(session: AsyncSession, venue_id: int) -> list[PublicHall]:
    """Active, non-archived halls with their active, non-archived tables (§34)."""
    hall_rows = await session.execute(
        select(Hall).where(Hall.venue_id == venue_id, Hall.archived_at.is_(None)).order_by(Hall.id)
    )
    halls = list(hall_rows.scalars().all())
    if not halls:
        return []
    hall_ids = [hall.id for hall in halls]
    table_rows = await session.execute(
        select(Table)
        .where(
            Table.venue_id == venue_id,
            Table.hall_id.in_(hall_ids),
            Table.archived_at.is_(None),
        )
        .order_by(Table.id)
    )
    tables_by_hall: dict[int, list[Table]] = {}
    for table in table_rows.scalars().all():
        tables_by_hall.setdefault(table.hall_id, []).append(table)
    return [PublicHall.from_model(hall, tables_by_hall.get(hall.id, [])) for hall in halls]


@router.get(
    "/venues/{slug}",
    response_model=PublicVenueResponse,
    responses={404: {"description": "venue not found"}},
)
async def get_public_venue(
    slug: str,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    request: Request,
) -> PublicVenueResponse:
    """Return only public venue/hall/table data for one tenant (§34)."""
    slug = _slug_guard(slug)
    with _translating_errors():
        venue = await resolve_public_venue(session, slug)
        _limit_read(venue.id, settings, request)
        halls = await _load_public_halls(session, venue.id)
    return PublicVenueResponse.from_venue(venue, halls, settings.public_privacy_policy_version)


# ---------------------------------------------------------------------------
# GET /venues/{slug}/availability
# ---------------------------------------------------------------------------


@router.get(
    "/venues/{slug}/availability",
    response_model=PublicAvailabilityResponse,
    responses={404: {"description": "venue not found"}, 429: {"description": "rate limited"}},
)
async def get_public_availability(
    slug: str,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    request: Request,
    business_date: Annotated[date | None, Query()] = None,
    hall_id: Annotated[int | None, Query()] = None,
    table_id: Annotated[int | None, Query()] = None,
    party_size: Annotated[int, Query(ge=1, le=200)] = 1,
) -> PublicAvailabilityResponse:
    """Return a read-only availability snapshot for one venue/date selection (§16, §34)."""
    slug = _slug_guard(slug)
    with _translating_errors():
        venue = await resolve_public_venue(session, slug)

    _limit_read(venue.id, settings, request)

    if business_date is None:
        tz = load_timezone(venue.timezone)
        schedule = await load_schedule_table(session, venue.id)
        business_date = current_business_date(datetime.now(UTC), schedule, tz)

    with _translating_errors():
        availability = await build_public_availability(
            session,
            venue,
            business_date=business_date,
            hall_id=hall_id,
            table_id=table_id,
            party_size=party_size,
            now=datetime.now(UTC),
        )
    return PublicAvailabilityResponse(
        business_date=availability.business_date,
        venue_timezone=availability.venue_timezone,
        shift_start=availability.shift_start,
        shift_end=availability.shift_end,
        is_open=availability.is_open,
        tables=[
            PublicAvailabilityTable(
                id=t.id,
                number=t.number,
                capacity=t.capacity,
                hall_id=t.hall_id,
                hall_name=t.hall_name,
                slots=[
                    PublicAvailabilitySlot(
                        start=s.start,
                        earliest_end=s.earliest_end,
                        latest_end=s.latest_end,
                        end_options=s.end_options,
                    )
                    for s in t.slots
                ],
            )
            for t in availability.tables
        ],
    )


# ---------------------------------------------------------------------------
# POST /venues/{slug}/bookings
# ---------------------------------------------------------------------------


@router.post(
    "/venues/{slug}/bookings",
    response_model=PublicCreateResponse,
    responses={
        200: {"description": "idempotent replay"},
        201: {"description": "new booking"},
        400: {"description": "missing or invalid Idempotency-Key / honeypot / captcha"},
        404: {"description": "venue not found"},
        409: {"description": "idempotency key reused, booking conflict, or online disabled"},
        429: {"description": "rate limited"},
        503: {"description": "service unavailable"},
    },
)
async def post_public_booking(
    slug: str,
    request: Request,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    body: PublicCreateRequest,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")] = "",
) -> PublicCreateResponse:
    """Create a public ONLINE booking idempotently (§18, §18.2, §32.2).

    The client must send:

    * ``Idempotency-Key``: a UUID generated once per user action and reused for
      network retries of the same action;
    * ``honeypot``: must be empty for a normal user (§40, §50).

    The server follows the §18.2 ordering: cheap transport guards, slug resolve,
    CAPTCHA hook, soft rate limit, then delegation to the booking core which does
    the idempotency lookup before mutable gates, the kill-switch final gate, and
    the transactional create under the canonical lock order.
    """
    slug = _slug_guard(slug)
    _parse_idempotency_key(idempotency_key)

    # 1. Honeypot transport guard (§40, §50).
    if body.honeypot and body.honeypot.strip():
        raise honeypot_filled()

    # 2. Resolve venue by slug without mutable-state gate (§18.2 step 2).
    with _translating_errors():
        venue = await resolve_public_venue(session, slug)
    await session.commit()

    # 3. CAPTCHA hook: only runs when enabled (§50).
    captcha = build_captcha_adapter(enabled=settings.public_captcha_enabled)
    try:
        await captcha.verify(body.captcha_token)
    except CaptchaVerificationError as exc:
        raise captcha_required() from exc

    # 4. Soft create rate limit (§40 transport guard, before DB work). The stored
    #    fingerprint and the rate-limit key share one canonicalisation of the
    #    client IP, so both agree on the identity of the caller.
    client_ip = _canonical_request_ip(request)
    client_ip_hmac = _client_ip_hmac(settings, request)
    limiters = get_public_rate_limiters()
    rate_key = public_create_fingerprint(venue.id, client_ip_hmac)
    retry_after = limiters.check_create(rate_key)
    if retry_after:
        raise public_rate_limited(retry_after)

    # 5. Delegate to the booking core (§32.2): idempotency lookup before mutable
    #    gates, kill-switch final gate, transactional create with shared locks.
    from app.services.bookings import PublicBookingInput

    booking_input = PublicBookingInput(
        starts_at=body.starts_at,
        ends_at=body.ends_at,
        table_id=body.table_id,
        party_size=body.party_size,
        guest_name=body.guest_name,
        guest_phone_raw=body.guest_phone_raw,
        guest_comment=body.guest_comment,
        privacy_policy_version=body.privacy_policy_version,
    )
    with _translating_errors():
        view, created = await create_public_booking(
            session,
            venue_id=venue.id,
            data=booking_input,
            idempotency_key=idempotency_key,
            hmac_key=settings.idempotency_hmac_key,
            request_ip_hmac=_venue_ip_hmac(settings, venue_id=venue.id, client_ip=client_ip),
            ip_hmac_ttl_days=settings.request_ip_hmac_ttl_days,
            vk_late_grace_seconds=settings.vk_notification_late_grace_seconds,
            vk_max_age_seconds=settings.vk_notification_max_age_seconds,
        )
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    if created:
        limiters.record_create(venue.id)
    return PublicCreateResponse.from_view(view)


def _limit_read(venue_id: int, settings: Settings, request: Request) -> None:
    limiter = get_public_rate_limiters().get
    key = public_get_fingerprint(venue_id, _client_ip_hmac(settings, request))
    if not limiter.is_allowed(key):
        raise public_availability_rate_limited(limiter.retry_after(key))
