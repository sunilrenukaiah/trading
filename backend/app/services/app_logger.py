"""App-wide logger helper — cheap stdlib logging, audit on ERROR is async."""

from __future__ import annotations

import logging

from app.logging_setup import configure_app_logging


def get_logger(name: str) -> logging.Logger:
    """Return a module logger (configures root handler on first use)."""
    configure_app_logging()
    return logging.getLogger(name)
