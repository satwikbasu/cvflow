"""cvflow-gate plugin: makes the approval-gate slash commands gateway-known.

The real gating work lives in the ``cvflow-gate`` HOOK (events
``command:apply`` / ``command:skip``), which the gateway fires only for *known*
slash commands. Neither ``/apply`` nor ``/skip`` is a Hermes built-in, so this
plugin registers both to make them known — which is what lets the hook fire.
(``/apply`` is used instead of ``/approve`` precisely to avoid the built-in
``/approve`` command.)

The registered handlers are fallbacks only: in practice the hook intercepts the
command first and short-circuits before any plugin command handler runs. If the
hook is ever missing, the handler tells the user to install it rather than
silently doing nothing.
"""

from __future__ import annotations

_FALLBACK = (
    "⚠️ cvflow approval gate not active: the cvflow-gate hook is not installed. "
    "Install artifacts/hermes/hooks/cvflow-gate into ~/.hermes/hooks/ and restart "
    "the gateway."
)


def _fallback(raw_args: str) -> str | None:
    return _FALLBACK


def register(ctx) -> None:
    ctx.register_command(
        "apply",
        handler=_fallback,
        description="Approve & apply to a cvflow application (handled by the cvflow-gate hook).",
        args_hint="<job_id>",
    )
    ctx.register_command(
        "skip",
        handler=_fallback,
        description="Skip a cvflow application (handled by the cvflow-gate hook).",
        args_hint="<job_id>",
    )
