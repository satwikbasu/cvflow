"""Tests for the Gemini tailoring client (Phase 2). No live key / network."""

from datetime import date

import pytest

from cvflow.llm import GeminiProvider, RpdExceeded


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeModels:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def generate_content(self, *, model: str, contents: str) -> _FakeResponse:
        self.calls.append((model, contents))
        return _FakeResponse(f"reply-to:{contents}")


class _FakeClient:
    def __init__(self) -> None:
        self.models = _FakeModels()


def _provider(rpd: int = 10, today: date | None = None) -> tuple[GeminiProvider, _FakeClient]:
    client = _FakeClient()
    clock_day = today or date(2026, 6, 3)
    holder = {"d": clock_day}
    p = GeminiProvider(
        api_key="x",
        model="gemini-2.5-flash",
        max_requests_per_day=rpd,
        client=client,
        now=lambda: holder["d"],
    )
    # expose the mutable day so tests can advance it
    p._test_holder = holder  # type: ignore[attr-defined]
    return p, client


def test_generate_calls_client_and_returns_text() -> None:
    p, client = _provider()
    out = p.generate("hello")
    assert out == "reply-to:hello"
    assert client.models.calls == [("gemini-2.5-flash", "hello")]


def test_rpd_accounting_blocks_over_budget() -> None:
    p, client = _provider(rpd=2)
    p.generate("a")
    p.generate("b")
    with pytest.raises(RpdExceeded):
        p.generate("c")
    assert len(client.models.calls) == 2  # the over-budget call never hit the client


def test_cache_hit_does_not_consume_rpd() -> None:
    p, client = _provider(rpd=1)
    first = p.generate("same")
    second = p.generate("same")  # served from cache, no RPD spent
    assert first == second
    assert len(client.models.calls) == 1


def test_day_rollover_resets_counter() -> None:
    p, client = _provider(rpd=1)
    p.generate("a")
    with pytest.raises(RpdExceeded):
        p.generate("b")
    p._test_holder["d"] = date(2026, 6, 4)  # type: ignore[attr-defined]
    assert p.generate("b") == "reply-to:b"
    assert len(client.models.calls) == 2


def test_zero_budget_raises_before_touching_client() -> None:
    p, client = _provider(rpd=0)
    with pytest.raises(RpdExceeded):
        p.generate("a")
    assert client.models.calls == []
