"""cvflow-gate plugin: makes the approval-gate slash commands gateway-known.

The real gating work lives in the ``cvflow-gate`` HOOK (events
``command:approve`` / ``command:skip``), which the gateway fires only for
*known* slash commands. ``/approve`` is already a built-in gateway command, but
``/skip`` is not — so this plugin registers ``/skip`` to make it known, which is
what lets the hook fire for it. (``register_command`` rejects names that clash
with a built-in, so attempting to register ``/approve`` here is a no-op; that is
fine because it is already known.)

The registered handler is a fallback only: in practice the hook intercepts the
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


def _handle_skip(raw_args: str) -> str | None:
    return _FALLBACK


def register(ctx) -> None:
    ctx.register_command(
        "skip",
        handler=_handle_skip,
        description="Skip a cvflow application (handled by the cvflow-gate hook).",
        args_hint="<job_id>",
    )
    ctx.register_command(
        "approve",
        handler=_handle_skip,
        description="Approve a cvflow application (handled by the cvflow-gate hook).",
        args_hint="<job_id>",
    )
