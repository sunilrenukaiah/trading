#!/usr/bin/env bash
# Run integration tests from repo root or backend directory.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ -x ".venv/bin/pytest" ]]; then
  PYTEST=".venv/bin/pytest"
elif command -v pytest >/dev/null 2>&1; then
  PYTEST="pytest"
else
  echo "pytest not found. Install dev deps: pip install -e '.[dev]'" >&2
  exit 1
fi

MODE="${1:-all}"
case "$MODE" in
  quick)
    exec "$PYTEST" tests -m quick -v --tb=short "${@:2}"
    ;;
  post_deploy)
    exec "$PYTEST" tests/post_deploy -m post_deploy -v --tb=short "${@:2}"
    ;;
  all)
    exec "$PYTEST" tests -v --tb=short "${@:2}"
    ;;
  *)
    echo "Usage: $0 [quick|post_deploy|all] [extra pytest args]" >&2
    exit 1
    ;;
esac
