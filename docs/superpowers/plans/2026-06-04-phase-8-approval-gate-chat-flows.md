# Phase 8 — Approval-gate skill + chat flows — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the human Telegram approval gate as deterministic out-of-band code, plus the surrounding chat flows (review message with a real Gemini-tailored PDF + diff + analysis, essay/free-text auto-answer, status report), without ever giving the agent loop a path to `approved`.

**Architecture:** A Hermes `command:approve`/`command:skip` hook (registered via a thin plugin) calls a pure `cvflow.gate.handle_gate_command`, the sole caller of `ApplicationStore.approve()`. The brain prepares reviews via an enhanced `request_review` MCP skill (Gemini tailoring → compiled tailored PDF → diff/analysis payload) and composes grounded essay answers via `cvflow.essays`; both stay out of the gate. All gate/essay logic is pure and unit-tested with mocked LLM/transport.

**Tech Stack:** Python 3.11+, FastMCP (stdio), Hermes plugin/hook adapters, GeminiProvider (Phase 2), ResumeTailor + Tectonic (Phase 6), SQLite ApplicationStore (Phase 1), pytest/ruff/mypy --strict.

**Spec:** `docs/superpowers/specs/2026-06-04-phase-8-approval-gate-chat-flows-design.md`

---

## File Structure

- Create `src/cvflow/gate/__init__.py` — pure deterministic gate command handler (`GateResult`, `handle_gate_command`). Sole `store.approve()` caller.
- Create `src/cvflow/essays/__init__.py` — pure personality-fingerprint composer (`EssayAnswer`, `compose_answer`).
- Modify `src/cvflow/resume/__init__.py` — add `ResumeTailor.tailored_document(plan) -> str` and `compile_tailored(plan, outdir) -> Path`.
- Modify `src/cvflow/mcp/tools.py` — enhance `request_review`, add `compose_essay` + `status_report`, extend `TOOL_NAMES`, wire `build_tools` collaborators.
- Modify `src/cvflow/resume/__init__.py` accepts optional `feedback` in `plan()`.
- Create `artifacts/hermes/plugins/cvflow-gate/{plugin.yaml,handler.py}` and `artifacts/hermes/hooks/cvflow-gate/{HOOK.yaml,handler.py}` — thin Hermes adapters over `cvflow.gate`.
- Tests: `tests/test_gate.py`, `tests/test_essays.py`, `tests/test_resume_tailored_compile.py`, `tests/test_mcp_tools_phase8.py`, `tests/test_hermes_gate_adapters.py`.

---

## Task 1: Pure gate command handler (`cvflow.gate`)

**Files:**
- Create: `src/cvflow/gate/__init__.py`
- Test: `tests/test_gate.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_gate.py
import pytest
from cvflow.gate import GateResult, handle_gate_command
from cvflow.statemachine import Status
from cvflow.storage import ApplicationStore

USER = 1291545895

def _store_with(status: Status) -> ApplicationStore:
    s = ApplicationStore(":memory:")
    s.add("j1", "Acme", "Engineer", "http://jd")
    if status is Status.PENDING_REVIEW:
        s.set_status("j1", Status.PENDING_REVIEW)
    return s

def test_approve_from_pending_is_only_route_to_approved():
    store = _store_with(Status.PENDING_REVIEW)
    res = handle_gate_command(
        command="approve", args="j1", user_id=USER,
        authorized_user_id=USER, store=store,
    )
    assert isinstance(res, GateResult)
    assert res.handled is True
    assert store.get("j1").status is Status.APPROVED
    assert "Approved" in res.message

def test_approve_wrong_state_is_reported_not_silent_and_no_change():
    store = _store_with(Status.DISCOVERED)  # not pending_review
    res = handle_gate_command(
        command="approve", args="j1", user_id=USER,
        authorized_user_id=USER, store=store,
    )
    assert res.handled is True
    assert res.message and "can't approve" in res.message.lower()
    assert store.get("j1").status is Status.DISCOVERED

def test_approve_unknown_job_reported():
    store = ApplicationStore(":memory:")
    res = handle_gate_command(
        command="approve", args="nope", user_id=USER,
        authorized_user_id=USER, store=store,
    )
    assert res.handled is True
    assert "no application" in res.message.lower()

def test_skip_transitions_and_reports():
    store = _store_with(Status.PENDING_REVIEW)
    res = handle_gate_command(
        command="skip", args="j1", user_id=USER,
        authorized_user_id=USER, store=store,
    )
    assert store.get("j1").status is Status.SKIPPED
    assert res.handled is True

def test_unauthorized_user_is_silently_ignored_no_state_change():
    store = _store_with(Status.PENDING_REVIEW)
    res = handle_gate_command(
        command="approve", args="j1", user_id=999,
        authorized_user_id=USER, store=store,
    )
    assert res.handled is False
    assert res.message is None
    assert store.get("j1").status is Status.PENDING_REVIEW

def test_user_id_compared_as_string_or_int():
    store = _store_with(Status.PENDING_REVIEW)
    res = handle_gate_command(
        command="approve", args="j1", user_id="1291545895",
        authorized_user_id=USER, store=store,
    )
    assert res.handled is True
    assert store.get("j1").status is Status.APPROVED
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_gate.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cvflow.gate'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/cvflow/gate/__init__.py
"""Deterministic gate command handler — the human-approval path, off the agent loop.

This module is the SOLE caller of ``ApplicationStore.approve`` in production code.
A Hermes ``command:approve`` / ``command:skip`` hook (see artifacts/hermes/hooks)
invokes :func:`handle_gate_command` with the inbound Telegram user id and args.
The agent/brain has no approve tool and cannot inject an inbound slash command,
so it can never reach this code path (CLAUDE.md invariant 1).
"""

from __future__ import annotations

from dataclasses import dataclass

from cvflow.statemachine import IllegalTransition, Status
from cvflow.storage import ApplicationStore, UnknownJob

__all__ = ["GateResult", "handle_gate_command"]


@dataclass(frozen=True)
class GateResult:
    """Outcome of a gate command. ``handled`` short-circuits Hermes dispatch."""

    handled: bool
    message: str | None


def _same_user(a: object, b: object) -> bool:
    return str(a).strip() == str(b).strip()


def handle_gate_command(
    *,
    command: str,
    args: str,
    user_id: object,
    authorized_user_id: object,
    store: ApplicationStore,
) -> GateResult:
    """Route a human ``/approve`` or ``/skip`` to deterministic state changes.

    Never fails silently for the authorized user: every outcome returns a
    message. An unauthorized user is ignored (handled=False, no message).
    """
    if not _same_user(user_id, authorized_user_id):
        return GateResult(handled=False, message=None)

    job_id = args.strip().split()[0] if args.strip() else ""
    if not job_id:
        return GateResult(handled=True, message="⚠️ Usage: /{0} <job_id>".format(command))

    if command == "approve":
        try:
            store.approve(job_id)
        except UnknownJob:
            return GateResult(handled=True, message=f"⚠️ No application {job_id}.")
        except IllegalTransition:
            app = store.get(job_id)
            status = app.status.value if app else "unknown"
            return GateResult(
                handled=True,
                message=f"⚠️ Can't approve {job_id}: it is {status}, not pending review.",
            )
        return GateResult(handled=True, message=f"✅ Approved {job_id} — submitting.")

    if command == "skip":
        try:
            store.set_status(job_id, Status.SKIPPED)
        except UnknownJob:
            return GateResult(handled=True, message=f"⚠️ No application {job_id}.")
        except IllegalTransition:
            app = store.get(job_id)
            status = app.status.value if app else "unknown"
            return GateResult(
                handled=True,
                message=f"⚠️ Can't skip {job_id}: it is {status}.",
            )
        return GateResult(handled=True, message=f"⏭️ Skipped {job_id}.")

    return GateResult(handled=False, message=None)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_gate.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Lint, type-check, commit**

```bash
ruff check . && mypy src
git add src/cvflow/gate/__init__.py tests/test_gate.py
git commit -m "feat(gate): deterministic /approve /skip handler — sole approve() caller"
```

---

## Task 2: Tailored-document builder + compile (`resume`)

**Files:**
- Modify: `src/cvflow/resume/__init__.py`
- Test: `tests/test_resume_tailored_compile.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_resume_tailored_compile.py
import shutil
import pytest
from cvflow.resume import ResumeTailor, TailoringPlan, parse_master

ROOT = "resume"  # committed modular master

class _Stub:
    def generate(self, prompt: str) -> str:  # not used by these tests
        return "{}"

def test_tailored_document_reorders_and_selects_projects():
    master = parse_master(ROOT)
    tailor = ResumeTailor(_Stub(), master)
    order = list(reversed(master.section_order))
    pid = master.projects[0].project_id
    plan = TailoringPlan(section_order=order, selected_project_ids=[pid], diff_narration="")
    doc = tailor.tailored_document(plan)
    # full compilable document: has preamble + begin/end document
    assert "\\begin{document}" in doc and "\\end{document}" in doc
    # selected project content present; a non-selected project absent
    assert master.projects[0].content.splitlines()[0].strip() in doc
    # no fabricated facts
    tailor.assert_no_new_facts(doc)

@pytest.mark.skipif(shutil.which("tectonic") is None, reason="tectonic not installed")
def test_compile_tailored_produces_pdf(tmp_path):
    master = parse_master(ROOT)
    tailor = ResumeTailor(_Stub(), master)
    plan = TailoringPlan(
        section_order=list(master.section_order),
        selected_project_ids=[master.projects[0].project_id],
        diff_narration="",
    )
    pdf = tailor.compile_tailored(plan, tmp_path)
    assert pdf.exists() and pdf.suffix == ".pdf"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_resume_tailored_compile.py -v`
Expected: FAIL — `AttributeError: 'ResumeTailor' object has no attribute 'tailored_document'`.

- [ ] **Step 3: Write minimal implementation**

Add to `src/cvflow/resume/__init__.py` inside `class ResumeTailor` (after `render`). The master's `master.tex` contains a preamble, `\begin{document}`, ordered `\input{sections/<name>}` lines including `\input{sections/projects}`, then `\end{document}`. Build a tailored full document by reusing the real preamble and emitting reordered `\input`s, replacing the `projects` slot with the selected per-project inputs:

```python
    def tailored_document(self, plan: "TailoringPlan") -> str:
        """Return a full compilable .tex: real preamble + reordered body inputs."""
        master_tex = (self._master.root / "master.tex").read_text()
        begin = master_tex.index("\\begin{document}")
        preamble = master_tex[:begin]
        body_lines: list[str] = []
        for name in plan.section_order:
            if name == "projects":
                for pid in plan.selected_project_ids:
                    body_lines.append(f"\\input{{sections/projects/{pid}}}")
            else:
                body_lines.append(f"\\input{{sections/{name}}}")
        body = "\n".join(body_lines)
        return f"{preamble}\\begin{{document}}\n{body}\n\\end{{document}}\n"

    def compile_tailored(self, plan: "TailoringPlan", outdir: str | Path) -> Path:
        """Write the tailored document into the resume root and Tectonic-compile it."""
        if shutil.which("tectonic") is None:
            raise CompileError("tectonic not found on PATH")
        self.assert_no_new_facts(self.render(plan))
        outdir = Path(outdir)
        outdir.mkdir(parents=True, exist_ok=True)
        tailored_path = self._master.root / "_tailored.tex"
        tailored_path.write_text(self.tailored_document(plan))
        try:
            result = subprocess.run(
                ["tectonic", "-X", "compile", str(tailored_path), "--outdir", str(outdir)],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                raise CompileError(result.stderr[-2000:])
            return outdir / "_tailored.pdf"
        finally:
            tailored_path.unlink(missing_ok=True)
```

Note: `tailored_document` references `\input{sections/projects/<pid>}`; confirm the per-project files live at `resume/sections/projects/<pid>.tex` (they do — see `parse_master`). `assert_no_new_facts` uses `render(plan)` (section/project bodies), which is the fact-bearing content; the preamble/`\input` scaffolding carries no profile facts.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_resume_tailored_compile.py -v`
Expected: PASS (compile test runs if `tectonic` present, else skips).

- [ ] **Step 5: Lint, type-check, commit**

```bash
ruff check . && mypy src
git add src/cvflow/resume/__init__.py tests/test_resume_tailored_compile.py
git commit -m "feat(resume): assemble + Tectonic-compile a tailored PDF from a plan"
```

---

## Task 3: `plan()` accepts optional tailoring feedback

**Files:**
- Modify: `src/cvflow/resume/__init__.py` (`ResumeTailor.plan`, `_PLAN_PROMPT`)
- Test: `tests/test_resume_tailored_compile.py` (add one test)

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_resume_tailored_compile.py
def test_plan_threads_feedback_into_prompt():
    master = parse_master(ROOT)
    captured = {}
    class _Capture:
        def generate(self, prompt: str) -> str:
            captured["prompt"] = prompt
            return '{"section_order": [], "selected_project_ids": [], "diff_narration": ""}'
    tailor = ResumeTailor(_Capture(), master)
    from cvflow.analysis import JDAnalysis
    jd = JDAnalysis(
        required_skills=["python"], preferred_quals=[], seniority="mid",
        tone="neutral", applicant_instructions=[],
    )
    tailor.plan(jd, feedback="emphasize backend work")
    assert "emphasize backend work" in captured["prompt"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_resume_tailored_compile.py::test_plan_threads_feedback_into_prompt -v`
Expected: FAIL — `TypeError: plan() got an unexpected keyword argument 'feedback'`.

- [ ] **Step 3: Write minimal implementation**

In `_PLAN_PROMPT` add a trailing line `User feedback to incorporate (optional): {feedback}`. Change the signature and `.format` call:

```python
    def plan(self, jd: JDAnalysis, *, feedback: str | None = None) -> TailoringPlan:
        prompt = _PLAN_PROMPT.format(
            sections=", ".join(self._master.section_order),
            projects="\n".join(
                f"- {p.project_id}: {p.content[:200]}" for p in self._master.projects
            ),
            req=", ".join(jd.required_skills),
            pref=", ".join(jd.preferred_quals),
            sen=jd.seniority,
            feedback=feedback or "(none)",
        )
        # ... rest unchanged
```

Confirm the `JDAnalysis` constructor field names match (`required_skills`, `preferred_quals`, `seniority`, `tone`, `applicant_instructions`) by checking `src/cvflow/analysis/__init__.py` before writing the test.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_resume_tailored_compile.py -v`
Expected: PASS.

- [ ] **Step 5: Lint, type-check, commit**

```bash
ruff check . && mypy src
git add src/cvflow/resume/__init__.py tests/test_resume_tailored_compile.py
git commit -m "feat(resume): thread optional edit feedback into the tailoring prompt"
```

---

## Task 4: Essay composer (`cvflow.essays`)

**Files:**
- Create: `src/cvflow/essays/__init__.py`
- Test: `tests/test_essays.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_essays.py
from cvflow.essays import EssayAnswer, compose_answer

class _KB:
    """Minimal knowledge stub exposing the accessors compose_answer uses."""
    def __init__(self, docs):
        self._docs = docs
    def doc_keys(self):
        return list(self._docs)
    def full_context(self):
        return "\n\n".join(f"## {k}\n{v}" for k, v in self._docs.items())

def test_grounded_answer_cites_real_doc_keys():
    kb = _KB({"experience": "Built a Django payments service handling 10k req/day."})
    class _P:
        def generate(self, prompt: str) -> str:
            return '{"answer": "I built a Django payments service.", "citations": ["experience"]}'
    ans = compose_answer("Describe a backend project.", kb, provider=_P())
    assert isinstance(ans, EssayAnswer)
    assert ans.grounded is True
    assert ans.needs_clarification is False
    assert ans.citations == ["experience"]
    assert ans.text

def test_ungroundable_question_triggers_clarification_not_a_guess():
    kb = _KB({"experience": "Backend engineer."})
    class _P:
        def generate(self, prompt: str) -> str:
            return '{"needs_clarification": true, "missing": "expected salary"}'
    ans = compose_answer("What salary do you expect?", kb, provider=_P())
    assert ans.needs_clarification is True
    assert ans.text is None
    assert ans.clarification and "salary" in ans.clarification.lower()

def test_citation_to_nonexistent_doc_is_treated_as_ungrounded():
    kb = _KB({"experience": "Backend engineer."})
    class _P:
        def generate(self, prompt: str) -> str:
            return '{"answer": "I won a Nobel prize.", "citations": ["awards"]}'
    ans = compose_answer("Tell us an achievement.", kb, provider=_P())
    assert ans.grounded is False
    assert ans.needs_clarification is True
    assert ans.text is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_essays.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cvflow.essays'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/cvflow/essays/__init__.py
"""Personality-fingerprint composer for essays / free-text fields.

Synthesizes answers from EXISTING profile facts (CLAUDE.md invariant 2 +
essay-auto-answer policy): the LLM may rephrase/emphasize real KB content in
the user's voice, but must NOT invent facts. Any answer whose citations don't
map to real KB docs — or any explicitly ungroundable question — returns
``needs_clarification`` instead of a guess.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

__all__ = ["EssayAnswer", "compose_answer"]


class _Provider(Protocol):
    def generate(self, prompt: str) -> str: ...


@dataclass(frozen=True)
class EssayAnswer:
    text: str | None
    grounded: bool
    citations: list[str]
    needs_clarification: bool
    clarification: str | None


_PROMPT = """You answer a job-application question in the applicant's voice.
Use ONLY facts present in the profile below. Do not invent anything.
If the question needs a fact that is genuinely absent, do not guess — instead
reply with JSON {{"needs_clarification": true, "missing": "<what is missing>"}}.
Otherwise reply with JSON {{"answer": "<text>", "citations": ["<doc keys used>"]}}.

PROFILE:
{context}

QUESTION:
{question}
"""


def _strip_fence(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        if t.endswith("```"):
            t = t[: t.rfind("```")]
    return t.strip()


def compose_answer(question: str, knowledge: Any, *, provider: _Provider) -> EssayAnswer:
    raw = provider.generate(_PROMPT.format(context=knowledge.full_context(), question=question))
    try:
        d = json.loads(_strip_fence(raw))
    except (ValueError, json.JSONDecodeError):
        return EssayAnswer(None, False, [], True, "could not compose an answer")

    if d.get("needs_clarification"):
        return EssayAnswer(None, False, [], True, str(d.get("missing", "missing information")))

    answer = str(d.get("answer", "")).strip()
    citations = [str(c) for c in d.get("citations", [])]
    valid_keys = set(knowledge.doc_keys())
    grounded = bool(answer) and bool(citations) and all(c in valid_keys for c in citations)
    if not grounded:
        return EssayAnswer(None, False, citations, True, "answer not grounded in profile")
    return EssayAnswer(answer, True, citations, False, None)
```

Note: this uses `knowledge.full_context()` and `knowledge.doc_keys()`. `full_context()` exists on `KnowledgeBase` (Phase 3). Confirm a `doc_keys()` accessor exists; if not, add a one-line `def doc_keys(self) -> list[str]: return list(self._docs)` to `src/cvflow/knowledge/__init__.py` (check the actual internal attribute name first) and commit it as part of this task.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_essays.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Lint, type-check, commit**

```bash
ruff check . && mypy src
git add src/cvflow/essays/__init__.py tests/test_essays.py src/cvflow/knowledge/__init__.py
git commit -m "feat(essays): grounded personality-fingerprint composer (cite-or-clarify)"
```

---

## Task 5: Enhance `request_review`, add `compose_essay` + `status_report`, wire `build_tools`

**Files:**
- Modify: `src/cvflow/mcp/tools.py`
- Test: `tests/test_mcp_tools_phase8.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_mcp_tools_phase8.py
import pytest
from cvflow.mcp.tools import CvflowTools, TOOL_NAMES
from cvflow.statemachine import Status
from cvflow.storage import ApplicationStore
from cvflow.analysis import JDAnalysis

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
    def full_context(self): return "## experience\nBackend engineer."
    def doc_keys(self): return ["experience"]

def _tools(tmp_path, status=Status.PENDING_REVIEW):
    store = ApplicationStore(":memory:")
    store.add("j1", "Acme", "Engineer", "http://jd")
    if status is not Status.DISCOVERED:
        store.set_status("j1", Status.PENDING_REVIEW)
    store.save_analysis("j1", JDAnalysis(
        required_skills=["python"], preferred_quals=[], seniority="mid",
        tone="neutral", applicant_instructions=[]))
    t = CvflowTools(store=store, knowledge=_FakeKB(), discovery=None,
                    analyzer=None, tailor=_FakeTailor())
    t._output_dir = str(tmp_path)  # see impl note
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
    assert "/approve j1" in out["instructions"]
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
        def generate(self, prompt): return '{"answer":"Backend.","citations":["experience"]}'
    t._essay_provider = _P()
    out = t.compose_essay("Describe your work.")
    assert out["grounded"] is True
    assert out["needs_clarification"] is False

def test_compose_essay_ungroundable_flags_clarification(tmp_path):
    store, t = _tools(tmp_path)
    class _P:
        def generate(self, prompt): return '{"needs_clarification": true, "missing":"salary"}'
    t._essay_provider = _P()
    out = t.compose_essay("Expected salary?")
    assert out["needs_clarification"] is True
    assert out["text"] is None

def test_status_report_counts_by_status(tmp_path):
    store, t = _tools(tmp_path)
    rep = t.status_report()
    assert rep["pending_review"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_mcp_tools_phase8.py -v`
Expected: FAIL — `request_review` has the old signature / `compose_essay` absent.

- [ ] **Step 3: Write minimal implementation**

In `src/cvflow/mcp/tools.py`:

(a) Extend the allowlist:
```python
TOOL_NAMES: tuple[str, ...] = (
    "ping",
    "discover",
    "analyze_jd",
    "list_applications",
    "get_application",
    "request_review",
    "submit",
    "compose_essay",
    "status_report",
)
```

(b) Add constructor fields (keep existing params; add two with defaults so callers/tests can inject):
```python
    def __init__(self, *, store, knowledge, discovery, analyzer, tailor,
                 output_dir: str = "data/tailored", essay_provider=None) -> None:
        self._store = store
        self._knowledge = knowledge
        self._discovery = discovery
        self._analyzer = analyzer
        self._tailor = tailor
        self._output_dir = output_dir
        self._essay_provider = essay_provider
```

(c) Replace `request_review`:
```python
    def request_review(self, job_id: str, *, feedback: str | None = None) -> dict[str, Any]:
        """Move to PENDING_REVIEW, tailor + compile the PDF, return the review payload.

        This prepares the review; it never approves. Approval is the human
        /approve command handled out-of-band by cvflow.gate.
        """
        app = self._store.get(job_id)
        if app is None:
            raise UnknownJob(job_id)
        if app.status is Status.DISCOVERED:
            self._store.set_status(job_id, Status.PENDING_REVIEW)
        analysis = self._store.get_analysis(job_id)
        if analysis is None:
            raise UnknownJob(f"no JD analysis for {job_id}; run analyze_jd first")
        plan = self._tailor.plan(analysis, feedback=feedback)
        pdf = self._tailor.compile_tailored(plan, self._output_dir)
        self._store.set_tailored_pdf(job_id, str(pdf))
        return {
            "job_id": job_id,
            "status": Status.PENDING_REVIEW.value,
            "pdf_path": str(pdf),
            "diff": self._tailor.diff(plan),
            "analysis_summary": json.loads(analysis.to_json()),
            "instructions": f"Reply /approve {job_id} to approve & apply, or /skip {job_id} to skip.",
        }
```

(d) Add the two new skills:
```python
    def compose_essay(self, question: str) -> dict[str, Any]:
        """Compose a grounded answer; flag clarification instead of guessing."""
        from cvflow.essays import compose_answer

        ans = compose_answer(question, self._knowledge, provider=self._essay_provider)
        return {
            "text": ans.text,
            "grounded": ans.grounded,
            "citations": ans.citations,
            "needs_clarification": ans.needs_clarification,
            "clarification": ans.clarification,
        }

    def status_report(self) -> dict[str, int]:
        """Return a count of applications per status."""
        return {
            s.value: len(self._store.list_by_status(s)) for s in Status
        }
```

(e) Update `build_tools` to wire the real collaborators (imports at top of function):
```python
def build_tools(config: Any) -> CvflowTools:
    from cvflow.analysis import JDAnalyzer
    from cvflow.discovery import DiscoveryService
    from cvflow.knowledge import KnowledgeBase
    from cvflow.llm import GeminiProvider
    from cvflow.resume import ResumeTailor, parse_master
    from cvflow.storage import ApplicationStore

    store = ApplicationStore(config.storage.db_path)
    knowledge = KnowledgeBase.load(
        config.profile.knowledge_base_dir, config.storage.form_fields_path
    )
    tailoring = GeminiProvider(
        api_key=config.llm.tailoring.api_key,
        model=config.llm.tailoring.model,
        max_requests_per_day=config.llm.tailoring.max_requests_per_day,
    )
    tailor = ResumeTailor(tailoring, parse_master(config.resume.master_tex_path))
    return CvflowTools(
        store=store,
        knowledge=knowledge,
        discovery=DiscoveryService(...) if False else None,  # see note
        analyzer=JDAnalyzer(...) if False else None,          # see note
        tailor=tailor,
        output_dir=config.resume.output_dir,
        essay_provider=tailoring,
    )
```

Implementation note: before writing `build_tools`, open `src/cvflow/llm/__init__.py`, `src/cvflow/resume/__init__.py`, `src/cvflow/analysis/__init__.py`, `src/cvflow/discovery/__init__.py` and match the real constructor signatures for `GeminiProvider`, `JDAnalyzer`, `DiscoveryService`. Wire `analyzer` and `discovery` with their real constructors (drop the `if False` placeholders) using `config` fields; if a constructor needs the brain provider, build a second provider from `config.llm.brain`. `request_review`/`compose_essay`/`status_report` only need `store`, `knowledge`, `tailor`, and `essay_provider`, so the phase's tests pass regardless — but wire all collaborators properly for the live server. Reuse `tailoring` as `essay_provider` (Gemini) since essays are low-volume, higher-quality (mirrors the tailoring escalation rationale).

`config.resume.master_tex_path` points at `resume/master.tex`; `parse_master` wants the **root dir**. Pass `Path(config.resume.master_tex_path).parent` if the config value is the file, else the dir — check the value in `config.yaml` and adapt.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_mcp_tools_phase8.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Full suite, lint, type-check, commit**

```bash
pytest && ruff check . && mypy src
git add src/cvflow/mcp/tools.py tests/test_mcp_tools_phase8.py
git commit -m "feat(mcp): review payload (PDF+diff+analysis), essay + status skills, wire tailoring"
```

---

## Task 6: Hermes gate adapters (plugin + hook) + smoke test

**Files:**
- Create: `artifacts/hermes/plugins/cvflow-gate/plugin.yaml`
- Create: `artifacts/hermes/plugins/cvflow-gate/handler.py`
- Create: `artifacts/hermes/hooks/cvflow-gate/HOOK.yaml`
- Create: `artifacts/hermes/hooks/cvflow-gate/handler.py`
- Test: `tests/test_hermes_gate_adapters.py`

- [ ] **Step 1: Write the failing test**

The adapters are thin shims; the test imports the hook handler module by path and asserts it dispatches to the pure `handle_gate_command`, building the store from cvflow config and returning a Hermes `decision` dict.

```python
# tests/test_hermes_gate_adapters.py
import importlib.util
from pathlib import Path
import pytest
from cvflow.statemachine import Status
from cvflow.storage import ApplicationStore

HOOK = Path("artifacts/hermes/hooks/cvflow-gate/handler.py")

def _load(path):
    spec = importlib.util.spec_from_file_location("cvflow_gate_hook", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

@pytest.mark.asyncio
async def test_hook_dispatches_approve_to_pure_handler(monkeypatch):
    store = ApplicationStore(":memory:")
    store.add("j1", "Acme", "Eng", "http://jd")
    store.set_status("j1", Status.PENDING_REVIEW)
    mod = _load(HOOK)
    # Inject our store + authorized user instead of building from live config.
    monkeypatch.setattr(mod, "_build_store", lambda: store)
    monkeypatch.setattr(mod, "_authorized_user_id", lambda: 1291545895)
    result = await mod.handle("command:approve",
        {"command": "approve", "args": "j1", "user_id": 1291545895})
    assert result["decision"] == "handled"
    assert "Approved" in result["message"]
    assert store.get("j1").status is Status.APPROVED

@pytest.mark.asyncio
async def test_hook_ignores_unauthorized(monkeypatch):
    store = ApplicationStore(":memory:")
    store.add("j1", "Acme", "Eng", "http://jd")
    store.set_status("j1", Status.PENDING_REVIEW)
    mod = _load(HOOK)
    monkeypatch.setattr(mod, "_build_store", lambda: store)
    monkeypatch.setattr(mod, "_authorized_user_id", lambda: 1291545895)
    result = await mod.handle("command:approve",
        {"command": "approve", "args": "j1", "user_id": 999})
    assert result == {}  # not handled → falls through, brain/transport already gate
    assert store.get("j1").status is Status.PENDING_REVIEW
```

Add `pytest-asyncio` to dev deps if absent; or rewrite the test to call `asyncio.run(mod.handle(...))` from a sync test to avoid the plugin. **Prefer the `asyncio.run` sync form** to avoid adding a dependency:

```python
import asyncio
def test_hook_dispatches_approve_to_pure_handler(monkeypatch):
    ...
    result = asyncio.run(mod.handle("command:approve",
        {"command": "approve", "args": "j1", "user_id": 1291545895}))
    ...
```

Use the `asyncio.run` form in both tests (drop the `@pytest.mark.asyncio` decorators and the `async def`).

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_hermes_gate_adapters.py -v`
Expected: FAIL — handler file does not exist.

- [ ] **Step 3: Write the adapters**

`artifacts/hermes/hooks/cvflow-gate/HOOK.yaml`:
```yaml
name: cvflow-gate
description: Deterministic human approval gate for cvflow (/approve, /skip).
events:
  - command:approve
  - command:skip
```

`artifacts/hermes/hooks/cvflow-gate/handler.py`:
```python
"""Hermes command hook: routes /approve and /skip to cvflow's deterministic gate.

Runs in the gateway process, outside the agent loop. Returns a Hermes
{"decision": "handled", "message": ...} dict so the reply is sent and the
brain never processes the command. Unauthorized users → {} (fall through).
"""

from __future__ import annotations

import os
import sys
from typing import Any


def _repo_root() -> str:
    return os.environ.get("CVFLOW_ROOT", os.path.expanduser("~/cvflow"))


def _ensure_import() -> None:
    root_src = os.path.join(_repo_root(), "src")
    if root_src not in sys.path:
        sys.path.insert(0, root_src)


def _build_store():
    _ensure_import()
    from cvflow.config import load_config
    from cvflow.storage import ApplicationStore

    cfg = load_config(os.path.join(_repo_root(), "config.yaml"))
    return ApplicationStore(cfg.storage.db_path)


def _authorized_user_id():
    _ensure_import()
    from cvflow.config import load_config

    cfg = load_config(os.path.join(_repo_root(), "config.yaml"))
    return cfg.telegram.authorized_user_id


async def handle(event_type: str, context: dict[str, Any]) -> dict[str, Any]:
    _ensure_import()
    from cvflow.gate import handle_gate_command

    res = handle_gate_command(
        command=str(context.get("command", "")),
        args=str(context.get("args", "")),
        user_id=context.get("user_id"),
        authorized_user_id=_authorized_user_id(),
        store=_build_store(),
    )
    if not res.handled:
        return {}
    return {"decision": "handled", "message": res.message}
```

`artifacts/hermes/plugins/cvflow-gate/plugin.yaml`:
```yaml
name: cvflow-gate
version: 0.1.0
description: Registers /approve and /skip so the cvflow gate hook can fire.
commands:
  - name: approve
    description: Approve a pending application and submit it.
    args_hint: "<job_id>"
  - name: skip
    description: Skip a pending application.
    args_hint: "<job_id>"
```

`artifacts/hermes/plugins/cvflow-gate/handler.py` (fallback handler; the hook intercepts first, so this only runs if the hook is missing):
```python
"""Fallback plugin command handlers for /approve and /skip.

The command:* hook (artifacts/hermes/hooks/cvflow-gate) intercepts these and
short-circuits before plugin dispatch. This fallback only fires if the hook is
not installed — it tells the user the gate hook is missing rather than acting,
because a plugin handler does not receive the user_id needed to gate safely.
"""

def approve(args: str) -> str:
    return "⚠️ cvflow gate hook not installed; cannot approve safely."

def skip(args: str) -> str:
    return "⚠️ cvflow gate hook not installed; cannot skip safely."
```

Confirm the exact plugin.yaml schema and command-registration keys against `~/.hermes/hermes-agent/hermes_cli/plugins.py` (`PluginManifest`, `register_command`) before finalizing; adapt key names if the manifest uses a different shape (e.g. an entrypoint module rather than top-level `commands:`).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_hermes_gate_adapters.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Lint, type-check, commit**

```bash
ruff check . && mypy src
git add artifacts/hermes/plugins/cvflow-gate artifacts/hermes/hooks/cvflow-gate tests/test_hermes_gate_adapters.py
git commit -m "feat(hermes): plugin+hook adapters wiring /approve /skip to the deterministic gate"
```

---

## Task 7: Live wiring + docs + plan sync

**Files:**
- Modify: `~/.hermes/config.yaml` (host, gitignored — back up first)
- Symlink/copy adapters into `~/.hermes/{plugins,hooks}/`
- Modify: `docs/superpowers/plans/2026-06-03-cvflow-build-plan.md` (Phase 8 checkbox + progress log)
- Modify: `artifacts/README.md` (document the gate adapters + install)

- [ ] **Step 1: Install adapters into the live Hermes tree**

```bash
cp -r artifacts/hermes/plugins/cvflow-gate ~/.hermes/plugins/cvflow-gate
cp -r artifacts/hermes/hooks/cvflow-gate ~/.hermes/hooks/cvflow-gate
cp ~/.hermes/config.yaml ~/.hermes/config.yaml.bak.$(date +%s)
```
Set `CVFLOW_ROOT=/home/ubuntu/cvflow` in `~/.hermes/.env` (the hook reads it; defaults to `~/cvflow`). Ensure `plugins.enabled` is true in `~/.hermes/config.yaml` and `agent.disabled_toolsets` keeps the Phase-7 trim.

- [ ] **Step 2: Restart the gateway and verify command registration**

```bash
hermes gateway run --replace   # systemd-managed instance takes over
hermes plugins                 # cvflow-gate listed
```
Expected: `/approve` and `/skip` appear as known commands; `hermes mcp test cvflow` still shows 9 tools, no `approve`.

- [ ] **Step 3: End-to-end gate proof from Telegram (manual, user-run)**

From the authorized chat: discover → pick a job → ask Hermes to `analyze_jd` then `request_review`; confirm the PDF + diff + analysis arrive. Then send `/approve <job_id>`; confirm the reply "✅ Approved …" and that `get_application` shows `approved`. Send `/approve <other_id>` for a non-pending job; confirm the "can't approve" message (never silent). Confirm the brain has no way to approve (ask it to "approve job X" → it has no tool and can only suggest the command).

- [ ] **Step 4: Sync the build plan**

Tick Phase 8's `[ ]` → `[x]` and append a progress-log entry dated 2026-06-04 summarizing: gate hook is the sole approve() caller; review payload carries PDF/diff/analysis; essays grounded/cite-or-clarify; Gemini tailoring wired; 9 MCP tools (no approve); test count. Note `/edit` is brain-mediated via `request_review(feedback=...)` (rationale in the spec).

- [ ] **Step 5: Commit (NO Co-Authored-By trailer)**

```bash
git add docs/superpowers/plans/2026-06-03-cvflow-build-plan.md artifacts/README.md docs/superpowers/specs docs/superpowers/plans/2026-06-04-phase-8-approval-gate-chat-flows.md
git commit -m "docs: mark Phase 8 complete; record approval-gate design + sub-plan"
```

---

## Self-review notes

- **Spec coverage:** gate (Task 1, 6) · review PDF/diff/analysis (Task 2, 5) · feedback/edit (Task 3, 5) · essays grounded + clarification (Task 4, 5) · status report (Task 5) · Gemini wiring (Task 5) · live + docs (Task 7). All spec sections map to a task.
- **Type consistency:** `handle_gate_command` signature, `GateResult`, `EssayAnswer`, `compose_answer(question, knowledge, *, provider)`, `request_review(job_id, *, feedback=None)`, `compile_tailored(plan, outdir)`, `tailored_document(plan)` are used identically across tasks.
- **Verify-before-write reminders** are embedded (JDAnalysis fields, KnowledgeBase accessors, GeminiProvider/JDAnalyzer/DiscoveryService constructors, plugin.yaml schema, master_tex_path file-vs-dir) — each must be checked against the real source during execution rather than assumed.
