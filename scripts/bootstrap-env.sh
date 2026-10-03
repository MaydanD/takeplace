#!/usr/bin/env bash
# =============================================================================
# Create a local .env from .env.example, filling in generated secrets.
#
# Idempotent: refuses to overwrite an existing .env.
# =============================================================================
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ -f .env ]]; then
  echo ".env already exists; leaving it untouched."
  exit 0
fi

if [[ ! -f .env.example ]]; then
  echo "error: .env.example not found" >&2
  exit 1
fi

# Cross-platform secret generation: prefer openssl, fall back to python.
gen_secret() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 32
  else
    python -c "import secrets; print(secrets.token_hex(32))"
  fi
}

gen_base64url_32() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -base64 32 | tr '+/' '-_' | tr -d '='
  else
    python -c "import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip('='))"
  fi
}

cp .env.example .env

# Replace placeholders with real random values. Uses a portable sed -i shim.
replace() {
  local key="$1" value="$2"
  if sed --version >/dev/null 2>&1; then
    sed -i "s|^${key}=.*|${key}=${value}|" .env
  else
    sed -i '' "s|^${key}=.*|${key}=${value}|" .env
  fi
}

replace "TAKEPLACE_POSTGRES_SUPERUSER_PASSWORD" "$(gen_secret)"
replace "TAKEPLACE_DB_MIGRATOR_PASSWORD" "$(gen_secret)"
replace "TAKEPLACE_DB_APP_PASSWORD" "$(gen_secret)"
replace "TAKEPLACE_IDEMPOTENCY_HMAC_KEY" "$(gen_secret)"
replace "TAKEPLACE_ABUSE_HMAC_KEY" "$(gen_secret)"
replace "TAKEPLACE_VK_ENCRYPTION_KEYS" "1:$(gen_base64url_32)"

echo ".env created with generated secrets."
