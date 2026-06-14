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
    _resume_item_bodies,
    _substitute_bullets,
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


def test_plan_default_floor_is_two_projects() -> None:
    # Default min_projects (no override) is 2: a single model pick is padded to two.
    payload = json.dumps(
        {
            "section_order": ["experience", "projects", "skills"],
            "selected_project_ids": ["crudbot"],
            "diff_narration": "x",
        }
    )
    m = _master()
    m.projects.append(Project("third", "THIRD"))
    tailor = ResumeTailor(_FakeProvider(payload), m)  # default floor = 2
    plan = tailor.plan(_jd())
    assert len(plan.selected_project_ids) == 2
    assert plan.selected_project_ids[0] == "crudbot"


def test_plan_never_drops_sections_appends_omitted() -> None:
    # Model lists only one section; the rest must still appear (reorder, never drop).
    payload = json.dumps(
        {"section_order": ["skills"], "selected_project_ids": ["crudbot"], "diff_narration": "x"}
    )
    tailor = ResumeTailor(_FakeProvider(payload), _master())
    plan = tailor.plan(_jd())
    assert plan.section_order[0] == "skills"  # the model's emphasis leads
    assert set(plan.section_order) == {"experience", "projects", "skills"}  # nothing dropped


def test_plan_pads_projects_to_configured_count() -> None:
    # Model picks none; we pad to the configured count by master order.
    payload = json.dumps(
        {
            "section_order": ["experience", "projects", "skills"],
            "selected_project_ids": [],
            "diff_narration": "x",
        }
    )
    tailor = ResumeTailor(_FakeProvider(payload), _master(), min_projects=2)
    plan = tailor.plan(_jd())
    assert len(plan.selected_project_ids) == 2


def test_plan_pads_to_min_projects_floor_keeping_the_models_pick_first() -> None:
    # Model picks one; the floor pads up to min_projects, model's pick leading.
    payload = json.dumps(
        {
            "section_order": ["experience", "projects", "skills"],
            "selected_project_ids": ["crudbot"],
            "diff_narration": "x",
        }
    )
    tailor = ResumeTailor(_FakeProvider(payload), _master(), min_projects=2)
    plan = tailor.plan(_jd())
    assert len(plan.selected_project_ids) == 2
    assert plan.selected_project_ids[0] == "crudbot"


def test_plan_has_no_upper_cap_keeps_all_relevant_picks() -> None:
    # min_projects is a floor, not a cap — extra strongly-relevant picks are kept.
    payload = json.dumps(
        {
            "section_order": ["experience", "projects", "skills"],
            "selected_project_ids": ["ipsec-dashboard", "crudbot", "third"],
            "diff_narration": "x",
        }
    )
    m = _master()
    m.projects.append(Project("third", "THIRD"))
    tailor = ResumeTailor(_FakeProvider(payload), m, min_projects=2)
    plan = tailor.plan(_jd())
    assert plan.selected_project_ids == ["ipsec-dashboard", "crudbot", "third"]


def test_plan_drops_disabled_section_and_empties_projects() -> None:
    payload = json.dumps(
        {"section_order": ["experience", "projects", "skills"],
         "selected_project_ids": ["crudbot"], "diff_narration": "x"}
    )
    t = ResumeTailor(
        _FakeProvider(payload), _master(),
        disabled_sections=frozenset({"projects"}), rephrase=False,
    )
    plan = t.plan(_jd())
    assert "projects" not in plan.section_order
    assert set(plan.section_order) == {"experience", "skills"}
    assert plan.selected_project_ids == []


def test_plan_disabled_removed_but_enabled_never_dropped() -> None:
    payload = json.dumps(
        {"section_order": ["skills"], "selected_project_ids": [], "diff_narration": "x"}
    )
    t = ResumeTailor(
        _FakeProvider(payload), _master(),
        disabled_sections=frozenset({"experience"}), rephrase=False,
    )
    plan = t.plan(_jd())
    assert "experience" not in plan.section_order
    assert set(plan.section_order) == {"projects", "skills"}


# --- rephrase pass ---


def _order_json() -> str:
    return json.dumps(
        {"section_order": ["experience", "projects", "skills"],
         "selected_project_ids": ["crudbot"], "diff_narration": "x"}
    )


class _TwoCallProvider:
    """First generate() -> order JSON; second -> rephrase JSON."""
    def __init__(self, order_payload: str, rephrase_payload: str) -> None:
        self._payloads = [order_payload, rephrase_payload]
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self._payloads[min(len(self.prompts) - 1, len(self._payloads) - 1)]


def _exp_master() -> Master:
    return Master(
        root=Path("/nonexistent"),
        section_order=["experience", "projects"],
        sections={
            "experience": Section(
                "experience", "\\resumeItem{Built python flask APIs for tooling}"
            ),
            "projects": Section("projects", "P"),
        },
        projects=[Project("crudbot", "\\resumeItem{Built a CRUD backend with postgres}")],
    )


def test_plan_keeps_clean_reword_rejects_fabrication() -> None:
    rephrase = json.dumps({"rewrites": [
        "Built python flask REST APIs for tooling",
        "Built a CRUD backend with postgres at 1000 rps",
    ]})
    m = _exp_master()
    t = ResumeTailor(
        _TwoCallProvider(_order_json(), rephrase),
        m, fact_corpus="rest microservices", rephrase=True,
    )
    plan = t.plan(_jd())
    assert plan.rephrased["Built python flask APIs for tooling"] == \
        "Built python flask REST APIs for tooling"
    assert "Built a CRUD backend with postgres" not in plan.rephrased


def test_plan_rephrase_disabled_makes_no_second_call() -> None:
    m = _exp_master()
    p = _TwoCallProvider(_order_json(), "{}")
    t = ResumeTailor(p, m, rephrase=False)
    plan = t.plan(_jd())
    assert plan.rephrased == {}
    assert len(p.prompts) == 1


def test_plan_rephrase_bad_json_falls_back_silently() -> None:
    m = _exp_master()
    t = ResumeTailor(_TwoCallProvider(_order_json(), "not json"), m, rephrase=True)
    plan = t.plan(_jd())  # must not raise
    assert plan.rephrased == {}


def test_plan_rephrase_provider_error_degrades_to_reorder_only() -> None:
    # A rate limit (e.g. Cerebras HTTP 429 -> LLMError) on the rephrase call must NOT fail
    # tailoring — it degrades to the master wording (reorder-only), never raises.
    class _OrderThenRaise:
        def __init__(self) -> None:
            self.prompts: list[str] = []

        def generate(self, prompt: str) -> str:
            self.prompts.append(prompt)
            if len(self.prompts) == 1:
                return _order_json()
            raise RuntimeError("NIM POST ... -> HTTP 429: Too Many Requests")

    t = ResumeTailor(_OrderThenRaise(), _exp_master(), rephrase=True)
    plan = t.plan(_jd())  # must not raise despite the rephrase-call error
    assert plan.rephrased == {}
    assert plan.section_order  # ordering still happened


def test_render_applies_accepted_rewrites() -> None:
    m = _exp_master()
    plan = TailoringPlan(
        section_order=["experience", "projects"],
        selected_project_ids=["crudbot"],
        diff_narration="",
        rephrased={"Built python flask APIs for tooling": "Built python flask REST APIs"},
    )
    t = ResumeTailor(_FakeProvider("{}"), m)
    out = t.render(plan)
    assert "Built python flask REST APIs" in out
    assert "Built python flask APIs for tooling" not in out


def test_diff_shows_before_after_for_reworded_bullets() -> None:
    m = _exp_master()
    plan = TailoringPlan(
        section_order=["experience"], selected_project_ids=[], diff_narration="why",
        rephrased={"Built python flask APIs for tooling": "Built python flask REST APIs"},
    )
    out = ResumeTailor(_FakeProvider("{}"), m).diff(plan)
    assert "- Built python flask APIs for tooling" in out
    assert "+ Built python flask REST APIs" in out
    assert "Section order:" in out and "why" in out


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


def test_guard_accepts_in_vocab_reword() -> None:
    m = _master()
    t = ResumeTailor(_FakeProvider("{}"), m, fact_corpus="kubernetes containers postgres")
    # original mentions "container"; reword uses in-vocab words only, no new numbers
    assert t._guard_ok("Built container tooling", "Built Kubernetes containers") is True


def test_guard_rejects_new_number() -> None:
    t = ResumeTailor(_FakeProvider("{}"), _master(), fact_corpus="")
    assert t._guard_ok("Built APIs for tooling", "Built APIs handling 1000000 requests") is False


def test_guard_rejects_out_of_vocab_word() -> None:
    t = ResumeTailor(_FakeProvider("{}"), _master(), fact_corpus="python flask")
    # "kubernetes" is in neither the original, the master content, nor the fact_corpus
    assert t._guard_ok("Built python flask APIs", "Built kubernetes python flask APIs") is False


def test_guard_stopwords_and_plurals_do_not_trip() -> None:
    t = ResumeTailor(_FakeProvider("{}"), _master(), fact_corpus="container pipeline")
    # plural "containers"/"pipelines" normalize to the singular in vocab; stopwords ignored
    assert t._guard_ok("the container", "managed the containers and pipelines") is True


def test_assert_no_new_facts_vocab_based() -> None:
    # _master() section/project content: EXP, P, SK, IPSEC, CRUD
    t = ResumeTailor(_FakeProvider("{}"), _master(), fact_corpus="")
    t.assert_no_new_facts("EXP SK")  # all words in vocab -> ok
    with pytest.raises(TailoringError):
        t.assert_no_new_facts("EXP kubernetes")  # out-of-vocab word
    with pytest.raises(TailoringError):
        t.assert_no_new_facts("EXP 4242")  # number not in master


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


# --- _resume_item_bodies + _substitute_bullets ---


def test_resume_item_bodies_extracts_inner_text() -> None:
    tex = "x\n  \\resumeItem{Built APIs}\n  \\resumeItem{Designed \\textbf{Docker} swarm}\n"
    bodies = [b for _, _, b in _resume_item_bodies(tex)]
    assert bodies == ["Built APIs", "Designed \\textbf{Docker} swarm"]


def test_substitute_bullets_replaces_only_mapped_bodies() -> None:
    tex = "\\resumeItem{Built APIs}\n\\resumeItem{Kept as-is}\n"
    out = _substitute_bullets(tex, {"Built APIs": "Built REST microservices"})
    assert "\\resumeItem{Built REST microservices}" in out
    assert "\\resumeItem{Kept as-is}" in out  # unmapped bodies untouched
    assert "Built APIs" not in out
