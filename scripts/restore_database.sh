#!/usr/bin/env bash
# Restore trading DB from a pg_dump SQL file (e.g. lab .lab-dumps/trading_*.sql).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DUMP="${1:-}"

if [[ -z "$DUMP" || ! -f "$DUMP" ]]; then
  echo "Usage: $0 /path/to/trading_backup.sql"
  echo "Example:"
  echo "  $0 ../trading-lab/.lab-dumps/trading_20260731T041257Z.sql"
  exit 1
fi

PSQL="${PSQL:-/opt/homebrew/opt/postgresql@15/bin/psql}"
if [[ ! -x "$PSQL" ]]; then
  PSQL="$(command -v psql || true)"
fi
if [[ -z "$PSQL" ]]; then
  echo "psql not found. Install PostgreSQL client tools."
  exit 1
fi

echo "Restoring from: $DUMP"
echo "Terminating connections to trading..."
"$PSQL" postgres -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'trading' AND pid <> pg_backend_pid();" >/dev/null 2>&1 || true

echo "Recreating database trading..."
"$PSQL" postgres -c "DROP DATABASE IF EXISTS trading;"
"$PSQL" postgres -c "CREATE DATABASE trading OWNER trading;"

echo "Loading dump (this may take a minute)..."
"$PSQL" -U trading -d trading -v ON_ERROR_STOP=1 -f "$DUMP"

echo "Done. Verify with:"
echo "  $PSQL -U trading -d trading -c \"SELECT COUNT(*) FROM paper_trades;\""
