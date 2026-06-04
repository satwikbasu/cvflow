from cvflow.mcp.server import build_server
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
