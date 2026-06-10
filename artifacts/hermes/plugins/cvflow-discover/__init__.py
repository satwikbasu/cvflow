"""cvflow-discover plugin: makes the /discover slash command gateway-known.

The real work lives in the ``cvflow-discover`` HOOK (event ``command:discover``), which
the gateway fires only for *known* slash commands. ``/discover`` is not a Hermes built-in,
so this plugin registers it to make it known — which is what lets the hook fire. The
registered handler is a fallback only: in practice the hook intercepts first. If the hook
is missing, the fallback tells the user to install it rather than silently doing nothing.
"""

from __future__ import annotations

_FALLBACK = (
    "⚠️ cvflow /discover not active: the cvflow-discover hook is not installed. "
    "Install artifacts/hermes/hooks/cvflow-discover into ~/.hermes/hooks/ and restart "
    "the gateway."
)


def _fallback(raw_args: str) -> str | None:
    return _FALLBACK


def register(ctx) -> None:
    ctx.register_command(
        "discover",
        handler=_fallback,
        description="Trigger a cvflow discovery run now (handled by the cvflow-discover hook).",
        args_hint="",
    )
