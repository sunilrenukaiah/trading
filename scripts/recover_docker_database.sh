#!/usr/bin/env bash
# Try to recover paper-trading data from the old Docker PostgreSQL (if still on disk).
# Start Docker Desktop first, then run:
#   ./scripts/recover_docker_database.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="$ROOT/backups"
STAMP="$(date +%Y%m%dT%H%M%SZ)"
OUT_FILE="$OUT_DIR/trading_docker_${STAMP}.sql"

mkdir -p "$OUT_DIR"

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker CLI not found."
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "Docker daemon is not running. Start Docker Desktop, then re-run this script."
  exit 1
fi

CONTAINER=""
while IFS= read -r line; do
  if [[ "$line" == *":5432->"* ]]; then
    CONTAINER="${line%%$'\t'*}"
    break
  fi
done < <(docker ps --format '{{.Names}}\t{{.Ports}}')

if [[ -z "$CONTAINER" ]]; then
  echo "No running container exposing port 5432."
  echo "Try: docker ps -a   (look for postgres / trading containers)"
  echo "If stopped: docker start <container_name>"
  exit 1
fi

echo "Found PostgreSQL container: $CONTAINER"
echo "Dumping to $OUT_FILE ..."

docker exec -e PGPASSWORD=trading "$CONTAINER" pg_dump -U trading -d trading --no-owner --no-acl > "$OUT_FILE"

echo "Dump complete. Row counts in source:"
docker exec -e PGPASSWORD=trading "$CONTAINER" psql -U trading -d trading -t -c "
SELECT 'paper_trades=' || COUNT(*) FROM paper_trades
UNION ALL SELECT 'paper_trade_plans=' || COUNT(*) FROM paper_trade_plans;
"

echo ""
echo "If counts look newer than the Jul 31 backup, restore with:"
echo "  ./scripts/restore_database.sh $OUT_FILE"
