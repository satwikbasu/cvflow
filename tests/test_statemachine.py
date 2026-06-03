"""Tests for the deterministic approval state machine (Phase 1 gate core)."""

import pytest

from cvflow.statemachine import (
    IllegalTransition,
    Status,
    SubmissionBlocked,
    approve,
    guard_can_submit,
    transition,
)

LEGAL = {
    (Status.DISCOVERED, Status.PENDING_REVIEW),
    (Status.DISCOVERED, Status.SKIPPED),
    (Status.DISCOVERED, Status.FAILED),
    (Status.PENDING_REVIEW, Status.SKIPPED),
    (Status.PENDING_REVIEW, Status.FAILED),
    (Status.APPROVED, Status.APPLIED),
    (Status.APPROVED, Status.OTP_TIMEOUT),
    (Status.APPROVED, Status.FAILED),
    (Status.OTP_TIMEOUT, Status.APPLIED),
    (Status.OTP_TIMEOUT, Status.SKIPPED),
    (Status.OTP_TIMEOUT, Status.FAILED),
}


def test_legal_transitions_pass_through() -> None:
    for current, target in LEGAL:
        assert transition(current, target) is target


def test_illegal_transitions_raise() -> None:
    for current in Status:
        for target in Status:
            if (current, target) in LEGAL or target is Status.APPROVED:
                continue
            with pytest.raises(IllegalTransition):
                transition(current, target)


def test_transition_never_produces_approved() -> None:
    # approved must be unreachable through the generic path; only approve() may yield it.
    for current in Status:
        with pytest.raises(IllegalTransition):
            transition(current, Status.APPROVED)


def test_approve_only_from_pending_review() -> None:
    assert approve(Status.PENDING_REVIEW) is Status.APPROVED


def test_approve_from_other_states_raises() -> None:
    for current in Status:
        if current is Status.PENDING_REVIEW:
            continue
        with pytest.raises(IllegalTransition):
            approve(current)


def test_guard_allows_only_approved() -> None:
    guard_can_submit(Status.APPROVED)  # does not raise


def test_guard_blocks_every_non_approved_status() -> None:
    for status in Status:
        if status is Status.APPROVED:
            continue
        with pytest.raises(SubmissionBlocked):
            guard_can_submit(status)
