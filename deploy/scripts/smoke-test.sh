#!/usr/bin/env bash
# =============================================================================
# Takeplace — end-to-end smoke test for the deployed closed test stand.
#
#   bash deploy/scripts/smoke-test.sh [--domain <host>] [--max-wait <seconds>]
#                                   [--admin-login <login>]
#                                   [--admin-password-file <path>]
#
# The inner admin credential is read from a mode-600 file (default
# <deploy root>/secrets/admin.pass; override with --admin-password-file or
# TAKEPLACE_ADMIN_PASSWORD_FILE) — it is deliberately not a literal here.
#
# Exercises the REAL deployment over its public HTTPS origin: Caddy (TLS, plus
# Basic Auth when it is enabled), the static SPA, the admin API
# (auth/session/CSRF), the schedule,
# halls/tables, the manual booking lifecycle with the temporal rules from
# PROJECT-SPEC §9-§23, the SSE realtime stream, two concurrent admin sessions,
# the health endpoints and login rate limiting.
#
# Idempotent: it reuses the venue's existing tables instead of re-saving a layout
# (which would archive tables still holding future bookings, correctly refused
# with 409 TABLE_ARCHIVE_BLOCKED), and it restores anything it toggles.
#
# Honest reporting: a check whose precondition cannot be met right now is
# reported as SKIP with the reason — never counted as a pass. Two rules make the
# lifecycle time-dependent by design:
#   * §10 WAITING requires `starts_at <= now < ends_at`;
#   * §10 OPEN requires `shift_starts_at <= now < ends_at`, so a booking for a
#     later business date cannot be opened yet.
# The WAITING leg therefore creates a booking starting at the next 5-minute
# boundary and polls until the interval begins (bounded by --max-wait).
#
# Only synthetic test data is created: guests are named "TEST …" and phone
# numbers are obviously fake. Nothing else on the host is touched.
#
# NOTE: the final step deliberately saturates the login limiter, so a second run
# within ~5 minutes can fail at the login step. That is expected.
# =============================================================================
set -uo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/../.." && pwd)"
secrets="$(cd "$repo_root/.." && pwd)/secrets"

domain=""; max_wait=420
admin_login="${TAKEPLACE_ADMIN_LOGIN:-admin}"
admin_password_file="${TAKEPLACE_ADMIN_PASSWORD_FILE:-}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --domain) domain="${2:?}"; shift 2 ;;
    --max-wait) max_wait="${2:?}"; shift 2 ;;
    --admin-login) admin_login="${2:?}"; shift 2 ;;
    --admin-password-file) admin_password_file="${2:?}"; shift 2 ;;
    -h|--help) sed -n '2,37p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 1 ;;
  esac
done

if [[ -z "$domain" ]]; then
  origin="$(grep -E '^TAKEPLACE_CORS_ORIGINS=' "$repo_root/.env" | tail -n1 | cut -d= -f2-)"
  domain="${origin#https://}"; domain="${domain%%,*}"
fi
[[ -n "$domain" ]] || { echo "error: could not determine the domain" >&2; exit 1; }

base="https://$domain"

# The outer Caddy Basic Auth gate is OPTIONAL (see install-caddy-site.sh
# --no-basic-auth). Detect it instead of assuming it: when it is present, every
# request must carry the credentials, so the flag list is built once.
basic_auth_file="$secrets/basic-auth.pass"
outer_gate=0
AUTH_ARGS=()
# Probe /health/ready, not "/": "/" is a 302 to the login page, so its status
# says nothing about the gate.
if [[ "$(curl -sS -o /dev/null -w '%{http_code}' --max-time 20 "$base/health/ready")" == "401" ]]; then
  outer_gate=1
  [[ -r "$basic_auth_file" ]] || { echo "error: cannot read $basic_auth_file" >&2; exit 1; }
  AUTH_ARGS=(-u "takeplace:$(cat "$basic_auth_file")")
  echo "outer gate: Caddy Basic Auth is ENABLED"
else
  echo "outer gate: Caddy Basic Auth is DISABLED (the Takeplace login is the only gate)"
fi

# The inner Takeplace admin credential is NEVER a literal in this script: it is
# read from a mode-600 secrets file so the test can live in Git without
# shipping a password. Default: <deploy root>/secrets/admin.pass.
[[ -n "$admin_password_file" ]] || admin_password_file="$secrets/admin.pass"
[[ -r "$admin_password_file" ]] || {
  echo "error: cannot read $admin_password_file" >&2
  echo "       create it with the inner admin password, mode 600:" >&2
  echo "       install -m 600 /dev/null $admin_password_file && printf '%s\\n' '<password>' > $admin_password_file" >&2
  exit 1
}
admin_password="$(tr -d '\r\n' < "$admin_password_file")"
[[ -n "$admin_password" ]] || { echo "error: $admin_password_file is empty" >&2; exit 1; }
login_body="{\"login\":\"$admin_login\",\"password\":\"$admin_password\"}"

work="$(mktemp -d)"; chmod 700 "$work"
trap 'rm -rf "$work"' EXIT

pass=0; fail=0; skipped=0
ok()   { printf 'PASS  %s\n' "$1"; pass=$((pass + 1)); }
ko()   { printf 'FAIL  %s\n        observed: %s\n' "$1" "${2:-}"; fail=$((fail + 1)); }
skip() { printf 'SKIP  %s\n' "$1"; skipped=$((skipped + 1)); }

jget() { # jget <file> <dotted.path> — "" when the path is absent
  python3 - "$1" "$2" <<'PY'
import json, sys
try:
    node = json.load(open(sys.argv[1]))
    for part in sys.argv[2].split("."):
        node = node[int(part)] if part.isdigit() else node[part]
except Exception:
    node = ""
print("true" if node is True else "false" if node is False else node)
PY
}
jlen() { # jlen <file> <key> — length of a list field, 0 when absent
  python3 - "$1" "$2" <<'PY'
import json, sys
try:
    print(len(json.load(open(sys.argv[1])).get(sys.argv[2]) or []))
except Exception:
    print(0)
PY
}
uuid() { python3 -c 'import uuid;print(uuid.uuid4())'; }

req() { # req <jar> <method> <path> [curl args...] -> HTTP status
  local jar="$1" method="$2" path="$3"; shift 3
  curl -sS -o "$work/body" -D "$work/headers" -w '%{http_code}' \
    --max-time 30 "${AUTH_ARGS[@]}" -b "$jar" -c "$jar" -X "$method" "$@" "$base$path"
}
expect_status() {
  if [[ "$3" == "$1" ]]; then
    ok "$2 ($1)"
  else
    ko "$2 (expected $1)" "$3"
    [[ -s "$work/body" ]] && printf '        body: %s\n' "$(head -c 500 "$work/body")"
  fi
}

echo "smoke test against $base"
echo "----------------------------------------------------------------"

# =========================================================================
# 1. TLS + outer gate (Basic Auth when enabled, direct login page when not)
# =========================================================================
if [[ "$outer_gate" -eq 1 ]]; then
  expect_status 401 "gate: health endpoint needs credentials" \
    "$(curl -sS -o /dev/null -w '%{http_code}' --max-time 30 "$base/health/ready")"
  expect_status 401 "wrong Basic Auth password is rejected" \
    "$(curl -sS -o /dev/null -w '%{http_code}' --max-time 30 -u "takeplace:wrong" "$base/health/ready")"
  hdr="$(curl -sS -o /dev/null -D - --max-time 30 "$base/health/ready" | tr -d '\r' | grep -i '^www-authenticate:' || true)"
  [[ -n "$hdr" ]] && ok "Basic Auth challenge header present" || ko "Basic Auth challenge header" "missing"
else
  # Landing page: with the gate off, "/" is a 302 to the login page (configured in
  # deploy/caddy/takeplace.caddyfile) and the login route itself answers 200.
  expect_status 302 "root path redirects to the login page (outer gate disabled)" \
    "$(curl -sS -o /dev/null -w '%{http_code}' --max-time 30 "$base/")"
  expect_status 200 "login page is served directly" \
    "$(curl -sS -o /dev/null -w '%{http_code}' --max-time 30 "$base/admin/login")"
  [[ -z "$(curl -sS -o /dev/null -D - --max-time 30 "$base/admin/login" | tr -d '\r' | grep -i '^www-authenticate:' || true)" ]] \
    && ok "no Basic Auth challenge is sent" || ko "no Basic Auth challenge" "challenge present"
  skip "wrong-Basic-Auth-password assertion (outer gate disabled by the operator)"
fi

# =========================================================================
# 2. SPA document, assets, deep-link fallback
# =========================================================================
expect_status 200 "SPA document served" \
  "$(curl -sS -o "$work/index.html" -w '%{http_code}' --max-time 30 "${AUTH_ARGS[@]}" "$base/admin/login")"
grep -qi 'content="[^"]*width=device-width' "$work/index.html" \
  && ok "SPA declares a mobile viewport" || ko "SPA mobile viewport" "missing meta"
grep -q 'id="root"' "$work/index.html" && ok "SPA mount point present" || ko "SPA mount point" "no #root"
asset="$(grep -oE '/assets/[A-Za-z0-9_.-]+\.js' "$work/index.html" | head -n1 || true)"
if [[ -n "$asset" ]]; then
  read -r astatus ctype < <(curl -sS -o /dev/null -w '%{http_code} %{content_type}' --max-time 30 "${AUTH_ARGS[@]}" "$base$asset")
  [[ "$astatus" == 200 && "$ctype" == *javascript* ]] \
    && ok "hashed JS bundle served ($ctype)" || ko "hashed JS bundle" "$astatus $ctype"
else
  ko "hashed JS bundle referenced by index.html" "none"
fi
read -r dstatus dctype < <(curl -sS -o /dev/null -w '%{http_code} %{content_type}' --max-time 30 "${AUTH_ARGS[@]}" "$base/admin/bookings")
[[ "$dstatus" == 200 && "$dctype" == *text/html* ]] \
  && ok "deep link /admin/bookings falls back to index.html" || ko "deep-link SPA fallback" "$dstatus $dctype"

# =========================================================================
# 3. CSRF origin check
# =========================================================================
expect_status 403 "state-changing request from a foreign Origin is rejected" \
  "$(curl -sS -o /dev/null -w '%{http_code}' --max-time 30 "${AUTH_ARGS[@]}" -X POST \
     -H 'Origin: https://evil.example' -H 'Content-Type: application/json' \
     -d "$login_body" "$base/api/admin/v1/auth/login")"

# =========================================================================
# 4. login / session
# =========================================================================
jarA="$work/jarA.cookies"; jarB="$work/jarB.cookies"
expect_status 401 "login with a wrong password fails" \
  "$(req "$jarA" POST /api/admin/v1/auth/login -H 'Content-Type: application/json' \
     -d "{\"login\":\"$admin_login\",\"password\":\"wrong-$admin_password\"}")"
expect_status 200 "login as the stand admin succeeds" \
  "$(req "$jarA" POST /api/admin/v1/auth/login -H 'Content-Type: application/json' -H "Origin: $base" -d "$login_body")"
grep -q '__Host-takeplace_admin' "$jarA" \
  && ok "session cookie is the __Host- production cookie" || ko "session cookie" "$(cut -d$'\t' -f6 "$jarA" | tr '\n' ' ')"
expect_status 200 "authenticated /me" "$(req "$jarA" GET /api/admin/v1/me)"
[[ "$(jget "$work/body" venue.slug)" == "dragon" ]] \
  && ok "session scoped to venue 'dragon'" || ko "venue slug" "$(jget "$work/body" venue.slug)"
[[ "$(jget "$work/body" venue.online_booking_enabled)" == "false" ]] \
  && ok "online_booking_enabled=false (public self-service closed)" \
  || ko "online_booking_enabled" "$(jget "$work/body" venue.online_booking_enabled)"
expect_status 200 "system status" "$(req "$jarA" GET /api/admin/v1/system/status)"

# --- pre-cleanup: release leftovers from earlier runs ----------------------
# A NEW/WAITING booking keeps an active occupancy, so an abandoned run would
# block the next one. Only rows whose guest name starts with "TEST" are touched.
req "$jarA" GET '/api/admin/v1/bookings?limit=200' >/dev/null
cp "$work/body" "$work/bookings.json"
mapfile -t leftovers < <(python3 - "$work/bookings.json" <<'PY'
import json, sys
for b in json.load(open(sys.argv[1])).get("items", []):
    if (b.get("guest_name") or "").startswith("TEST") and b.get("status") in ("NEW", "WAITING", "OPEN"):
        print(b["id"], b["version"], b["status"])
PY
)
for row in "${leftovers[@]:-}"; do
  [[ -n "$row" ]] || continue
  read -r lid lver lstatus <<<"$row"
  if [[ "$lstatus" == "OPEN" ]]; then
    req "$jarA" POST "/api/admin/v1/bookings/$lid/close" -H 'Content-Type: application/json' -H "Origin: $base" \
      -d "{\"expected_version\":$lver}" >/dev/null
  else
    req "$jarA" POST "/api/admin/v1/bookings/$lid/cancel" -H 'Content-Type: application/json' -H "Origin: $base" \
      -d "{\"expected_version\":$lver,\"reason\":\"OTHER\",\"note\":\"smoke pre-cleanup\"}" >/dev/null
  fi
done
ok "released ${#leftovers[@]} leftover test booking(s) from earlier runs"

# =========================================================================
# 5. schedule
# =========================================================================
expect_status 200 "weekly schedule read" "$(req "$jarA" GET /api/admin/v1/schedule)"
schedule_body='{"weekdays":[
 {"weekday":0,"is_open":true,"open_time":"10:00","close_time":"23:00"},
 {"weekday":1,"is_open":true,"open_time":"10:00","close_time":"23:00"},
 {"weekday":2,"is_open":true,"open_time":"10:00","close_time":"23:00"},
 {"weekday":3,"is_open":true,"open_time":"10:00","close_time":"23:00"},
 {"weekday":4,"is_open":true,"open_time":"10:00","close_time":"23:00"},
 {"weekday":5,"is_open":true,"open_time":"10:00","close_time":"23:00"},
 {"weekday":6,"is_open":true,"open_time":"10:00","close_time":"23:00"}]}'
expect_status 200 "weekly schedule written (placeholder 10:00-23:00)" \
  "$(req "$jarA" PUT '/api/admin/v1/schedule?confirm=true' -H 'Content-Type: application/json' -H "Origin: $base" -d "$schedule_body")"
expect_status 200 "business-day computed" "$(req "$jarA" GET /api/admin/v1/schedule/business-day)"

# =========================================================================
# 6. halls + tables (idempotent: never archives a table with future bookings)
# =========================================================================
expect_status 200 "halls listed" "$(req "$jarA" GET /api/admin/v1/halls)"
hall_id="$(jget "$work/body" halls.0.id)"
[[ -n "$hall_id" ]] || { echo "error: the venue has no hall" >&2; exit 1; }
expect_status 200 "hall detail" "$(req "$jarA" GET "/api/admin/v1/halls/$hall_id")"
cp "$work/body" "$work/hall.json"

# Re-save the hall with its CURRENT tables preserved and topped up to three, so
# an existing table is never archived (which the API correctly refuses with 409
# while a future booking still references it).
layout_body="$(python3 - "$work/hall.json" <<'PY'
import json, sys
hall = json.load(open(sys.argv[1]))
live = [t for t in hall["tables"] if t.get("archived_at") is None]
tables = [{"id": t["id"], "number": t["number"], "capacity": t["capacity"],
           "shape": t["shape"], "x": t["x"], "y": t["y"], "width": t["width"],
           "height": t["height"], "rotation": t["rotation"], "z_index": t["z_index"]}
          for t in live]
used = {t["number"] for t in live}
nxt = len(live)
while len(tables) < 3:
    nxt += 1
    number = str(nxt)
    while number in used:
        nxt += 1
        number = str(nxt)
    used.add(number)
    i = len(tables)
    tables.append({"number": number, "capacity": 4, "shape": "rect", "x": 60 + i * 160,
                   "y": 80, "width": 110, "height": 80, "rotation": 0, "z_index": i})
print(json.dumps({"expected_revision": hall["layout_revision"],
                  "canvas_width": hall["canvas_width"], "canvas_height": hall["canvas_height"],
                  "tables": tables, "static_elements": hall.get("static_elements") or []}))
PY
)"
if [[ "$(jlen "$work/hall.json" tables)" -ge 3 ]]; then
  ok "venue already has >= 3 tables (layout left untouched)"
else
  expect_status 200 "hall layout saved to reach 3 tables" \
    "$(req "$jarA" PUT "/api/admin/v1/halls/$hall_id/layout" -H 'Content-Type: application/json' -H "Origin: $base" -d "$layout_body")"
fi

expect_status 200 "tables listed" "$(req "$jarA" GET /api/admin/v1/tables)"
[[ "$(jlen "$work/body" tables)" -ge 3 ]] && ok "at least 3 tables available" || ko "tables available" "$(jlen "$work/body" tables)"
t1="$(jget "$work/body" tables.0.id)"; t2="$(jget "$work/body" tables.1.id)"; t3="$(jget "$work/body" tables.2.id)"
# A previous run may have toggled bookability; make the used tables bookable.
for table in "$t1" "$t2" "$t3"; do
  req "$jarA" PATCH "/api/admin/v1/tables/$table" -H 'Content-Type: application/json' -H "Origin: $base" -d '{"is_bookable":true}' >/dev/null
done
ok "used tables set bookable"

# =========================================================================
# 7. booking windows
# =========================================================================
req "$jarA" GET /api/admin/v1/schedule/business-day >/dev/null
cp "$work/body" "$work/bd.json"
windows="$(python3 - "$work/bd.json" <<'PY'
import json, sys
from datetime import datetime, time, timedelta, timezone
st = json.load(open(sys.argv[1]))
iso = lambda s: datetime.fromisoformat(s.replace("Z", "+00:00"))
def ceil5(d):
    base = d.replace(second=0, microsecond=0) + timedelta(minutes=(-d.minute) % 5)
    return base if base >= d else base + timedelta(minutes=5)
fmt = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")
now = datetime.now(timezone.utc)
ok_shift = bool(st.get("is_open") and st.get("shift_start") and st.get("shift_end"))
tz = timezone(timedelta(hours=3))
tomorrow = (datetime.now(tz) + timedelta(days=1)).date()
def at(h, m):
    return datetime.combine(tomorrow, time(h, m), tzinfo=tz).astimezone(timezone.utc)
# NEXT: a future interval inside the placeholder 10:00-23:00 shift (for create
# idempotency, change-time and cancel — all legal on a future booking).
print("NEXT", fmt(at(14, 0)), fmt(at(15, 0)), fmt(at(18, 0)), fmt(at(19, 0)), fmt(at(20, 0)), fmt(at(21, 0)))
# NOW: the next 5-minute boundary. It is >= now (create requires that) and becomes
# <= now shortly, which is what §10 WAITING needs.
if ok_shift:
    shift_start, shift_end = iso(st["shift_start"]), iso(st["shift_end"])
    start = ceil5(now)
    # Keep a margin so the request cannot arrive after its own start time.
    if (start - now).total_seconds() < 30:
        start += timedelta(minutes=5)
    end = start + timedelta(minutes=60)
    if shift_start <= now and end <= shift_end:
        print("NOW_OK", fmt(start), fmt(end))
    else:
        print("NOW_SKIP", "-", "-")
else:
    print("NOW_SKIP", "-", "-")
PY
)"
read -r _ n1s n1e n2s n2e n3s n3e <<<"$(echo "$windows" | grep '^NEXT')"
read -r now_state w_now_s w_now_e <<<"$(echo "$windows" | grep '^NOW')"
[[ -n "${n1s:-}" ]] || { echo "error: could not compute booking windows" >&2; exit 1; }
echo "        next window: $n1s .. $n1e | now window: $w_now_s .. $w_now_e ($now_state)"

# =========================================================================
# 8. lifecycle on a booking inside the CURRENT shift
#    NEW -> WAITING -> OPEN -> UNDO_OPEN -> OPEN -> CLOSED
# =========================================================================
if [[ "$now_state" == "NOW_OK" ]]; then
  expect_status 201 "current-shift booking created" \
    "$(req "$jarA" POST /api/admin/v1/bookings -H 'Content-Type: application/json' -H "Origin: $base" \
       -H "Idempotency-Key: $(uuid)" \
       -d "{\"starts_at\":\"$w_now_s\",\"ends_at\":\"$w_now_e\",\"table_ids\":[$t1],\"party_size\":2,\"source\":\"PHONE\",\"guest_name\":\"TEST LIFECYCLE\",\"guest_phone_raw\":\"+70000000222\"}")"
  bid="$(jget "$work/body" id)"

  if [[ -n "$bid" ]]; then
    # §10: WAITING becomes legal at starts_at, which is at most 5 minutes away.
    waited=0; wcode=0
    deadline=$((SECONDS + max_wait))
    while (( SECONDS < deadline )); do
      req "$jarA" GET "/api/admin/v1/bookings/$bid" >/dev/null
      ver="$(jget "$work/body" version)"
      wcode="$(req "$jarA" POST "/api/admin/v1/bookings/$bid/wait" -H 'Content-Type: application/json' -H "Origin: $base" -d "{\"expected_version\":$ver}")"
      [[ "$wcode" == 200 ]] && break
      waited=$((waited + 15)); sleep 15
    done
    if [[ "$wcode" == 200 ]]; then
      ok "NEW -> WAITING (legal at starts_at; waited ${waited}s)"
      ver="$(jget "$work/body" version)"
      [[ "$(jget "$work/body" status)" == "WAITING" ]] && ok "status is WAITING" || ko "status is WAITING" "$(jget "$work/body" status)"

      expect_status 200 "WAITING -> OPEN" \
        "$(req "$jarA" POST "/api/admin/v1/bookings/$bid/open" -H 'Content-Type: application/json' -H "Origin: $base" -d "{\"expected_version\":$ver}")"
      ver="$(jget "$work/body" version)"
      [[ "$(jget "$work/body" status)" == "OPEN" ]] && ok "status is OPEN" || ko "status is OPEN" "$(jget "$work/body" status)"
      [[ "$(jlen "$work/body" live_table_ids)" == 1 ]] && ok "OPEN booking holds 1 live table" || ko "live table recorded" "$(jlen "$work/body" live_table_ids)"

      expect_status 200 "OPEN -> UNDO_OPEN" \
        "$(req "$jarA" POST "/api/admin/v1/bookings/$bid/undo-open" -H 'Content-Type: application/json' -H "Origin: $base" -d "{\"expected_version\":$ver}")"
      ver="$(jget "$work/body" version)"; st="$(jget "$work/body" status)"
      [[ "$st" == "NEW" || "$st" == "WAITING" ]] && ok "undo-open restored status $st" || ko "undo-open status" "$st"

      expect_status 200 "re-OPEN after undo" \
        "$(req "$jarA" POST "/api/admin/v1/bookings/$bid/open" -H 'Content-Type: application/json' -H "Origin: $base" -d "{\"expected_version\":$ver}")"
      ver="$(jget "$work/body" version)"
      expect_status 200 "OPEN -> CLOSED" \
        "$(req "$jarA" POST "/api/admin/v1/bookings/$bid/close" -H 'Content-Type: application/json' -H "Origin: $base" -d "{\"expected_version\":$ver}")"
      [[ "$(jget "$work/body" status)" == "CLOSED" ]] && ok "status is CLOSED" || ko "status is CLOSED" "$(jget "$work/body" status)"

      expect_status 200 "booking history readable" "$(req "$jarA" GET "/api/admin/v1/bookings/$bid/history")"
      events="$(jlen "$work/body" events)"
      [[ "$events" -ge 5 ]] && ok "history has $events append-only events" || ko "history events" "$events"
      req "$jarA" GET "/api/admin/v1/bookings/$bid" >/dev/null
      [[ "$(jlen "$work/body" live_table_ids)" == 0 ]] && ok "CLOSED booking holds no live table" || ko "live tables after close" "$(jlen "$work/body" live_table_ids)"
    else
      ko "NEW -> WAITING within ${max_wait}s" "last status $wcode"
      [[ -s "$work/body" ]] && printf '        body: %s\n' "$(head -c 300 "$work/body")"
    fi
  else
    ko "current-shift booking created" "no id in the response"
  fi
else
  skip "NEW -> WAITING -> OPEN -> UNDO_OPEN -> CLOSED (the venue is not inside an open shift right now)"
fi

# =========================================================================
# 9. idempotency, change-time, cancel on a FUTURE interval
# =========================================================================
fbody="{\"starts_at\":\"$n1s\",\"ends_at\":\"$n1e\",\"table_ids\":[$t2],\"party_size\":2,\"source\":\"PHONE\",\"guest_name\":\"TEST FUTURE\",\"guest_phone_raw\":\"+70000000333\"}"
fkey="$(uuid)"
expect_status 201 "future booking created (PHONE)" \
  "$(req "$jarA" POST /api/admin/v1/bookings -H 'Content-Type: application/json' -H "Origin: $base" -H "Idempotency-Key: $fkey" -d "$fbody")"
fid="$(jget "$work/body" id)"

replay_status="$(req "$jarA" POST /api/admin/v1/bookings -H 'Content-Type: application/json' -H "Origin: $base" -H "Idempotency-Key: $fkey" -d "$fbody")"
if [[ "$replay_status" == 200 && "$(jget "$work/body" id)" == "$fid" ]]; then
  ok "idempotent replay returns 200 with the same booking"
else
  ko "idempotent replay" "$replay_status id=$(jget "$work/body" id)"
fi

expect_status 201 "change-time booking created (OTHER)" \
  "$(req "$jarA" POST /api/admin/v1/bookings -H 'Content-Type: application/json' -H "Origin: $base" -H "Idempotency-Key: $(uuid)" \
     -d "{\"starts_at\":\"$n2s\",\"ends_at\":\"$n2e\",\"table_ids\":[$t3],\"party_size\":4,\"source\":\"OTHER\",\"guest_name\":\"TEST MOVE\",\"guest_phone_raw\":\"+70000000444\"}")"
mid="$(jget "$work/body" id)"; mver="$(jget "$work/body" version)"
expect_status 200 "booking time changed" \
  "$(req "$jarA" POST "/api/admin/v1/bookings/$mid/change-time" -H 'Content-Type: application/json' -H "Origin: $base" \
     -d "{\"expected_version\":$mver,\"starts_at\":\"$n3s\",\"ends_at\":\"$n3e\"}")"
[[ "$(jget "$work/body" starts_at)" == "$n3s" ]] \
  && ok "start moved to the requested instant" || ko "start moved" "$(jget "$work/body" starts_at) != $n3s"

expect_status 201 "cancel-test booking created" \
  "$(req "$jarA" POST /api/admin/v1/bookings -H 'Content-Type: application/json' -H "Origin: $base" -H "Idempotency-Key: $(uuid)" \
     -d "{\"starts_at\":\"$n2s\",\"ends_at\":\"$n2e\",\"table_ids\":[$t2],\"party_size\":2,\"source\":\"PHONE\",\"guest_name\":\"TEST CANCEL\",\"guest_phone_raw\":\"+70000000555\"}")"
cid="$(jget "$work/body" id)"; cver="$(jget "$work/body" version)"
if [[ -n "$cid" ]]; then
  expect_status 200 "booking canceled" \
    "$(req "$jarA" POST "/api/admin/v1/bookings/$cid/cancel" -H 'Content-Type: application/json' -H "Origin: $base" \
       -d "{\"expected_version\":$cver,\"reason\":\"GUEST_CANCELED\",\"note\":\"smoke test\"}")"
  [[ "$(jget "$work/body" status)" == "CANCELED" ]] && ok "status is CANCELED" || ko "status is CANCELED" "$(jget "$work/body" status)"
fi

expect_status 200 "booking list" "$(req "$jarA" GET '/api/admin/v1/bookings?limit=50')"

# =========================================================================
# 10. atomic WALK_IN create + OPEN (§19/§58)
# =========================================================================
if [[ "$now_state" == "NOW_OK" ]]; then
  wi_end="$(python3 - "$w_now_e" <<'PY'
import sys, datetime as dt
e = dt.datetime.strptime(sys.argv[1], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
print((e + dt.timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ"))
PY
)"
  expect_status 201 "WALK_IN atomic create+OPEN" \
    "$(req "$jarA" POST /api/admin/v1/bookings -H 'Content-Type: application/json' -H "Origin: $base" \
       -H "Idempotency-Key: $(uuid)" \
       -d "{\"open_immediately\":true,\"starts_at\":\"$w_now_s\",\"ends_at\":\"$wi_end\",\"table_ids\":[$t2],\"party_size\":2,\"source\":\"WALK_IN\",\"guest_name\":\"TEST WALKIN\"}")"
  wi_id="$(jget "$work/body" id)"; wi_ver="$(jget "$work/body" version)"
  [[ "$(jget "$work/body" status)" == "OPEN" ]] && ok "WALK_IN booking is immediately OPEN" || ko "WALK_IN is OPEN" "$(jget "$work/body" status)"
  [[ "$(jlen "$work/body" live_table_ids)" == 1 ]] && ok "WALK_IN booking holds 1 live table" || ko "WALK_IN live table" "$(jlen "$work/body" live_table_ids)"
  if [[ -n "$wi_id" ]]; then
    expect_status 200 "WALK_IN booking closed (clean state)" \
      "$(req "$jarA" POST "/api/admin/v1/bookings/$wi_id/close" -H 'Content-Type: application/json' -H "Origin: $base" -d "{\"expected_version\":$wi_ver}")"
  fi
else
  skip "atomic WALK_IN create+OPEN (the venue is not inside an open shift right now)"
fi

# --- cleanup: free the future slots so the next run is not blocked ---------
# A NEW/WAITING booking keeps an active occupancy, so the same table+interval
# would conflict on a re-run. Cancelling an unopened booking deactivates it.
for pair in "$fid" "$mid"; do
  [[ -n "$pair" ]] || continue
  req "$jarA" GET "/api/admin/v1/bookings/$pair" >/dev/null
  v="$(jget "$work/body" version)"
  st="$(jget "$work/body" status)"
  if [[ -n "$v" && ( "$st" == "NEW" || "$st" == "WAITING" ) ]]; then
    req "$jarA" POST "/api/admin/v1/bookings/$pair/cancel" -H 'Content-Type: application/json' -H "Origin: $base" \
      -d "{\"expected_version\":$v,\"reason\":\"OTHER\",\"note\":\"smoke cleanup\"}" >/dev/null
  fi
done
ok "test bookings cleaned up (future slots released)"

# =========================================================================
# 11. two admin sessions at the same time
# =========================================================================
expect_status 200 "second admin session logs in" \
  "$(req "$jarB" POST /api/admin/v1/auth/login -H 'Content-Type: application/json' -H "Origin: $base" -d "$login_body")"
if [[ -n "$fid" ]]; then
  expect_status 200 "second session reads the first session's booking" "$(req "$jarB" GET "/api/admin/v1/bookings/$fid")"
fi
expect_status 200 "first session still valid while the second is open" "$(req "$jarA" GET /api/admin/v1/me)"
expect_status 200 "second session independently authenticated" "$(req "$jarB" GET /api/admin/v1/me)"

# =========================================================================
# 12. SSE realtime stream + reconnect
# =========================================================================
sse="$work/sse1.txt"
( curl -sS -N --max-time 14 "${AUTH_ARGS[@]}" -b "$jarA" "$base/api/admin/v1/stream" > "$sse" 2>&1 ) &
sse_pid=$!
sleep 3
grep -q ': connected' "$sse" && ok "SSE stream opens (initial frame)" || ko "SSE initial frame" "$(head -c 120 "$sse")"
req "$jarA" PATCH "/api/admin/v1/tables/$t3" -H 'Content-Type: application/json' -H "Origin: $base" -d '{"is_bookable":false}' >/dev/null
sleep 3
if grep -q '"type": *"resync"' "$sse" || grep -q '"type":"resync"' "$sse"; then
  ok "mutation delivered to the open SSE stream (realtime end-to-end)"
else
  ko "mutation delivered to the SSE stream" "$(tr -d '\n' < "$sse" | head -c 200)"
fi
wait "$sse_pid" 2>/dev/null || true
req "$jarA" PATCH "/api/admin/v1/tables/$t3" -H 'Content-Type: application/json' -H "Origin: $base" -d '{"is_bookable":true}' >/dev/null

sse2="$work/sse2.txt"
( curl -sS -N --max-time 6 "${AUTH_ARGS[@]}" -b "$jarA" "$base/api/admin/v1/stream" > "$sse2" 2>&1 ) &
sse2_pid=$!
sleep 3
grep -q ': connected' "$sse2" && ok "SSE reconnects (second stream opens)" || ko "SSE reconnect" "$(head -c 120 "$sse2")"
wait "$sse2_pid" 2>/dev/null || true

# =========================================================================
# 13. health endpoints over HTTPS
# =========================================================================
for ep in /health/live /health/ready /health/ops; do
  expect_status 200 "$ep" "$(curl -sS -o "$work/body" -w '%{http_code}' --max-time 30 "${AUTH_ARGS[@]}" "$base$ep")"
done
[[ "$(jget "$work/body" environment)" == "production" ]] \
  && ok "/health/ops reports environment=production" || ko "environment" "$(jget "$work/body" environment)"
[[ "$(jget "$work/body" status)" == "ok" ]] \
  && ok "/health/ops status=ok (no degraded subsystem)" || ko "/health/ops status" "$(jget "$work/body" status)"
if [[ "$outer_gate" -eq 1 ]]; then
  expect_status 401 "health endpoints are also behind Basic Auth" \
    "$(curl -sS -o /dev/null -w '%{http_code}' --max-time 30 "$base/health/ready")"
else
  expect_status 200 "health endpoints reachable without the outer gate" \
    "$(curl -sS -o /dev/null -w '%{http_code}' --max-time 30 "$base/health/ready")"
fi

# =========================================================================
# 14. logout
# =========================================================================
expect_status 200 "logout" "$(req "$jarA" POST /api/admin/v1/auth/logout -H "Origin: $base")"
expect_status 401 "session is revoked after logout" "$(req "$jarA" GET /api/admin/v1/me)"

# =========================================================================
# 15. login rate limiting
# =========================================================================
saw_429=0
for i in $(seq 1 25); do
  code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 15 "${AUTH_ARGS[@]}" -X POST \
    -H 'Content-Type: application/json' -d '{"login":"admin","password":"wrong"}' \
    "$base/api/admin/v1/auth/login")"
  if [[ "$code" == 429 ]]; then saw_429=1; break; fi
done
[[ "$saw_429" == 1 ]] && ok "login rate limiting returns 429 (attempt $i)" \
  || ko "login rate limiting returns 429" "no 429 in 25 attempts"

echo "----------------------------------------------------------------"
echo "PASSED: $pass    FAILED: $fail    SKIPPED: $skipped"
[[ "$fail" -eq 0 ]] || exit 1
