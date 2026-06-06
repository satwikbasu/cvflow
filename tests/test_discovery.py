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
