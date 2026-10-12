#!/usr/bin/env bash
# =============================================================================
# Takeplace — verify (and optionally fix) TAKEPLACE_FORWARDED_ALLOW_IPS.
#
# PROJECT-SPEC §39.5 requires the trusted-proxy list to contain ONLY the actual
# reverse proxy address. The value is the Caddy container's IP on the shared
# `edge` network — a single address, never a subnet and never "*".
#
# Run this after any recreation of the `infra` Caddy container: if its address
# changed, forwarded client IPs stop being trusted (rate limits collapse onto the
# proxy address) even though the site keeps working.
#
#   bash deploy/scripts/check-proxy-ip.sh            # report
#   bash deploy/scripts/check-proxy-ip.sh --fix      # update .env in place
# =============================================================================
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/../.." && pwd)"
env_path="$repo_root/.env"
container="infra-caddy-1"
fix=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env) env_path="${2:?}"; shift 2 ;;
    --container) container="${2:?}"; shift 2 ;;
    --fix) fix=1; shift ;;
    -h|--help) sed -n '2,15p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 1 ;;
  esac
done

actual="$(docker inspect "$container" --format '{{(index .NetworkSettings.Networks "edge").IPAddress}}')"
[[ -r "$env_path" ]] || { echo "error: cannot read $env_path" >&2; exit 1; }
[[ -n "$actual" ]] || { echo "error: $container has no address on the edge network" >&2; exit 1; }

configured="$(grep -E '^TAKEPLACE_FORWARDED_ALLOW_IPS=' "$env_path" | tail -n1 | cut -d= -f2- || true)"

echo "Caddy edge address : $actual"
echo "configured value   : ${configured:-<unset>}"

if [[ "$configured" == "$actual" ]]; then
  echo "OK: the trusted proxy list matches the actual reverse proxy address."
  exit 0
fi

echo "MISMATCH: forwarded client IPs are currently not trusted."
if [[ "$fix" -ne 1 ]]; then
  echo "re-run with --fix to update $env_path, then recreate the api container:"
  echo "  docker compose -f docker-compose.prod.yml up -d --force-recreate api"
  exit 1
fi

key="TAKEPLACE_FORWARDED_ALLOW_IPS="
tmp="$(mktemp)"
awk -v key="$key" -v value="$actual" '
  BEGIN { replaced = 0 }
  index($0, key) == 1 { print key value; replaced = 1; next }
  { print }
  END { if (!replaced) print key value }
' "$env_path" > "$tmp"
cat "$tmp" > "$env_path"   # preserve the original inode/permissions
rm -f "$tmp"
echo "updated $env_path -> $actual"
echo "now recreate the api container so uvicorn picks it up:"
echo "  docker compose -f docker-compose.prod.yml up -d --force-recreate api"
