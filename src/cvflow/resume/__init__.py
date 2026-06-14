"""Resume tailoring (Goal 3 + the diff for Goal 4).

Tailoring works at the granularity of **whole existing units** — sections and
per-project blocks of the modular master. It only **selects and reorders** them;
it never rewrites unit text. That makes "no new facts" (CLAUDE.md invariant 2) a
deterministic, exact check: every content line in the tailored ``.tex`` must
already exist in the master. The LLM (same ``generate(prompt)->str`` seam as
discovery/analysis) only chooses ordering + selection; deterministic code
validates the plan (dropping anything fabricated), caps projects at
:data:`MAX_PROJECTS`, renders, asserts, and diffs. Compilation is via Tectonic.

User requirement (2026-06-04): show at most 2 projects, chosen by JD relevance
(e.g. a Java/Spring Boot role → CRUDbot).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from cvflow.analysis import JDAnalysis

__all__ = [
    "Section",
    "Project",
    "Master",
    "parse_master",
    "TailoringPlan",
    "ResumeTailor",
    "TailoringError",
    "CompileError",
    "MAX_PROJECTS",
]

MAX_PROJECTS = 2

_INPUT_RE = re.compile(r"\\input\{sections/([^}]+?)\.tex\}")
_PROJECT_INPUT_RE = re.compile(r"\\input\{sections/projects/([^}]+?)\.tex\}")


class TailoringError(Exception):
    """Raised when a tailoring plan is invalid or would add facts."""


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
    """Parse the modular ``master.tex`` into ordered sections + selectable projects."""
    root = Path(root)
    master_tex = (root / "master.tex").read_text()
    order: list[str] = []
    sections: dict[str, Section] = {}
    for name in _INPUT_RE.findall(master_tex):
        if name.startswith("projects/"):
            continue  # nested per-project inputs are handled below
        order.append(name)
        sections[name] = Section(
            name=name, content=(root / "sections" / f"{name}.tex").read_text()
        )
    projects: list[Project] = []
    projects_tex = root / "sections" / "projects.tex"
    if projects_tex.exists():
        for pid in _PROJECT_INPUT_RE.findall(projects_tex.read_text()):
            projects.append(
                Project(
                    project_id=pid,
                    content=(root / "sections" / "projects" / f"{pid}.tex").read_text(),
                )
            )
    return Master(root=root, section_order=order, sections=sections, projects=projects)


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
    "## Available projects (id: content)\n{projects}\n\n"
    "## Target job\nrequired_skills: {req}\npreferred_quals: {pref}\nseniority: {sen}\n"
    "\nUser feedback to incorporate (optional): {feedback}\n"
)


class ResumeTailor:
    """Plans, renders, fact-checks, diffs and compiles a JD-tailored resume."""

    def __init__(self, provider: _Provider, master: Master) -> None:
        self._provider = provider
        self._master = master

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
        picks = [p for p in d.get("selected_project_ids", []) if p in valid_pids][
            :MAX_PROJECTS
        ]

        return TailoringPlan(
            section_order=order,
            selected_project_ids=picks,
            diff_narration=str(d.get("diff_narration", "")),
        )

    def _render_projects(self, selected: list[str]) -> str:
        by_id = {p.project_id: p for p in self._master.projects}
        return "\n".join(by_id[pid].content for pid in selected if pid in by_id)

    def render(self, plan: TailoringPlan) -> str:
        parts: list[str] = []
        for name in plan.section_order:
            if name == "projects":
                parts.append(self._render_projects(plan.selected_project_ids))
            else:
                parts.append(self._master.sections[name].content)
        return "\n".join(parts)

    @staticmethod
    def _content_lines(text: str) -> set[str]:
        return {ln.strip() for ln in text.splitlines() if ln.strip()}

    def assert_no_new_facts(self, tailored_tex: str) -> None:
        """Raise if the tailored tex has any content line absent from the master."""
        master_lines: set[str] = set()
        for s in self._master.sections.values():
            master_lines |= self._content_lines(s.content)
        for p in self._master.projects:
            master_lines |= self._content_lines(p.content)
        extra = self._content_lines(tailored_tex) - master_lines
        if extra:
            raise TailoringError(
                f"tailored resume adds content not in master: {sorted(extra)[:3]}"
            )

    def diff(self, plan: TailoringPlan) -> str:
        lines = [
            "Section order: " + " → ".join(plan.section_order),
            "Projects shown: " + (", ".join(plan.selected_project_ids) or "(none)"),
        ]
        if plan.diff_narration:
            lines.append("")
            lines.append(plan.diff_narration)
        return "\n".join(lines)

    def tailored_document(self, plan: TailoringPlan) -> str:
        """Return a full compilable .tex: preamble + the name/contact heading + reordered
        body inputs. The heading (between ``\\begin{document}`` and the first section input)
        is always kept, whatever sections the plan selects."""
        master_tex = (self._master.root / "master.tex").read_text()
        marker = "\\begin{document}"
        begin = master_tex.index(marker)
        preamble = master_tex[:begin]
        after_begin = master_tex[begin + len(marker) :]
        first_input = _INPUT_RE.search(after_begin)
        heading = after_begin[: first_input.start()].strip("\n") if first_input else ""
        body_lines: list[str] = []
        if heading:
            body_lines.append(heading)
        for name in plan.section_order:
            if name == "projects":
                body_lines.append("\\section{Projects}")
                body_lines.append("    \\resumeSubHeadingListStart")
                for pid in plan.selected_project_ids:
                    body_lines.append(f"      \\input{{sections/projects/{pid}.tex}}")
                body_lines.append("    \\resumeSubHeadingListEnd")
            else:
                body_lines.append(f"\\input{{sections/{name}.tex}}")
        body = "\n".join(body_lines)
        return f"{preamble}\\begin{{document}}\n{body}\n\\end{{document}}\n"

    def compile_tailored(
        self, plan: TailoringPlan, outdir: str | Path, *, stem: str = "_tailored"
    ) -> Path:
        """Write the tailored document into the resume root and Tectonic-compile it.

        ``stem`` names the output (``<stem>.pdf``) so per-job resumes don't overwrite each
        other; callers pass a job-derived stem.
        """
        if shutil.which("tectonic") is None:
            raise CompileError("tectonic not found on PATH")
        self.assert_no_new_facts(self.render(plan))
        outdir = Path(outdir)
        outdir.mkdir(parents=True, exist_ok=True)
        tailored_path = self._master.root / f"{stem}.tex"
        tailored_path.write_text(self.tailored_document(plan))
        try:
            result = subprocess.run(
                ["tectonic", "-X", "compile", str(tailored_path), "--outdir", str(outdir)],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                raise CompileError(result.stderr[-2000:])
            return outdir / f"{stem}.pdf"
        finally:
            tailored_path.unlink(missing_ok=True)

    def compile_master(self, outdir: str | Path) -> Path:
        """Compile the repo's ``master.tex`` as-is to a PDF in ``outdir`` (Tectonic)."""
        if shutil.which("tectonic") is None:
            raise CompileError("tectonic not found on PATH")
        outdir = Path(outdir)
        outdir.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            [
                "tectonic",
                "-X",
                "compile",
                str(self._master.root / "master.tex"),
                "--outdir",
                str(outdir),
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise CompileError(result.stderr[-2000:])
        pdf = outdir / "master.pdf"
        if not pdf.exists():
            raise CompileError(f"tectonic reported success but {pdf} is missing")
        return pdf
