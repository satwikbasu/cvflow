"""Preference-learning summary — proposes, never applies. No network."""

from cvflow.learning import summarize_decisions


class _Prov:
    def __init__(self, reply):
        self._reply = reply
        self.prompt = None

    def generate(self, prompt):
        self.prompt = prompt
        return self._reply


def test_summarize_builds_prompt_and_returns_suggestion():
    prov = _Prov("Consider avoiding service companies.")
    out = summarize_decisions(
        applied=[{"role": "DevOps", "company": "ProdCo"}],
        skipped=[{"role": "Support", "company": "ServiceCo"}],
        provider=prov, min_decisions=1,
    )
    assert "ServiceCo" in prov.prompt
    assert out == "Consider avoiding service companies."


def test_summarize_too_little_data_returns_sentinel():
    prov = _Prov("should not be called")
    out = summarize_decisions(applied=[], skipped=[], provider=prov, min_decisions=5)
    assert out is None
    assert prov.prompt is None
