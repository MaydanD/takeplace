#!/bin/bash
# =============================================================================
# Takeplace — PostgreSQL bootstrap (PROJECT-SPEC §46, §32.6)
#
# Runs once, as the bootstrap superuser, when the data volume is created.
# It provisions the two application roles and their baseline privileges.
#
#   takeplace_migrator : owns schema objects, runs DDL/migrations.
#   takeplace_app      : DML only, no CREATE/ALTER/DROP.
#
# The bootstrap superuser is never used by the application.
#
# Note: psql `:'var'` substitution does NOT happen inside dollar-quoted `DO $$`
# blocks, so conditional role creation uses `\gexec` instead of PL/pgSQL.
# =============================================================================
set -euo pipefail

: "${POSTGRES_USER:?POSTGRES_USER is required}"
: "${POSTGRES_DB:?POSTGRES_DB is required}"

: "${TAKEPLACE_DB_MIGRATOR_USER:?TAKEPLACE_DB_MIGRATOR_USER is required}"
: "${TAKEPLACE_DB_MIGRATOR_PASSWORD:?TAKEPLACE_DB_MIGRATOR_PASSWORD is required}"
: "${TAKEPLACE_DB_APP_USER:?TAKEPLACE_DB_APP_USER is required}"
: "${TAKEPLACE_DB_APP_PASSWORD:?TAKEPLACE_DB_APP_PASSWORD is required}"

: "${TAKEPLACE_DB_STATEMENT_TIMEOUT_MS:=10000}"
: "${TAKEPLACE_DB_LOCK_TIMEOUT_MS:=3000}"
: "${TAKEPLACE_DB_IDLE_IN_TRANSACTION_TIMEOUT_MS:=15000}"
: "${TAKEPLACE_DB_MIGRATION_STATEMENT_TIMEOUT_MS:=300000}"

psql \
  -v ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  -v migrator_user="$TAKEPLACE_DB_MIGRATOR_USER" \
  -v migrator_password="$TAKEPLACE_DB_MIGRATOR_PASSWORD" \
  -v app_user="$TAKEPLACE_DB_APP_USER" \
  -v app_password="$TAKEPLACE_DB_APP_PASSWORD" \
  -v database_name="$POSTGRES_DB" \
  -v statement_timeout="${TAKEPLACE_DB_STATEMENT_TIMEOUT_MS}ms" \
  -v lock_timeout="${TAKEPLACE_DB_LOCK_TIMEOUT_MS}ms" \
  -v idle_timeout="${TAKEPLACE_DB_IDLE_IN_TRANSACTION_TIMEOUT_MS}ms" \
  -v migration_statement_timeout="${TAKEPLACE_DB_MIGRATION_STATEMENT_TIMEOUT_MS}ms" \
  <<-'EOSQL'
-- --- roles -----------------------------------------------------------------
-- `\gexec` runs the generated CREATE ROLE only when the role does not exist.
SELECT format('CREATE ROLE %I LOGIN', :'migrator_user')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'migrator_user')
\gexec

SELECT format('CREATE ROLE %I LOGIN', :'app_user')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'app_user')
\gexec

-- Passwords are (re)set on every bootstrap so that the role always matches .env.
ALTER ROLE :"migrator_user" LOGIN PASSWORD :'migrator_password';
ALTER ROLE :"app_user"      LOGIN PASSWORD :'app_password';

-- --- per-role session safety net (PROJECT-SPEC §32.6) ----------------------
-- Connection-level settings from the backend override these, but any other
-- client (psql, admin tooling, worker) still gets safe defaults.
ALTER ROLE :"migrator_user" SET statement_timeout = :'migration_statement_timeout';
ALTER ROLE :"migrator_user" SET lock_timeout = :'lock_timeout';
ALTER ROLE :"migrator_user" SET idle_in_transaction_session_timeout = :'idle_timeout';

ALTER ROLE :"app_user" SET statement_timeout = :'statement_timeout';
ALTER ROLE :"app_user" SET lock_timeout = :'lock_timeout';
ALTER ROLE :"app_user" SET idle_in_transaction_session_timeout = :'idle_timeout';
ALTER ROLE :"app_user" SET default_transaction_isolation = 'read committed';

-- --- database / schema access ----------------------------------------------
GRANT CONNECT ON DATABASE :"database_name" TO :"migrator_user", :"app_user";

-- migrator needs CREATE on the database to install trusted extensions such as
-- btree_gist, and CREATE on the schema to own tables (§45).
GRANT CREATE ON DATABASE :"database_name" TO :"migrator_user";
GRANT CREATE, USAGE ON SCHEMA public TO :"migrator_user";
GRANT USAGE ON SCHEMA public TO :"app_user";

-- Defense in depth: PG15 already removes CREATE from PUBLIC, state it explicitly.
REVOKE CREATE ON SCHEMA public FROM PUBLIC;

-- app must not be able to change the schema (PROJECT-SPEC §46).
REVOKE CREATE ON DATABASE :"database_name" FROM :"app_user";
REVOKE CREATE ON DATABASE :"database_name" FROM PUBLIC;

-- --- DML grants for existing objects ---------------------------------------
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO :"app_user";
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO :"app_user";

-- --- DML grants for objects created later by migrator ----------------------
ALTER DEFAULT PRIVILEGES FOR ROLE :"migrator_user" IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO :"app_user";
ALTER DEFAULT PRIVILEGES FOR ROLE :"migrator_user" IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO :"app_user";
EOSQL

echo "[takeplace] PostgreSQL roles and privileges provisioned."
