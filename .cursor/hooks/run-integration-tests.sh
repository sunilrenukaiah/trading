#!/usr/bin/env bash
# After Python edits under backend/app or backend/ui, run fast integration checks.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
LOG_DIR="$ROOT/.cursor/test-runs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/latest-quick.log"

cd "$ROOT/backend"

if [[ -x ".venv/bin/pytest" ]]; then
  PYTEST=".venv/bin/pytest"
elif command -v pytest >/dev/null 2>&1; then
  PYTEST="pytest"
else
  echo '{"additional_context":"Integration tests skipped: pytest not installed. Run: cd backend && pip install -e \".[dev]\""}' 
  exit 0
fi

set +e
"$PYTEST" tests -m quick -q --tb=line >"$LOG_FILE" 2>&1
STATUS=$?
set -e

if [[ $STATUS -ne 0 ]]; then
  FAILURES="$(tail -n 40 "$LOG_FILE" | sed 's/"/\\"/g' | tr '\n' ' ')"
  cat <<EOF
{"additional_context":"Integration tests FAILED after edit. Fix before publishing. Output (tail): ${FAILURES}"}
EOF
  exit 0
fi

echo '{"additional_context":"Quick integration tests passed."}'
exit 0
