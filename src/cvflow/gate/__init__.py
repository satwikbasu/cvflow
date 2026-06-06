"""Deterministic gate command handler — the human-approval path, off the agent loop.

This module is the SOLE caller of ``ApplicationStore.approve`` in production code.
A Hermes ``command:apply`` / ``command:skip`` hook invokes
:func:`handle_gate_command` with the inbound Telegram user id and args. The
agent/brain has no approve tool and cannot inject an inbound slash command, so it
can never reach this code path (CLAUDE.md invariant 1).

The user-facing command is ``/apply <job_id>`` (deliberately NOT ``/approve``,
which is a Hermes built-in — keeping a distinct verb avoids any collision).
"""

from __future__ import annotations

from dataclasses import dataclass

from cvflow.statemachine import IllegalTransition, Status
from cvflow.storage import ApplicationStore, UnknownJob

__all__ = ["GateResult", "handle_gate_command", "resolve_targets"]


@dataclass(frozen=True)
class GateResult:
    """Outcome of a gate command. ``handled`` short-circuits Hermes dispatch."""

    handled: bool
    message: str | None


def _same_user(a: object, b: object) -> bool:
    return str(a).strip() == str(b).strip()


def resolve_targets(args: str, store: ApplicationStore) -> tuple[list[str], list[str]]:
    """Resolve an apply/skip argument string into job_ids.

    Accepts ordinals (1 2), comma lists (1,2), ranges (1-3), 'all', and raw
    job_ids (containing ':'). Returns (resolved_job_ids_in_order, unrecognized_tokens),
    de-duplicated, order preserved.
    """
    text = args.strip().lower()
    resolved: list[str] = []
    unknown: list[str] = []
    seen: set[str] = set()

    def _add(job_id: str) -> None:
        if job_id and job_id not in seen:
            seen.add(job_id)
            resolved.append(job_id)

    if text == "all":
        for jid in store.digest_slots():
            _add(jid)
        return resolved, unknown

    for token in text.replace(",", " ").split():
        if ":" in token:  # raw job_id
            _add(token)
        elif "-" in token and all(part.isdigit() for part in token.split("-", 1)):
            lo, hi = (int(x) for x in token.split("-", 1))
            for n in range(lo, hi + 1):
                jid = store.get_digest_slot(n)
                if jid:
                    _add(jid)
                else:
                    unknown.append(str(n))
        elif token.isdigit():
            jid = store.get_digest_slot(int(token))
            if jid:
                _add(jid)
            else:
                unknown.append(token)
        else:
            unknown.append(token)
    return resolved, unknown


def handle_gate_command(
    *,
    command: str,
    args: str,
    user_id: object,
    authorized_user_id: object,
    store: ApplicationStore,
) -> GateResult:
    """Route a human ``/approve`` or ``/skip`` to deterministic state changes.

    Never fails silently for the authorized user: every outcome returns a
    message. An unauthorized user is ignored (handled=False, no message).
    """
    if not _same_user(user_id, authorized_user_id):
        return GateResult(handled=False, message=None)

    job_id = args.strip().split()[0] if args.strip() else ""
    if not job_id:
        return GateResult(handled=True, message=f"⚠️ Usage: /{command} <job_id>")

    if command == "apply":
        try:
            store.approve(job_id)
        except UnknownJob:
            return GateResult(handled=True, message=f"⚠️ No application {job_id}.")
        except IllegalTransition:
            app = store.get(job_id)
            status = app.status.value if app else "unknown"
            return GateResult(
                handled=True,
                message=f"⚠️ Can't approve {job_id}: it is {status}, not pending review.",
            )
        return GateResult(handled=True, message=f"✅ Approved {job_id} — submitting.")

    if command == "skip":
        try:
            store.set_status(job_id, Status.SKIPPED)
        except UnknownJob:
            return GateResult(handled=True, message=f"⚠️ No application {job_id}.")
        except IllegalTransition:
            app = store.get(job_id)
            status = app.status.value if app else "unknown"
            return GateResult(handled=True, message=f"⚠️ Can't skip {job_id}: it is {status}.")
        return GateResult(handled=True, message=f"⏭️ Skipped {job_id}.")

    return GateResult(handled=False, message=None)
