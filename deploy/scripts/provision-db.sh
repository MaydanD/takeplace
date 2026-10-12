#!/usr/bin/env bash
# =============================================================================
# Takeplace — provision the isolated database and roles in the EXISTING shared
# PostgreSQL (the `data` compose project) without touching any other project.
#
#   sudo bash deploy/scripts/provision-db.sh [--env /opt/takeplace/repo/.env]
#
# Creates, idempotently:
#   * role  takeplace_migrator  (DDL: owns schema objects, runs Alembic)
#   * role  takeplace_app       (DML only: no CREATE/ALTER/DROP)
#   * DB    $TAKEPLACE_DB_NAME  owned by takeplace_migrator, plus a separate
#            ${TAKEPLACE_DB_NAME}_restore_check database used only by
#            restore-verify.sh so recovery drills never touch live data.
#
# Nothing here references smm_planner or control: those databases, roles and
# privileges are left exactly as they are. `pg_dump`/`psql` run inside the
# existing PostgreSQL container over its local socket, so the superuser password
# is never placed on a command line.
#
# Idempotent: safe to re-run; it re-asserts passwords, grants and timeouts from
# the current .env (rotation is the same command).
# =============================================================================
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/../.." && pwd)"
env_path="$repo_root/.env"
pg_container="data-postgres-1"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env) env_path="${2:?}"; shift 2 ;;
    --pg-container) pg_container="${2:?}"; shift 2 ;;
    -h|--help) sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 1 ;;
  esac
done

[[ -r "$env_path" ]] || { echo "error: cannot read $env_path" >&2; exit 1; }

# Load only the keys we need, without exporting the whole file into the process
# environment (keeps secrets out of any child's `env`).
get_env() {
  local key="$1"
  local line
  line="$(grep -E "^${key}=" "$env_path" | tail -n1 || true)"
  [[ -n "$line" ]] || { echo "error: $key is not set in $env_path" >&2; exit 1; }
  printf '%s' "${line#*=}"
}

db_name="$(get_env TAKEPLACE_DB_NAME)"
app_user="$(get_env TAKEPLACE_DB_APP_USER)"
app_password="$(get_env TAKEPLACE_DB_APP_PASSWORD)"
migrator_user="$(get_env TAKEPLACE_DB_MIGRATOR_USER)"
migrator_password="$(get_env TAKEPLACE_DB_MIGRATOR_PASSWORD)"
statement_timeout="$(get_env TAKEPLACE_DB_STATEMENT_TIMEOUT_MS 2>/dev/null || echo 10000)"
lock_timeout="$(get_env TAKEPLACE_DB_LOCK_TIMEOUT_MS 2>/dev/null || echo 3000)"
idle_timeout="$(get_env TAKEPLACE_DB_IDLE_IN_TRANSACTION_TIMEOUT_MS 2>/dev/null || echo 15000)"
migration_timeout="$(get_env TAKEPLACE_DB_MIGRATION_STATEMENT_TIMEOUT_MS 2>/dev/null || echo 300000)"

restore_db="${db_name}_restore_check"

docker inspect "$pg_container" >/dev/null 2>&1 \
  || { echo "error: container $pg_container not found" >&2; exit 1; }

echo "provisioning roles/databases in shared PostgreSQL ($pg_container)"
echo "  database : $db_name (owner $migrator_user)"
echo "  restore  : $restore_db"
echo "  app role : $app_user"

# --- roles + databases -------------------------------------------------------
# The SQL is streamed over stdin: passwords never appear in any process argv.
docker exec -i "$pg_container" psql -v ON_ERROR_STOP=1 -U postgres -d postgres -q \
  -v migrator_user="$migrator_user" \
  -v app_user="$app_user" \
  -v db_name="$db_name" \
  -v restore_db="$restore_db" <<SQL
\set migrator_password '${migrator_password}'
\set app_password '${app_password}'

SELECT format('CREATE ROLE %I LOGIN', :'migrator_user')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'migrator_user')
\gexec

SELECT format('CREATE ROLE %I LOGIN', :'app_user')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'app_user')
\gexec

ALTER ROLE :"migrator_user" LOGIN PASSWORD :'migrator_password';
ALTER ROLE :"app_user"      LOGIN PASSWORD :'app_password';

-- Per-role safety net (PROJECT-SPEC §32.6); the backend sets its own per
-- connection values, other clients get safe defaults.
ALTER ROLE :"migrator_user" SET statement_timeout = '${migration_timeout}ms';
ALTER ROLE :"migrator_user" SET lock_timeout = '${lock_timeout}ms';
ALTER ROLE :"migrator_user" SET idle_in_transaction_session_timeout = '${idle_timeout}ms';

ALTER ROLE :"app_user" SET statement_timeout = '${statement_timeout}ms';
ALTER ROLE :"app_user" SET lock_timeout = '${lock_timeout}ms';
ALTER ROLE :"app_user" SET idle_in_transaction_session_timeout = '${idle_timeout}ms';
ALTER ROLE :"app_user" SET default_transaction_isolation = 'read committed';

-- The roles must not be able to touch other projects' data beyond their grants.
ALTER ROLE :"migrator_user" NOSUPERUSER NOCREATEDB NOCREATEROLE;
ALTER ROLE :"app_user"      NOSUPERUSER NOCREATEDB NOCREATEROLE;

SELECT format('CREATE DATABASE %I OWNER %I', :'db_name', :'migrator_user')
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'db_name')
\gexec

SELECT format('CREATE DATABASE %I OWNER %I', :'restore_db', :'migrator_user')
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'restore_db')
\gexec

SELECT format('REVOKE ALL ON DATABASE %I FROM PUBLIC', :'db_name')
\gexec
SELECT format('REVOKE ALL ON DATABASE %I FROM PUBLIC', :'restore_db')
\gexec

SELECT format('GRANT CONNECT, TEMPORARY ON DATABASE %I TO %I, %I', :'db_name', :'migrator_user', :'app_user')
\gexec
SELECT format('GRANT CONNECT, TEMPORARY ON DATABASE %I TO %I', :'restore_db', :'migrator_user')
\gexec

-- migrator needs CREATE on the database to install trusted extensions
-- (btree_gist, §45) and CREATE on the schema to own tables.
SELECT format('GRANT CREATE ON DATABASE %I TO %I', :'db_name', :'migrator_user')
\gexec
SELECT format('REVOKE CREATE ON DATABASE %I FROM %I', :'db_name', :'app_user')
\gexec
SELECT format('REVOKE CREATE ON DATABASE %I FROM PUBLIC', :'db_name')
\gexec
SQL

# --- schema privileges (must run connected to the target database) -----------
for target in "$db_name" "$restore_db"; do
  docker exec -i "$pg_container" psql -v ON_ERROR_STOP=1 -U postgres -d "$target" -q \
    -v migrator_user="$migrator_user" \
    -v app_user="$app_user" <<'SQL'
GRANT CREATE, USAGE ON SCHEMA public TO :"migrator_user";
GRANT USAGE ON SCHEMA public TO :"app_user";
REVOKE CREATE ON SCHEMA public FROM PUBLIC;

GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO :"app_user";
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO :"app_user";

-- DML grants for objects the migrator creates later (all Alembic tables).
ALTER DEFAULT PRIVILEGES FOR ROLE :"migrator_user" IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO :"app_user";
ALTER DEFAULT PRIVILEGES FOR ROLE :"migrator_user" IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO :"app_user";
SQL
done

# The shared instance has no catch-all `host all all` rule: each application is
# authorised explicitly in pg_hba.conf. Add the Takeplace lines and reload.
bash "$script_dir/provision-pg-hba.sh" --env "$env_path" --pg-container "$pg_container"

echo
echo "done. databases, roles and host-based access are ready (no other project was modified)."
