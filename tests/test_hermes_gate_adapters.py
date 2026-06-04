import asyncio
import importlib.util
from pathlib import Path

from cvflow.statemachine import Status
from cvflow.storage import ApplicationStore

HOOK = Path("artifacts/hermes/hooks/cvflow-gate/handler.py")


def _load(path):
    spec = importlib.util.spec_from_file_location("cvflow_gate_hook", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _pending_store():
    store = ApplicationStore(":memory:")
    store.add("j1", "Acme", "Eng", "http://jd")
    store.set_status("j1", Status.PENDING_REVIEW)
    return store


def test_hook_dispatches_approve_to_pure_handler(monkeypatch):
    store = _pending_store()
    mod = _load(HOOK)
    monkeypatch.setattr(mod, "_build_store", lambda: store)
    monkeypatch.setattr(mod, "_authorized_user_id", lambda: 1291545895)
    result = asyncio.run(mod.handle("command:approve",
        {"command": "approve", "args": "j1", "user_id": 1291545895}))
    assert result["decision"] == "handled"
    assert "Approved" in result["message"]
    assert store.get("j1").status is Status.APPROVED


def test_bare_approve_falls_through_to_builtin(monkeypatch):
    store = _pending_store()
    mod = _load(HOOK)
    monkeypatch.setattr(mod, "_build_store", lambda: store)
    monkeypatch.setattr(mod, "_authorized_user_id", lambda: 1291545895)
    result = asyncio.run(mod.handle("command:approve",
        {"command": "approve", "args": "", "user_id": 1291545895}))
    assert result == {}
    assert store.get("j1").status is Status.PENDING_REVIEW


def test_hook_ignores_unauthorized(monkeypatch):
    store = _pending_store()
    mod = _load(HOOK)
    monkeypatch.setattr(mod, "_build_store", lambda: store)
    monkeypatch.setattr(mod, "_authorized_user_id", lambda: 1291545895)
    result = asyncio.run(mod.handle("command:approve",
        {"command": "approve", "args": "j1", "user_id": 999}))
    assert result == {}
    assert store.get("j1").status is Status.PENDING_REVIEW
