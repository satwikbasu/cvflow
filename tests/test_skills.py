"""Deterministic candidate-skill coverage gate (must-have hard requirements)."""

from cvflow.discovery.skills import coverage_drop, load_skill_profile, uncovered_skills

HAVE = frozenset({"python", "java", "go", "docker", "kafka", "postgresql", "ci/cd"})
SYN = {"golang": "go", "postgres": "postgresql", "apache kafka": "kafka"}


def test_uncovered_uses_synonyms_and_normalizes():
    # golang->go (have), Postgres->postgresql (have), python (have) -> all covered
    assert uncovered_skills(["Golang", " Postgres ", "PYTHON"], HAVE, SYN) == []
    # solidity / k8s not in profile -> reported missing (normalized)
    assert uncovered_skills(["python", "Solidity", "k8s"], HAVE, SYN) == ["solidity", "k8s"]


def test_empty_must_have_never_drops():
    assert coverage_drop([], HAVE, SYN) == (False, [])


def test_drops_when_majority_missing():
    # llm + openai missing, python present -> 2/3 missing > 50% -> drop
    drop, missing = coverage_drop(["llm", "openai", "python"], HAVE, SYN)
    assert drop is True
    assert set(missing) == {"llm", "openai"}


def test_keeps_when_minority_missing():
    # k8s + terraform missing of 5 -> 2/5 = 40% -> keep
    drop, missing = coverage_drop(
        ["java", "python", "go", "kubernetes", "terraform"], HAVE, SYN
    )
    assert drop is False
    assert set(missing) == {"kubernetes", "terraform"}


def test_exactly_half_missing_is_kept():
    # 1 of 2 missing = 50%, not > 50% -> keep (threshold is strict majority)
    drop, _ = coverage_drop(["python", "rust"], HAVE, SYN)
    assert drop is False


def test_load_skill_profile(tmp_path):
    p = tmp_path / "candidate_skills.yaml"
    p.write_text("skills: [Python, Golang]\nsynonyms: {golang: go}\n")
    have, syn = load_skill_profile(p)
    assert have == frozenset({"python", "golang"})
    assert syn == {"golang": "go"}


def test_load_missing_file_returns_empty(tmp_path):
    have, syn = load_skill_profile(tmp_path / "nope.yaml")
    assert have == frozenset()
    assert syn == {}
