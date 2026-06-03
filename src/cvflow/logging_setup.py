"""Rotating file logging for cvflow.

Writes to ``<log_dir>/cvflow.log`` plus the console. Telegram surfacing of
errors (invariant: never fail silently) is wired later, in the Telegram phase.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

_LOGGER_NAME = "cvflow"
_MAX_BYTES = 5 * 1024 * 1024
_BACKUP_COUNT = 5


def setup_logging(log_dir: str | Path, level: int = logging.INFO) -> logging.Logger:
    """Configure and return the ``cvflow`` logger.

    Idempotent: repeated calls reconfigure the level without stacking handlers.
    """
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = False

    if logger.handlers:
        for handler in logger.handlers:
            handler.setLevel(level)
        return logger

    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")

    file_handler = RotatingFileHandler(
        log_dir / "cvflow.log", maxBytes=_MAX_BYTES, backupCount=_BACKUP_COUNT
    )
    file_handler.setFormatter(fmt)
    file_handler.setLevel(level)
    logger.addHandler(file_handler)

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    console.setLevel(level)
    logger.addHandler(console)

    return logger
