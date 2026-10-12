#!/usr/bin/env bash
# =============================================================================
# Takeplace — add the pg_hba.conf entries the shared PostgreSQL needs.
#
#   sudo bash deploy/scripts/provision-pg-hba.sh [--env <path>] [--hba <path>]
#
# The shared PostgreSQL (`data` compose project) does NOT have a catch-all
# `host all all ...` rule. It authorises each application explicitly, one line
# per project, scoped by database + role + the `data` subnet:
#
#   host  smm_planner  smm_planner  172.19.0.0/16  scram-sha-256
#   host  control      control      172.19.0.0/16  scram-sha-256
#
# This script appends the equivalent lines for Takeplace inside managed markers.
# The lines are additive and db+role-scoped:
#   * existing projects keep their exact rules — nothing is rewritten or removed;
#   * `takeplace_app` can authenticate ONLY to the `takeplace` database;
#   * `takeplace_migrator` additionally to the restore-drill database.
#
# It then applies the change with `SELECT pg_reload_conf()` — a configuration
# reload, not a restart, so no other application is interrupted.
#
# Idempotent: re-running replaces only the managed block.
# =============================================================================
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/../.." && pwd)"
env_path="$repo_root/.env"
hba_file="/opt/data/postgres/pg_hba.conf"
pg_container="data-postgres-1"
data_subnet="172.19.0.0/16"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env) env_path="${2:?}"; shift 2 ;;
    --hba) hba_file="${2:?}"; shift 2 ;;
    --pg-container) pg_container="${2:?}"; shift 2 ;;
    -h|--help) sed -n '2,25p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 1 ;;
  esac
done

[[ -r "$env_path" ]] || { echo "error: cannot read $env_path" >&2; exit 1; }
docker inspect "$pg_container" >/dev/null 2>&1 \
  || { echo "error: container $pg_container not found" >&2; exit 1; }

get_env() {
  local line
  line="$(grep -E "^$1=" "$env_path" | tail -n1 || true)"
  printf '%s' "${line#*=}"
}

db_name="$(get_env TAKEPLACE_DB_NAME)"
app_user="$(get_env TAKEPLACE_DB_APP_USER)"
migrator_user="$(get_env TAKEPLACE_DB_MIGRATOR_USER)"
[[ -n "$db_name" && -n "$app_user" && -n "$migrator_user" ]] \
  || { echo "error: DB name/roles are not set in $env_path" >&2; exit 1; }

# --- confirm we are editing the file PostgreSQL actually reads ---------------
container_hba="$(docker exec "$pg_container" psql -U postgres -Atc 'SHOW hba_file;')"
mount_source="$(docker inspect "$pg_container" \
  --format '{{range .Mounts}}{{.Source}}:{{.Destination}}{{"\n"}}{{end}}' \
  | awk -F: '$2 != "" && index("'"$container_hba"'", $2) == 1 { print $1; exit }')"
expected="$mount_source/$(basename "$container_hba")"
if [[ -z "$mount_source" || "$expected" != "$hba_file" ]]; then
  echo "error: $hba_file is not the file PostgreSQL$container_hba reads." >&2
  echo "       PostgreSQL reads $container_hba, which maps to: ${expected:-<not found>}" >&2
  echo "       pass --hba <path> to override." >&2
  exit 1
fi

backup="$hba_file.bak-takeplace-$(date +%Y%m%d-%H%M%S)"
cp -a "$hba_file" "$backup"
echo "backup: $backup"

# --- rewrite only the managed block -----------------------------------------
tmp="$(mktemp)"; chmod 600 "$tmp"
awk '
  /# >>> takeplace >>>/ { skip = 1; next }
  /# <<< takeplace <<</ { skip = 0; next }
  !skip { print }
' "$hba_file" > "$tmp"

cat >> "$tmp" <<EOF

# >>> takeplace >>> managed by deploy/scripts/provision-pg-hba.sh — do not edit by hand
# Database+role scoped: the app role can only reach its own database.
host    ${db_name}              ${app_user}       ${data_subnet}  scram-sha-256
host    ${db_name}              ${migrator_user}  ${data_subnet}  scram-sha-256
host    ${db_name}_restore_check ${migrator_user} ${data_subnet}  scram-sha-256
# <<< takeplace <<<
EOF

cat "$tmp" > "$hba_file"   # preserve inode and permissions
rm -f "$tmp"

docker exec "$pg_container" psql -v ON_ERROR_STOP=1 -U postgres -d postgres -q \
  -c 'SELECT pg_reload_conf();' >/dev/null
sleep 1

echo "applied and reloaded. managed block:"
awk '/# >>> takeplace >>>/{f=1} f; /# <<< takeplace <<</{f=0}' "$hba_file"

# --- prove the app role can now connect over TCP -----------------------------
env_file="$(mktemp)"; chmod 600 "$env_file"
printf 'PGPASSWORD=%s\n' "$(get_env TAKEPLACE_DB_APP_PASSWORD)" > "$env_file"
if docker run --rm --network data --env-file "$env_file" postgres:16-alpine \
     psql -h postgres -U "$app_user" -d "$db_name" -Atc 'SELECT 1' >/dev/null 2>&1; then
  echo "verified: $app_user can authenticate to $db_name over the data network."
else
  rm -f "$env_file"
  echo "error: $app_user still cannot connect to $db_name" >&2
  exit 1
fi
rm -f "$env_file"
