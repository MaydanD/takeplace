# Stage 7 — Admin Booking Book

Starting HEAD: `c8f77366e0fc4eae09ba08f4f858b68173eb1639`.

## Delivered

- `/admin/bookings`: business-day list, table map/filter, booking card, manual
  PHONE/VK/OTHER, atomic WALK_IN, guest editing, cancel/change-time, event history.
- Generated OpenAPI/TypeScript contracts. Dates and history use venue timezone.
- Cursor pagination with combined date/status/source/table/exact phone/number
  filters. Unresolved includes overdue OPEN. Network investigation is tenant-scoped,
  uses booking identity rather than exposing HMAC, and checks both fingerprint TTLs.
- PATCH whitelists guest fields. Booking row lock precedes expected_version check;
  stale requests return BOOKING_STALE. Each successful edit/cancel/time command
  increments once. Create (including atomic WALK_IN) starts at version 1.
- WALK_IN uses table FOR UPDATE, server clock after locks, ceil-to-five-minute start,
  actual opened_at, early-gap conflict validation, capacity and shared booking core.
  Booking, occupancies, live rows, BOOKING_CREATED and BOOKING_OPENED commit together.
  Replay preserves identity/version/events; PHONE/VK/OTHER keep existing HMAC compatibility.
- Migration `20261004_0006`: live table uniqueness and tenant/business-date composite FKs.
- BOOKING_EDITED events store field names only. Existing cancellation/time events
  are reused. SQL parameters and query-string access logs are suppressed.

## Verification — 2026-10-04

- Backend full run: **435 passed, 0 failed, 0 skipped**; 264 unit + 171 PostgreSQL integration.
- Stage 5 concurrency: **15 passed**; Stage 6 concurrency: **10 passed**.
- Stage 7 PostgreSQL suite: **19 passed**; WALK_IN domain tests: **7 passed**.
  Includes two edits with version 1, WALK_IN vs admin/public create, concurrent
  idempotency replay, capacity edit vs layout shrink, early gap/BLOCK rollback,
  near-close duration, guest whitelist, events/PII, fingerprint TTL and tenant isolation.
- The final overdue OPEN party-size correction additionally passed its targeted
  PostgreSQL regression after the full run.
- Ruff, format and mypy: passed. Frontend lint, format, typecheck, **46 tests**,
  production build and api:check: passed. Exported OpenAPI matches backend.
- Separate clean database: all migrations from zero, Stage 7 downgrade/re-upgrade,
  alembic check: passed, no drift.
- Browser/Docker smoke: login, PHONE create, guest edit, card/history, two-client
  stale conflict with refresh, WALK_IN without phone immediately OPEN with live table
  and two events: passed using an isolated local test venue and synthetic guest data.
- Diff review, whitespace check, known-environment-secret scan and credential-pattern
  scan: passed. No real guest data or credentials were added to tracked files.

## Defects corrected

- BLOCK was omitted by the pre-check because its booking_id is NULL; critical for
  the WALK_IN gap before the rounded plan start.
- Serialized WALK_IN idempotency retries could encounter the first request's live
  conflict before INSERT; replay now resolves that race.
- End-only changes on an already-started NEW/WAITING incorrectly used future-start
  validation; they now retain the shift snapshot and validate a non-expired end.
- OPEN live capacity and current-day archive guards were needed as soon as Stage 7
  began creating factual placement. Overdue OPEN party changes check live capacity
  without requiring plan coverage after the plan has ended.
- Booking list avoids per-row table queries; empty normalized phone cannot match
  bookings with no phone. UI create defaults wait for schedule and use the time grid.

Remaining Stage 7 gaps: none identified by the checks above. Full WAIT/OPEN/CLOSE,
reseating, realtime, editor, and VK outbox remain Stage 8+ and were not implemented.
