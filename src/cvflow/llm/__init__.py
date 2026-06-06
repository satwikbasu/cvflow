"""Thin Gemini client for the resume-tailoring escalation (Phase 2).

Only the tailoring model lives here. The agent *brain* (NIM
``meta/llama-3.3-70b-instruct``) is configured inside Hermes (Phase 7), not in code.

Respects the free tier by design (CLAUDE.md invariant 4): requests-per-day are
tracked and the provider raises :class:`RpdExceeded` rather than overrunning the
quota silently. Identical prompts are cache-served and do not consume budget.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any, Protocol, cast


class LLMError(Exception):
    """Base class for LLM client errors."""


class RpdExceeded(LLMError):
    """Raised when a call would exceed the configured requests-per-day budget."""


def _today_utc() -> date:
    return datetime.now(UTC).date()


class _GenaiClient(Protocol):
    models: Any


class GeminiProvider:
    """Gemini text generation with per-day budget tracking + in-memory response cache."""

    def __init__(
        self,
        api_key: str,
        model: str,
        max_requests_per_day: int,
        *,
        client: _GenaiClient | None = None,
        now: Callable[[], date] = _today_utc,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._max_rpd = max_requests_per_day
        self._now = now
        self._client = client
        self._cache: dict[str, str] = {}
        self._count_date: date | None = None
        self._count = 0

    def _get_client(self) -> _GenaiClient:
        if self._client is None:
            from google import genai

            self._client = cast(_GenaiClient, genai.Client(api_key=self._api_key))
        return self._client

    def _spend_one(self) -> None:
        today = self._now()
        if self._count_date != today:
            self._count_date = today
            self._count = 0
        if self._count >= self._max_rpd:
            raise RpdExceeded(
                f"Gemini RPD budget reached ({self._max_rpd}) for {today.isoformat()}"
            )
        self._count += 1

    def generate(self, prompt: str) -> str:
        """Return generated text for ``prompt``; cache hits never consume budget."""
        cache_key = f"{self._model}\x00{prompt}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        self._spend_one()
        response = self._get_client().models.generate_content(
            model=self._model, contents=prompt
        )
        text = response.text or ""
        self._cache[cache_key] = text
        return text


class RpmExceeded(LLMError):
    """Raised when a call would exceed the configured requests-per-minute budget."""


def _now_seconds() -> float:
    import time
    return time.monotonic()


def _urllib_post(url: str, headers: dict[str, str], body: str) -> str:
    from urllib.request import Request, urlopen

    req = Request(url, data=body.encode(), headers=headers, method="POST")
    # NIM free-tier latency can swing past a minute; give the call room. The cron
    # path has no 120s agent limit, and the RPM budget still guards call volume.
    with urlopen(req, timeout=180) as resp:  # noqa: S310 (https by config)
        if resp.status != 200:
            raise LLMError(f"NIM POST {url} -> HTTP {resp.status}")
        raw: bytes = resp.read()
        return raw.decode("utf-8", errors="replace")


class NimProvider:
    """OpenAI-compatible chat client for the NIM brain; generate(prompt)->str.

    Mirrors GeminiProvider's seam. Stdlib transport (no new dep). Per-minute
    budget guard honors the free-tier invariant (raises before calling out).
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        max_requests_per_minute: int,
        post_fn: Callable[[str, dict[str, str], str], str] = _urllib_post,
        now: Callable[[], float] = _now_seconds,
    ) -> None:
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._api_key = api_key
        self._model = model
        self._max_rpm = max_requests_per_minute
        self._post = post_fn
        self._now = now
        self._window_start = now()
        self._count = 0

    def _spend_one(self) -> None:
        t = self._now()
        if t - self._window_start >= 60.0:
            self._window_start = t
            self._count = 0
        if self._count >= self._max_rpm:
            raise RpmExceeded(f"NIM RPM budget reached ({self._max_rpm})")
        self._count += 1

    def generate(self, prompt: str) -> str:
        self._spend_one()
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        body = json.dumps(
            {"model": self._model, "messages": [{"role": "user", "content": prompt}]}
        )
        raw = self._post(self._url, headers, body)
        try:
            data = json.loads(raw)
            return str(data["choices"][0]["message"]["content"])
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"could not parse NIM reply: {exc}") from exc
