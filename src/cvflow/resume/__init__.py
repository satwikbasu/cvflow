"""Resume tailoring (Goal 3 + the diff for Goal 4).

Tailoring works at the granularity of **whole existing units** — sections and
per-project blocks of the modular master. It only **selects and reorders** them;
it never rewrites unit text. That makes "no new facts" (CLAUDE.md invariant 2) a
deterministic, exact check: every content line in the tailored ``.tex`` must
already exist in the master. The LLM (same ``generate(prompt)->str`` seam as
discovery/analysis) only chooses ordering + selection; deterministic code
validates the plan (dropping anything fabricated), **never drops a section**
(only reorders — omitted sections are appended in master order), keeps **at least**
``min_projects`` projects (config ``resume.min_projects``; padded by master order if
the model picks fewer), renders, asserts, and diffs. Compilation is via Tectonic.

Projects are chosen by JD relevance (e.g. a Java/Spring Boot role → CRUDbot).
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
    "MIN_PROJECTS",
]

MIN_PROJECTS = 2  # default project floor (overridden by config.resume.min_projects)

_INPUT_RE = re.compile(r"\\input\{sections/([^}]+?)\.tex\}")
_PROJECT_INPUT_RE = re.compile(r"\\input\{sections/projects/([^}]+?)\.tex\}")

_WORD_RE = re.compile(r"[A-Za-z]+")
_NUM_RE = re.compile(r"\d[\d,]*")
_LATEX_CMD_RE = re.compile(r"\\[A-Za-z]+")


def _norm(word: str) -> str:
    """Lowercase + strip a common plural/tense suffix so 'containers' matches 'container'."""
    w = word.lower()
    for suf in ("ing", "ed", "s", "es", "d"):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            return w[: -len(suf)]
    return w


# Function words + generic résumé verbs/adjectives. These never trip the fact guard — only
# content words (nouns, tools, skills, numbers) must trace to the candidate's own data. NOTE:
# normalized with the SAME _norm as tokens, so e.g. "managed"->"manag" matches at compare time.
STOPWORDS: frozenset[str] = frozenset(
    _norm(w)
    for w in """
    a an the and or but for to of in on at by with from into as is are was were be been being
    this that these those it its their our your his her my we you they i he she them us
    using used use via per across over under between within without about above below
    built build building designed design develop developed developing led lead leading
    created create creating made make making implemented implement implementing
    integrated integrate integrating improved improve improving managed manage managing
    enabled enable enabling added add adding set setting up out leveraged leverage leveraging
    deployed deploy deploying maintained maintain scaling scaled scalable robust custom
    real time end full multi high low new own based around alongside top layer layers
    work working hands on while which who whose where when then so than more most less
    """.split()
)


def _tokens(text: str) -> set[str]:
    """Normalized content-word tokens from prose or LaTeX (commands/markup stripped)."""
    stripped = _LATEX_CMD_RE.sub(" ", text)
    return {_norm(w) for w in _WORD_RE.findall(stripped)}


def _numbers(text: str) -> set[str]:
    """Digit-runs (commas removed), e.g. '500K'->'500', '10,000'->'10000'."""
    return {m.replace(",", "") for m in _NUM_RE.findall(text)}


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
    "Return ONLY a JSON object: section_order (ALL the given section names in your chosen "
    "order — reorder for emphasis, do NOT omit any), selected_project_ids (the most relevant "
    "project ids, most relevant first), diff_narration (one short paragraph explaining the "
    "choices in plain language).\n\n"
    "## Available sections (default order)\n{sections}\n\n"
    "## Available projects (id: content)\n{projects}\n\n"
    "## Target job\nrequired_skills: {req}\npreferred_quals: {pref}\nseniority: {sen}\n"
    "\nUser feedback to incorporate (optional): {feedback}\n"
)


class ResumeTailor:
    """Plans, renders, fact-checks, diffs and compiles a JD-tailored resume."""

    def __init__(
        self,
        provider: _Provider,
        master: Master,
        *,
        min_projects: int = MIN_PROJECTS,
        fact_corpus: str = "",
        rephrase: bool = True,
    ) -> None:
        self._provider = provider
        self._master = master
        self._min_projects = min_projects
        self._rephrase = rephrase
        corpus = "\n".join(
            [s.content for s in master.sections.values()]
            + [p.content for p in master.projects]
            + [fact_corpus]
        )
        self._allowed_vocab = _tokens(corpus)
        self._master_numbers = _numbers(corpus)

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

        # Never DROP a section — only reorder. Keep the model's order for the sections it
        # listed, then append any it omitted in master order (so nothing is ever lost).
        master_order = list(self._master.section_order)
        valid_sections = set(master_order)
        order: list[str] = []
        seen_sections: set[str] = set()
        for s in d.get("section_order", []):
            if s in valid_sections and s not in seen_sections:
                order.append(s)
                seen_sections.add(s)
        order += [s for s in master_order if s not in seen_sections]

        # Projects: keep the model's relevant picks, then PAD up to the configured floor
        # (master order) so the resume always shows at least ``min_projects`` (or all, if
        # fewer exist). No upper cap — a strongly-relevant extra project is kept.
        valid_pids = [p.project_id for p in self._master.projects]
        pid_set = set(valid_pids)
        picks: list[str] = []
        seen_pids: set[str] = set()
        for p in d.get("selected_project_ids", []):
            if p in pid_set and p not in seen_pids:
                picks.append(p)
                seen_pids.add(p)
        floor = min(self._min_projects, len(valid_pids))
        for pid in valid_pids:
            if len(picks) >= floor:
                break
            if pid not in seen_pids:
                picks.append(pid)
                seen_pids.add(pid)

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

    def _guard_ok(self, original: str, reword: str) -> bool:
        """True if ``reword`` adds no fact: no number absent from ``original``, and every
        non-stopword word is in the allowed vocabulary (master + profile + known skills) OR in
        the original bullet itself (keeping an original word is always fine)."""
        if not _numbers(reword) <= _numbers(original):
            return False
        allowed = self._allowed_vocab | _tokens(original)
        content = {t for t in _tokens(reword) if t not in STOPWORDS}
        return content <= allowed

    def assert_no_new_facts(self, tailored_tex: str) -> None:
        """Raise if the rendered tex introduces a number not in the master or a content word
        outside the allowed vocabulary (master + profile + known skills). This is the
        deterministic expression of CLAUDE.md invariant 2 for the (possibly reworded) resume."""
        extra_numbers = _numbers(tailored_tex) - self._master_numbers
        bad_words = {
            t for t in _tokens(tailored_tex) if t not in STOPWORDS
        } - self._allowed_vocab
        if extra_numbers or bad_words:
            raise TailoringError(
                f"tailored resume adds facts not in master: "
                f"numbers={sorted(extra_numbers)[:3]} words={sorted(bad_words)[:3]}"
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
