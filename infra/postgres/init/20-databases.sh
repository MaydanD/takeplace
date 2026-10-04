#!/bin/bash
# =============================================================================
# Takeplace — dedicated integration-test database.
#
# The Stage 5+ concurrency suite must run on a real PostgreSQL database that is
# separate from the development data, so integration tests never touch dev rows.
# =============================================================================
set -euo pipefail

: "${POSTGRES_USER:?POSTGRES_USER is required}"
# POSTGRES_DB is the primary database name passed to the official image; the
# container does not receive TAKEPLACE_POSTGRES_DB.
: "${POSTGRES_DB:?POSTGRES_DB is required}"
: "${TAKEPLACE_DB_MIGRATOR_USER:?TAKEPLACE_DB_MIGRATOR_USER is required}"
: "${TAKEPLACE_DB_APP_USER:?TAKEPLACE_DB_APP_USER is required}"

TEST_DB="${TAKEPLACE_TEST_DB:-${POSTGRES_DB}_test}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
  -v test_db="$TEST_DB" \
  -v migrator_user="$TAKEPLACE_DB_MIGRATOR_USER" \
  -v app_user="$TAKEPLACE_DB_APP_USER" \
  <<-'EOSQL'
SELECT format('CREATE DATABASE %I OWNER %I', :'test_db', :'migrator_user')
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'test_db')
\gexec

SELECT format('GRANT CONNECT ON DATABASE %I TO %I', :'test_db', :'app_user')
\gexec
EOSQL

# Mirror the schema privileges that 10-roles.sh applies to the main database.
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$TEST_DB" \
  -v test_db="$TEST_DB" \
  -v migrator_user="$TAKEPLACE_DB_MIGRATOR_USER" \
  -v app_user="$TAKEPLACE_DB_APP_USER" \
  <<-'EOSQL'
GRANT CREATE ON DATABASE :"test_db" TO :"migrator_user";
GRANT CREATE, USAGE ON SCHEMA public TO :"migrator_user";
GRANT USAGE ON SCHEMA public TO :"app_user";
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO :"app_user";
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO :"app_user";
ALTER DEFAULT PRIVILEGES FOR ROLE :"migrator_user" IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO :"app_user";
ALTER DEFAULT PRIVILEGES FOR ROLE :"migrator_user" IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO :"app_user";
EOSQL

echo "[takeplace] integration-test database '$TEST_DB' ready."
