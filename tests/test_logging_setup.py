"""Tests for cvflow.logging_setup — rotating file logging into logs/."""

import logging
from pathlib import Path

from cvflow.logging_setup import setup_logging


def test_writes_log_file(tmp_path: Path) -> None:
    logger = setup_logging(tmp_path)
    logger.info("hello cvflow")
    for h in logger.handlers:
        h.flush()
    log_file = tmp_path / "cvflow.log"
    assert log_file.exists()
    assert "hello cvflow" in log_file.read_text()


def test_idempotent_no_duplicate_handlers(tmp_path: Path) -> None:
    first = setup_logging(tmp_path)
    count = len(first.handlers)
    second = setup_logging(tmp_path)
    assert first is second
    assert len(second.handlers) == count


def test_returns_logger_at_info(tmp_path: Path) -> None:
    logger = setup_logging(tmp_path)
    assert logger.level == logging.INFO
