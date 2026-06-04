from cvflow.essays import EssayAnswer, compose_answer


class _KB:
    """Minimal knowledge stub exposing the accessors compose_answer uses."""

    def __init__(self, docs):
        self._docs = docs

    def doc_keys(self):
        return list(self._docs)

    def full_context(self):
        return "\n\n".join(f"## {k}\n{v}" for k, v in self._docs.items())


def test_grounded_answer_cites_real_doc_keys():
    kb = _KB({"experience": "Built a Django payments service handling 10k req/day."})

    class _P:
        def generate(self, prompt: str) -> str:
            return '{"answer": "I built a Django payments service.", "citations": ["experience"]}'

    ans = compose_answer("Describe a backend project.", kb, provider=_P())
    assert isinstance(ans, EssayAnswer)
    assert ans.grounded is True
    assert ans.needs_clarification is False
    assert ans.citations == ["experience"]
    assert ans.text


def test_ungroundable_question_triggers_clarification_not_a_guess():
    kb = _KB({"experience": "Backend engineer."})

    class _P:
        def generate(self, prompt: str) -> str:
            return '{"needs_clarification": true, "missing": "expected salary"}'

    ans = compose_answer("What salary do you expect?", kb, provider=_P())
    assert ans.needs_clarification is True
    assert ans.text is None
    assert ans.clarification and "salary" in ans.clarification.lower()


def test_citation_to_nonexistent_doc_is_treated_as_ungrounded():
    kb = _KB({"experience": "Backend engineer."})

    class _P:
        def generate(self, prompt: str) -> str:
            return '{"answer": "I won a Nobel prize.", "citations": ["awards"]}'

    ans = compose_answer("Tell us an achievement.", kb, provider=_P())
    assert ans.grounded is False
    assert ans.needs_clarification is True
    assert ans.text is None
