#!/usr/bin/env bash
# When the agent finishes a turn, run the full suite and request a fix loop on failure.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
LOG_DIR="$ROOT/.cursor/test-runs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/latest-full.log"

cd "$ROOT/backend"

if [[ -x ".venv/bin/pytest" ]]; then
  PYTEST=".venv/bin/pytest"
elif command -v pytest >/dev/null 2>&1; then
  PYTEST="pytest"
else
  exit 0
fi

set +e
"$PYTEST" tests -v --tb=short >"$LOG_FILE" 2>&1
STATUS=$?
set -e

if [[ $STATUS -eq 0 ]]; then
  exit 0
fi

SUMMARY="$(grep -E 'FAILED|ERROR' "$LOG_FILE" | head -n 15 | sed 's/"/\\"/g' | tr '\n' '; ')"
TAIL="$(tail -n 25 "$LOG_FILE" | sed 's/"/\\"/g' | tr '\n' ' ')"

cat <<EOF
{"followup_message":"Integration tests failed after the last code change. Read $LOG_FILE, fix every failing test/import error, then re-run: cd backend && ./scripts/run_tests.sh all. Failures: ${SUMMARY} Tail: ${TAIL}"}
EOF
exit 0
