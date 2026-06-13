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
    resolve_tailoring_analysis,
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


# --- resolve_tailoring_analysis ---


class _FakeStore:
    def __init__(self, *, crux=None, jd_text=None, app=None):
        self._crux = crux
        self._jd_text = jd_text
        self._app = app
        self.saved = None

    def get_crux(self, job_id):
        return _json.dumps(self._crux) if self._crux else None

    def get_jd_text(self, job_id):
        return self._jd_text

    def get(self, job_id):
        return self._app

    def save_analysis(self, job_id, analysis):
        self.saved = analysis


class _FakeAnalyzer:
    def __init__(self, result):
        self._result = result
        self.calls = 0

    def analyze(self, text):
        self.calls += 1
        return self._result


class _RaisingAnalyzer:
    def analyze(self, text):
        raise RuntimeError("LLM down")


def test_crux_path_maps_three_fields() -> None:
    store = _FakeStore(crux={
        "must_have_skills": ["go", "python"],
        "tech_stack": ["go", "python", "postgresql", "redis"],
        "seniority_signal": "junior",
        "applicant_instructions": "mention OSS",
    })
    ana = resolve_tailoring_analysis(
        "x:1", store=store, analyzer=_FakeAnalyzer(None), use_jd_analysis=False,
    )
    assert ana.required_skills == ["go", "python", "postgresql", "redis"]
    assert ana.preferred_quals == ["postgresql", "redis"]
    assert ana.seniority == "junior"
    assert ana.applicant_instructions == ["mention OSS"]


def test_analysis_path_uses_stored_text_without_fetch() -> None:
    expected = JDAnalysis(
        required_skills=["go"],
        preferred_quals=[],
        seniority="mid",
        tone="",
        applicant_instructions=[],
    )
    analyzer = _FakeAnalyzer(expected)
    store = _FakeStore(jd_text="full JD text", crux={"tech_stack": ["x"]})
    ana = resolve_tailoring_analysis(
        "x:1",
        store=store,
        analyzer=analyzer,
        use_jd_analysis=True,
        fetch_fn=lambda url: (_ for _ in ()).throw(AssertionError("should not fetch")),
    )
    assert analyzer.calls == 1 and ana is expected and store.saved is expected


def test_analysis_path_falls_back_to_crux_and_notifies_on_failure() -> None:
    store = _FakeStore(
        jd_text="text",
        crux={"tech_stack": ["go"], "seniority_signal": "junior"},
    )
    notices: list[str] = []
    ana = resolve_tailoring_analysis(
        "x:1",
        store=store,
        analyzer=_RaisingAnalyzer(),
        use_jd_analysis=True,
        notify=notices.append,
        fetch_fn=lambda url: "text",
    )
    assert ana.required_skills == ["go"]  # crux fallback
    assert notices and "falling back" in notices[0].lower()
