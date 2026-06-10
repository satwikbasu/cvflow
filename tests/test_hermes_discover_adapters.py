"""cvflow-discover hook adapter — routes /discover to handle_discover_command."""

import asyncio
import importlib.util
from pathlib import Path

HOOK = Path("artifacts/hermes/hooks/cvflow-discover/handler.py")


def _load(path):
    spec = importlib.util.spec_from_file_location("cvflow_discover_hook", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_hook_authorized_spawns_and_acks(monkeypatch):
    mod = _load(HOOK)
    calls = {"n": 0}
    monkeypatch.setattr(mod, "_authorized_user_id", lambda: 42)
    monkeypatch.setattr(mod, "_spawn", lambda: calls.__setitem__("n", calls["n"] + 1))
    monkeypatch.setattr(mod, "_is_locked", lambda: False)
    result = asyncio.run(mod.handle("command:discover", {"user_id": 42}))
    assert result["decision"] == "handled"
    assert "Discovery started" in result["message"]
    assert calls["n"] == 1


def test_hook_ignores_unauthorized(monkeypatch):
    mod = _load(HOOK)
    calls = {"n": 0}
    monkeypatch.setattr(mod, "_authorized_user_id", lambda: 42)
    monkeypatch.setattr(mod, "_spawn", lambda: calls.__setitem__("n", calls["n"] + 1))
    monkeypatch.setattr(mod, "_is_locked", lambda: False)
    result = asyncio.run(mod.handle("command:discover", {"user_id": 999}))
    assert result == {}
    assert calls["n"] == 0
