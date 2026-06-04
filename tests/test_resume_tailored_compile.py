"""Tests for ResumeTailor.tailored_document and compile_tailored (Phase 8, Task 2)."""

import shutil

import pytest

from cvflow.resume import ResumeTailor, TailoringPlan, parse_master

ROOT = "resume"


class _Stub:
    def generate(self, prompt: str) -> str:
        return "{}"


def test_tailored_document_reorders_and_selects_projects() -> None:
    master = parse_master(ROOT)
    tailor = ResumeTailor(_Stub(), master)
    order = list(reversed(master.section_order))
    pid = master.projects[0].project_id
    plan = TailoringPlan(section_order=order, selected_project_ids=[pid], diff_narration="")
    doc = tailor.tailored_document(plan)

    # Full compilable document structure
    assert "\\begin{document}" in doc and "\\end{document}" in doc

    # Selected project's input line is present
    assert pid in doc

    # Unselected project omitted
    if len(master.projects) > 1:
        assert master.projects[1].project_id not in doc

    # Section order follows the plan: the first \input line in the body
    # should reference the first section in the reversed order
    first_section = order[0]
    if first_section == "projects":
        assert f"\\input{{sections/projects/{pid}.tex}}" in doc
    else:
        assert f"\\input{{sections/{first_section}.tex}}" in doc


@pytest.mark.skipif(shutil.which("tectonic") is None, reason="tectonic not installed")
def test_compile_tailored_produces_pdf(tmp_path: pytest.TempPathFactory) -> None:
    master = parse_master(ROOT)
    tailor = ResumeTailor(_Stub(), master)
    plan = TailoringPlan(
        section_order=list(master.section_order),
        selected_project_ids=[master.projects[0].project_id],
        diff_narration="",
    )
    pdf = tailor.compile_tailored(plan, tmp_path)
    assert pdf.exists() and pdf.suffix == ".pdf"
