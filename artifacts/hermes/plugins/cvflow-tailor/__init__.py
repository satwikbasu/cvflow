"""cvflow-tailor plugin: makes the /tailor slash command gateway-known.

The real work lives in the ``cvflow-tailor`` HOOK (event ``command:tailor``), which
the gateway fires only for *known* slash commands. ``/tailor`` is not a Hermes built-in,
so this plugin registers it to make it known — which is what lets the hook fire. The
registered handler is a fallback only: in practice the hook intercepts first. If the hook
is missing, the fallback tells the user to install it rather than silently doing nothing.
"""

from __future__ import annotations

_FALLBACK = (
    "⚠️ cvflow /tailor not active: the cvflow-tailor hook is not installed. "
    "Install artifacts/hermes/hooks/cvflow-tailor into ~/.hermes/hooks/ and restart "
    "the gateway."
)


def _fallback(raw_args: str) -> str | None:
    return _FALLBACK


def register(ctx) -> None:
    ctx.register_command(
        "tailor",
        handler=_fallback,
        description="Tailor one digest job now (handled by the cvflow-tailor hook).",
        args_hint="<number>",
    )
