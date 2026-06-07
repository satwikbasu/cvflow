"""Tests for discovery: normalization, dedup, two-stage distill+benchmark (Phase 14).

Fully mocked — no live JobSpy/network and no live LLM.
"""

import json
import re

from cvflow.discovery import (
    DiscoveryService,
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


def test_normalize_carries_experience_range() -> None:
    p = normalize_rows([_row("1", experience_range="2-4 Yrs")])[0]
    assert p.experience_range == "2-4 Yrs"


def test_normalize_experience_range_absent_is_none() -> None:
    assert normalize_rows([_row("1")])[0].experience_range is None


# --- two-stage discovery (distill -> benchmark) ---


class _StubGemini:
    """Stub Mistral provider: distills (generate_structured) AND fit-scores (generate)."""

    def __init__(self) -> None:
        self.calls = 0

    def generate_structured(self, prompt, *, schema, seed, max_output_tokens):
        self.calls += 1
        m = re.search(r"job_id: (\S+)", prompt)
        jid = m.group(1) if m else "unknown"
        return json.dumps({
            "job_id": jid, "role_family": "devops", "seniority_signal": "junior",
            "min_years_required": 1, "max_years_required": 2, "work_mode": "remote",
            "location_text": "Remote", "country": "india", "stated_salary": None,
            "tech_stack": ["k8s"], "night_shift_only": False, "app_maintenance_focus": False,
            "company_type": "product", "red_flags": [], "applicant_instructions": None,
            "one_line": "infra"})

    def generate(self, prompt, **kw):
        ids = sorted(set(re.findall(r'"job_id": "([^"]+)"', prompt)))
        return json.dumps({"results": [{"job_id": i, "fit_score": 80, "fit_reason": "ok",
                                        "concern_codes": []} for i in ids]})


class _StubNim:
    """Unused brain stub (fit now runs on the distillation provider)."""

    def generate(self, prompt, **kw):
        return json.dumps({"results": []})


def _two_stage_service(store, search_fn, **over):
    kwargs = dict(
        store=store, search_fn=search_fn,
        search_terms=["go developer"], locations=["Remote", "India"],
        sites=["linkedin", "indeed"], results_wanted_per_site=10, hours_old=72,
        top_n=5, throttle_seconds=1.0, sleep=lambda s: None,
        exclude_title_keywords=["senior", "lead"], min_ctc_lpa=7,
        distiller=_StubGemini(), brain=_StubNim(), fingerprint="FP",
        prefer_roles={"devops": 1.0}, exclude_when=[], fit_weight=0.70,
        comp_weight=0.30, top_ctc_lpa=40, max_distill_per_cohort=60, top_n_per_cohort=5,
    )
    kwargs.update(over)
    return DiscoveryService(**kwargs)


def _all_ids(result):
    return {bj.posting.job_id for bj in result["M"] + result["N"]}


def test_discover_cross_day_dedup_excludes_known_jobs() -> None:
    store = ApplicationStore(":memory:")
    store.add("linkedin:1", "Co 1", "Role 1", "https://x/1")  # already seen previously
    svc = _two_stage_service(store, lambda **k: [_row("1"), _row("2")])
    result = svc.discover()
    assert _all_ids(result) == {"linkedin:2"}  # the known job was filtered out


def test_discover_persists_presented_jobs_as_discovered() -> None:
    store = ApplicationStore(":memory:")
    svc = _two_stage_service(store, lambda **k: [_row("1"), _row("2")])
    svc.discover()
    discovered = store.list_by_status(Status.DISCOVERED)
    assert {a.job_id for a in discovered} == {"linkedin:1", "linkedin:2"}


def test_discover_throttles_once_per_scrape_pair() -> None:
    store = ApplicationStore(":memory:")
    calls = {"n": 0}
    sleeps: list[float] = []

    def search_fn(**kwargs):
        calls["n"] += 1
        return [_row("1")]

    svc = _two_stage_service(store, search_fn, throttle_seconds=1.0, sleep=sleeps.append)
    svc.discover()
    assert calls["n"] == 2  # 1 term x 2 locations
    assert sleeps == [1.0, 1.0]


def test_discover_survives_a_failing_search_batch() -> None:
    """One (term,location) batch raising must not abort the whole run (invariant 3)."""
    store = ApplicationStore(":memory:")
    calls = {"n": 0}

    def flaky_search(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("LinkedInException: Invalid country string: 'kosovo'")
        return [_row("2", site="indeed")]

    svc = _two_stage_service(store, flaky_search, throttle_seconds=0.0, sleep=lambda s: None)
    result = svc.discover()
    assert _all_ids(result) == {"indeed:2"}  # good batch survived


def test_prefilter_drops_excluded_titles_and_below_floor_salary() -> None:
    store = ApplicationStore(":memory:")
    rows = [
        _row("1", title="Senior DevOps Engineer"),
        _row("2", title="DevOps Engineer", min_amount=400000.0,
             max_amount=500000.0, currency="INR"),
        _row("3", title="DevOps Engineer", min_amount=800000.0,
             max_amount=1200000.0, currency="INR"),
        _row("4", title="Platform Engineer"),
    ]
    svc = _two_stage_service(store, lambda **k: rows, throttle_seconds=0.0,
                             sleep=lambda s: None, locations=["Remote"])
    result = svc.discover()
    assert _all_ids(result) == {"linkedin:3", "linkedin:4"}


def test_discovery_passes_country_and_fetch_description_to_search_fn() -> None:
    store = ApplicationStore(":memory:")
    captured = {}

    def search_fn(**kwargs):
        captured.update(kwargs)
        return []

    svc = DiscoveryService(
        store=store, search_fn=search_fn,
        search_terms=["x"], locations=["Remote"], sites=["indeed"],
        results_wanted_per_site=10, hours_old=72, top_n=5,
        throttle_seconds=0.0, sleep=lambda s: None,
        country_indeed="india", linkedin_fetch_description=True,
    )
    svc.discover()
    assert captured.get("country_indeed") == "india"
    assert captured.get("linkedin_fetch_description") is True
    assert "job_type" not in captured  # not sent (Indeed hours_old conflict)


def test_discover_two_stage_partitions_and_benchmarks():
    store = ApplicationStore(":memory:")
    rows = [
        _row("1", title="DevOps Engineer", min_amount=2_000_000, max_amount=2_300_000,
             currency="INR", description="CI/CD pipelines, 2 years"),       # M
        _row("2", title="Platform Engineer", description="K8s platform, fresher"),  # N
    ]

    def _distill(prompt, *, schema, seed, max_output_tokens):
        jid = "linkedin:1" if "linkedin:1" in prompt else "linkedin:2"
        return json.dumps({
            "job_id": jid, "role_family": "devops", "seniority_signal": "junior",
            "min_years_required": 2, "max_years_required": 3, "work_mode": "remote",
            "location_text": "Remote", "country": "india", "stated_salary": None,
            "tech_stack": ["k8s"], "night_shift_only": False, "app_maintenance_focus": False,
            "company_type": "product", "red_flags": [], "applicant_instructions": None,
            "one_line": "infra"})

    class _Gem:
        def generate_structured(self, prompt, *, schema, seed, max_output_tokens):
            return _distill(prompt, schema=schema, seed=seed, max_output_tokens=max_output_tokens)

        def generate(self, prompt, **kw):
            ids = [x for x in ("linkedin:1", "linkedin:2") if x in prompt]
            return json.dumps({"results": [{"job_id": i, "fit_score": 80, "fit_reason": "ok",
                                            "concern_codes": []} for i in ids]})

    svc = DiscoveryService(
        store=store, search_fn=lambda **k: rows,
        search_terms=["x"], locations=["Remote"], sites=["linkedin"],
        results_wanted_per_site=10, hours_old=72, top_n=5,
        throttle_seconds=0.0, sleep=lambda s: None,
        exclude_title_keywords=["senior"], min_ctc_lpa=7,
        distiller=_Gem(), brain=None, fingerprint="FP", prefer_roles={"devops": 1.0},
        exclude_when=[], fit_weight=0.70, comp_weight=0.30, top_ctc_lpa=40,
        max_distill_per_cohort=60, top_n_per_cohort=5,
    )
    result = svc.discover()
    m_ids = [j.posting.job_id for j in result["M"]]
    n_ids = [j.posting.job_id for j in result["N"]]
    assert m_ids == ["linkedin:1"]      # has INR salary
    assert n_ids == ["linkedin:2"]      # no salary
    assert result["M"][0].cohort == "M" and result["N"][0].cohort == "N"
    assert store.exists("linkedin:1") and store.exists("linkedin:2")  # persisted as discovered
    assert store.get_crux("linkedin:1") is not None                   # crux cached


def test_discover_drops_jobs_via_exclude_when():
    store = ApplicationStore(":memory:")
    rows = [_row("1", title="DevOps Engineer", description="night shift only role")]

    class _Gem:
        def generate_structured(self, prompt, *, schema, seed, max_output_tokens):
            return json.dumps({
                "job_id": "linkedin:1", "role_family": "devops", "seniority_signal": "junior",
                "min_years_required": 1, "max_years_required": 2, "work_mode": "onsite",
                "location_text": "X", "country": "india", "stated_salary": None,
                "tech_stack": [], "night_shift_only": True, "app_maintenance_focus": False,
                "company_type": "product", "red_flags": [], "applicant_instructions": None,
                "one_line": "x"})

    class _Nim:
        def generate(self, prompt, **kw):
            return "[]"

    svc = DiscoveryService(
        store=store, search_fn=lambda **k: rows,
        search_terms=["x"], locations=["Remote"], sites=["linkedin"],
        results_wanted_per_site=10, hours_old=72, top_n=5, throttle_seconds=0.0,
        sleep=lambda s: None, distiller=_Gem(), brain=_Nim(), fingerprint="FP",
        prefer_roles={}, exclude_when=[{"field": "night_shift_only", "equals": True}],
        fit_weight=0.70, comp_weight=0.30, top_ctc_lpa=40, max_distill_per_cohort=60,
        top_n_per_cohort=5,
    )
    result = svc.discover()
    assert result["M"] == [] and result["N"] == []  # dropped by exclude_when


def test_parse_min_years():
    from cvflow.discovery import _parse_min_years
    assert _parse_min_years("2-4 Yrs") == 2
    assert _parse_min_years("5+ years") == 5
    assert _parse_min_years("0-1 Yrs") == 0
    assert _parse_min_years("Fresher") == 0
    assert _parse_min_years("") is None
    assert _parse_min_years(None) is None
    assert _parse_min_years("competitive") is None  # unparseable -> keep (never fabricate)


def test_prefilter_drops_naukri_experience_over_ceiling():
    store = ApplicationStore(":memory:")
    rows = [
        _row("1", site="naukri", title="DevOps Engineer", experience_range="6-9 Yrs"),
        _row("2", site="naukri", title="DevOps Engineer", experience_range="0-2 Yrs"),
        _row("3", site="naukri", title="DevOps Engineer", experience_range="competitive"),
    ]
    svc = _two_stage_service(store, lambda **k: rows, throttle_seconds=0.0,
                             sleep=lambda s: None, locations=["Remote"], yoe_ceiling=3)
    result = svc.discover()
    # 6-9 dropped (min 6 > 3); 0-2 kept; unparseable kept (never drop on absent fact)
    assert _all_ids(result) == {"naukri:2", "naukri:3"}


def test_prefilter_no_ceiling_keeps_high_experience():
    store = ApplicationStore(":memory:")
    rows = [_row("1", site="naukri", title="DevOps Engineer", experience_range="8-10 Yrs")]
    svc = _two_stage_service(store, lambda **k: rows, throttle_seconds=0.0,
                             sleep=lambda s: None, locations=["Remote"])  # no yoe_ceiling
    result = svc.discover()
    assert _all_ids(result) == {"naukri:1"}


def test_discover_logs_stage_timings(caplog):
    import logging
    store = ApplicationStore(":memory:")
    rows = [_row("1", title="DevOps Engineer", min_amount=2_000_000,
                 max_amount=2_300_000, currency="INR")]
    svc = _two_stage_service(store, lambda **k: rows, throttle_seconds=0.0,
                             sleep=lambda s: None, locations=["Remote"])
    with caplog.at_level(logging.INFO, logger="cvflow.discovery"):
        svc.discover()
    msgs = " ".join(r.message for r in caplog.records)
    assert "stage scrape" in msgs
    assert "stage prefilter" in msgs
    assert "cohort M" in msgs
    assert "discover total" in msgs


def test_prefilter_drops_internship_by_job_type():
    store = ApplicationStore(":memory:")
    rows = [
        _row("1", title="DevOps Engineer", job_type="internship"),
        _row("2", title="DevOps Engineer", job_type="fulltime"),
        _row("3", title="DevOps Engineer"),  # no job_type -> kept (never drop on absent)
    ]
    svc = _two_stage_service(store, lambda **k: rows, throttle_seconds=0.0,
                             sleep=lambda s: None, locations=["Remote"])
    result = svc.discover()
    assert _all_ids(result) == {"linkedin:2", "linkedin:3"}


def test_normalize_carries_job_type():
    assert normalize_rows([_row("1", job_type="internship")])[0].job_type == "internship"
    assert normalize_rows([_row("1")])[0].job_type is None


def test_discover_partitions_M_from_crux_salary_not_structured():
    # JobSpy structured salary is empty, but the JD (crux) states INR pay -> must land in M.
    store = ApplicationStore(":memory:")
    rows = [
        _row("1", title="DevOps Engineer", description="pays 18 LPA"),     # crux: 18 LPA/yr -> M
        _row("2", title="DevOps Engineer", description="₹50k/month"),      # crux: 6 LPA -> below floor, drop
        _row("3", title="DevOps Engineer", description="no pay listed"),   # no salary -> N
    ]

    def _crux_json(jid, salary):
        return json.dumps({
            "job_id": jid, "role_family": "devops", "seniority_signal": "junior",
            "min_years_required": 1, "max_years_required": 2, "work_mode": "remote",
            "location_text": "Remote", "country": "india", "stated_salary": salary,
            "tech_stack": ["k8s"], "night_shift_only": False, "app_maintenance_focus": False,
            "company_type": "product", "red_flags": [], "applicant_instructions": None,
            "one_line": "infra"})

    class _Gem:
        def generate_structured(self, prompt, *, schema, seed, max_output_tokens):
            jid = re.search(r"job_id: (\S+)", prompt).group(1)
            sal = {
                "linkedin:1": {"min_amount": 1500000, "max_amount": 1800000, "currency": "INR",
                               "period": "year"},
                "linkedin:2": {"min_amount": 50000, "max_amount": 50000, "currency": "INR",
                               "period": "month"},
                "linkedin:3": None,
            }[jid]
            return _crux_json(jid, sal)

        def generate(self, prompt, **kw):
            ids = sorted(set(re.findall(r'"job_id": "([^"]+)"', prompt)))
            return json.dumps({"results": [{"job_id": i, "fit_score": 80, "fit_reason": "ok",
                                            "concern_codes": []} for i in ids]})

    svc = _two_stage_service(store, lambda **k: rows, throttle_seconds=0.0,
                             sleep=lambda s: None, locations=["Remote"], min_ctc_lpa=7,
                             distiller=_Gem())
    result = svc.discover()
    assert [j.posting.job_id for j in result["M"]] == ["linkedin:1"]   # 18 LPA -> M
    assert [j.posting.job_id for j in result["N"]] == ["linkedin:3"]   # no pay -> N
    assert result["M"][0].ctc_lpa == 18.0                              # crux salary used for comp
    # linkedin:2 (₹50k/mo = 6 LPA) dropped by the salary floor
    assert "linkedin:2" not in {j.posting.job_id for j in result["M"] + result["N"]}


def test_reconsider_discovered_reshows_discovered_but_not_applied():
    from cvflow.statemachine import Status
    store = ApplicationStore(":memory:")
    store.add("linkedin:1", "Co", "Role", "https://x/1")  # stays 'discovered'
    store.add("linkedin:2", "Co", "Role", "https://x/2")
    store.set_status("linkedin:2", Status.PENDING_REVIEW)
    store.approve("linkedin:2")  # advanced past discovered -> still excluded

    rows = [_row("1"), _row("2"), _row("3")]
    svc = _two_stage_service(store, lambda **k: rows, throttle_seconds=0.0,
                             sleep=lambda s: None, locations=["Remote"],
                             reconsider_discovered=True)
    result = svc.discover()
    ids = _all_ids(result)
    assert "linkedin:1" in ids   # re-shown (was only 'discovered')
    assert "linkedin:2" not in ids  # acted on -> still deduped
    assert "linkedin:3" in ids   # brand new


def test_default_dedup_excludes_all_seen():
    store = ApplicationStore(":memory:")
    store.add("linkedin:1", "Co", "Role", "https://x/1")  # 'discovered'
    svc = _two_stage_service(store, lambda **k: [_row("1"), _row("2")], throttle_seconds=0.0,
                             sleep=lambda s: None, locations=["Remote"])  # default: not reconsider
    result = svc.discover()
    assert _all_ids(result) == {"linkedin:2"}
