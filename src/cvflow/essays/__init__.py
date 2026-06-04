"""Personality-fingerprint composer for essays / free-text fields.

Synthesizes answers from EXISTING profile facts (CLAUDE.md invariant 2 +
essay-auto-answer policy): the LLM may rephrase/emphasize real KB content in
the user's voice, but must NOT invent facts. Any answer whose citations don't
map to real KB docs — or any explicitly ungroundable question — returns
``needs_clarification`` instead of a guess.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

__all__ = ["EssayAnswer", "compose_answer"]


class _Provider(Protocol):
    def generate(self, prompt: str) -> str: ...


@dataclass(frozen=True)
class EssayAnswer:
    text: str | None
    grounded: bool
    citations: list[str]
    needs_clarification: bool
    clarification: str | None


_PROMPT = """You answer a job-application question in the applicant's voice.
Use ONLY facts present in the profile below. Do not invent anything.
If the question needs a fact that is genuinely absent, do not guess — instead
reply with JSON {{"needs_clarification": true, "missing": "<what is missing>"}}.
Otherwise reply with JSON {{"answer": "<text>", "citations": ["<doc keys used>"]}}.

PROFILE:
{context}

QUESTION:
{question}
"""


def _strip_fence(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        if t.endswith("```"):
            t = t[: t.rfind("```")]
    return t.strip()


def compose_answer(question: str, knowledge: Any, *, provider: _Provider) -> EssayAnswer:
    raw = provider.generate(_PROMPT.format(context=knowledge.full_context(), question=question))
    try:
        d = json.loads(_strip_fence(raw))
    except (ValueError, json.JSONDecodeError):
        return EssayAnswer(None, False, [], True, "could not compose an answer")

    if d.get("needs_clarification"):
        return EssayAnswer(None, False, [], True, str(d.get("missing", "missing information")))

    answer = str(d.get("answer", "")).strip()
    citations = [str(c) for c in d.get("citations", [])]
    valid_keys = set(knowledge.doc_keys())
    grounded = bool(answer) and bool(citations) and all(c in valid_keys for c in citations)
    if not grounded:
        return EssayAnswer(None, False, citations, True, "answer not grounded in profile")
    return EssayAnswer(answer, True, citations, False, None)
