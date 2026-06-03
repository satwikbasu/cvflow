"""Tests for the knowledge-base loader (Phase 3)."""

import shutil
from pathlib import Path

import pytest

from cvflow.knowledge import KnowledgeBase


def _build_profile(tmp_path: Path) -> Path:
    prof = tmp_path / "profile"
    (prof / "projects").mkdir(parents=True)
    (prof / "skills.md").write_text("# Skills\n- Python (expert)\n")
    (prof / "experience.md").write_text("# Experience\n## Engineer — Acme\n")
    (prof / "essay_answers.md").write_text("# Essay Answers\nWhy: because.\n")
    (prof / "education.md").write_text("# Education\nB.Tech\n")
    (prof / "personality.md").write_text("# Personality\nCurious.\n")
    (prof / "projects" / "foo.md").write_text("# Foo\nA project.\n")
    # Files that MUST be excluded:
    (prof / "skills.example.md").write_text("# Template\n")
    (prof / "README.md").write_text("# guidance\n")
    (prof / "form_fields.json").write_text(
        '{"_comment": "x", "full_name": "Satwik", "salary_expectation": ""}'
    )
    return prof


def test_loads_expected_documents(tmp_path: Path) -> None:
    kb = KnowledgeBase.load(_build_profile(tmp_path))
    keys = set(kb.documents)
    expected = {"skills", "experience", "essay_answers", "education", "personality", "projects/foo"}
    assert expected <= keys
    # excluded:
    assert "skills.example" not in keys
    assert "README" not in keys


def test_convenience_accessors(tmp_path: Path) -> None:
    kb = KnowledgeBase.load(_build_profile(tmp_path))
    assert "Python (expert)" in kb.skills
    assert "Acme" in kb.experience
    assert "because" in kb.essays
    assert "B.Tech" in kb.education
    assert "Curious" in kb.personality


def test_project_docs_only_projects(tmp_path: Path) -> None:
    kb = KnowledgeBase.load(_build_profile(tmp_path))
    projects = kb.project_docs()
    assert set(projects) == {"projects/foo"}
    assert "A project." in projects["projects/foo"]


def test_missing_form_fields_lists_empties(tmp_path: Path) -> None:
    kb = KnowledgeBase.load(_build_profile(tmp_path))
    assert kb.missing_form_fields() == ["salary_expectation"]
    assert kb.form_fields.require("full_name") == "Satwik"


def test_full_context_contains_every_doc(tmp_path: Path) -> None:
    kb = KnowledgeBase.load(_build_profile(tmp_path))
    ctx = kb.full_context()
    for needle in ("Python (expert)", "Acme", "A project.", "B.Tech"):
        assert needle in ctx


def test_missing_doc_accessor_raises(tmp_path: Path) -> None:
    prof = tmp_path / "profile"
    prof.mkdir()
    (prof / "skills.md").write_text("# Skills\n")
    (prof / "form_fields.json").write_text('{"full_name": "x"}')
    kb = KnowledgeBase.load(prof)
    with pytest.raises(KeyError):
        _ = kb.experience


def test_loads_committed_example_templates(tmp_path: Path) -> None:
    src = Path("profile")
    if not (src / "skills.example.md").exists():
        pytest.skip("example templates not present")
    prof = tmp_path / "profile"
    (prof / "projects").mkdir(parents=True)
    # Copy each *.example.md to its real name so the loader ingests it.
    for ex in src.glob("*.example.md"):
        shutil.copy(ex, prof / ex.name.replace(".example", ""))
    (prof / "form_fields.json").write_text((src / "form_fields.example.json").read_text())
    kb = KnowledgeBase.load(prof)
    assert kb.skills
    assert kb.experience
    assert kb.essays
