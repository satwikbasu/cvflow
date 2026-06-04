"""Tests for resume tailoring: parse, plan, render, no-new-facts, diff, compile (Phase 6).

LLM is fully mocked. The compile test skips when `tectonic` is not installed.
"""

import json
import shutil
from pathlib import Path

import pytest

from cvflow.analysis import JDAnalysis
from cvflow.resume import (
    Master,
    Project,
    ResumeTailor,
    Section,
    TailoringError,
    TailoringPlan,
    parse_master,
)

# --- parse_master ---


def test_parse_master_returns_section_order_and_projects(tmp_path: Path) -> None:
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


# --- shared fixtures for plan/render/diff ---


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


# --- plan ---


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
    assert plan.section_order == ["skills", "experience", "projects"]
    assert plan.selected_project_ids == ["crudbot", "ipsec-dashboard"]
    assert "Java" in tailor._provider.prompts[0]  # type: ignore[attr-defined]


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


# --- render + no-new-facts ---


def test_render_reorders_sections_and_selects_projects() -> None:
    tailor = ResumeTailor(_FakeProvider("{}"), _master())
    plan = TailoringPlan(
        section_order=["skills", "projects", "experience"],
        selected_project_ids=["crudbot"],
        diff_narration="",
    )
    tex = tailor.render(plan)
    assert tex.index("SK") < tex.index("EXP")
    assert "CRUD" in tex  # selected project rendered
    assert "IPSEC" not in tex  # unselected project omitted


def test_assert_no_new_facts_passes_for_subset_and_fails_for_addition() -> None:
    tailor = ResumeTailor(_FakeProvider("{}"), _master())
    ok_plan = TailoringPlan(["experience"], [], "")
    tailor.assert_no_new_facts(tailor.render(ok_plan))  # no raise
    with pytest.raises(TailoringError):
        tailor.assert_no_new_facts("EXP\n\\resumeItem{Fabricated 10 years at Google}")


# --- diff + compile ---


def test_diff_describes_reorder_and_project_selection() -> None:
    tailor = ResumeTailor(_FakeProvider("{}"), _master())
    plan = TailoringPlan(
        ["skills", "experience", "projects"], ["crudbot"], "Led with skills."
    )
    diff = tailor.diff(plan)
    assert "skills" in diff.lower()
    assert "crudbot" in diff.lower()
    assert "Led with skills." in diff


@pytest.mark.skipif(shutil.which("tectonic") is None, reason="tectonic not installed")
def test_compile_real_master_produces_pdf(tmp_path: Path) -> None:
    repo_resume = Path(__file__).resolve().parents[1] / "resume"
    master = parse_master(repo_resume)
    tailor = ResumeTailor(_FakeProvider("{}"), master)
    pdf = tailor.compile_master(tmp_path)
    assert pdf.exists() and pdf.stat().st_size > 0
