"""Automator control flow: gate, pause/resume, disclosure, crash-notify. No browser."""

import pytest

from cvflow.automation import Automator, FieldSpec
from cvflow.statemachine import Status, SubmissionBlocked
from cvflow.storage import ApplicationStore, FormFields


class _FakeFiller:
    def __init__(self, specs, *, raise_on_submit=False):
        self._specs = specs
        self.applied = []
        self._raise = raise_on_submit
    def discover_fields(self): return self._specs
    def apply(self, name, ftype, value): self.applied.append((name, value))
    def submit(self):
        if self._raise:
            raise RuntimeError("network died")
    def capture_proof(self, path):
        from cvflow.automation import Proof
        return Proof(url="https://done", page_title="Application received",
                     screenshot_path=path, confirmation_ref="#XYZ-1")


class _FakeSessions:
    def __init__(self, page="P"):
        self._page = page
        self.closed = []
    def open(self, job_id, url): return self._page
    def page(self, job_id): return self._page
    def close(self, job_id): self.closed.append(job_id)


class _KB:
    form_fields = FormFields(values={"full_name": "Ada Lovelace"})
    def full_context(self): return "ctx"
    def doc_keys(self): return ["experience"]


def _provider(text="{}"):
    class P:
        def generate(self, prompt): return text
    return P()


def _store():
    s = ApplicationStore(":memory:")
    s.add("indeed:1", "Acme", "Backend", "https://jobs/1")
    return s


def _automator(store, filler, sessions, notes, **kw):
    return Automator(
        store=store, knowledge=_KB(), provider=_provider(kw.pop("ptext", "{}")),
        sessions=sessions,
        filler_factory=lambda page: filler,
        notify=lambda msg: notes.append(msg),
        screenshot_dir="/tmp",
        **kw,
    )


def test_gate_blocks_non_approved():
    store = _store()  # status discovered
    notes = []
    auto = _automator(store, _FakeFiller([]), _FakeSessions(), notes)
    with pytest.raises(SubmissionBlocked):
        auto.fill("indeed:1")


def _approve(store):
    store.set_status("indeed:1", Status.PENDING_REVIEW)
    store.approve("indeed:1")


def test_completes_and_records_proof_when_all_resolved():
    store = _store()
    _approve(store)
    specs = [FieldSpec("Full name", "full_name", "text", [], True)]
    filler = _FakeFiller(specs)
    notes = []
    auto = _automator(store, filler, _FakeSessions(), notes)
    result = auto.fill("indeed:1")
    assert result["status"] == "applied"
    assert ("full_name", "Ada Lovelace") in filler.applied
    assert store.get("indeed:1").status == Status.APPLIED
    assert store.get("indeed:1").proof_url == "https://done"


def test_required_unknown_field_pauses_for_clarification():
    store = _store()
    _approve(store)
    specs = [FieldSpec("Expected start date", "start_date", "text", [], True)]
    auto = _automator(store, _FakeFiller(specs), _FakeSessions(), [])
    result = auto.fill("indeed:1")
    assert result["needs_clarification"] is True
    assert result["question"] == "Expected start date"
    assert store.get("indeed:1").status == Status.APPROVED  # not submitted


def test_resume_uses_clarified_answer_then_completes():
    store = _store()
    _approve(store)
    specs = [FieldSpec("Expected start date", "start_date", "text", [], True)]
    filler = _FakeFiller(specs)
    auto = _automator(store, filler, _FakeSessions(), [])
    auto.fill("indeed:1")
    result = auto.resume("indeed:1", "2026-07-01")
    assert ("start_date", "2026-07-01") in filler.applied
    assert result["status"] == "applied"


def test_composed_answers_disclosed_before_submit():
    store = _store()
    _approve(store)
    specs = [FieldSpec("Why do you want to work here?", "why_us", "textarea", [], True)]
    filler = _FakeFiller(specs)
    notes = []
    grounded = '{"answer": "I love backends", "citations": ["experience"]}'
    auto = _automator(store, filler, _FakeSessions(), notes, ptext=grounded)
    auto.fill("indeed:1")
    assert any("why_us" in n or "Why do you want" in n for n in notes)
    assert any("I love backends" in n for n in notes)


def test_crash_marks_failed_and_notifies_with_url():
    store = _store()
    _approve(store)
    specs = [FieldSpec("Full name", "full_name", "text", [], True)]
    filler = _FakeFiller(specs, raise_on_submit=True)
    sessions = _FakeSessions()
    notes = []
    auto = _automator(store, filler, sessions, notes)
    result = auto.fill("indeed:1")
    assert result["failed"] is True
    assert store.get("indeed:1").status == Status.FAILED
    assert any("https://jobs/1" in n for n in notes)
    assert "indeed:1" in sessions.closed
