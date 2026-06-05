"""Pure field-resolution logic — deterministic-first, never guess. No browser."""

from cvflow.automation import FieldSpec, resolve_field
from cvflow.storage import FormFields


class _KB:
    def full_context(self): return "ctx"
    def doc_keys(self): return ["experience"]


def _ff(**vals): return FormFields(values=vals)


def _provider(answer):
    class P:
        def generate(self, prompt): return answer
    return P()


def test_lookup_hit_uses_form_fields_value():
    spec = FieldSpec(label="Full name", name="full_name", field_type="text",
                     options=[], required=True)
    r = resolve_field(spec, _ff(full_name="Ada Lovelace"), _KB(), provider=_provider("{}"))
    assert r.fill.value == "Ada Lovelace"
    assert r.fill.source == "form_fields"
    assert r.clarify is False


def test_textarea_uses_composed_answer():
    spec = FieldSpec(label="Why do you want to work here?", name="why_us",
                     field_type="textarea", options=[], required=False)
    grounded = '{"answer": "I love backends", "citations": ["experience"]}'
    r = resolve_field(spec, _ff(), _KB(), provider=_provider(grounded))
    assert r.fill.value == "I love backends"
    assert r.fill.source == "composed"


def test_required_unresolved_field_requests_clarification():
    spec = FieldSpec(label="Expected start date", name="start_date",
                     field_type="text", options=[], required=True)
    r = resolve_field(spec, _ff(), _KB(), provider=_provider("{}"))
    assert r.clarify is True
    assert r.question == "Expected start date"
    assert r.fill is None


def test_optional_unresolved_field_is_skipped_not_guessed():
    spec = FieldSpec(label="LinkedIn URL", name="linkedin", field_type="text",
                     options=[], required=False)
    r = resolve_field(spec, _ff(), _KB(), provider=_provider("{}"))
    assert r.clarify is False
    assert r.fill.source == "skipped"
    assert r.fill.value == ""


def test_ungroundable_textarea_required_clarifies():
    spec = FieldSpec(label="Describe a secret", name="secret",
                     field_type="textarea", options=[], required=True)
    ungroundable = '{"needs_clarification": true, "missing": "a secret"}'
    r = resolve_field(spec, _ff(), _KB(), provider=_provider(ungroundable))
    assert r.clarify is True
