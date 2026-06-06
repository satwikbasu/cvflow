"""Tests for discovery: normalization, dedup, LLM ranking, top-N (Phase 4).

Fully mocked — no live JobSpy/network and no live LLM.
"""

import json

from cvflow.discovery import (
    DiscoveryService,
    JobPosting,
    LLMRanker,
    RankedJob,
    normalize_rows,
)
from cvflow.statemachine import Status
from cvflow.storage import ApplicationStore


def _row(id_: str, site: str = "linkedin", **over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": id_,
        "site": site,
        "title": f"Role {id_}",
        "company": f"Co {id_}",
        "location": "Remote",
        "description": f"desc {id_}",
        "job_url": f"https://x/{id_}",
        "date_posted": "2026-06-02",
    }
    base.update(over)
    return base


# --- normalization ---


def test_normalize_builds_stable_job_id() -> None:
    postings = normalize_rows([_row("1"), _row("2", site="indeed")])
    ids = [p.job_id for p in postings]
    assert ids == ["linkedin:1", "indeed:2"]
    assert postings[0].title == "Role 1"
    assert postings[0].url == "https://x/1"


def test_normalize_dedups_within_batch_first_wins() -> None:
    postings = normalize_rows([_row("1", title="A"), _row("1", title="B")])
    assert len(postings) == 1
    assert postings[0].title == "A"


def test_normalize_falls_back_to_url_hash_when_no_id() -> None:
    postings = normalize_rows([_row("", id="")])
    assert len(postings) == 1
    assert postings[0].job_id.startswith("linkedin:")  # url-hash suffix, still stable


# --- LLMRanker ---


class _FakeProvider:
    def __init__(self, payload: str) -> None:
        self.payload = payload
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.payload


def test_ranker_parses_json_orders_and_drops_unknown() -> None:
    postings = normalize_rows([_row("1"), _row("2"), _row("3")])
    payload = "```json\n" + json.dumps(
        [
            {"job_id": "linkedin:2", "summary": "s2", "rationale": "r2"},
            {"job_id": "linkedin:9", "summary": "ghost", "rationale": "x"},  # unknown -> dropped
            {"job_id": "linkedin:1", "summary": "s1", "rationale": "r1"},
        ]
    ) + "\n```"
    ranker = LLMRanker(_FakeProvider(payload), profile_context="PROFILE")
    ranked = ranker.rank(postings, top_n=5)
    assert [r.posting.job_id for r in ranked] == ["linkedin:2", "linkedin:1"]
    assert ranked[0].summary == "s2"
    assert ranked[0].rationale == "r2"
    assert "PROFILE" in ranker_prompt(ranker)


def ranker_prompt(ranker: LLMRanker) -> str:
    provider = ranker._provider  # type: ignore[attr-defined]
    return provider.prompts[0]


def test_ranker_truncates_to_top_n() -> None:
    postings = normalize_rows([_row(str(i)) for i in range(5)])
    payload = json.dumps(
        [{"job_id": f"linkedin:{i}", "summary": "", "rationale": ""} for i in range(5)]
    )
    ranker = LLMRanker(_FakeProvider(payload), profile_context="P")
    ranked = ranker.rank(postings, top_n=2)
    assert len(ranked) == 2


# --- DiscoveryService ---


class _RecordingRanker:
    """Returns candidates in given order as RankedJobs, truncated to top_n."""

    def __init__(self) -> None:
        self.seen_candidates: list[JobPosting] = []

    def rank(self, postings: list[JobPosting], top_n: int) -> list[RankedJob]:
        self.seen_candidates = list(postings)
        return [RankedJob(posting=p, summary="s", rationale="r") for p in postings[:top_n]]


def _service(store: ApplicationStore, ranker: object, rows_per_call: list[dict[str, object]]):
    calls = {"n": 0}
    sleeps: list[float] = []

    def search_fn(**kwargs: object) -> list[dict[str, object]]:
        calls["n"] += 1
        return rows_per_call

    svc = DiscoveryService(
        store=store,
        ranker=ranker,  # type: ignore[arg-type]
        search_fn=search_fn,
        search_terms=["go developer"],
        locations=["Remote", "India"],
        sites=["linkedin", "indeed"],
        results_wanted_per_site=10,
        hours_old=72,
        top_n=3,
        throttle_seconds=1.0,
        sleep=sleeps.append,
    )
    return svc, calls, sleeps


def test_discover_cross_day_dedup_excludes_known_jobs() -> None:
    store = ApplicationStore(":memory:")
    store.add("linkedin:1", "Co 1", "Role 1", "https://x/1")  # already seen previously
    ranker = _RecordingRanker()
    svc, _, _ = _service(store, ranker, [_row("1"), _row("2")])
    svc.discover()
    seen_ids = {p.job_id for p in ranker.seen_candidates}
    assert seen_ids == {"linkedin:2"}  # the known job was filtered out


def test_discover_returns_ranked_order_truncated_to_top_n() -> None:
    store = ApplicationStore(":memory:")
    ranker = _RecordingRanker()
    svc, _, _ = _service(store, ranker, [_row("1"), _row("2"), _row("3"), _row("4")])
    result = svc.discover()
    assert [r.posting.job_id for r in result] == ["linkedin:1", "linkedin:2", "linkedin:3"]


def test_discover_persists_presented_jobs_as_discovered() -> None:
    store = ApplicationStore(":memory:")
    ranker = _RecordingRanker()
    svc, _, _ = _service(store, ranker, [_row("1"), _row("2")])
    svc.discover()
    discovered = store.list_by_status(Status.DISCOVERED)
    assert {a.job_id for a in discovered} == {"linkedin:1", "linkedin:2"}


def test_discover_throttles_once_per_scrape_pair() -> None:
    store = ApplicationStore(":memory:")
    ranker = _RecordingRanker()
    # 1 search_term x 2 locations = 2 scrape calls
    svc, calls, sleeps = _service(store, ranker, [_row("1")])
    svc.discover()
    assert calls["n"] == 2
    assert sleeps == [1.0, 1.0]


def test_discover_survives_a_failing_search_batch() -> None:
    """One (term,location) batch raising must not abort the whole run (invariant 3)."""
    store = ApplicationStore(":memory:")
    ranker = _RecordingRanker()
    calls = {"n": 0}

    def flaky_search(**kwargs: object) -> list[dict[str, object]]:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("LinkedInException: Invalid country string: 'kosovo'")
        return [_row("2", site="indeed")]

    svc = DiscoveryService(
        store=store,
        ranker=ranker,  # type: ignore[arg-type]
        search_fn=flaky_search,
        search_terms=["backend"],
        locations=["Remote", "India"],  # 2 batches: first raises, second returns
        sites=["linkedin", "indeed"],
        results_wanted_per_site=10,
        hours_old=72,
        top_n=3,
        throttle_seconds=0.0,
        sleep=lambda s: None,
    )
    ranked = svc.discover()
    assert [rj.posting.job_id for rj in ranked] == ["indeed:2"]  # good batch survived


def test_discover_falls_back_to_unranked_when_ranking_fails() -> None:
    """If the ranking LLM errors, present unranked candidates (never lose the digest)."""
    store = ApplicationStore(":memory:")

    class _BrokenRanker:
        def rank(self, postings: list[JobPosting], top_n: int) -> list[RankedJob]:
            raise RuntimeError("NIM read timeout")

    svc, _, _ = _service(store, _BrokenRanker(), [_row("1"), _row("2")])
    ranked = svc.discover()
    assert {rj.posting.job_id for rj in ranked} == {"linkedin:1", "linkedin:2"}
    assert all(rj.rationale == "(ranking unavailable)" for rj in ranked)
    assert store.exists("linkedin:1")  # still persisted as discovered


def test_normalize_cleans_nan_and_carries_salary() -> None:
    rows = [{
        "id": "1", "site": "indeed", "title": "Backend", "company": float("nan"),
        "location": "Remote", "description": "d", "job_url": "https://x/1",
        "date_posted": "2026-06-02", "min_amount": 800000.0, "max_amount": 1200000.0,
        "currency": "INR",
    }]
    p = normalize_rows(rows)[0]
    assert p.company == ""
    assert p.min_amount == 800000.0
    assert p.max_amount == 1200000.0
    assert p.currency == "INR"


def test_normalize_missing_salary_is_none() -> None:
    p = normalize_rows([_row("1")])[0]
    assert p.min_amount is None
    assert p.currency is None


def test_prefilter_drops_excluded_titles_and_below_floor_salary() -> None:
    store = ApplicationStore(":memory:")
    ranker = _RecordingRanker()
    rows = [
        _row("1", title="Senior DevOps Engineer"),
        _row("2", title="DevOps Engineer", min_amount=400000.0,
             max_amount=500000.0, currency="INR"),
        _row("3", title="DevOps Engineer", min_amount=800000.0,
             max_amount=1200000.0, currency="INR"),
        _row("4", title="Platform Engineer"),
    ]
    svc = DiscoveryService(
        store=store, ranker=ranker, search_fn=lambda **k: rows,
        search_terms=["x"], locations=["Remote"], sites=["indeed"],
        results_wanted_per_site=10, hours_old=72, top_n=10,
        throttle_seconds=0.0, sleep=lambda s: None,
        exclude_title_keywords=["senior", "lead"], min_ctc_lpa=7,
    )
    svc.discover()
    kept = {p.job_id for p in ranker.seen_candidates}
    assert kept == {"linkedin:3", "linkedin:4"}


def test_prefilter_passes_job_type_to_search_fn() -> None:
    store = ApplicationStore(":memory:")
    captured = {}

    def search_fn(**kwargs):
        captured.update(kwargs)
        return []

    DiscoveryService(
        store=store, ranker=_RecordingRanker(), search_fn=search_fn,
        search_terms=["x"], locations=["Remote"], sites=["indeed"],
        results_wanted_per_site=10, hours_old=72, top_n=5,
        throttle_seconds=0.0, sleep=lambda s: None, job_type="fulltime",
    ).discover()
    assert captured.get("job_type") == "fulltime"


def test_ranker_prompt_includes_preferences_and_parses_score_concerns() -> None:
    captured = {}

    class _Prov:
        def generate(self, prompt: str) -> str:
            captured["prompt"] = prompt
            return (
                '[{"job_id": "linkedin:1", "fit_score": 88, '
                '"rationale": "infra fit", "concerns": ["salary not stated"]}]'
            )

    ranker = LLMRanker(_Prov(), "PROFILE", preferences="HARD: yoe<=1; product cos")
    out = ranker.rank(normalize_rows([_row("1")]), top_n=5)
    assert "product cos" in captured["prompt"]
    assert out[0].fit_score == 88
    assert out[0].concerns == ["salary not stated"]


def test_ranker_excluded_jobs_simply_absent() -> None:
    class _Prov:
        def generate(self, prompt: str) -> str:
            return '[{"job_id": "linkedin:2", "fit_score": 70, "rationale": "ok", "concerns": []}]'

    ranker = LLMRanker(_Prov(), "P", preferences="prefs")
    out = ranker.rank(normalize_rows([_row("1"), _row("2")]), top_n=5)
    assert [r.posting.job_id for r in out] == ["linkedin:2"]


def test_ranker_batches_and_sorts_by_fit_score() -> None:
    import re as _re
    calls = []

    class _Prov:
        def generate(self, prompt: str) -> str:
            calls.append(prompt)
            ids = _re.findall(r'"job_id": "(linkedin:\d+)"', prompt)
            return json.dumps(
                [{"job_id": i, "fit_score": int(i.split(":")[1]),
                  "rationale": "r", "concerns": []} for i in ids]
            )

    postings = normalize_rows([_row(str(n)) for n in range(25)])
    ranker = LLMRanker(_Prov(), "P", preferences="pref", batch_size=10)
    out = ranker.rank(postings, top_n=5)
    assert len(calls) == 3  # 25 -> 10 + 10 + 5
    scores = [r.fit_score for r in out]
    assert scores == sorted(scores, reverse=True)
    assert out[0].fit_score == 24


def test_ranker_partial_batch_failure_keeps_other_batches() -> None:
    import re as _re

    class _Prov:
        def __init__(self) -> None:
            self.n = 0

        def generate(self, prompt: str) -> str:
            self.n += 1
            if self.n == 1:
                raise RuntimeError("read timeout")
            ids = _re.findall(r'"job_id": "(linkedin:\d+)"', prompt)
            return json.dumps(
                [{"job_id": i, "fit_score": 50, "rationale": "r", "concerns": []} for i in ids]
            )

    postings = normalize_rows([_row(str(n)) for n in range(15)])
    ranker = LLMRanker(_Prov(), "P", batch_size=10)
    out = ranker.rank(postings, top_n=20)
    assert len(out) == 5  # batch 1 (10) failed; batch 2 (5) survived


def test_ranker_all_batches_fail_raises() -> None:
    import pytest

    class _Prov:
        def generate(self, prompt: str) -> str:
            raise RuntimeError("down")

    ranker = LLMRanker(_Prov(), "P", batch_size=10)
    with pytest.raises(RuntimeError):
        ranker.rank(normalize_rows([_row("1")]), top_n=5)


def test_discover_caps_candidates_by_recency_before_ranking() -> None:
    store = ApplicationStore(":memory:")
    ranker = _RecordingRanker()
    rows = [_row(str(n), date_posted=f"2026-06-{(n % 28) + 1:02d}") for n in range(60)]
    svc = DiscoveryService(
        store=store, ranker=ranker, search_fn=lambda **k: rows,
        search_terms=["x"], locations=["Remote"], sites=["indeed"],
        results_wanted_per_site=100, hours_old=72, top_n=5,
        throttle_seconds=0.0, sleep=lambda s: None,
        max_rank_candidates=10,
    )
    svc.discover()
    assert len(ranker.seen_candidates) == 10
    dates = [p.date_posted for p in ranker.seen_candidates]
    assert dates == sorted(dates, reverse=True)  # newest first
