from pathlib import Path

from cvflow.mcp.server import build_server, resolve_config_path
from cvflow.mcp.tools import TOOL_NAMES, CvflowTools


def _tools():
    from cvflow.storage import ApplicationStore

    return CvflowTools(
        store=ApplicationStore(":memory:"),
        knowledge=None, discovery=None, analyzer=None, tailor=None,
    )


def test_server_registers_exactly_the_allowlist():
    server = build_server(_tools())
    import asyncio

    registered = {t.name for t in asyncio.run(server.list_tools())}
    assert registered == set(TOOL_NAMES)


def test_approve_is_not_exposed():
    assert "approve" not in TOOL_NAMES
    server = build_server(_tools())
    import asyncio

    registered = {t.name for t in asyncio.run(server.list_tools())}
    assert "approve" not in registered


def test_resolve_config_path_prefers_env(monkeypatch):
    monkeypatch.setenv("CVFLOW_CONFIG", "/tmp/somewhere/config.yaml")
    assert resolve_config_path() == Path("/tmp/somewhere/config.yaml")


def test_resolve_config_path_default_is_absolute_repo_config(monkeypatch):
    """Without the env var, the path must be an ABSOLUTE config.yaml at the repo
    root — never a cwd-relative 'config.yaml' (the bug that loaded Hermes's own
    config when the gateway spawned us from ~/.hermes)."""
    monkeypatch.delenv("CVFLOW_CONFIG", raising=False)
    path = resolve_config_path()
    assert path.is_absolute()
    assert path.name == "config.yaml"
    # repo root holds pyproject.toml next to the resolved config path
    assert (path.parent / "pyproject.toml").exists()
