"""Deterministic ``/discover`` trigger — the gate's twin, off the agent loop.

A Hermes ``command:discover`` hook invokes :func:`handle_discover_command` in the gateway
process (outside the brain). It is a *trigger*, never an approval: it spawns a detached
discovery run and acks. ``spawn`` and ``is_locked`` are injected so tests never fork a
process or touch a real lock. The approval-gate invariant (CLAUDE.md #1) is untouched —
there is no ``approve`` anywhere here.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

__all__ = ["DiscoverResult", "handle_discover_command"]


@dataclass(frozen=True)
class DiscoverResult:
    """Outcome of a /discover command. ``handled`` short-circuits Hermes dispatch."""

    handled: bool
    message: str


def _same_user(a: object, b: object) -> bool:
    return str(a).strip() == str(b).strip()


def handle_discover_command(
    *,
    user_id: object,
    authorized_user_id: object,
    spawn: Callable[[], None],
    is_locked: Callable[[], bool],
) -> DiscoverResult:
    if not _same_user(user_id, authorized_user_id):
        return DiscoverResult(handled=False, message="")
    if is_locked():
        return DiscoverResult(
            handled=True,
            message="⏳ A discovery run is already in progress — the digest is on its way.",
        )
    spawn()
    return DiscoverResult(
        handled=True,
        message="🔎 Discovery started — I'll post progress, the digest, "
        "and the dropped-jobs report here.",
    )
