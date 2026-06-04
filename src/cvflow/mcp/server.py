"""FastMCP stdio adapter — registers exactly the TOOL_NAMES allowlist.

Defense in depth: this server only registers the allowlisted skills, AND the
Hermes config pins the same set under ``tools.include``. ``approve`` appears in
neither, so the agent has no path to satisfy the gate.
"""

from __future__ import annotations

import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from cvflow.mcp.tools import TOOL_NAMES, CvflowTools, build_tools


def resolve_config_path() -> Path:
    """Locate cvflow's ``config.yaml`` with an ABSOLUTE path.

    The gateway spawns this server with its own working directory (e.g.
    ``~/.hermes``), so a cwd-relative ``"config.yaml"`` would load the wrong
    file. Resolution order: the ``CVFLOW_CONFIG`` env var, else the repo root
    derived from this file's location (``src/cvflow/mcp/server.py`` →
    ``parents[3]`` is the repo root, alongside ``pyproject.toml``).
    """
    env = os.environ.get("CVFLOW_CONFIG")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[3] / "config.yaml"


def build_server(tools: CvflowTools) -> FastMCP:
    """Build a FastMCP server exposing the allowlisted cvflow skills."""
    server = FastMCP("cvflow")
    for name in TOOL_NAMES:
        method = getattr(tools, name, None)
        if method is None or not callable(method):
            raise RuntimeError(
                f"TOOL_NAMES lists {name!r} but CvflowTools has no such method"
            )
        server.add_tool(method, name=name)
    return server


def main() -> None:
    """Entrypoint: build from the live config and serve over stdio."""
    from cvflow.config import load_config

    config_path = resolve_config_path()
    # cvflow's config holds paths relative to the repo root (profile/, data/…).
    # The gateway spawns us from its own cwd (e.g. ~/.hermes), so anchor to the
    # repo root here — one chdir fixes every relative path consistently.
    os.chdir(config_path.parent)
    config = load_config(config_path)
    server = build_server(build_tools(config))
    server.run(transport="stdio")
