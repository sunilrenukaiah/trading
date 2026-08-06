#!/usr/bin/env bash
# Daily backup of the trading PostgreSQL database.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="$ROOT/backend"
ENV_FILE="$BACKEND/.env"
OUT_DIR="$ROOT/backups"
STAMP="$(date +%Y%m%dT%H%M%SZ)"
OUT_FILE="$OUT_DIR/trading_${STAMP}.sql"

mkdir -p "$OUT_DIR"

if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  set -a
  source "$ENV_FILE"
  set +a
fi

DATABASE_URL="${DATABASE_URL:-postgresql+asyncpg://trading:trading@localhost:5432/trading}"

PG_DUMP="${PG_DUMP:-/opt/homebrew/opt/postgresql@15/bin/pg_dump}"
if [[ ! -x "$PG_DUMP" ]]; then
  PG_DUMP="$(command -v pg_dump || true)"
fi
if [[ -z "$PG_DUMP" ]]; then
  echo "pg_dump not found."
  exit 1
fi

# Parse postgresql+asyncpg://user:pass@host:port/db
if [[ "$DATABASE_URL" =~ postgresql\+asyncpg://([^:]+):([^@]+)@([^:/]+):?([0-9]*)/([^?]+) ]]; then
  PGUSER="${BASH_REMATCH[1]}"
  PGPASSWORD="${BASH_REMATCH[2]}"
  PGHOST="${BASH_REMATCH[3]}"
  PGPORT="${BASH_REMATCH[4]:-5432}"
  PGDATABASE="${BASH_REMATCH[5]}"
else
  echo "Unsupported DATABASE_URL: $DATABASE_URL"
  exit 1
fi

export PGPASSWORD
"$PG_DUMP" -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$PGDATABASE" --no-owner --no-acl -f "$OUT_FILE"
ln -sf "$(basename "$OUT_FILE")" "$OUT_DIR/latest.sql"

echo "Backup written: $OUT_FILE"
echo "Symlink: $OUT_DIR/latest.sql"
