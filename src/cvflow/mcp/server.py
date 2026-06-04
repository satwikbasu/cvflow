"""FastMCP stdio adapter — registers exactly the TOOL_NAMES allowlist.

Defense in depth: this server only registers the allowlisted skills, AND the
Hermes config pins the same set under ``tools.include``. ``approve`` appears in
neither, so the agent has no path to satisfy the gate.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from cvflow.mcp.tools import TOOL_NAMES, CvflowTools, build_tools


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

    config = load_config("config.yaml")
    server = build_server(build_tools(config))
    server.run(transport="stdio")
