"""Launch FastAPI from PyCharm run configuration."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
sys.path.insert(0, str(ROOT / "scripts"))

from platform_utils import venv_python  # noqa: E402


def main() -> int:
    py = str(venv_python())
    cmd = [py, "-m", "uvicorn", "app.main:app", "--reload", "--port", "8000"]
    print("Starting FastAPI:", " ".join(cmd))
    return subprocess.call(cmd, cwd=BACKEND)


if __name__ == "__main__":
    raise SystemExit(main())
