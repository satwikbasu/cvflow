"""cvflow's MCP surface — exposes deterministic domain skills to Hermes.

The approval gate stays un-bypassable by construction: this package exposes NO
``approve`` tool. The agent may call ``request_review`` (which tailors and moves a
job to ``pending_review``), but only the human Telegram approval callback (Phase 8)
reaches :meth:`cvflow.storage.ApplicationStore.approve`.
"""

from cvflow.mcp.tools import TOOL_NAMES, CvflowTools, build_tools

__all__ = ["CvflowTools", "TOOL_NAMES", "build_tools"]
