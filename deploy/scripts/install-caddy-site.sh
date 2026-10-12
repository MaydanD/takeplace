#!/usr/bin/env bash
# =============================================================================
# Takeplace — install (or refresh) the Takeplace site in the EXISTING Caddy.
#
#   sudo bash deploy/scripts/install-caddy-site.sh \
#        --domain takeplace.94-16-106-2.sslip.io [--no-basic-auth]
#
# What it does
# ------------
#   1. generates a strong random Basic Auth password ONCE into
#      <secrets>/basic-auth.pass (mode 600, never committed, never printed)
#      — unless --no-basic-auth is given, in which case the outer gate is
#      removed from the rendered site and no password/hash is used at all;
#   2. renders deploy/caddy/takeplace.caddyfile with the domain and a bcrypt
#      hash of that password (the plaintext never enters the Caddyfile or Git);
#   3. backs up /opt/infra/Caddyfile to a timestamped .bak file;
#   4. replaces only the block between the `>>> takeplace >>>` /
#      `<<< takeplace <<<` managed markers, leaving every other site block
#      (SMM Planner, Control Panel) byte-for-byte untouched;
#   5. validates the full Caddyfile with `caddy validate`, then applies it with
#      `caddy reload` — a graceful reload, NOT a container restart. If validation
#      fails the previous config keeps running.
#
# Options:
#   --domain <host>        site hostname (also used for the CORS origin in .env)
#   --env <path>           .env to read the domain default from
#   --caddyfile <path>     default /opt/infra/Caddyfile
#   --container <name>     default infra-caddy-1
#   --secrets-dir <path>   default <repo>/../secrets
#   --rotate-password      generate a NEW Basic Auth password
#   --no-basic-auth        remove the outer gate: the browser lands directly on
#                          the Takeplace login page (the app's own account, its
#                          Argon2id hash and its login rate limiter stay in
#                          force). Re-run without the flag to put it back.
# =============================================================================
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/../.." && pwd)"
template="$repo_root/deploy/caddy/takeplace.caddyfile"

env_path="$repo_root/.env"
domain=""
caddyfile="/opt/infra/Caddyfile"
container="infra-caddy-1"
secrets_dir=""
rotate=0
no_basic_auth=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --domain) domain="${2:?}"; shift 2 ;;
    --env) env_path="${2:?}"; shift 2 ;;
    --caddyfile) caddyfile="${2:?}"; shift 2 ;;
    --container) container="${2:?}"; shift 2 ;;
    --secrets-dir) secrets_dir="${2:?}"; shift 2 ;;
    --rotate-password) rotate=1; shift ;;
    --no-basic-auth) no_basic_auth=1; shift ;;
    -h|--help) sed -n '2,32p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 1 ;;
  esac
done

[[ -r "$template" ]] || { echo "error: template not found: $template" >&2; exit 1; }
[[ -r "$caddyfile" ]] || { echo "error: Caddyfile not found: $caddyfile" >&2; exit 1; }
docker inspect "$container" >/dev/null 2>&1 \
  || { echo "error: Caddy container $container not found" >&2; exit 1; }

if [[ -z "$secrets_dir" ]]; then
  secrets_dir="$(cd "$(dirname "$env_path")/.." && pwd)/secrets"
fi
mkdir -p "$secrets_dir"
chmod 700 "$secrets_dir"

# Derive the domain from the CORS origin in .env when not passed explicitly.
if [[ -z "$domain" && -r "$env_path" ]]; then
  origin="$(grep -E '^TAKEPLACE_CORS_ORIGINS=' "$env_path" | tail -n1 || true)"
  if [[ -n "$origin" ]]; then
    domain="${origin#*=}"
    domain="${domain#https://}"
    domain="${domain%%,*}"
  fi
fi
[[ -n "$domain" ]] || { echo "error: --domain is required (or TAKEPLACE_CORS_ORIGINS in .env)" >&2; exit 1; }

hash=""
if [[ "$no_basic_auth" -eq 0 ]]; then
  # --- Basic Auth password (generated once, stored on the server only) -------
  password_file="$secrets_dir/basic-auth.pass"
  if [[ "$rotate" -eq 1 || ! -s "$password_file" ]]; then
    ( umask 077; head -c 32 /dev/urandom | base64 | tr -d '\n' > "$password_file" )
    echo "generated a new Basic Auth password -> $password_file"
  else
    echo "reusing existing Basic Auth password from $password_file"
  fi
  chmod 600 "$password_file"
  basic_password="$(cat "$password_file")"

  # --- bcrypt hash via the Caddy binary in the running container ------------
  # Prefer stdin so the plaintext never appears in a process argument list.
  hash="$(
    printf '%s\n' "$basic_password" \
      | docker exec -i "$container" caddy hash-password 2>/dev/null | tr -d '\r' || true
  )"
  if [[ "$hash" != \$2* ]]; then
    # Fallback for Caddy builds whose hash-password only reads --plaintext.
    hash="$(docker exec "$container" caddy hash-password --plaintext "$basic_password" | tr -d '\r')"
  fi
  [[ "$hash" == \$2* ]] || { echo "error: could not compute a bcrypt hash" >&2; exit 1; }
else
  echo "--no-basic-auth: the outer Basic Auth gate is being REMOVED from the site"
  echo "                 (the Takeplace login + its rate limiter remain the only gate)"
fi

# --- render the fragment -----------------------------------------------------
rendered="$(mktemp)"; chmod 600 "$rendered"
# awk keeps this dependency-free (no python on the host is required). Neither the
# domain nor a bcrypt hash can contain '&' or '\', so gsub is safe here.
awk -v domain="$domain" -v hash="$hash" '
  { gsub(/__DOMAIN__/, domain); gsub(/__BASIC_AUTH_HASH__/, hash); print }
' "$template" > "$rendered"

if [[ "$no_basic_auth" -eq 1 ]]; then
  stripped="$(mktemp)"; chmod 600 "$stripped"
  awk '
    /# >>> takeplace-basic-auth >>>/ { skip = 1; next }
    /# <<< takeplace-basic-auth <<</ { skip = 0; next }
    !skip { print }
  ' "$rendered" > "$stripped"
  mv -f "$stripped" "$rendered"
  if grep -qE '^[[:space:]]*basic_auth' "$rendered"; then
    echo "error: a basic_auth directive survived the strip" >&2
    rm -f "$rendered"
    exit 1
  fi
fi

# --- swap the managed block in the shared Caddyfile --------------------------
backup="$caddyfile.bak-takeplace-$(date +%Y%m%d-%H%M%S)"
cp -a "$caddyfile" "$backup"

new_config="$(mktemp)"; chmod 600 "$new_config"
awk '
  /# >>> takeplace >>>/ { skip = 1; next }
  /# <<< takeplace <<</ { skip = 0; next }
  !skip { print }
' "$caddyfile" > "$new_config"

# Guarantee a separating blank line before the appended block.
printf '\n' >> "$new_config"
cat "$rendered" >> "$new_config"

# Write IN PLACE (truncate + rewrite). Never `cp`/`install`/`mv` over this path:
# it is a single-file bind mount into the Caddy container, so replacing the file
# would give the container a new inode while its mount keeps pointing at the old
# (now deleted) one — Caddy would then keep serving the previous config and every
# later `caddy reload` would silently revert this block.
cat "$new_config" > "$caddyfile"
rm -f "$new_config" "$rendered"

echo "backup written: $backup"

# --- verify the container actually sees the new content ----------------------
host_hash="$(sha256sum < "$caddyfile" | cut -d' ' -f1)"
container_hash="$(docker exec "$container" sh -c 'sha256sum < /etc/caddy/Caddyfile' 2>/dev/null | cut -d' ' -f1 || true)"
if [[ -z "$container_hash" || "$container_hash" != "$host_hash" ]]; then
  echo "error: $container is NOT seeing the file we just wrote." >&2
  echo "       host:      $host_hash" >&2
  echo "       container: ${container_hash:-<unreadable>}" >&2
  echo "       The single-file bind mount is stale (it points at a deleted inode)." >&2
  echo "       Recreate the Caddy container so the mount is re-established, then re-run:" >&2
  echo "         docker restart $container" >&2
  exit 1
fi

# --- validate, then apply with a graceful reload -----------------------------
if ! docker exec "$container" caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile; then
  echo "error: rendered Caddyfile failed validation; restoring $backup" >&2
  cp -a "$backup" "$caddyfile"
  docker exec "$container" caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile || true
  exit 1
fi

docker exec "$container" caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile
echo "Caddy reloaded; $domain is now routed to the Takeplace containers."
echo
if [[ "$no_basic_auth" -eq 1 ]]; then
  echo "Outer gate: DISABLED — the browser goes straight to the Takeplace login page."
  echo "The inner Takeplace account is the only gate; its password lives in"
  echo "  $secrets_dir/admin.pass  (mode 600, not printed here)."
  echo "Re-enable with: bash deploy/scripts/install-caddy-site.sh --domain $domain"
else
  echo "Outer gate: enabled (HTTP Basic Auth)"
  echo "Basic Auth user: takeplace"
  echo "Basic Auth password file: $password_file  (read it on the server; it is not printed here)"
fi
echo
echo "TLS is issued automatically on first request. Watch it with:"
echo "  docker logs --tail 50 -f $container"
