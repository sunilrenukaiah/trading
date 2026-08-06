"""Central app logging configuration."""

from __future__ import annotations

import logging
import sys


def configure_app_logging(level: int = logging.INFO) -> None:
    """Configure root logger once so app.* loggers emit to stderr."""
    root = logging.getLogger()
    if root.handlers:
        return

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    root.addHandler(handler)
    root.setLevel(level)

    # Audit backend emits here; keep visible but not noisy.
    logging.getLogger("app.audit").setLevel(logging.INFO)
