"""cron core — format_digest + run_job dispatch with fakes. No network/browser."""

import pytest

from cvflow.cron import format_digest, run_job
from cvflow.discovery import JobPosting, RankedJob
from cvflow.statemachine import Status
from cvflow.storage import ApplicationStore


def _ranked():
    p = JobPosting(
        job_id="indeed:7", title="Backend Dev", company="Acme", location="Remote",
        description="d", url="https://jobs/7", site="indeed", date_posted="2026-06-05",
    )
    return [RankedJob(posting=p, summary="s", rationale="great fit")]


def test_format_digest_includes_url_and_commands():
    text = format_digest(_ranked())
    assert "https://jobs/7" in text
    assert "/apply indeed:7" in text
    assert "/skip indeed:7" in text
    assert "Backend Dev" in text


def test_format_digest_empty():
    assert format_digest([]) == "No new jobs today."


def test_run_job_discover_sends_digest():
    notes = []

    class _Disc:
        def discover(self):
            return _ranked()

    run_job("discover", store=None, discovery=_Disc(), otp=None, notify=notes.append)
    assert notes and "https://jobs/7" in notes[0]


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
    services = (store, None, None, notes.append)

    from cvflow.cron import main
    main(["heartbeat"], services=services)
    assert notes and "alive" in notes[0]


def test_main_bad_args_exits():
    from cvflow.cron import main
    with pytest.raises(SystemExit):
        main([], services=(None, None, None, lambda m: None))
