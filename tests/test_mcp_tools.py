"""Tests for the pure-Python CvflowTools MCP dispatcher."""

from __future__ import annotations

import pytest

from cvflow.mcp.tools import CvflowTools
from cvflow.statemachine import Status, SubmissionBlocked


def _tools(tmp_path=None):
    from cvflow.storage import ApplicationStore

    return CvflowTools(
        store=ApplicationStore(":memory:"),
        knowledge=None,
        discovery=None,
        analyzer=None,
        tailor=None,
    )


def test_ping_returns_ok(tmp_path):
    tools = _tools(tmp_path)
    result = tools.ping()
    assert result["status"] == "ok"
    assert result["service"] == "cvflow"


def test_list_and_get_applications_dispatch_to_store(tmp_path):
    from cvflow.storage import ApplicationStore

    store = ApplicationStore(":memory:")
    store.add("indeed:1", "Acme", "Backend Engineer", "https://x/1")
    store.add("indeed:2", "Globex", "Platform Engineer", "https://x/2")
    tools = CvflowTools(
        store=store, knowledge=None, discovery=None, analyzer=None, tailor=None
    )

    listed = tools.list_applications(status="discovered")
    assert listed["total"] == 2
    assert listed["count"] == 2
    assert listed["offset"] == 0
    assert listed["next_offset"] is None
    assert {a["job_id"] for a in listed["applications"]} == {"indeed:1", "indeed:2"}
    assert listed["applications"][0]["company"] in {"Acme", "Globex"}
    # minimal fields only — status is the query, not repeated per row
    assert set(listed["applications"][0]) == {"job_id", "company", "role"}

    one = tools.get_application("indeed:1")
    assert one["role"] == "Backend Engineer"
    assert one["status"] == Status.DISCOVERED.value
    assert one["jd_url"] == "https://x/1"
    # compact view drops always-null proof internals so the URL stays prominent
    assert "proof_url" not in one
    assert "proof_screenshot_path" not in one
    assert "otp_deadline" not in one


def test_list_applications_paginates_default_10(tmp_path):
    from cvflow.storage import ApplicationStore

    store = ApplicationStore(":memory:")
    for i in range(23):
        store.add(f"indeed:{i}", f"Co{i}", "Engineer", f"https://x/{i}")
    tools = CvflowTools(
        store=store, knowledge=None, discovery=None, analyzer=None, tailor=None
    )

    page1 = tools.list_applications(status="discovered")
    assert page1["total"] == 23
    assert page1["count"] == 10
    assert page1["offset"] == 0
    assert page1["next_offset"] == 10
    assert len(page1["applications"]) == 10
    assert set(page1["applications"][0]) == {"job_id", "company", "role"}

    page2 = tools.list_applications(status="discovered", offset=page1["next_offset"])
    assert page2["offset"] == 10
    assert page2["count"] == 10
    assert page2["next_offset"] == 20

    page3 = tools.list_applications(status="discovered", offset=20)
    assert page3["count"] == 3
    assert page3["next_offset"] is None


def test_get_application_missing_returns_none(tmp_path):
    from cvflow.storage import ApplicationStore

    tools = CvflowTools(
        store=ApplicationStore(":memory:"),
        knowledge=None, discovery=None, analyzer=None, tailor=None,
    )
    assert tools.get_application("nope:0") is None


def _store_with_job():
    from cvflow.storage import ApplicationStore

    store = ApplicationStore(":memory:")
    store.add("indeed:9", "Acme", "Backend Engineer", "https://x/9")
    return store


def test_request_review_moves_to_pending_review(tmp_path):
    from cvflow.analysis import JDAnalysis

    class _FakeTailor:
        def plan(self, jd, *, feedback=None):
            return "PLAN"

        def diff(self, plan):
            return "diff"

        def compile_tailored(self, plan, outdir):
            from pathlib import Path

            p = Path(outdir) / "_tailored.pdf"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"%PDF-1.5")
            return p

    store = _store_with_job()
    store.save_analysis(
        "indeed:9",
        JDAnalysis(
            required_skills=["python"], preferred_quals=[], seniority="mid",
            tone="neutral", applicant_instructions=[],
        ),
    )
    tools = CvflowTools(
        store=store, knowledge=None, discovery=None, analyzer=None,
        tailor=_FakeTailor(), output_dir=str(tmp_path),
    )
    tools.request_review("indeed:9")
    assert store.get("indeed:9").status == Status.PENDING_REVIEW


def test_submit_blocks_when_not_approved():
    store = _store_with_job()
    tools = CvflowTools(
        store=store, knowledge=None, discovery=None, analyzer=None, tailor=None
    )
    store.set_status("indeed:9", Status.PENDING_REVIEW)
    with pytest.raises(SubmissionBlocked):
        tools.submit("indeed:9")


def test_submit_blocks_for_freshly_discovered():
    store = _store_with_job()
    tools = CvflowTools(
        store=store, knowledge=None, discovery=None, analyzer=None, tailor=None
    )
    with pytest.raises(SubmissionBlocked):
        tools.submit("indeed:9")


def test_submit_succeeds_only_after_out_of_band_approval():
    store = _store_with_job()
    tools = CvflowTools(
        store=store, knowledge=None, discovery=None, analyzer=None, tailor=None
    )
    store.set_status("indeed:9", Status.PENDING_REVIEW)
    store.approve("indeed:9")
    result = tools.submit("indeed:9")
    assert result["job_id"] == "indeed:9"
    assert result["status"] == Status.APPROVED.value


def test_cvflowtools_has_no_approve_method():
    store = _store_with_job()
    tools = CvflowTools(
        store=store, knowledge=None, discovery=None, analyzer=None, tailor=None
    )
    assert not hasattr(tools, "approve")


def test_discover_dispatches_to_discovery_service():
    from cvflow.discovery import JobPosting
    from cvflow.discovery.benchmark import BenchmarkedJob

    class FakeDiscovery:
        def __init__(self):
            self.called = False

        def discover(self):
            self.called = True
            p = JobPosting(
                job_id="indeed:7", title="Backend Engineer", company="Acme",
                location="Remote", description="...", url="https://x/7",
                site="indeed", date_posted="2026-06-04",
            )
            return {
                "M": [BenchmarkedJob(posting=p, benchmark=72, fit_score=80,
                                     fit_reason="Python + SQL match", concerns=[],
                                     cohort="M", ctc_lpa=18.0)],
                "N": [],
            }

    fake = FakeDiscovery()
    tools = CvflowTools(
        store=None, knowledge=None, discovery=fake, analyzer=None, tailor=None
    )
    out = tools.discover()
    assert fake.called is True
    assert out[0]["job_id"] == "indeed:7"
    assert out[0]["title"] == "Backend Engineer"
    assert out[0]["cohort"] == "M"
    assert out[0]["benchmark"] == 72
    assert out[0]["fit_reason"] == "Python + SQL match"
    assert out[0]["url"] == "https://x/7"


def test_analyze_jd_dispatches_and_persists():
    from cvflow.analysis import JDAnalysis
    from cvflow.storage import ApplicationStore

    store = ApplicationStore(":memory:")
    store.add("indeed:5", "Acme", "Backend Engineer", "https://x/5")

    class FakeAnalyzer:
        def analyze(self, jd_text):
            assert jd_text == "RAW JD TEXT"
            return JDAnalysis(
                required_skills=["python"], preferred_quals=["aws"],
                seniority="mid", tone="casual",
                applicant_instructions=["include the word pineapple"],
            )

    tools = CvflowTools(
        store=store, knowledge=None, discovery=None,
        analyzer=FakeAnalyzer(), tailor=None,
    )
    out = tools.analyze_jd("indeed:5", jd_text="RAW JD TEXT")
    assert out["required_skills"] == ["python"]
    assert out["applicant_instructions"] == ["include the word pineapple"]
    assert store.get_analysis("indeed:5").seniority == "mid"


def test_build_tools_wires_store_and_knowledge(tmp_path, monkeypatch):
    from cvflow.config import load_config

    cfg_path = tmp_path / "config.yaml"
    import shutil
    from pathlib import Path

    real = Path("config.yaml")
    if not real.exists():
        import pytest

        pytest.skip("no live config.yaml present")
    shutil.copy(real, cfg_path)

    from cvflow.mcp.tools import build_tools

    cfg = load_config(cfg_path)
    tools = build_tools(cfg)
    assert tools.ping()["status"] == "ok"
    assert isinstance(tools.list_applications(status="discovered")["applications"], list)


def test_discover_returns_url_for_every_job():
    from cvflow.discovery import JobPosting
    from cvflow.discovery.benchmark import BenchmarkedJob
    from cvflow.storage import ApplicationStore

    class _StubDiscovery:
        def discover(self):
            p = JobPosting(
                job_id="indeed:7", title="Backend Dev", company="Acme",
                location="Remote", description="d", url="https://jobs/7",
                site="indeed", date_posted="2026-06-04",
            )
            return {"M": [], "N": [BenchmarkedJob(posting=p, benchmark=70, fit_score=70,
                                                  fit_reason="r", concerns=[], cohort="N")]}

    tools = CvflowTools(
        store=ApplicationStore(":memory:"), knowledge=None,
        discovery=_StubDiscovery(), analyzer=None, tailor=None,
    )
    jobs = tools.discover()
    assert jobs and all(j["url"] for j in jobs)
    assert jobs[0]["url"] == "https://jobs/7"


def test_fill_application_is_gated_and_has_no_approve_tool():
    from cvflow.mcp.tools import TOOL_NAMES
    from cvflow.statemachine import SubmissionBlocked, guard_can_submit
    from cvflow.storage import ApplicationStore

    assert "approve" not in TOOL_NAMES
    assert "fill_application" in TOOL_NAMES
    assert "resume_application" in TOOL_NAMES

    store = ApplicationStore(":memory:")
    store.add("indeed:1", "Acme", "Backend", "https://jobs/1")

    class _Auto:
        def fill(self, job_id):
            guard_can_submit(store.get(job_id).status)
            return {"status": "applied"}
        def resume(self, job_id, answer):
            return {"status": "applied"}

    tools = CvflowTools(
        store=store, knowledge=None, discovery=None, analyzer=None,
        tailor=None, automator=_Auto(),
    )
    with pytest.raises(SubmissionBlocked):
        tools.fill_application("indeed:1")
    assert not hasattr(tools, "approve")


def test_submit_otp_routes_on_time_and_times_out_late():
    from cvflow.mcp.tools import TOOL_NAMES

    assert "approve" not in TOOL_NAMES
    assert "submit_otp" in TOOL_NAMES

    class _Coord:
        def __init__(self, code):
            self._code = code
        def provide(self, job_id, otp):
            return self._code  # None simulates expiry

    class _Auto:
        def __init__(self):
            self.resumed = None
        def resume(self, job_id, answer):
            self.resumed = (job_id, answer)
            return {"status": "applied"}

    auto = _Auto()
    tools = CvflowTools(
        store=None, knowledge=None, discovery=None, analyzer=None,
        tailor=None, automator=auto, otp_coordinator=_Coord("123456"),
    )
    assert tools.submit_otp("indeed:1", "123456") == {"status": "applied"}
    assert auto.resumed == ("indeed:1", "123456")

    auto2 = _Auto()
    tools2 = CvflowTools(
        store=None, knowledge=None, discovery=None, analyzer=None,
        tailor=None, automator=auto2, otp_coordinator=_Coord(None),
    )
    assert tools2.submit_otp("indeed:1", "123456") == {"otp_timeout": True, "job_id": "indeed:1"}
    assert auto2.resumed is None
