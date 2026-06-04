"""Tests for JD analysis: fetch, HTML-strip, extraction, persistence (Phase 5).

Fully mocked — no network and no live LLM.
"""

import json as _json

import pytest

from cvflow.analysis import (
    JDAnalysis,
    JDAnalysisError,
    JDAnalyzer,
    JDFetchError,
    fetch_jd,
)
from cvflow.storage import ApplicationStore, UnknownJob

# --- JDAnalysis ---


def test_jdanalysis_json_round_trip() -> None:
    a = JDAnalysis(
        required_skills=["Go", "Kubernetes"],
        preferred_quals=["AWS"],
        seniority="senior",
        tone="fast-paced startup",
        applicant_instructions=["include the word pineapple"],
    )
    assert JDAnalysis.from_json(a.to_json()) == a


# --- fetch_jd ---


def test_fetch_jd_strips_html_and_drops_script() -> None:
    html = (
        "<html><head><style>.x{}</style></head>"
        "<body><h1>Senior Go Engineer</h1>"
        "<script>var x=1;</script><p>Build &amp; ship services.</p></body></html>"
    )
    text = fetch_jd("https://x/job/1", fetch_fn=lambda url: html)
    assert "Senior Go Engineer" in text
    assert "Build & ship services." in text
    assert "var x" not in text  # script body dropped
    assert ".x{}" not in text  # style body dropped


def test_fetch_jd_raises_on_empty_body() -> None:
    with pytest.raises(JDFetchError):
        fetch_jd("https://x/job/1", fetch_fn=lambda url: "   ")


# --- JDAnalyzer ---


class _FakeProvider:
    def __init__(self, payload: str) -> None:
        self.payload = payload
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.payload


def test_analyze_extracts_fields_and_embedded_instruction() -> None:
    payload = (
        "```json\n"
        + _json.dumps(
            {
                "required_skills": ["Go", "Kubernetes"],
                "preferred_quals": ["AWS certification"],
                "seniority": "senior",
                "tone": "fast-paced, collaborative",
                "applicant_instructions": [
                    "include the word pineapple in your cover letter"
                ],
            }
        )
        + "\n```"
    )
    provider = _FakeProvider(payload)
    analyzer = JDAnalyzer(provider)
    result = analyzer.analyze("Senior Go Engineer ... include the word pineapple ...")
    assert isinstance(result, JDAnalysis)
    assert result.required_skills == ["Go", "Kubernetes"]
    assert result.seniority == "senior"
    assert (
        "include the word pineapple in your cover letter"
        in result.applicant_instructions
    )
    assert "Senior Go Engineer" in provider.prompts[0]  # JD embedded in prompt


def test_analyze_raises_on_unparseable_reply() -> None:
    analyzer = JDAnalyzer(_FakeProvider("not json at all"))
    with pytest.raises(JDAnalysisError):
        analyzer.analyze("some jd text")


# --- persistence ---


def _analysis() -> JDAnalysis:
    return JDAnalysis(
        required_skills=["Go"],
        preferred_quals=[],
        seniority="mid",
        tone="calm",
        applicant_instructions=["mention pineapple"],
    )


def test_save_and_get_analysis_round_trip() -> None:
    store = ApplicationStore(":memory:")
    store.add("linkedin:1", "Co", "Role", "https://x/1")
    store.save_analysis("linkedin:1", _analysis())
    got = store.get_analysis("linkedin:1")
    assert got == _analysis()


def test_get_analysis_none_when_unanalyzed() -> None:
    store = ApplicationStore(":memory:")
    store.add("linkedin:1", "Co", "Role", "https://x/1")
    assert store.get_analysis("linkedin:1") is None


def test_save_analysis_unknown_job_raises() -> None:
    store = ApplicationStore(":memory:")
    with pytest.raises(UnknownJob):
        store.save_analysis("linkedin:404", _analysis())
