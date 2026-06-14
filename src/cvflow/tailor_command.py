"""Deterministic ``/tailor`` trigger — single job, off the agent loop.

A Hermes ``command:tailor`` hook calls :func:`handle_tailor_command` in the gateway process. It
resolves exactly ONE digest ordinal to a job_id and spawns a detached tailoring run; it never
approves. ``spawn`` and ``is_locked`` are injected so tests never fork or touch a real lock. The
approval-gate invariant (CLAUDE.md #1) is untouched — there is no ``approve`` anywhere here.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from cvflow.gate import resolve_targets
from cvflow.storage import ApplicationStore

__all__ = ["TailorResult", "handle_tailor_command"]


@dataclass(frozen=True)
class TailorResult:
    """Outcome of a /tailor command. ``handled`` short-circuits Hermes dispatch."""

    handled: bool
    message: str


def _same_user(a: object, b: object) -> bool:
    return str(a).strip() == str(b).strip()


def handle_tailor_command(
    *,
    args: str,
    user_id: object,
    authorized_user_id: object,
    store: ApplicationStore,
    spawn: Callable[[str], None],
    is_locked: Callable[[], bool],
) -> TailorResult:
    if not _same_user(user_id, authorized_user_id):
        return TailorResult(handled=False, message="")
    if not args.strip():
        return TailorResult(
            handled=True, message="⚠️ Usage: /tailor <number> — one job, e.g. /tailor 4"
        )
    targets, unknown = resolve_targets(args, store)
    if unknown:
        return TailorResult(
            handled=True,
            message=f"⚠️ I don't recognise {', '.join(unknown)} — pick a number from the last "
            "digest, e.g. /tailor 4",
        )
    if len(targets) != 1:
        return TailorResult(handled=True, message="⚠️ One job at a time, e.g. /tailor 4")
    if is_locked():
        return TailorResult(
            handled=True, message="⏳ A tailoring run is already in progress — try again shortly."
        )
    target = targets[0]
    spawn(target)
    # Show role @ company in the ack (not the raw job_id); fall back to the id if unknown.
    getter = getattr(store, "get", None)
    app = getter(target) if callable(getter) else None
    label = f"{app.role} @ {app.company}" if app is not None else target
    return TailorResult(
        handled=True, message=f"✂️ Tailoring {label} — I'll post the résumé + diff here."
    )
