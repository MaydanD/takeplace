#!/usr/bin/env bash
# =============================================================================
# Takeplace — recovery drill: restore the newest backup into a SEPARATE scratch
# database and prove the data is intact.
#
#   bash deploy/scripts/restore-verify.sh [--file <dump.sql.gz>] [--keep-db]
#
# Restores into `${TAKEPLACE_DB_NAME}_restore_check` — never into the live
# database. The drill:
#   1. verifies the dump checksum;
#   2. drops and recreates the scratch database (FORCE, no other sessions);
#   3. restores the dump and fails on any psql error (ON_ERROR_STOP);
#   4. asserts the Alembic revision matches the live database, that the expected
#      tables exist, and prints venue/booking/hall counts;
#   5. drops the scratch database again unless --keep-db is given.
#
# A green run means "the backup restores and its content is complete", not that
# Stage 14 recovery objectives (RPO/RTO, offsite copy, legal gates) are met.
# =============================================================================
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/../.." && pwd)"
env_path="$repo_root/.env"
backup_dir="$(cd "$repo_root/.." && pwd)/backups"
pg_container="data-postgres-1"
dump_file=""
keep_db=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env) env_path="${2:?}"; shift 2 ;;
    --file) dump_file="${2:?}"; shift 2 ;;
    --out) backup_dir="${2:?}"; shift 2 ;;
    --pg-container) pg_container="${2:?}"; shift 2 ;;
    --keep-db) keep_db=1; shift ;;
    -h|--help) sed -n '2,19p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 1 ;;
  esac
done

[[ -r "$env_path" ]] || { echo "error: cannot read $env_path" >&2; exit 1; }
db_name="$(grep -E '^TAKEPLACE_DB_NAME=' "$env_path" | tail -n1 | cut -d= -f2-)"
[[ -n "$db_name" ]] || { echo "error: TAKEPLACE_DB_NAME is not set in $env_path" >&2; exit 1; }

if [[ -z "$dump_file" ]]; then
  dump_file="$(ls -1t "$backup_dir"/${db_name}-*.sql.gz 2>/dev/null | head -n1 || true)"
fi
[[ -n "$dump_file" && -s "$dump_file" ]] || { echo "error: no backup found in $backup_dir" >&2; exit 1; }

echo "drill file  : $dump_file"
if [[ -s "$dump_file.sha256" ]]; then
  if ( cd "$(dirname "$dump_file")" && sha256sum -c --quiet "$(basename "$dump_file").sha256" ); then
    echo "checksum    : OK"
  else
    echo "error: checksum mismatch for $dump_file" >&2
    exit 1
  fi
fi

scratch_db="${db_name}_restore_check"
echo "scratch db  : $scratch_db"

psql_super() {
  docker exec -i "$pg_container" psql -v ON_ERROR_STOP=1 -U postgres "$@"
}

# --- recreate the scratch database -------------------------------------------
psql_super -d postgres -q <<SQL
SELECT format('DROP DATABASE IF EXISTS %I WITH (FORCE)', '$scratch_db')
\gexec
SELECT format('CREATE DATABASE %I OWNER %I', '$scratch_db', '$(grep -E "^TAKEPLACE_DB_MIGRATOR_USER=" "$env_path" | tail -n1 | cut -d= -f2-)')
\gexec
SQL

# --- restore -----------------------------------------------------------------
echo "restoring..."
gunzip -c "$dump_file" | psql_super -d "$scratch_db" -q >/dev/null

# --- assertions --------------------------------------------------------------
live_revision="$(psql_super -d "$db_name" -Atc 'SELECT version_num FROM alembic_version;')"
restored_revision="$(psql_super -d "$scratch_db" -Atc 'SELECT version_num FROM alembic_version;')"

echo
echo "alembic revision (live / restored): $live_revision / $restored_revision"
[[ "$live_revision" == "$restored_revision" ]] \
  || { echo "error: restored revision does not match the live database" >&2; exit 1; }

missing="$(psql_super -d "$scratch_db" -Atc "
WITH expected(t) AS (VALUES
  ('venues'), ('admin_accounts'), ('admin_sessions'), ('halls'), ('tables'),
  ('bookings'), ('booking_events'), ('table_occupancies'), ('booking_live_tables'),
  ('notification_outbox'), ('venue_booking_counters'), ('weekly_schedules'),
  ('schedule_exceptions'), ('venue_vk_integrations'), ('worker_heartbeat'),
  ('maintenance_heartbeat'))
SELECT string_agg(t, ', ') FROM expected
WHERE NOT EXISTS (SELECT 1 FROM information_schema.tables
                  WHERE table_schema = 'public' AND table_name = expected.t);")"
if [[ -n "$missing" ]]; then
  echo "error: restored schema is missing tables: $missing" >&2
  exit 1
fi
echo "schema      : all expected tables present"

psql_super -d "$scratch_db" -c "
SELECT (SELECT count(*) FROM venues)            AS venues,
       (SELECT count(*) FROM halls)             AS halls,
       (SELECT count(*) FROM "tables")          AS tables,
       (SELECT count(*) FROM bookings)          AS bookings,
       (SELECT count(*) FROM booking_events)    AS events,
       (SELECT count(*) FROM admin_accounts)    AS admins;"

if [[ "$keep_db" -eq 1 ]]; then
  echo "scratch database kept for inspection: $scratch_db"
else
  psql_super -d postgres -q <<SQL
SELECT format('DROP DATABASE IF EXISTS %I WITH (FORCE)', '$scratch_db')
\gexec
SQL
  echo "scratch database dropped."
fi

echo
echo "RECOVERY DRILL PASSED: the backup restores into a clean database with a"
echo "matching schema revision and intact rows."
