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


def test_filtered_footer_renders_in_bucket_order():
    from cvflow.cron import _filtered_footer

    dropped = {"wrong stack": 59, "too senior": 71, "already seen": 4, "abroad": 0}
    footer = _filtered_footer(dropped)
    # shown in DROP_BUCKET_ORDER (too senior before wrong stack), zero-count bucket omitted
    assert footer == "🔍 Filtered today: 71 too senior · 59 wrong stack · 4 already seen"


def test_format_digest_never_includes_footer():
    # The drop counts are delivered separately now (drop summary / footer fallback), never in
    # the digest — so it isn't shown twice on a manual /discover.
    result = _result()
    result["_dropped"] = {"wrong stack": 59, "too senior": 71}
    assert "Filtered today" not in format_digest(result)


def test_run_job_discover_sends_digest():
    notes = []

    class _Disc:
        def discover(self, progress=lambda _m: None):
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
        def discover(self, progress=lambda _m: None):
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
        def discover(self, progress=lambda _m: None):
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


def test_format_digest_shows_location():
    result = {"M": [_bj("indeed:7", "M", 70, lpa=18.0)], "N": []}
    text = format_digest(result)
    assert "Remote" in text  # _bj posting.location == "Remote"


def test_format_digest_empty_section_shows_one_liner():
    result = {"M": [], "N": [_bj("indeed:2", "N", 80)]}
    text = format_digest(result)
    assert "With stated pay" in text
    assert "No stated-pay jobs today." in text
    assert "Pay not stated" in text


def test_run_job_discover_with_progress_streams_and_reports_drops(tmp_path) -> None:
    from cvflow.cron import run_job
    from cvflow.discovery import DropRecord
    notes: list[str] = []

    class _FakeProv:
        def generate(self, prompt: str, **kw: object) -> str:
            return "Filtered some senior roles."

    class _Disc:
        def discover(self, progress=lambda _m: None):
            progress("📡 Scraped 5 raw postings in 1s")
            r = _result()
            r["_drop_records"] = [DropRecord("wrong stack", "X @ Y", "missing react")]
            return r

    run_job("discover", store=ApplicationStore(":memory:"), discovery=_Disc(),
            otp=None, notify=notes.append, progress=notes.append, report_drops=True,
            summary_provider=_FakeProv(), log_dir=str(tmp_path))
    assert any("📡 Scraped" in n for n in notes)
    assert any("https://jobs/indeed:7" in n for n in notes)         # digest unchanged
    assert any("Filtered some senior roles." in n for n in notes)   # LLM summary
    assert any("Full breakdown:" in n for n in notes)               # log pointer
    assert not any(n.startswith("🚫 Dropped") for n in notes)       # raw chunks gone
    assert any(tmp_path.iterdir())                                   # log file written


def test_run_job_discover_plain_sends_neither_progress_nor_drops() -> None:
    from cvflow.cron import run_job
    from cvflow.discovery import DropRecord
    notes: list[str] = []

    class _Disc:
        def discover(self, progress=lambda _m: None):
            progress("📡 should-not-be-sent")
            r = _result()
            r["_drop_records"] = [DropRecord("wrong stack", "X @ Y", "missing react")]
            return r

    run_job("discover", store=ApplicationStore(":memory:"), discovery=_Disc(),
            otp=None, notify=notes.append)
    assert not any("should-not-be-sent" in n for n in notes)
    assert not any("wrong stack" in n for n in notes)
    assert any("https://jobs/indeed:7" in n for n in notes)   # digest still sent


def test_run_job_discover_bails_when_lock_held() -> None:
    from contextlib import contextmanager

    from cvflow.cron import run_job
    from cvflow.runlock import AlreadyRunning

    @contextmanager
    def _held_lock():
        raise AlreadyRunning("held")
        yield  # pragma: no cover

    notes: list[str] = []

    class _Disc:
        def discover(self, progress=lambda _m: None):
            raise AssertionError("discover must not run when lock is held")

    run_job("discover", store=ApplicationStore(":memory:"), discovery=_Disc(),
            otp=None, notify=notes.append, lock=_held_lock())
    assert any("already in progress" in n for n in notes)


def test_main_discover_progress_flag_wires_progress_and_drop_report() -> None:
    from cvflow.cron import main
    from cvflow.discovery import DropRecord
    notes: list[str] = []

    class _Disc:
        def discover(self, progress=lambda _m: None):
            progress("📡 Scraped 3 raw postings in 1s")
            r: dict = {"M": [], "N": []}
            r["_drop_records"] = [DropRecord("wrong stack", "X @ Y", "missing react")]
            r["_dropped"] = {"wrong stack": 1}
            return r

    # Injected services path: summary_provider=None → fallback to _filtered_footer
    main(["discover", "--progress"],
         services=(ApplicationStore(":memory:"), _Disc(), None, notes.append, None))
    assert any("📡 Scraped" in n for n in notes)
    # footer text (from _filtered_footer) should appear, NOT a raw "🚫 Dropped" chunk header
    assert any("wrong stack" in n for n in notes)
    assert not any(n.startswith("🚫 Dropped") for n in notes)


def test_run_job_discover_summary_provider_raises_sends_footer_fallback(tmp_path) -> None:
    """LLM failure → deterministic footer fallback + log pointer; never raw chunks, never silent."""
    from cvflow.cron import run_job
    from cvflow.discovery import DropRecord
    from cvflow.llm import RpmExceeded
    notes: list[str] = []

    class _FailProv:
        def generate(self, prompt: str, **kw: object) -> str:
            raise RpmExceeded("rate limited")

    class _Disc:
        def discover(self, progress=lambda _m: None):
            r = _result()
            r["_drop_records"] = [DropRecord("wrong stack", "X @ Y", "missing react")]
            r["_dropped"] = {"wrong stack": 1}
            return r

    run_job("discover", store=ApplicationStore(":memory:"), discovery=_Disc(),
            otp=None, notify=notes.append, report_drops=True,
            summary_provider=_FailProv(), log_dir=str(tmp_path))
    # digest sent
    assert any("https://jobs/indeed:7" in n for n in notes)
    # fallback footer (not raw chunks)
    assert not any(n.startswith("🚫 Dropped") for n in notes)
    assert any("wrong stack" in n for n in notes)       # footer mentions bucket
    # log file still written (retention before LLM call)
    assert any(tmp_path.iterdir())
    # log pointer appended
    assert any("Full breakdown:" in n for n in notes)


def test_run_job_discover_daily_no_drop_msg_but_log_written(tmp_path) -> None:
    """Daily run (report_drops=False): digest sent, no drop message, log file still written."""
    from cvflow.cron import run_job
    from cvflow.discovery import DropRecord
    notes: list[str] = []

    class _Disc:
        def discover(self, progress=lambda _m: None):
            r = _result()
            r["_drop_records"] = [DropRecord("wrong stack", "X @ Y", "missing react")]
            return r

    run_job("discover", store=ApplicationStore(":memory:"), discovery=_Disc(),
            otp=None, notify=notes.append, report_drops=False, log_dir=str(tmp_path))
    assert any("https://jobs/indeed:7" in n for n in notes)      # digest sent
    assert not any("wrong stack" in n for n in notes)             # no drop message
    assert not any("Full breakdown:" in n for n in notes)         # no pointer in chat
    assert any(tmp_path.iterdir())                                # log written to disk


def test_run_tailor_posts_pdf_and_diff(tmp_path):
    from contextlib import nullcontext

    from cvflow.cron import run_tailor

    class _Tools:
        def request_review(self, job_id):
            return {"job_id": job_id, "company": "Acme", "role": "Backend Engineer",
                    "status": "pending_review", "pdf_path": "/x/r.pdf",
                    "diff": "Section order: experience -> projects"}
    notices = []
    docs = []
    run_tailor(
        "x:1", tools=_Tools(), notify=notices.append,
        send_document=lambda path, caption: docs.append((path, caption)),
        lock=nullcontext(),
    )
    msg = "\n".join(notices)
    # the PDF goes straight to the chat as a document; its path is NOT dumped as text
    assert docs == [("/x/r.pdf", "✅ Tailored — Backend Engineer @ Acme")]
    assert "/x/r.pdf" not in msg
    # text shows role @ company + diff, no raw job_id, no /apply or /skip nag
    assert "Backend Engineer @ Acme" in msg
    assert "experience -> projects" in msg
    assert "/apply" not in msg and "/skip" not in msg
    assert "x:1" not in msg


def test_run_tailor_notifies_on_failure(tmp_path):
    from contextlib import nullcontext

    from cvflow.cron import run_tailor

    class _Tools:
        def request_review(self, job_id): raise RuntimeError("compile failed")
    notices = []
    with pytest.raises(RuntimeError):
        run_tailor("x:1", tools=_Tools(), notify=notices.append, lock=nullcontext())
    assert any("failed" in m.lower() for m in notices)


def test_run_job_discover_digest_text_identical_to_format_digest(tmp_path) -> None:
    """Digest text in the notification is byte-identical to format_digest(result)."""
    from cvflow.cron import format_digest, run_job
    from cvflow.discovery import DropRecord
    notes: list[str] = []

    class _Disc:
        def discover(self, progress=lambda _m: None):
            r = _result()
            r["_drop_records"] = [DropRecord("wrong stack", "X @ Y", "missing react")]
            return r

    run_job("discover", store=ApplicationStore(":memory:"), discovery=_Disc(),
            otp=None, notify=notes.append, report_drops=True, log_dir=str(tmp_path))
    expected = format_digest(_result())
    assert any(n == expected for n in notes)
