"""Operator CLI (PROJECT-SPEC §53).

There is no superadmin UI in v1; venue lifecycle is operated from the command
line. The CLI performs DML only (it is not a migration tool) and therefore
connects with the application role, exactly like a request would.

Commands
--------
``create-venue``   create a venue and its single admin account.
``reset-password`` replace the admin password and invalidate every session.
``suspend-venue``  disable a venue and revoke its sessions.
``enable-venue``   re-enable a previously suspended venue.
``list-venues``    show venues and their status.

By default ``create-venue`` and ``reset-password`` generate a cryptographically
random password and print it exactly once; ``--password`` is the explicit
opt-in to a manual value. Secrets are never echoed in error output.

Scope note: §53 lists the booking counter, base schedule rows and first hall as
part of onboarding. Those tables belong to Stage 3 (schedule) and Stage 4
(halls), so they are created by their owning stages once they exist; creating
them here would require inventing schema ahead of the spec.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence

from app.db.session import dispose_engine, init_engine, session_scope
from app.domain.timezone import (
    UnknownTimezoneError,
    UnsupportedTimezoneError,
    assert_supported_timezone,
)
from app.domain.venues import InvalidSlugError, validate_slug
from app.security.passwords import get_password_hasher, init_password_hasher
from app.security.tokens import generate_password
from app.services.errors import ServiceError, VenueNotFoundError
from app.services.venues import (
    create_venue,
    get_admin_for_venue,
    get_venue,
    list_venues,
    revoke_all_sessions,
    set_password_hash,
    set_venue_active,
)
from app.settings import get_settings

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="takeplace",
        description="Takeplace operator CLI (venue onboarding and lifecycle).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create-venue", help="create a venue and its admin account")
    create.add_argument("--slug", required=True, help="lowercase slug, 2-40 chars a-z0-9-")
    create.add_argument("--name", required=True, help="public venue name")
    create.add_argument("--timezone", required=True, help="IANA timezone, e.g. Europe/Moscow")
    create.add_argument("--login", required=True, help="globally unique admin login")
    create.add_argument("--address", default=None)
    create.add_argument("--phone", default=None)
    create.add_argument(
        "--password",
        default=None,
        help="explicit initial password; omit to generate and print one once",
    )
    create.add_argument(
        "--online-booking",
        action="store_true",
        help="enable online booking at creation (default: disabled)",
    )

    reset = sub.add_parser(
        "reset-password", help="replace the admin password and invalidate all sessions"
    )
    reset.add_argument("venue", help="venue id or slug")
    reset.add_argument(
        "--password",
        default=None,
        help="explicit new password; omit to generate and print one once",
    )

    for name, help_text in (
        ("suspend-venue", "disable a venue and revoke its sessions"),
        ("enable-venue", "re-enable a suspended venue"),
    ):
        command = sub.add_parser(name, help=help_text)
        command.add_argument("venue", help="venue id or slug")

    sub.add_parser("list-venues", help="list venues and their status")

    return parser


def _print_generated_password(login: str, password: str) -> None:
    print()
    print("  initial password (shown once, store it now):")
    print(f"    login:    {login}")
    print(f"    password: {password}")
    print()


async def _cmd_create_venue(args: argparse.Namespace) -> int:
    # Validate cheap, reversible inputs before paying for Argon2.
    validate_slug(args.slug)
    assert_supported_timezone(args.timezone)

    password = args.password or generate_password()
    generated = args.password is None
    password_hash = await get_password_hasher().hash(password)

    async with session_scope() as session:
        venue, admin = await create_venue(
            session,
            slug=args.slug,
            name=args.name,
            timezone=args.timezone,
            login=args.login,
            password_hash=password_hash,
            address=args.address,
            phone=args.phone,
            online_booking_enabled=args.online_booking,
        )
        venue_id, slug = venue.id, venue.slug
        login = admin.login

    print(f"created venue {slug!r} (id={venue_id}) with admin login {login!r}")
    print(f"  timezone: {args.timezone}")
    print(f"  online booking: {'enabled' if args.online_booking else 'disabled'}")
    if generated:
        _print_generated_password(login, password)
    return EXIT_OK


async def _cmd_reset_password(args: argparse.Namespace) -> int:
    password = args.password or generate_password()
    generated = args.password is None
    password_hash = await get_password_hasher().hash(password)

    async with session_scope() as session:
        venue = await get_venue(session, args.venue)
        admin = await get_admin_for_venue(session, venue.id)
        await set_password_hash(session, admin, password_hash)
        revoked = await revoke_all_sessions(session, venue.id)
        login = admin.login
        slug = venue.slug

    print(f"reset password for venue {slug!r} (admin {login!r})")
    print(f"  invalidated sessions: {revoked}")
    if generated:
        _print_generated_password(login, password)
    return EXIT_OK


async def _cmd_set_active(args: argparse.Namespace, *, active: bool) -> int:
    async with session_scope() as session:
        venue = await get_venue(session, args.venue)
        await set_venue_active(session, venue, active=active)
        slug = venue.slug
        venue_id = venue.id

    verb = "enabled" if active else "suspended"
    print(f"{verb} venue {slug!r} (id={venue_id})")
    if not active:
        print("  all sessions for this venue were revoked")
    return EXIT_OK


async def _cmd_list_venues(_args: argparse.Namespace) -> int:
    async with session_scope() as session:
        venues = await list_venues(session)

    if not venues:
        print("no venues")
        return EXIT_OK
    header = f"{'id':>4}  {'slug':<24}  {'active':<7}  {'online':<7}  {'timezone':<24}  name"
    print(header)
    for venue in venues:
        print(
            f"{venue.id:>4}  {venue.slug:<24}  "
            f"{'yes' if venue.is_active else 'no':<7}  "
            f"{'yes' if venue.online_booking_enabled else 'no':<7}  "
            f"{venue.timezone:<24}  {venue.name}"
        )
    return EXIT_OK


async def _dispatch(args: argparse.Namespace) -> int:
    settings = get_settings()
    init_engine(settings)
    init_password_hasher(settings.argon2_max_concurrency)
    try:
        if args.command == "create-venue":
            return await _cmd_create_venue(args)
        if args.command == "reset-password":
            return await _cmd_reset_password(args)
        if args.command == "suspend-venue":
            return await _cmd_set_active(args, active=False)
        if args.command == "enable-venue":
            return await _cmd_set_active(args, active=True)
        if args.command == "list-venues":
            return await _cmd_list_venues(args)
        raise AssertionError(f"unhandled command: {args.command}")
    finally:
        await dispose_engine()


def _report_error(exc: Exception) -> int:
    """Print an operator-facing message without leaking any secret."""
    if isinstance(exc, InvalidSlugError):
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    if isinstance(exc, UnknownTimezoneError):
        print(f"error: {exc}. Use an IANA zone such as Europe/Moscow.", file=sys.stderr)
        return EXIT_USAGE
    if isinstance(exc, UnsupportedTimezoneError):
        print(
            f"error: {exc}. v1 supports only zones without DST/offset transitions "
            "within the rolling horizon.",
            file=sys.stderr,
        )
        return EXIT_USAGE
    if isinstance(exc, VenueNotFoundError):
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    if isinstance(exc, ServiceError):
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
    return EXIT_ERROR


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point. Returns a process exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return asyncio.run(_dispatch(args))
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return EXIT_ERROR
    except Exception as exc:  # noqa: BLE001 - top-level operator boundary
        return _report_error(exc)


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())
