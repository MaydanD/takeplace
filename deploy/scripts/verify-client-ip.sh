#!/usr/bin/env bash
# =============================================================================
# Takeplace — verify client-IP / trusted-proxy handling on the live stand.
#
#   bash deploy/scripts/verify-client-ip.sh [--domain <host>]
#
# PROJECT-SPEC §39.5 requires X-Forwarded-* to be trusted ONLY from the actual
# reverse proxy. The property is tested through the login rate limiter, which is
# keyed by the client identity the API derived (an HMAC of the client address):
# an identity is "saturated" after 20 login attempts in 60s, so a 429 vs 401
# reveals whether two requests shared one identity.
#
#   A. An UNTRUSTED peer (a container on the `data` network) cannot spoof its
#      identity: 25 attempts each carrying a DIFFERENT X-Forwarded-For must still
#      collapse into one bucket (429), and a brand-new forged value must not mint
#      a fresh bucket.
#   B. Through Caddy the derived identity is per-client, not Caddy's own address:
#      immediately after A saturates the untrusted bucket, a normal request
#      through Caddy is NOT limited.
#   C. A client-supplied X-Forwarded-For sent THROUGH Caddy cannot mint a new
#      identity either (the proxy's appended value wins).
#
# Nothing here changes configuration. Exit 0 = every executed assertion passed.
# =============================================================================
set -uo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
secrets="$(cd "$repo_root/.." && pwd)/secrets"
domain=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --domain) domain="${2:?}"; shift 2 ;;
    -h|--help) sed -n '2,25p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 1 ;;
  esac
done
[[ -n "$domain" ]] || domain="$(grep -E '^TAKEPLACE_CORS_ORIGINS=' "$repo_root/.env" | tail -n1 | cut -d= -f2- | sed 's#https://##')"
[[ -n "$domain" ]] || { echo "error: could not determine the domain" >&2; exit 1; }

# The outer Caddy Basic Auth gate is optional (see install-caddy-site.sh
# --no-basic-auth): detect it and carry the credentials only when it is on.
base="https://$domain"
AUTH_ARGS=()
if [[ "$(curl -sS -o /dev/null -w '%{http_code}' --max-time 20 "$base/health/ready")" == "401" ]]; then
  AUTH_ARGS=(-u "takeplace:$(cat "$secrets/basic-auth.pass")")
  echo "outer gate: Caddy Basic Auth is ENABLED"
else
  echo "outer gate: Caddy Basic Auth is DISABLED"
fi
api_container="takeplace-api-1"
backend_image="takeplace-backend:local"

pass=0; fail=0
ok() { printf 'PASS  %s\n' "$1"; pass=$((pass + 1)); }
ko() { printf 'FAIL  %s\n        observed: %s\n' "$1" "${2:-}"; fail=$((fail + 1)); }

echo "client-IP / trusted-proxy verification against $base"
echo "----------------------------------------------------------------"

# --- A. configuration matches the real proxy --------------------------------
configured="$(grep -E '^TAKEPLACE_FORWARDED_ALLOW_IPS=' "$repo_root/.env" | tail -n1 | cut -d= -f2-)"
caddy_ip="$(docker inspect infra-caddy-1 --format '{{(index .NetworkSettings.Networks "edge").IPAddress}}')"
container_value="$(docker exec "$api_container" printenv TAKEPLACE_FORWARDED_ALLOW_IPS 2>/dev/null || true)"
echo "        configured=$configured  caddy-edge=$caddy_ip  in-container=$container_value"
[[ "$configured" == "$caddy_ip" && "$container_value" == "$caddy_ip" ]] \
  && ok "trusted-proxy list is exactly the Caddy edge address" \
  || ko "trusted-proxy list" "configured=$configured container=$container_value caddy=$caddy_ip"
case "$configured" in
  *"*"*|*"/"*|"") ko "trusted-proxy list has no wildcard and no subnet" "$configured" ;;
  *) ok "trusted-proxy list has no wildcard and no subnet" ;;
esac
# The API must not be reachable from the internet directly.
if ss -tlnp 2>/dev/null | grep -qE ':(8000|5432)\b'; then
  ko "API/PostgreSQL not published on the host" "$(ss -tln | grep -E ':(8000|5432)\b' | tr '\n' ' ')"
else
  ok "neither the API port nor 5432 is published on the host"
fi

# --- B. an untrusted peer cannot spoof its identity --------------------------
echo "        saturating the untrusted path (direct to the API from the data network)…"
result="$(docker run --rm -i --network data "$backend_image" python - <<'PY'
import json, urllib.error, urllib.request

URL = "http://takeplace-api-1:8000/api/admin/v1/auth/login"


def attempt(xff: str) -> int:
    body = json.dumps({"login": "admin", "password": "wrong"}).encode()
    headers = {"Content-Type": "application/json", "X-Forwarded-For": xff}
    req = urllib.request.Request(URL, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return int(response.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)
    except Exception:
        return -1


# Each attempt claims a DIFFERENT client address. If the API trusted them, every
# request would land in its own bucket and no 429 would ever appear.
codes = [attempt(f"198.51.100.{i}") for i in range(1, 26)]
first_429 = next((i + 1 for i, c in enumerate(codes) if c == 429), 0)
# A never-before-seen forged address must NOT get a fresh bucket.
probe = attempt("203.0.113.200")
print(json.dumps({"codes": codes, "first_429": first_429, "probe": probe}))
PY
)"
codes="$(echo "$result" | python3 -c 'import json,sys;print(json.loads(sys.stdin.read())["codes"][-5:])' 2>/dev/null || echo "?")"
first_429="$(echo "$result" | python3 -c 'import json,sys;print(json.loads(sys.stdin.read())["first_429"])' 2>/dev/null || echo 0)"
probe="$(echo "$result" | python3 -c 'import json,sys;print(json.loads(sys.stdin.read())["probe"])' 2>/dev/null || echo "?")"
echo "        last codes: $codes  first_429=at attempt $first_429  forged-probe=$probe"
[[ "$first_429" -gt 0 && "$first_429" -le 22 ]] \
  && ok "X-Forwarded-For from an untrusted peer is ignored (bucket saturated at attempt $first_429)" \
  || ko "untrusted peer X-Forwarded-For ignored" "no 429 by attempt 22 (codes tail $codes)"
[[ "$probe" == 429 ]] \
  && ok "a fresh forged address from an untrusted peer does not mint a new identity" \
  || ko "forged address must not mint an identity" "got $probe"

# --- C. through Caddy the identity is per client, not the proxy's address ----
code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 20 "${AUTH_ARGS[@]}" -X POST \
  -H 'Content-Type: application/json' -d '{"login":"admin","password":"wrong"}' \
  "$base/api/admin/v1/auth/login")"
[[ "$code" != 429 ]] \
  && ok "a request through Caddy has a different identity than the untrusted peer ($code, not 429)" \
  || ko "Caddy path shares the untrusted identity" "429"

echo "        saturating the path through Caddy…"
first_429_proxy=0
for i in $(seq 1 25); do
  code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 15 "${AUTH_ARGS[@]}" -X POST \
    -H 'Content-Type: application/json' -d '{"login":"admin","password":"wrong"}' \
    "$base/api/admin/v1/auth/login")"
  if [[ "$code" == 429 ]]; then first_429_proxy=$i; break; fi
done
[[ "$first_429_proxy" -gt 0 && "$first_429_proxy" -le 22 ]] \
  && ok "the Caddy path rate-limits (429 at attempt $first_429_proxy)" \
  || ko "Caddy path rate-limits" "no 429 in 25 attempts"

# A client-supplied X-Forwarded-For sent through Caddy must not mint a bucket.
forged="203.0.113.$((RANDOM % 200 + 10))"
code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 20 "${AUTH_ARGS[@]}" -X POST \
  -H 'Content-Type: application/json' -H "X-Forwarded-For: $forged" \
  -d '{"login":"admin","password":"wrong"}' "$base/api/admin/v1/auth/login")"
[[ "$code" == 429 ]] \
  && ok "a client-supplied X-Forwarded-For through Caddy cannot mint a new identity" \
  || ko "client-supplied XFF through Caddy changed the identity" "got $code, expected 429"

echo "----------------------------------------------------------------"
echo "PASSED: $pass    FAILED: $fail"
[[ "$fail" -eq 0 ]] || exit 1
