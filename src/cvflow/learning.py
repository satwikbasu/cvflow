"""Preference learning (Phase 13C): propose preference edits from apply/skip history.

PROPOSES ONLY — never edits preferences.md or filters. The durable source of truth
stays the version-controlled preferences.md + config (auditable, like the gate).
"""

from __future__ import annotations

import json
from typing import Any, Protocol


class _Provider(Protocol):
    def generate(self, prompt: str) -> str: ...


_PROMPT = """You analyze a user's job-application decisions and propose preference updates.
APPLIED (wanted) and SKIPPED (not wanted) jobs are below. Identify concrete patterns and
suggest specific, optional edits to their preferences file. Do not invent facts about the user.
Reply with a short plain-language message (no JSON).

APPLIED:
{applied}

SKIPPED:
{skipped}
"""


def summarize_decisions(
    applied: list[dict[str, Any]],
    skipped: list[dict[str, Any]],
    *,
    provider: _Provider,
    min_decisions: int = 5,
) -> str | None:
    """Return a suggestion string, or None when there's too little signal."""
    if len(applied) + len(skipped) < min_decisions:
        return None
    return provider.generate(
        _PROMPT.format(
            applied=json.dumps(applied, indent=2), skipped=json.dumps(skipped, indent=2)
        )
    ).strip()
