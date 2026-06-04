from cvflow.analysis import JDAnalysis
from cvflow.mcp.tools import TOOL_NAMES, CvflowTools
from cvflow.statemachine import Status
from cvflow.storage import ApplicationStore


class _FakeTailor:
    def plan(self, jd, *, feedback=None):
        return "PLAN"

    def diff(self, plan):
        return "Section order: a → b"

    def compile_tailored(self, plan, outdir):
        from pathlib import Path

        p = Path(outdir) / "_tailored.pdf"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"%PDF-1.5")
        return p


class _FakeKB:
    def full_context(self):
        return "## experience\nBackend engineer."

    def doc_keys(self):
        return ["experience"]


def _tools(tmp_path, status=Status.PENDING_REVIEW):
    store = ApplicationStore(":memory:")
    store.add("j1", "Acme", "Engineer", "http://jd")
    if status is not Status.DISCOVERED:
        store.set_status("j1", Status.PENDING_REVIEW)
    store.save_analysis(
        "j1",
        JDAnalysis(
            required_skills=["python"],
            preferred_quals=[],
            seniority="mid",
            tone="neutral",
            applicant_instructions=[],
        ),
    )
    t = CvflowTools(
        store=store,
        knowledge=_FakeKB(),
        discovery=None,
        analyzer=None,
        tailor=_FakeTailor(),
        output_dir=str(tmp_path),
        essay_provider=None,
    )
    return store, t


def test_no_approve_tool_on_surface():
    assert "approve" not in TOOL_NAMES
    assert not hasattr(CvflowTools, "approve")


def test_request_review_returns_pdf_diff_analysis(tmp_path):
    store, t = _tools(tmp_path, status=Status.DISCOVERED)
    out = t.request_review("j1")
    assert out["status"] == Status.PENDING_REVIEW.value
    assert out["pdf_path"].endswith("_tailored.pdf")
    assert "Section order" in out["diff"]
    assert out["analysis_summary"]["required_skills"] == ["python"]
    assert "/apply j1" in out["instructions"]
    assert store.get("j1").tailored_pdf_path == out["pdf_path"]


def test_request_review_threads_feedback(tmp_path):
    store, t = _tools(tmp_path)
    seen = {}

    def plan(jd, *, feedback=None):
        seen["fb"] = feedback
        return "PLAN"

    t._tailor.plan = plan
    t.request_review("j1", feedback="more backend")
    assert seen["fb"] == "more backend"


def test_compose_essay_grounded(tmp_path):
    store, t = _tools(tmp_path)

    class _P:
        def generate(self, prompt):
            return '{"answer":"Backend.","citations":["experience"]}'

    t._essay_provider = _P()
    out = t.compose_essay("Describe your work.")
    assert out["grounded"] is True
    assert out["needs_clarification"] is False


def test_compose_essay_ungroundable_flags_clarification(tmp_path):
    store, t = _tools(tmp_path)

    class _P:
        def generate(self, prompt):
            return '{"needs_clarification": true, "missing":"salary"}'

    t._essay_provider = _P()
    out = t.compose_essay("Expected salary?")
    assert out["needs_clarification"] is True
    assert out["text"] is None


def test_status_report_counts_by_status(tmp_path):
    store, t = _tools(tmp_path)
    rep = t.status_report()
    assert rep["pending_review"] == 1
