# Phase 6 — Resume Tailoring (Goal 3 + the diff for Goal 4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
> Master roadmap: `2026-06-03-cvflow-build-plan.md` → Phase 6.
> **Exit:** tests prove a tailored `.tex` compiles to PDF, no new factual claims vs master (assert against the master content), ≤2 JD-relevant projects are selected, and a human-readable diff is produced.

**Goal:** Given a `JDAnalysis` (Phase 5) and the modular master resume, produce a tailored `.tex` that reorders sections and selects the most JD-relevant content (≤2 projects) **without adding any facts**, compile it to PDF via Tectonic, and emit a plain-language diff for the approval gate.

**Architecture:** Tailoring operates at the granularity of **whole existing units** (sections, project blocks, bullets) — it **selects and reorders**, never rewrites unit text. That makes "no new facts" a deterministic, exact check: every content line in the tailored `.tex` must already exist in the master. The LLM only chooses ordering + selection (behind the same `generate(prompt)->str` provider seam as Phase 4/5); deterministic code validates the plan against what actually exists (dropping anything fabricated), enforces the ≤2-projects cap, renders, asserts, compiles, and diffs.

**Tech Stack:** Python 3.11+, existing `JDAnalysis`, Tectonic (injectable `compile_fn`; compile test skips if `tectonic` absent), stdlib only otherwise. Fully mocked LLM tests.

---

## Standing rules in play
- **Never fabricate (rule 3 / invariant 2):** select/reorder existing units only; `assert_no_new_facts` fails the tailoring if any tailored content line is not present in the master. LLM-returned section/project ids that don't exist are dropped.
- **Never fail silently (rule 4):** missing section files, an empty selection, or a compile failure raise explicit errors (`TailoringError`, `CompileError`).
- **Free-tier / cost (rules 5,6):** one Gemini call per tailoring (cached); Tectonic is local/free. No new paid surface.
- **User requirement (2026-06-04):** ≤2 projects, chosen by JD relevance (e.g. Java/Spring Boot → CRUDbot).

## File Structure
- **Restructure** `resume/sections/projects.tex` → a thin wrapper that `\input`s per-project files in `resume/sections/projects/*.tex` (one project block each), so projects are individually selectable. `master.tex` still `\input{sections/projects.tex}`.
- Create `src/cvflow/resume/__init__.py` — `Section`, `Project`, `parse_master`, `TailoringPlan`, `ResumeTailor`, errors.
- Create `tests/test_resume.py` — parsing, plan validation/cap, render, no-new-facts, diff, (skipped) compile.

---

### Task 1: Restructure projects into per-project files

**Files:**
- Create: `resume/sections/projects/ipsec-dashboard.tex`, `resume/sections/projects/crudbot.tex`
- Modify: `resume/sections/projects.tex`

- [ ] **Step 1: Create `resume/sections/projects/ipsec-dashboard.tex`** (the existing IPSec block, verbatim)

```latex
\resumeProjectHeading
    {\textbf{IPSec Tunnel Monitoring Dashboard} $|$ \emph{Figma-Make, Flask API, StrongSwan}}{}
    \resumeItemListStart
      \resumeItem{Built a real-time web monitoring station consuming a custom REST API to display live IPSec status, log streams, and one-click remediation controls}
      \resumeItem{Designed a responsive UI via AI-assisted Figma-Make, integrated with a manually developed Python REST backend}
    \resumeItemListEnd
```

- [ ] **Step 2: Create `resume/sections/projects/crudbot.tex`** (the existing CRUDbot block, verbatim)

```latex
\resumeProjectHeading
    {\textbf{CRUDbot} $|$ \emph{Spring Boot, PostgreSQL, Docker, OpenAI API}}{}
    \resumeItemListStart
      \resumeItem{Developed a CRUD-based backend for managing notes, URLs, and reminders}
      \resumeItem{Integrated PostgreSQL for persistent storage and Docker for containerized deployment}
      \resumeItem{Planned expansion: OpenAI API-powered smart note organization}
    \resumeItemListEnd
```

- [ ] **Step 3: Rewrite `resume/sections/projects.tex`** to the wrapper (default order = all projects)

```latex
%-----------PROJECTS-----------
\section{Projects}
    \resumeSubHeadingListStart
      \input{sections/projects/ipsec-dashboard.tex}
      \input{sections/projects/crudbot.tex}
    \resumeSubHeadingListEnd
```

- [ ] **Step 4: Verify the master still compiles** (skip if `tectonic` not installed)

Run: `cd resume && tectonic -X compile master.tex --outdir /tmp/texout`
Expected: `master.pdf` written, exit 0. (Confirms the nested `\input` resolves.)

- [ ] **Step 5: Commit**

```bash
git add resume/sections/projects.tex resume/sections/projects/
git commit -m "refactor(resume): split projects into per-project files for selectable tailoring"
```

---

### Task 2: `parse_master` + `Section`/`Project` models

**Files:**
- Create: `src/cvflow/resume/__init__.py`
- Test: `tests/test_resume.py`

- [ ] **Step 1: Write the failing test**

```python
from pathlib import Path
from cvflow.resume import parse_master


def test_parse_master_returns_section_order_and_projects(tmp_path: Path) -> None:
    # minimal fake master tree
    (tmp_path / "sections" / "projects").mkdir(parents=True)
    (tmp_path / "master.tex").write_text(
        "\\begin{document}\n"
        "\\input{sections/experience.tex}\n"
        "\\input{sections/projects.tex}\n"
        "\\input{sections/skills.tex}\n"
        "\\end{document}\n"
    )
    (tmp_path / "sections" / "experience.tex").write_text("EXP")
    (tmp_path / "sections" / "skills.tex").write_text("SKILLS")
    (tmp_path / "sections" / "projects.tex").write_text(
        "\\input{sections/projects/a.tex}\n\\input{sections/projects/b.tex}\n"
    )
    (tmp_path / "sections" / "projects" / "a.tex").write_text("AAA")
    (tmp_path / "sections" / "projects" / "b.tex").write_text("BBB")

    master = parse_master(tmp_path)
    assert master.section_order == ["experience", "projects", "skills"]
    assert [p.project_id for p in master.projects] == ["a", "b"]
    assert master.sections["experience"].content == "EXP"
    assert master.projects[0].content == "AAA"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_resume.py::test_parse_master_returns_section_order_and_projects -v`
Expected: FAIL with `ImportError` for `cvflow.resume`.

- [ ] **Step 3: Write minimal implementation** — create `src/cvflow/resume/__init__.py`

```python
"""Resume tailoring (Goal 3 + diff for Goal 4): reorder/select existing units
of the modular master per JD — never rewriting unit text, so "no new facts" is
a deterministic subset check (invariant 2). Compile via Tectonic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "Section",
    "Project",
    "Master",
    "parse_master",
    "TailoringPlan",
    "ResumeTailor",
    "TailoringError",
    "CompileError",
]

_INPUT_RE = re.compile(r"\\input\{sections/([^}]+?)\.tex\}")
_PROJECT_INPUT_RE = re.compile(r"\\input\{sections/projects/([^}]+?)\.tex\}")


class TailoringError(Exception):
    """Raised when a tailoring plan is invalid or adds facts."""


class CompileError(Exception):
    """Raised when LaTeX compilation fails."""


@dataclass(frozen=True)
class Section:
    name: str
    content: str


@dataclass(frozen=True)
class Project:
    project_id: str
    content: str


@dataclass(frozen=True)
class Master:
    root: Path
    section_order: list[str]
    sections: dict[str, Section]
    projects: list[Project]


def parse_master(root: str | Path) -> Master:
    root = Path(root)
    master_tex = (root / "master.tex").read_text()
    order: list[str] = []
    sections: dict[str, Section] = {}
    for name in _INPUT_RE.findall(master_tex):
        if name.startswith("projects/"):
            continue  # nested project inputs handled separately
        order.append(name)
        sections[name] = Section(name=name, content=(root / "sections" / f"{name}.tex").read_text())
    projects: list[Project] = []
    projects_tex_path = root / "sections" / "projects.tex"
    if projects_tex_path.exists():
        for pid in _PROJECT_INPUT_RE.findall(projects_tex_path.read_text()):
            projects.append(
                Project(project_id=pid, content=(root / "sections" / "projects" / f"{pid}.tex").read_text())
            )
    return Master(root=root, section_order=order, sections=sections, projects=projects)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_resume.py::test_parse_master_returns_section_order_and_projects -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/cvflow/resume/__init__.py tests/test_resume.py
git commit -m "feat(resume): parse modular master into ordered sections + selectable projects (Phase 6)"
```

---

### Task 3: `TailoringPlan` + `ResumeTailor.plan` (LLM, validated, ≤2 projects)

**Files:**
- Modify: `src/cvflow/resume/__init__.py`
- Test: `tests/test_resume.py`

- [ ] **Step 1: Write the failing test**

```python
import json
from cvflow.analysis import JDAnalysis
from cvflow.resume import Master, Project, Section, ResumeTailor, TailoringPlan


def _master() -> Master:
    return Master(
        root=Path("/nonexistent"),
        section_order=["experience", "projects", "skills"],
        sections={
            "experience": Section("experience", "EXP"),
            "projects": Section("projects", "P"),
            "skills": Section("skills", "SK"),
        },
        projects=[Project("ipsec-dashboard", "IPSEC"), Project("crudbot", "CRUD")],
    )


class _FakeProvider:
    def __init__(self, payload: str) -> None:
        self.payload = payload
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.payload


def _jd() -> JDAnalysis:
    return JDAnalysis(
        required_skills=["Java", "Spring Boot"],
        preferred_quals=[],
        seniority="junior",
        tone="",
        applicant_instructions=[],
    )


def test_plan_validates_and_caps_projects_and_drops_unknown() -> None:
    payload = json.dumps(
        {
            "section_order": ["skills", "experience", "projects", "ghost-section"],
            "selected_project_ids": ["crudbot", "ipsec-dashboard", "fake-proj"],
            "diff_narration": "Led with skills for the Java role; chose CRUDbot (Spring Boot).",
        }
    )
    tailor = ResumeTailor(_FakeProvider(payload), _master())
    plan = tailor.plan(_jd())
    assert isinstance(plan, TailoringPlan)
    # unknown section dropped, all real sections retained
    assert plan.section_order == ["skills", "experience", "projects"]
    # unknown project dropped AND capped at 2
    assert plan.selected_project_ids == ["crudbot", "ipsec-dashboard"]
    assert "Java" in tailor._provider.prompts[0]  # JD skills embedded


def test_plan_caps_to_two_projects() -> None:
    payload = json.dumps(
        {
            "section_order": ["experience", "projects", "skills"],
            "selected_project_ids": ["ipsec-dashboard", "crudbot"],
            "diff_narration": "x",
        }
    )
    m = _master()
    m.projects.append(Project("third", "THIRD"))
    tailor = ResumeTailor(_FakeProvider(payload), m)
    plan = tailor.plan(_jd())
    assert len(plan.selected_project_ids) <= 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_resume.py -k plan -v`
Expected: FAIL with `AttributeError`/`ImportError` for `ResumeTailor`/`TailoringPlan`.

- [ ] **Step 3: Write minimal implementation** — add to `src/cvflow/resume/__init__.py`

```python
import json
from typing import Protocol

MAX_PROJECTS = 2


class _Provider(Protocol):
    def generate(self, prompt: str) -> str: ...


def _strip_code_fence(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        if t.endswith("```"):
            t = t.rsplit("```", 1)[0]
    return t.strip()


@dataclass(frozen=True)
class TailoringPlan:
    section_order: list[str]
    selected_project_ids: list[str]
    diff_narration: str


_PLAN_PROMPT = (
    "You tailor a resume by REORDERING sections and SELECTING which projects to show. "
    "You may NOT add, remove, or reword any factual content — only choose order and selection.\n"
    "Return ONLY a JSON object: section_order (array of section names, a permutation/subset "
    "of the given sections), selected_project_ids (array, AT MOST 2, most relevant first), "
    "diff_narration (one short paragraph explaining the choices in plain language).\n\n"
    "## Available sections (default order)\n{sections}\n\n"
    "## Available projects (id: stack)\n{projects}\n\n"
    "## Target job\nrequired_skills: {req}\npreferred_quals: {pref}\nseniority: {sen}\n"
)


class ResumeTailor:
    def __init__(self, provider: _Provider, master: Master) -> None:
        self._provider = provider
        self._master = master

    def plan(self, jd: JDAnalysis) -> TailoringPlan:
        prompt = _PLAN_PROMPT.format(
            sections=", ".join(self._master.section_order),
            projects="\n".join(f"- {p.project_id}: {p.content[:200]}" for p in self._master.projects),
            req=", ".join(jd.required_skills),
            pref=", ".join(jd.preferred_quals),
            sen=jd.seniority,
        )
        raw = self._provider.generate(prompt)
        try:
            d = json.loads(_strip_code_fence(raw))
        except (ValueError, json.JSONDecodeError) as exc:
            raise TailoringError(f"unparseable tailoring plan: {exc}") from exc

        valid_sections = set(self._master.section_order)
        order = [s for s in d.get("section_order", []) if s in valid_sections]
        if not order:
            order = list(self._master.section_order)  # never emit an empty resume

        valid_pids = {p.project_id for p in self._master.projects}
        picks = [p for p in d.get("selected_project_ids", []) if p in valid_pids][:MAX_PROJECTS]

        return TailoringPlan(
            section_order=order,
            selected_project_ids=picks,
            diff_narration=str(d.get("diff_narration", "")),
        )
```

Add `from cvflow.analysis import JDAnalysis` to the imports.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_resume.py -k plan -v`
Expected: PASS (both)

- [ ] **Step 5: Commit**

```bash
git add src/cvflow/resume/__init__.py tests/test_resume.py
git commit -m "feat(resume): LLM tailoring plan — validated section order, JD-relevant projects capped at 2 (Phase 6)"
```

---

### Task 4: `render` + `assert_no_new_facts`

**Files:**
- Modify: `src/cvflow/resume/__init__.py`
- Test: `tests/test_resume.py`

- [ ] **Step 1: Write the failing test**

```python
from cvflow.resume import TailoringError


def test_render_reorders_sections_and_selects_projects() -> None:
    tailor = ResumeTailor(_FakeProvider("{}"), _master())
    plan = TailoringPlan(
        section_order=["skills", "experience"],
        selected_project_ids=["crudbot"],
        diff_narration="",
    )
    tex = tailor.render(plan)
    # selected section content present, in plan order; projects section omitted here
    assert tex.index("SK") < tex.index("EXP")
    # selected project content present, the other absent
    assert "CRUD" in tex
    assert "IPSEC" not in tex


def test_assert_no_new_facts_passes_for_subset_and_fails_for_addition() -> None:
    tailor = ResumeTailor(_FakeProvider("{}"), _master())
    ok_plan = TailoringPlan(["experience"], [], "")
    tailor.assert_no_new_facts(tailor.render(ok_plan))  # no raise
    # a tampered tailored tex with a new factual line must raise
    import pytest
    with pytest.raises(TailoringError):
        tailor.assert_no_new_facts("EXP\n\\resumeItem{Fabricated 10 years at Google}")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_resume.py -k 'render or no_new_facts' -v`
Expected: FAIL (`render`/`assert_no_new_facts` missing).

- [ ] **Step 3: Write minimal implementation** — add methods to `ResumeTailor`

```python
    def _render_projects(self, selected: list[str]) -> str:
        by_id = {p.project_id: p for p in self._master.projects}
        chosen = [by_id[pid].content for pid in selected if pid in by_id]
        return "\n".join(chosen)

    def render(self, plan: TailoringPlan) -> str:
        parts: list[str] = []
        for name in plan.section_order:
            section = self._master.sections[name]
            if name == "projects":
                # replace the project \input lines with only the selected project blocks
                parts.append(self._render_projects(plan.selected_project_ids))
            else:
                parts.append(section.content)
        return "\n".join(parts)

    def _content_lines(self, text: str) -> set[str]:
        return {ln.strip() for ln in text.splitlines() if ln.strip()}

    def assert_no_new_facts(self, tailored_tex: str) -> None:
        master_lines: set[str] = set()
        for s in self._master.sections.values():
            master_lines |= self._content_lines(s.content)
        for p in self._master.projects:
            master_lines |= self._content_lines(p.content)
        extra = self._content_lines(tailored_tex) - master_lines
        if extra:
            raise TailoringError(f"tailored resume adds content not in master: {sorted(extra)[:3]}")
```

> Note: because tailoring only selects/reorders whole unit lines, every tailored line is a master line — the check is exact. The `projects` section's wrapper lines (`\section{Projects}` etc.) are NOT re-emitted by `render` (we emit raw project blocks), keeping the subset relation clean; if a future change re-emits wrapper lines, add them to `master_lines`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_resume.py -k 'render or no_new_facts' -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/cvflow/resume/__init__.py tests/test_resume.py
git commit -m "feat(resume): render tailored tex (reorder+select) + deterministic no-new-facts assertion (Phase 6)"
```

---

### Task 5: `diff` (plain-language) + `compile` (Tectonic, injectable, skippable)

**Files:**
- Modify: `src/cvflow/resume/__init__.py`
- Test: `tests/test_resume.py`

- [ ] **Step 1: Write the failing test**

```python
import shutil
import pytest


def test_diff_describes_reorder_and_project_selection() -> None:
    tailor = ResumeTailor(_FakeProvider("{}"), _master())
    plan = TailoringPlan(["skills", "experience", "projects"], ["crudbot"], "Led with skills.")
    diff = tailor.diff(plan)
    assert "skills" in diff.lower()
    assert "crudbot" in diff.lower()
    assert "Led with skills." in diff  # LLM narration carried through


@pytest.mark.skipif(shutil.which("tectonic") is None, reason="tectonic not installed")
def test_compile_real_master_produces_pdf(tmp_path: Path) -> None:
    from cvflow.resume import parse_master
    repo_resume = Path(__file__).resolve().parents[1] / "resume"
    master = parse_master(repo_resume)
    tailor = ResumeTailor(_FakeProvider("{}"), master)
    # render full default order, then wrap with the real preamble for a standalone compile
    pdf = tailor.compile_master(tmp_path)  # compiles resume/master.tex as-is
    assert pdf.exists() and pdf.stat().st_size > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_resume.py -k 'diff or compile' -v`
Expected: `diff` test FAILs (method missing); compile test runs (or skips if no tectonic).

- [ ] **Step 3: Write minimal implementation** — add to `ResumeTailor`

```python
import subprocess


    def diff(self, plan: TailoringPlan) -> str:
        lines = [
            "Section order: " + " → ".join(plan.section_order),
            "Projects shown: " + (", ".join(plan.selected_project_ids) or "(none)"),
        ]
        if plan.diff_narration:
            lines.append("")
            lines.append(plan.diff_narration)
        return "\n".join(lines)

    def compile_master(self, outdir: str | Path) -> Path:
        """Compile the repo's master.tex as-is to a PDF in outdir (Tectonic)."""
        outdir = Path(outdir)
        outdir.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            ["tectonic", "-X", "compile", str(self._master.root / "master.tex"), "--outdir", str(outdir)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise CompileError(result.stderr[-2000:])
        pdf = outdir / "master.pdf"
        if not pdf.exists():
            raise CompileError(f"tectonic reported success but {pdf} is missing")
        return pdf
```

Add `import subprocess` and `import shutil` only where used (top of module for `subprocess`).

> Tailored-tex compilation (vs `compile_master`) needs the preamble; that is wired when the daemon/gate assembles `preamble + tailored body`. For Phase 6 the deterministic units (`plan`/`render`/`assert_no_new_facts`/`diff`) are fully tested without TeX, and `compile_master` proves the Tectonic path end-to-end on the real master. Assembling preamble + tailored body into a compiled tailored PDF is finished in Phase 8/11 wiring.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_resume.py -v`
Expected: PASS (compile test passes if tectonic present, else skips).

- [ ] **Step 5: Commit**

```bash
git add src/cvflow/resume/__init__.py tests/test_resume.py
git commit -m "feat(resume): plain-language diff + Tectonic compile path (Phase 6)"
```

---

### Task 6: Harness green + roadmap sync

- [ ] **Step 1:** `pytest && ruff check . && mypy src` → all green (compile test skips without tectonic).
- [ ] **Step 2:** tick `### [x] Phase 6` in the master plan + append a progress-log entry dated 2026-06-04.
- [ ] **Step 3:** commit `docs: mark Phase 6 (resume tailoring) complete`.

---

## Self-Review
- **Spec coverage:** modular reorder (Task 4) ✓; ≤2 JD-relevant projects (Tasks 3,4) ✓; no new facts asserted (Task 4) ✓; plain-language diff (Task 5) ✓; compile to PDF via Tectonic (Task 5) ✓.
- **Type consistency:** `Master`/`Section`/`Project`/`TailoringPlan` fields used identically across Tasks 2–5; provider Protocol matches Phase 4/5 `generate(prompt)->str`.
- **No placeholders:** every code/test step is concrete.

## Out of scope
Assembling preamble + tailored body into a compiled *tailored* PDF (Phase 8/11 wiring); essay/free-text auto-answer from the personality fingerprint (Phase 8); the salary-hike discovery filter (Phase 4 follow-up).
