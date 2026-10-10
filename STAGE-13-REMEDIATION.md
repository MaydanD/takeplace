# Stage 13 post-audit remediation

This document records the fixes applied after the two independent Stage 13
audits and the operational handoff for Stage 14. It is deliberately short: the
code and tests are the source of truth.

## What changed

| Fix | Scope | Files |
| --- | --- | --- |
| FIX-01 | Public booking `request_ip_hmac` is now derived from the canonicalised client IP (shared `UNKNOWN_CLIENT_IP` fallback), not the raw `request.client.host`. | `backend/app/api/public/venues.py` |
| FIX-02 | Retention/anonymization no longer depends on VK. The worker always runs the maintenance loop, in maintenance-only mode when VK is disabled, and publishes a durable maintenance heartbeat. | `backend/app/worker/__init__.py`, `backend/app/services/maintenance_heartbeat.py`, `backend/app/api/health.py`, `backend/app/cli.py`, migration `20261007_0009` |
| FIX-03 | Production `FORWARDED_ALLOW_IPS` rejects every trust-all form, including `0.0.0.0/0` and `::/0`, and malformed entries. | `backend/app/settings.py` |
| FIX-04 | The invariant audit also checks `notification_outbox.payload` for forbidden PII keys. | `backend/app/services/invariants.py` |
| FIX-05 | Expired IP-HMAC and admin-session cleanup use bounded batches with `FOR UPDATE SKIP LOCKED`. | `backend/app/services/privacy.py`, `backend/app/services/auth.py` |
| FIX-06 | Log redaction protects `guest_name` and stops masking ordinary 8-digit dates/identifiers as phone numbers. | `backend/app/logging_config.py` |

## Maintenance architecture (FIX-02)

* The background process (`python -m app.worker`) runs two independent `asyncio`
  tasks: VK delivery (only when `TAKEPLACE_VK_WORKER_ENABLED=true`) and the
  periodic maintenance loop (always). Neither loop can stop the other; a failed
  cycle is logged and retried on its own cadence.
* Maintenance never needs VK credentials. Disabling VK keeps the process alive
  and keeps anonymization running.
* Each maintenance pass runs `run_maintenance` (retention + read-only invariant
  audit) and, in the **same transaction**, upserts the `maintenance_heartbeat`
  row. A crash rolls back the heartbeat, so it only advances when cleanup
  actually committed.
* `GET /health/ops` exposes `maintenance_last_success_age_seconds`,
  `maintenance_stale` and `maintenance_last_error` (a sanitized exception class
  name). `maintenance_stale` is true when no pass has ever committed or the last
  one is older than `3 × TAKEPLACE_MAINTENANCE_INTERVAL_SECONDS`, and it degrades
  ops **independently of VK**. A silently stopped retention job is therefore
  visible to monitoring.
* Manual runs (`python -m app.cli run-maintenance`) remain available and also
  refresh the heartbeat.

### Guaranteeing maintenance runs on Stage 14

The periodic loop lives in the `python -m app.worker` process, so Stage 14 must
run **exactly one** worker replica (or elect a single maintenance leader if the
worker is horizontally scaled). Recommended for the production Compose stack:

1. Define a `worker` service in `docker-compose.yml` with the same image as the
   API, `command: python -m app.worker`, `restart: unless-stopped`, no published
   ports, and the API's environment/network.
2. Run a single `worker` instance (the v1 fleet size). If it is ever scaled out,
   add a leader election or a separate maintenance-only service so cleanup is
   not run concurrently from many replicas.
3. Optionally schedule `python -m app.cli run-maintenance` as a cron/one-shot
   task as a belt-and-braces backup; it reuses the same idempotent code path and
   refreshes the same heartbeat.
4. Alert on `/health/ops`: `maintenance_stale == true`, `maintenance_last_error`
   not null, or `status == "degraded"`.

## Stage 14 handoff checklist

* **Trusted proxy:** set `TAKEPLACE_FORWARDED_ALLOW_IPS` to the actual Caddy
  address/network on the production network (never `*`, `0.0.0.0/0` or `::/0`).
  Determine the address from the real Compose network, not a wide Docker subnet.
* **Production scheduler:** run exactly one worker (see above); confirm
  `maintenance_stale` is false after the first interval and alert on it.
* **Backup:** schedule `pg_dump`/WAL archiving for the PostgreSQL volume and
  verify a restore into a scratch database.
* **Recovery:** document and rehearse restoring the database plus re-running
  `alembic upgrade head`; the outbox and `maintenance_heartbeat` are ordinary
  tables and restore with the rest of the schema.
* **Monitoring:** scrape `/health/ops` for `status`, `outbox_unacknowledged_dead`,
  `worker_heartbeat_age_seconds`, `maintenance_stale` and `maintenance_last_error`;
  alert on `maintenance_stale` and on unacknowledged DEAD rows.
* **Secrets/TLS/DNS/VPS, real Compose:** out of scope for Stage 13; handled in
  Stage 14.
