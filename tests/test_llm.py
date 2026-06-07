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


def test_gemini_generate_structured_sets_config_and_returns_text():
    from cvflow.llm import GeminiProvider

    captured = {}

    class _Models:
        def generate_content(self, *, model, contents, config):
            captured["model"] = model
            captured["config"] = config

            class _R:
                text = '{"ok": true}'

            return _R()

    class _Client:
        models = _Models()

    prov = GeminiProvider(api_key="k", model="gemini-2.5-flash", max_requests_per_day=10,
                          client=_Client())

    class _Schema:  # stand-in pydantic-like schema object
        pass

    out = prov.generate_structured("prompt", schema=_Schema, seed=42, max_output_tokens=256)
    assert out == '{"ok": true}'
    cfg = captured["config"]
    assert cfg.temperature == 0
    assert cfg.seed == 42
    assert cfg.response_mime_type == "application/json"
    assert cfg.response_schema is _Schema
    assert cfg.thinking_config.thinking_budget == 0


def test_nim_generate_sends_seed_and_json_object():
    import json

    from cvflow.llm import NimProvider

    captured = {}

    def post_fn(url, headers, body):
        captured["body"] = json.loads(body)
        return json.dumps({"choices": [{"message": {"content": "[]"}}]})

    prov = NimProvider(base_url="https://x/v1", api_key="k", model="m",
                       max_requests_per_minute=40, post_fn=post_fn)
    prov.generate("p", temperature=0, seed=11, top_p=0.1, max_tokens=200, json_object=True)
    b = captured["body"]
    assert b["temperature"] == 0
    assert b["seed"] == 11
    assert b["top_p"] == 0.1
    assert b["max_tokens"] == 200
    assert b["response_format"] == {"type": "json_object"}


def test_nim_generate_default_no_extra_params():
    import json

    from cvflow.llm import NimProvider
    captured = {}

    def post_fn(url, headers, body):
        captured["body"] = json.loads(body)
        return json.dumps({"choices": [{"message": {"content": "ok"}}]})

    NimProvider(base_url="https://x/v1", api_key="k", model="m",
                max_requests_per_minute=40, post_fn=post_fn).generate("p")
    assert "seed" not in captured["body"]
    assert "response_format" not in captured["body"]


def test_nim_generate_structured_uses_json_object_and_returns_text():
    import json

    from cvflow.llm import NimProvider
    captured = {}

    def post_fn(url, headers, body):
        captured["body"] = json.loads(body)
        return json.dumps({"choices": [{"message": {"content": '{"job_id":"x"}'}}]})

    prov = NimProvider(base_url="https://api.mistral.ai/v1", api_key="k",
                       model="mistral-small-2506", max_requests_per_minute=300, post_fn=post_fn)
    out = prov.generate_structured("p", schema=object, seed=7, max_output_tokens=256)
    assert out == '{"job_id":"x"}'
    assert captured["body"]["response_format"] == {"type": "json_object"}
    assert captured["body"]["seed"] == 7
    assert captured["body"]["max_tokens"] == 256


def test_urllib_post_sends_user_agent_header():
    # Cerebras (Cloudflare) rejects the default urllib UA with 403/1010.
    import inspect

    from cvflow.llm import _urllib_post
    src = inspect.getsource(_urllib_post)
    assert "User-Agent" in src
