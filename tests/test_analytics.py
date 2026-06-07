"""Analytics rollups + decision recording (Phase 14D). No network."""

from cvflow.analytics import record_decision, summarize
from cvflow.discovery import JobPosting
from cvflow.discovery.benchmark import BenchmarkedJob
from cvflow.statemachine import Status
from cvflow.storage import ApplicationStore


def _bj(jid, role, ctype, bench, lpa=None, cohort="N"):
    p = JobPosting(jid, title="T", company=f"Co-{jid}", location="L", description="d",
                   url=f"https://{jid}", site="indeed", date_posted="x")
    return BenchmarkedJob(posting=p, benchmark=bench, fit_score=bench, fit_reason="r",
                          concerns=[], cohort=cohort, ctc_lpa=lpa)


def test_record_decision_pulls_context_from_benchmarked_job():
    store = ApplicationStore(":memory:")
    bj = _bj("indeed:1", "devops", "product", 80, lpa=18.0, cohort="M")
    record_decision(store, "apply", bj, role_family="devops", company_type="product")
    row = store.recent_decisions(1)[0]
    assert row["decision"] == "apply" and row["role_family"] == "devops"
    assert row["cohort"] == "M" and row["ctc_lpa"] == 18.0


def test_summarize_apply_rate_by_role():
    store = ApplicationStore(":memory:")
    for jid, _st in [("a", Status.APPLIED), ("b", Status.SKIPPED)]:
        store.add(f"indeed:{jid}", "Co", "Role", "https://x")
    record_decision(store, "apply", _bj("indeed:a", "devops", "product", 80),
                    role_family="devops", company_type="product")
    record_decision(store, "skip", _bj("indeed:b", "frontend", "service", 40),
                    role_family="frontend", company_type="service")
    summary = summarize(store)
    assert summary["totals"]["apply"] == 1 and summary["totals"]["skip"] == 1
    assert summary["apply_rate_by_role"]["devops"] == 1.0
    assert summary["apply_rate_by_role"]["frontend"] == 0.0
