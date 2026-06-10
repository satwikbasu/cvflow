"""runlock — fcntl single-run guard for discovery (daily cron + manual /discover)."""

from pathlib import Path

import pytest

from cvflow.runlock import AlreadyRunning, discovery_lock, is_locked


def test_second_acquire_raises_while_held(tmp_path: Path) -> None:
    path = str(tmp_path / "discover.lock")
    with discovery_lock(path):
        with pytest.raises(AlreadyRunning):
            with discovery_lock(path):
                pass


def test_lock_frees_on_context_exit(tmp_path: Path) -> None:
    path = str(tmp_path / "discover.lock")
    with discovery_lock(path):
        pass
    # released — re-acquirable, no raise
    with discovery_lock(path):
        pass


def test_is_locked_true_while_held_false_after(tmp_path: Path) -> None:
    path = str(tmp_path / "discover.lock")
    assert is_locked(path) is False
    with discovery_lock(path):
        assert is_locked(path) is True
    assert is_locked(path) is False
