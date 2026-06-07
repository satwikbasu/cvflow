"""Hybrid benchmark math (Phase 14C). Pure functions; no network."""

from cvflow.discovery import JobPosting
from cvflow.discovery.benchmark import FitResult, benchmark_cohort, comp_score


def test_comp_score_anchors():
    assert comp_score(7.0, 7, 40) == 0.0
    assert comp_score(40.0, 7, 40) == 1.0
    assert abs(comp_score(23.0, 7, 40) - 0.4848) < 0.01
    assert comp_score(3.0, 7, 40) == 0.0  # below floor clamps


def _job(jid, max_amount=None, currency=None):
    return JobPosting(job_id=jid, title="T", company="C", location="L", description="d",
                      url=f"https://{jid}", site="indeed", date_posted="2026-06-05",
                      max_amount=max_amount, currency=currency)


def test_benchmark_cohort_M_blends_fit_and_comp_sorted():
    jobs = {"a": _job("a", 2_300_000, "INR"), "b": _job("b", 700_000, "INR")}
    fits = {"a": FitResult(70, "ok", []), "b": FitResult(85, "great", [])}
    out = benchmark_cohort(jobs, fits, cohort="M", fit_weight=0.70, comp_weight=0.30,
                           min_lpa=7, top_lpa=40)
    # a: 0.7*0.70 + 0.3*0.4848 = 0.4900+0.1454=0.6354 -> 64 ; b: 0.7*0.85+0.3*0 = 60
    assert [j.job_id for j in out] == ["a", "b"]
    assert out[0].benchmark == 64 and out[1].benchmark == 60
    assert out[0].cohort == "M"


def test_benchmark_cohort_N_is_fit_only():
    jobs = {"a": _job("a"), "b": _job("b")}
    fits = {"a": FitResult(80, "x", ["PAY_UNKNOWN"]), "b": FitResult(45, "y", [])}
    out = benchmark_cohort(jobs, fits, cohort="N", fit_weight=0.70, comp_weight=0.30,
                           min_lpa=7, top_lpa=40)
    assert [j.job_id for j in out] == ["a", "b"]
    assert out[0].benchmark == 80 and out[1].benchmark == 45
    assert "PAY_UNKNOWN" in out[0].concerns
