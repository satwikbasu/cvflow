"""Single-run guard for discovery (daily cron + manual /discover must never double-run).

Uses ``fcntl.flock(LOCK_EX | LOCK_NB)`` on a lock file. The lock is tied to the open
file description, so it auto-releases when the fd closes — robust against crashes and
stale locks (CLAUDE.md invariant 3: never fail silently, but also never wedge). The
authoritative guard is the run that acquires the lock; ``is_locked`` is a best-effort
probe for fast command-time UX.
"""

from __future__ import annotations

import fcntl
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

__all__ = ["AlreadyRunning", "discovery_lock", "is_locked", "tailor_lock"]


class AlreadyRunning(Exception):
    """Raised when a discovery run is already holding the lock."""


@contextmanager
def discovery_lock(path: str = "data/discover.lock") -> Iterator[None]:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(p), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise AlreadyRunning(f"discovery lock held: {path}") from exc
        yield
    finally:
        os.close(fd)  # closing the fd releases the flock


@contextmanager
def tailor_lock(path: str = "data/tailor.lock") -> Iterator[None]:
    """Serialize tailoring runs (one /tailor at a time; protects Tectonic + LLM concurrency)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(p), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise AlreadyRunning(f"tailor lock held: {path}") from exc
        yield
    finally:
        os.close(fd)  # closing the fd releases the flock


def is_locked(path: str = "data/discover.lock") -> bool:
    """Best-effort probe: try to take the lock on a separate fd and release it."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(p), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    except OSError:
        return True
    finally:
        os.close(fd)
