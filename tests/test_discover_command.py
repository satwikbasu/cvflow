"""handle_discover_command — deterministic /discover trigger (off the agent loop)."""

from cvflow.discover_command import DiscoverResult, handle_discover_command


def _spy():
    calls = {"n": 0}

    def spawn() -> None:
        calls["n"] += 1

    return spawn, calls


def test_authorized_and_free_spawns_and_acks() -> None:
    spawn, calls = _spy()
    res = handle_discover_command(
        user_id=42, authorized_user_id=42, spawn=spawn, is_locked=lambda: False
    )
    assert isinstance(res, DiscoverResult)
    assert res.handled is True
    assert calls["n"] == 1
    assert "Discovery started" in res.message


def test_authorized_but_locked_does_not_spawn() -> None:
    spawn, calls = _spy()
    res = handle_discover_command(
        user_id=42, authorized_user_id=42, spawn=spawn, is_locked=lambda: True
    )
    assert res.handled is True
    assert calls["n"] == 0
    assert "already in progress" in res.message


def test_unauthorized_ignored_no_spawn() -> None:
    spawn, calls = _spy()
    res = handle_discover_command(
        user_id=999, authorized_user_id=42, spawn=spawn, is_locked=lambda: False
    )
    assert res.handled is False
    assert calls["n"] == 0
    assert res.message == ""
