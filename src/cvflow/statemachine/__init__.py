"""The deterministic application state machine — the un-bypassable approval gate.

CLAUDE.md invariant 1: the only path to ``approved`` is :func:`approve`, and the
submission entrypoint asserts ``status == approved`` via :func:`guard_can_submit`.
The generic :func:`transition` deliberately *refuses* ``approved`` as a target, so
an agent loop driving status changes can never satisfy the gate on its own.
"""

from __future__ import annotations

from enum import StrEnum


class Status(StrEnum):
    DISCOVERED = "discovered"
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    APPLIED = "applied"
    OTP_TIMEOUT = "otp_timeout"
    SKIPPED = "skipped"
    FAILED = "failed"


class IllegalTransition(Exception):
    """Raised on a status change that the legal-transition table forbids."""


class SubmissionBlocked(Exception):
    """Raised by the submission guard when status is not ``approved``."""


# Legal transitions EXCLUDING the approved gate (handled solely by approve()).
_LEGAL: dict[Status, frozenset[Status]] = {
    Status.DISCOVERED: frozenset({Status.PENDING_REVIEW, Status.SKIPPED, Status.FAILED}),
    Status.PENDING_REVIEW: frozenset({Status.SKIPPED, Status.FAILED}),
    Status.APPROVED: frozenset({Status.APPLIED, Status.OTP_TIMEOUT, Status.FAILED}),
    Status.OTP_TIMEOUT: frozenset({Status.APPLIED, Status.SKIPPED, Status.FAILED}),
    Status.APPLIED: frozenset(),
    Status.SKIPPED: frozenset(),
    Status.FAILED: frozenset(),
}


def transition(current: Status, target: Status) -> Status:
    """Validate and return ``target`` for a non-approval status change.

    Raises :class:`IllegalTransition` for any disallowed change. ``approved`` is
    never a valid target here — use :func:`approve`.
    """
    if target is Status.APPROVED:
        raise IllegalTransition(
            "approved can only be reached via approve(); it is not a generic transition target"
        )
    if target not in _LEGAL[current]:
        raise IllegalTransition(f"illegal transition: {current.value} -> {target.value}")
    return target


def approve(current: Status) -> Status:
    """The *sole* producer of ``approved``. Requires ``current == pending_review``."""
    if current is not Status.PENDING_REVIEW:
        raise IllegalTransition(f"approve() requires pending_review, got {current.value}")
    return Status.APPROVED


def guard_can_submit(status: Status) -> None:
    """Raise :class:`SubmissionBlocked` unless ``status == approved``."""
    if status is not Status.APPROVED:
        raise SubmissionBlocked(f"submission blocked: status is {status.value}, not approved")
