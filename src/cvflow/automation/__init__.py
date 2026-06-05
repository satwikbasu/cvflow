"""Deterministic browser automation skill (Phase 9, Goals 5 & 8).

Field values are resolved deterministic-first (form_fields lookup -> grounded
essay composition -> clarify/skip); an LLM never guesses *what fact* fills a box
(invariant 2). guard_can_submit gates every entrypoint (Goal 4). Failures are
never silent: a crash marks the app failed and notifies the user with the job URL
(invariant 3).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol

from cvflow.essays import compose_answer

__all__ = [
    "FieldSpec",
    "FieldFill",
    "FieldResolution",
    "NeedsClarification",
    "resolve_field",
]


class _Provider(Protocol):
    def generate(self, prompt: str) -> str: ...


@dataclass(frozen=True)
class FieldSpec:
    label: str
    name: str
    field_type: str  # text | textarea | select | checkbox | file
    options: list[str]
    required: bool


@dataclass(frozen=True)
class FieldFill:
    label: str
    value: str
    source: str  # form_fields | composed | clarified | skipped


@dataclass(frozen=True)
class FieldResolution:
    fill: FieldFill | None
    clarify: bool
    question: str | None


@dataclass(frozen=True)
class NeedsClarification:
    job_id: str
    question: str


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.strip().lower()).strip("_")


def resolve_field(
    spec: FieldSpec, form_fields: Any, knowledge: Any, *, provider: _Provider
) -> FieldResolution:
    """Resolve one field deterministic-first; never guess a fact."""
    # 1) literal lookup against form_fields (exact name, then normalized label)
    for key in (spec.name, _normalize(spec.name), _normalize(spec.label)):
        if form_fields.is_filled(key):
            return FieldResolution(
                FieldFill(spec.label, form_fields.require(key), "form_fields"),
                False, None,
            )
    # 2) free-text / essay -> grounded composition
    if spec.field_type == "textarea":
        ans = compose_answer(spec.label, knowledge, provider=provider)
        if ans.grounded and ans.text:
            return FieldResolution(
                FieldFill(spec.label, ans.text, "composed"), False, None
            )
    # 3) escalate (required) or skip (optional) -- never guess
    if spec.required:
        return FieldResolution(None, True, spec.label)
    return FieldResolution(FieldFill(spec.label, "", "skipped"), False, None)
