"""cron core — format_digest + run_job dispatch with fakes. No network/browser."""

import pytest

from cvflow.cron import format_digest, run_job
from cvflow.discovery import JobPosting
from cvflow.discovery.benchmark import BenchmarkedJob
from cvflow.statemachine import Status
from cvflow.storage import ApplicationStore


def _bj(jid, cohort="M", bench=70, lpa=None, concerns=None):
    p = JobPosting(
        job_id=jid, title="Backend Dev", company="Acme", location="Remote",
        description="d", url=f"https://jobs/{jid}", site="indeed", date_posted="2026-06-05",
    )
    return BenchmarkedJob(posting=p, benchmark=bench, fit_score=bench,
                          fit_reason="great fit", concerns=concerns or [], cohort=cohort,
                          ctc_lpa=lpa)


def _result():
    return {"M": [_bj("indeed:7", "M", 70, lpa=18.0)], "N": []}


def test_format_digest_includes_url_and_commands():
    text = format_digest(_result())
    assert "https://jobs/indeed:7" in text
    assert "/apply indeed:7" in text
    assert "/skip indeed:7" in text
    assert "Backend Dev" in text


def test_format_digest_empty():
    assert format_digest({"M": [], "N": []}) == "No new jobs today."


def test_run_job_discover_sends_digest():
    notes = []

    class _Disc:
        def discover(self):
            return _result()

    run_job("discover", store=ApplicationStore(":memory:"), discovery=_Disc(),
            otp=None, notify=notes.append)
    assert notes and "https://jobs/indeed:7" in notes[0]


def test_run_job_sweep_otp_calls_expire_overdue():
    called = {"n": 0}

    class _Otp:
        def expire_overdue(self):
            called["n"] += 1
            return []

    run_job("sweep-otp", store=None, discovery=None, otp=_Otp(), notify=lambda m: None)
    assert called["n"] == 1


def test_run_job_heartbeat_reports_status_counts():
    store = ApplicationStore(":memory:")
    store.add("indeed:1", "Acme", "Backend", "https://jobs/1")
    notes = []
    run_job("heartbeat", store=store, discovery=None, otp=None, notify=notes.append)
    assert notes and "alive" in notes[0]
    assert f"{Status.DISCOVERED.value}=1" in notes[0]


def test_run_job_unknown_raises():
    with pytest.raises(ValueError):
        run_job("bogus", store=None, discovery=None, otp=None, notify=lambda m: None)


def test_main_dispatches_with_injected_services():
    notes = []
    store = ApplicationStore(":memory:")
    services = (store, None, None, notes.append, None)

    from cvflow.cron import main
    main(["heartbeat"], services=services)
    assert notes and "alive" in notes[0]


def test_main_bad_args_exits():
    from cvflow.cron import main
    with pytest.raises(SystemExit):
        main([], services=(None, None, None, lambda m: None, None))


def test_main_notifies_on_job_failure():
    notes = []

    class _Disc:
        def discover(self):
            raise RuntimeError("boom")

    from cvflow.cron import main
    with pytest.raises(RuntimeError):
        main(["discover"], services=(None, _Disc(), None, notes.append, None))
    assert any("failed" in n for n in notes)


def test_format_digest_two_sections_and_continuous_numbering():
    def _bj2(jid, cohort, bench, lpa=None, concerns=None):
        p = JobPosting(job_id=jid, title="DevOps", company="Acme", location="Remote",
                       description="d", url=f"https://{jid}", site="indeed", date_posted="x")
        return BenchmarkedJob(posting=p, benchmark=bench, fit_score=bench,
                              fit_reason="infra fit", concerns=concerns or [], cohort=cohort,
                              ctc_lpa=lpa)

    result = {"M": [_bj2("indeed:1", "M", 74, lpa=23.0)],
              "N": [_bj2("indeed:2", "N", 80, concerns=["PAY_UNKNOWN"])]}
    text = format_digest(result)
    assert "With stated pay" in text and "Pay not stated" in text
    assert "1. " in text and "2. " in text         # continuous numbering across sections
    assert "23 LPA" in text and "74" in text
    assert "PAY_UNKNOWN" in text
    assert "/apply indeed:1" in text and "/apply indeed:2" in text


def test_run_job_discover_sets_slots_in_combined_order():
    store = ApplicationStore(":memory:")

    def _bj2(jid, cohort):
        p = JobPosting(job_id=jid, title="T", company="C", location="L", description="d",
                       url=f"https://{jid}", site="indeed", date_posted="x")
        return BenchmarkedJob(posting=p, benchmark=70, fit_score=70, fit_reason="r",
                              concerns=[], cohort=cohort)

    class _Disc:
        def discover(self):
            return {"M": [_bj2("indeed:1", "M")], "N": [_bj2("indeed:2", "N")]}

    run_job("discover", store=store, discovery=_Disc(), otp=None, notify=lambda m: None)
    assert store.digest_slots() == ["indeed:1", "indeed:2"]  # M first, then N


def test_run_job_learn_appends_dated_log(tmp_path):
    from cvflow.cron import run_job
    from cvflow.storage import ApplicationStore
    store = ApplicationStore(":memory:")
    for i in range(3):
        store.add(f"indeed:{i}", "Co", "Role", "https://x")
        store.add_decision(job_id=f"indeed:{i}", decision="skip", role_family="frontend",
                           company="Svc", company_type="service", fit_score=40)
    notes = []

    class _Prov:
        def generate(self, prompt, **kw):
            return "Consider down-ranking service companies."

    run_job("learn", store=store, discovery=None, otp=None, notify=notes.append,
            learn_provider=_Prov(), min_decisions=1, learning_dir=str(tmp_path))
    assert any("service companies" in n for n in notes)
    logs = list(tmp_path.glob("*.md"))
    assert len(logs) == 1
    body = logs[0].read_text()
    assert "service companies" in body and "frontend" in body  # suggestion + stats snapshot
