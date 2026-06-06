# Phase 14 — Discovery Relevance v2 (Distill → Benchmark) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Phase-13's single-stage LLM ranker with a two-stage pipeline — Gemini distils each JD into a structured crux, then NIM/llama benchmarks all cruxes in one call per cohort — plus an M/N (stated-INR-pay / no-pay) split, fully config-driven scoring, and an analytics/learning-memory layer.

**Architecture:** scrape (descriptions on, India-pinned, +Naukri) → deterministic title prefilter → partition M/N by stated INR salary → cap each cohort to newest K → distil each JD to a cached `Crux` (Gemini, thinking off) → declarative `exclude_when` crux gates → benchmark+rank each cohort in one NIM call (hybrid: LLM `fit` + code `comp`, weights/roles from config) → two-section continuously-numbered digest. A `decisions` table + `analytics.summarize` + a weekly appended learning log give an auditable, dashboard-ready memory.

**Tech Stack:** Python 3.11+, python-jobspy 1.1.82, google-genai 2.8.0 (Gemini 2.5 Flash, `thinking_budget=0`, `response_schema`), NIM llama-3.3-70b (OpenAI-compat, `seed` + `response_format=json_object`), pydantic 2, SQLite, pytest. No new dependency.

**Specs:** `docs/superpowers/specs/2026-06-06-phase-14{a,b,c,d}-*.md`.

---

## File structure

- `src/cvflow/config.py` — **modify**: new `PreferencesConfig`/`DiscoveryConfig` fields + validation.
- `src/cvflow/discovery/__init__.py` — **modify**: `JobPosting.experience_range`; `_jobspy_search` params; remove single-stage ranker; rewrite `DiscoveryService.discover()` for the two-stage cohort pipeline; `BenchmarkedJob`.
- `src/cvflow/discovery/rules.py` — **create**: declarative `exclude_when` evaluator.
- `src/cvflow/discovery/distill.py` — **create**: `Crux`/`Salary` models + `Distiller` + `distill_all`.
- `src/cvflow/discovery/benchmark.py` — **create**: `comp_score`, `fit_scores`, `benchmark_cohort`, `build_fingerprint`, `BenchmarkedJob`.
- `src/cvflow/llm/__init__.py` — **modify**: `GeminiProvider.generate_structured`; `NimProvider.generate` extra params.
- `src/cvflow/storage/__init__.py` — **modify**: `job_cruxes` + `decisions` tables and accessors.
- `src/cvflow/analytics.py` — **create**: `record_decision`, `summarize`.
- `src/cvflow/gate/__init__.py` — **modify**: record each apply/skip decision (best-effort).
- `src/cvflow/cron.py` — **modify**: two-section digest + `digest_slots`; `learn` appends dated log.
- `src/cvflow/mcp/tools.py` — **modify**: `discover` maps `BenchmarkedJob`; wire new config into `build_tools`.
- Tests: `tests/test_config.py`, `tests/test_discovery.py`, `tests/test_rules.py`, `tests/test_distill.py`, `tests/test_benchmark.py`, `tests/test_storage.py`, `tests/test_analytics.py`, `tests/test_gate.py`, `tests/test_cron.py`, `tests/test_llm.py`.

---

# PART A — Config, sourcing, gates, partition

### Task A1: config — new preference/discovery fields + validation

**Files:** Modify `src/cvflow/config.py`; Test `tests/test_config.py`.

- [ ] **Step 1: Augment `VALID_YAML` + write the failing tests** (`tests/test_config.py`)

First, extend the module-level `VALID_YAML` literal directly (it is `textwrap.dedent`-ed, so use **2-space** indent under `preferences:` and `discovery:`). Add under `preferences:`:

```yaml
      yoe_buffer: 2
      top_ctc_lpa: 40
      fit_weight: 0.7
      comp_weight: 0.3
      prefer_roles:
        devops: 1.0
        backend: 0.8
      exclude_when:
        - {field: night_shift_only, equals: true}
        - {field: min_years_required, greater_than: 3}
```

and under `discovery:`:

```yaml
      country_indeed: india
      linkedin_fetch_description: true
      max_distill_per_cohort: 60
      top_n_per_cohort: 5
```

(Match the surrounding indentation in the file — the existing `preferences:`/`discovery:` keys you added in Phase 13 show the exact column to use.) Then append these tests, which load the now-augmented `VALID_YAML` (no fragile string surgery):

```python
def test_phase14_preferences_and_discovery_fields(tmp_path):
    from cvflow.config import load_config
    cfg = load_config(_write(tmp_path, VALID_YAML))
    assert cfg.preferences.yoe_buffer == 2
    assert cfg.preferences.top_ctc_lpa == 40
    assert cfg.preferences.fit_weight == 0.7
    assert cfg.preferences.comp_weight == 0.3
    assert cfg.preferences.prefer_roles == {"devops": 1.0, "backend": 0.8}
    assert cfg.preferences.exclude_when == [
        {"field": "night_shift_only", "equals": True},
        {"field": "min_years_required", "greater_than": 3},
    ]
    assert cfg.discovery.country_indeed == "india"
    assert cfg.discovery.linkedin_fetch_description is True
    assert cfg.discovery.max_distill_per_cohort == 60
    assert cfg.discovery.top_n_per_cohort == 5


def test_phase14_weights_must_sum_to_one(tmp_path):
    import pytest
    from cvflow.config import ConfigError, load_config
    bad = VALID_YAML.replace("fit_weight: 0.7", "fit_weight: 0.8")
    with pytest.raises(ConfigError) as exc:
        load_config(_write(tmp_path, bad))
    assert "fit_weight" in str(exc.value) and "sum" in str(exc.value).lower()
```

(The `test_phase14_weights_must_sum_to_one` replace is safe — `fit_weight: 0.7` is a unique substring. After this change `0.8 + 0.3 ≠ 1.0` → `ConfigError`.)

- [ ] **Step 2: Run** `pytest tests/test_config.py -v` → FAIL (new fields missing).

- [ ] **Step 3: Implement.** In `src/cvflow/config.py`:

Add helpers after `_get_str_list`:

```python
def _get_float(data: dict[str, Any], key: str, path: str) -> float:
    if key not in data:
        raise ConfigError(f"missing required key: {path}{key}")
    value = data[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"key {path}{key} must be a number")
    return float(value)


def _get_role_map(data: dict[str, Any], key: str, path: str) -> dict[str, float]:
    value = _get(data, key, dict, path)
    out: dict[str, float] = {}
    for k, v in value.items():
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ConfigError(f"key {path}{key}.{k} must be a number")
        out[str(k)] = float(v)
    return out


def _get_rule_list(data: dict[str, Any], key: str, path: str) -> list[dict[str, Any]]:
    value = _get(data, key, list, path)
    rules: list[dict[str, Any]] = []
    for i, item in enumerate(value):
        if not isinstance(item, dict) or "field" not in item:
            raise ConfigError(f"{path}{key}[{i}] must be a mapping with a 'field' key")
        rules.append(dict(item))
    return rules
```

Add fields to `PreferencesConfig` (after `exclude_night_shift_only`):

```python
    yoe_buffer: int
    top_ctc_lpa: int
    fit_weight: float
    comp_weight: float
    prefer_roles: dict[str, float]
    exclude_when: list[dict[str, Any]]
```

Add fields to `DiscoveryConfig` (after `top_n_to_present`):

```python
    country_indeed: str
    linkedin_fetch_description: bool
    max_distill_per_cohort: int
    top_n_per_cohort: int
```

In `load_config`, extend the `DiscoveryConfig(...)` construction:

```python
            top_n_to_present=_get_int(disc, "top_n_to_present", "discovery."),
            country_indeed=_get_str(disc, "country_indeed", "discovery."),
            linkedin_fetch_description=_get_bool(disc, "linkedin_fetch_description", "discovery."),
            max_distill_per_cohort=_get_int(disc, "max_distill_per_cohort", "discovery."),
            top_n_per_cohort=_get_int(disc, "top_n_per_cohort", "discovery."),
```

Extend the `PreferencesConfig(...)` construction:

```python
            exclude_night_shift_only=_get_bool(pref, "exclude_night_shift_only", "preferences."),
            yoe_buffer=_get_int(pref, "yoe_buffer", "preferences."),
            top_ctc_lpa=_get_int(pref, "top_ctc_lpa", "preferences."),
            fit_weight=_get_float(pref, "fit_weight", "preferences."),
            comp_weight=_get_float(pref, "comp_weight", "preferences."),
            prefer_roles=_get_role_map(pref, "prefer_roles", "preferences."),
            exclude_when=_get_rule_list(pref, "exclude_when", "preferences."),
```

Immediately after building the `Config(...)` object but before returning it, add the weight-sum check (place this as a guard right after the `pref = _section(...)` reads, using the parsed floats):

```python
    _fit_w = _get_float(pref, "fit_weight", "preferences.")
    _comp_w = _get_float(pref, "comp_weight", "preferences.")
    if abs(_fit_w + _comp_w - 1.0) > 1e-6:
        raise ConfigError(
            f"preferences.fit_weight + comp_weight must sum to 1.0, got {_fit_w + _comp_w}"
        )
```

(Place this block just after `daily_time` validation, alongside the other pre-construction checks.)

- [ ] **Step 4:** Add the same keys to the real `config.yaml` and `config.example.yaml` under `preferences:` and `discovery:` (values from the spec defaults: `yoe_buffer: 2`, `top_ctc_lpa: 40`, `fit_weight: 0.70`, `comp_weight: 0.30`, `prefer_roles` map per 14C §10, `exclude_when` list per 14A §8; `country_indeed: "india"`, `linkedin_fetch_description: true`, `max_distill_per_cohort: 60`, `top_n_per_cohort: 5`).

- [ ] **Step 5: Run** `pytest tests/test_config.py -v` → PASS. Then `ruff check . && mypy --strict src`.

- [ ] **Step 6: Commit** `git commit -am "feat(config): phase-14 preference/discovery fields (weights, prefer_roles, exclude_when, country_indeed)"`

---

### Task A2: `JobPosting.experience_range` + carry it through `normalize_rows`

**Files:** Modify `src/cvflow/discovery/__init__.py`; Test `tests/test_discovery.py`.

- [ ] **Step 1: Write the failing test** (append to `tests/test_discovery.py`)

```python
def test_normalize_carries_experience_range() -> None:
    p = normalize_rows([_row("1", experience_range="2-4 Yrs")])[0]
    assert p.experience_range == "2-4 Yrs"


def test_normalize_experience_range_absent_is_none() -> None:
    assert normalize_rows([_row("1")])[0].experience_range is None
```

- [ ] **Step 2: Run** `pytest tests/test_discovery.py::test_normalize_carries_experience_range -v` → FAIL.

- [ ] **Step 3: Implement.** Add field to `JobPosting` (after `currency`):

```python
    experience_range: str | None = None
```

In `normalize_rows`, add to the `JobPosting(...)` kwargs:

```python
                experience_range=_clean(row.get("experience_range")) or None,
```

- [ ] **Step 4: Run** `pytest tests/test_discovery.py -v` → PASS.
- [ ] **Step 5: Commit** `git commit -am "feat(discovery): carry Naukri experience_range on JobPosting"`

---

### Task A3: `_jobspy_search` — India pin, descriptions on, drop job_type

**Files:** Modify `src/cvflow/discovery/__init__.py`; Test `tests/test_discovery.py`.

- [ ] **Step 1: Write the failing test** (append to `tests/test_discovery.py`)

```python
def test_discovery_passes_country_and_fetch_description_to_search_fn() -> None:
    store = ApplicationStore(":memory:")
    captured = {}

    def search_fn(**kwargs):
        captured.update(kwargs)
        return []

    svc = DiscoveryService(
        store=store, ranker=_RecordingRanker(), search_fn=search_fn,
        search_terms=["x"], locations=["Remote"], sites=["indeed"],
        results_wanted_per_site=10, hours_old=72, top_n=5,
        throttle_seconds=0.0, sleep=lambda s: None,
        country_indeed="india", linkedin_fetch_description=True,
    )
    svc.discover()
    assert captured.get("country_indeed") == "india"
    assert captured.get("linkedin_fetch_description") is True
    assert "job_type" not in captured  # not sent (Indeed hours_old conflict)
```

(`_RecordingRanker` stays as the cohort-aware shim defined in Task C5; for now this test only checks the search call. If C5 isn't done yet, temporarily keep the existing `_RecordingRanker`.)

- [ ] **Step 2: Run** the test → FAIL.

- [ ] **Step 3: Implement.** Add to `DiscoveryService.__init__` keyword params (after `job_type`):

```python
        country_indeed: str = "usa",
        linkedin_fetch_description: bool = False,
```

Store them: `self._country_indeed = country_indeed`; `self._linkedin_fetch_description = linkedin_fetch_description`.

In `_gather_rows`, change the `self._search_fn(...)` call: remove `job_type=self._job_type` and add:

```python
                            hours_old=self._hours_old,
                            country_indeed=self._country_indeed,
                            linkedin_fetch_description=self._linkedin_fetch_description,
```

Update the default `_jobspy_search` signature + body:

```python
def _jobspy_search(
    *,
    site_name: list[str],
    search_term: str,
    location: str,
    results_wanted: int,
    hours_old: int,
    country_indeed: str = "usa",
    linkedin_fetch_description: bool = False,
) -> list[dict[str, Any]]:
    from jobspy import scrape_jobs

    df = scrape_jobs(
        site_name=site_name,
        search_term=search_term,
        location=location,
        results_wanted=results_wanted,
        hours_old=hours_old,
        country_indeed=country_indeed,
        linkedin_fetch_description=linkedin_fetch_description,
        enforce_annual_salary=True,
    )
    if df is None or df.empty:
        return []
    return list(df.to_dict("records"))
```

Remove the now-unused `job_type` plumbing from `DiscoveryService` (the `self._job_type` attr and its `__init__` param) — search the file and delete those two lines; mention any other reference in the commit if found.

- [ ] **Step 4: Run** `pytest tests/test_discovery.py -v` → PASS.
- [ ] **Step 5: Commit** `git commit -am "feat(discovery): pin Indeed to country_indeed, fetch LinkedIn descriptions, stop sending job_type"`

---

### Task A4: declarative `exclude_when` evaluator

**Files:** Create `src/cvflow/discovery/rules.py`; Test `tests/test_rules.py`.

- [ ] **Step 1: Write the failing test** (`tests/test_rules.py`)

```python
"""Declarative crux-field exclusion rules (Phase 14A)."""

from cvflow.discovery.rules import crux_excluded


CRUX = {
    "night_shift_only": False,
    "app_maintenance_focus": True,
    "min_years_required": 5,
    "red_flags": ["vague"],
}


def test_equals_rule_matches():
    excluded, reason = crux_excluded(CRUX, [{"field": "app_maintenance_focus", "equals": True}])
    assert excluded is True
    assert "app_maintenance_focus" in reason


def test_greater_than_rule_matches():
    excluded, _ = crux_excluded(CRUX, [{"field": "min_years_required", "greater_than": 3}])
    assert excluded is True


def test_contains_any_rule():
    excluded, _ = crux_excluded(
        {"red_flags": ["unpaid"]}, [{"field": "red_flags", "contains_any": ["unpaid", "scam"]}]
    )
    assert excluded is True


def test_no_rule_matches_returns_false():
    excluded, reason = crux_excluded(CRUX, [{"field": "night_shift_only", "equals": True}])
    assert excluded is False
    assert reason is None


def test_null_field_never_matches():
    # min_years_required unknown -> a greater_than rule must NOT drop it (never-fabricate)
    excluded, _ = crux_excluded(
        {"min_years_required": None}, [{"field": "min_years_required", "greater_than": 3}]
    )
    assert excluded is False
```

- [ ] **Step 2: Run** `pytest tests/test_rules.py -v` → FAIL (module missing).

- [ ] **Step 3: Implement** (`src/cvflow/discovery/rules.py`)

```python
"""Declarative, config-driven exclusion rules over crux fields (Phase 14A §5).

A job is dropped if it matches ANY rule. A rule whose field is null/absent NEVER
matches (invariant 2 — never drop on a fact we don't have). Operators are a small
fixed set; the RULES are config, the EVALUATOR is code.
"""

from __future__ import annotations

from typing import Any


def _rule_matches(value: Any, rule: dict[str, Any]) -> bool:
    if value is None:
        return False  # never drop on an absent fact
    if "equals" in rule:
        return bool(value == rule["equals"])
    if "greater_than" in rule:
        return isinstance(value, (int, float)) and value > rule["greater_than"]
    if "less_than" in rule:
        return isinstance(value, (int, float)) and value < rule["less_than"]
    if "contains_any" in rule:
        wanted = set(rule["contains_any"])
        return bool(wanted.intersection(value if isinstance(value, (list, set, tuple)) else [value]))
    return False


def crux_excluded(
    crux: dict[str, Any], rules: list[dict[str, Any]]
) -> tuple[bool, str | None]:
    """Return (True, reason) if the crux matches any exclusion rule, else (False, None)."""
    for rule in rules:
        field = rule.get("field")
        if field is None:
            continue
        if _rule_matches(crux.get(field), rule):
            op = next((k for k in ("equals", "greater_than", "less_than", "contains_any") if k in rule), "?")
            return True, f"{field} {op} {rule.get(op)}"
    return False, None
```

- [ ] **Step 4: Run** `pytest tests/test_rules.py -v` → PASS.
- [ ] **Step 5: Commit** `git commit -am "feat(discovery): declarative exclude_when rule evaluator"`

---

# PART B — Distillation (Gemini → crux)

### Task B1: `GeminiProvider.generate_structured`

**Files:** Modify `src/cvflow/llm/__init__.py`; Test `tests/test_llm.py`.

- [ ] **Step 1: Write the failing test** (append to `tests/test_llm.py`)

```python
def test_gemini_generate_structured_sets_config_and_returns_text():
    from cvflow.llm import GeminiProvider

    captured = {}

    class _Models:
        def generate_content(self, *, model, contents, config):
            captured["model"] = model
            captured["config"] = config
            class _R: text = '{"ok": true}'
            return _R()

    class _Client:
        models = _Models()

    prov = GeminiProvider(api_key="k", model="gemini-2.5-flash", max_requests_per_day=10,
                          client=_Client())

    class _Schema:  # stand-in pydantic-like schema object
        pass

    out = prov.generate_structured("prompt", schema=_Schema, seed=42, max_output_tokens=256)
    assert out == '{"ok": true}'
    cfg = captured["config"]
    assert cfg.temperature == 0
    assert cfg.seed == 42
    assert cfg.response_mime_type == "application/json"
    assert cfg.response_schema is _Schema
    assert cfg.thinking_config.thinking_budget == 0
```

- [ ] **Step 2: Run** `pytest tests/test_llm.py::test_gemini_generate_structured_sets_config_and_returns_text -v` → FAIL.

- [ ] **Step 3: Implement.** In `GeminiProvider`, add (after `generate`):

```python
    def generate_structured(
        self,
        prompt: str,
        *,
        schema: Any,
        seed: int = 0,
        max_output_tokens: int = 512,
    ) -> str:
        """Structured-JSON generation with thinking disabled (fast extraction).

        Returns the raw JSON string. Cache hits never consume budget (mirrors generate()).
        """
        from google.genai import types

        cache_key = f"{self._model}\x00structured\x00{seed}\x00{prompt}"
        if cache_key in self._cache:
            return self._cache[cache_key]
        self._spend_one()
        config = types.GenerateContentConfig(
            temperature=0,
            seed=seed,
            top_p=0.1,
            max_output_tokens=max_output_tokens,
            response_mime_type="application/json",
            response_schema=schema,
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        )
        response = self._get_client().models.generate_content(
            model=self._model, contents=prompt, config=config
        )
        text = response.text or ""
        self._cache[cache_key] = text
        return text
```

(`Any` is already imported in this module.)

- [ ] **Step 4: Run** `pytest tests/test_llm.py -v` → PASS.
- [ ] **Step 5: Commit** `git commit -am "feat(llm): GeminiProvider.generate_structured (thinking off, response_schema, seed)"`

---

### Task B2: `Crux`/`Salary` models + `Distiller`

**Files:** Create `src/cvflow/discovery/distill.py`; Test `tests/test_distill.py`.

- [ ] **Step 1: Write the failing test** (`tests/test_distill.py`)

```python
"""JD distillation -> structured crux (Phase 14B). Mocked Gemini; no network."""

import json

from cvflow.discovery import JobPosting
from cvflow.discovery.distill import Crux, Distiller


def _posting(jid="indeed:1", desc="Build CI/CD pipelines. 2+ years."):
    return JobPosting(job_id=jid, title="DevOps Engineer", company="Acme",
                      location="Remote", description=desc, url=f"https://x/{jid}",
                      site="indeed", date_posted="2026-06-05")


_REPLY = json.dumps({
    "job_id": "indeed:1", "role_family": "devops", "seniority_signal": "junior",
    "min_years_required": 2, "max_years_required": 4, "work_mode": "remote",
    "location_text": "Remote (IN)", "country": "india", "stated_salary": None,
    "tech_stack": ["docker", "k8s"], "night_shift_only": False,
    "app_maintenance_focus": False, "company_type": "product", "red_flags": [],
    "applicant_instructions": None, "one_line": "Build CI/CD for a product team",
})


class _Prov:
    def __init__(self, reply=_REPLY):
        self.reply = reply
        self.calls = []

    def generate_structured(self, prompt, *, schema, seed, max_output_tokens):
        self.calls.append({"prompt": prompt, "schema": schema, "seed": seed})
        return self.reply


def test_distiller_parses_crux_and_passes_jd_text():
    prov = _Prov()
    crux = Distiller(prov, seed=7).distill(_posting())
    assert isinstance(crux, Crux)
    assert crux.role_family == "devops"
    assert crux.min_years_required == 2
    assert crux.stated_salary is None
    assert crux.tech_stack == ["docker", "k8s"]
    assert "Build CI/CD pipelines" in prov.calls[0]["prompt"]  # JD text fed in
    assert prov.calls[0]["schema"] is Crux
    assert prov.calls[0]["seed"] == 7


def test_distiller_truncates_long_jd():
    prov = _Prov()
    long_desc = "x" * 50_000
    Distiller(prov, seed=1, max_jd_chars=1000).distill(_posting(desc=long_desc))
    assert len(prov.calls[0]["prompt"]) < 5000  # JD truncated


def test_distiller_raises_on_unparseable_reply():
    import pytest
    prov = _Prov(reply="not json")
    with pytest.raises(Exception):
        Distiller(prov, seed=1).distill(_posting())
```

- [ ] **Step 2: Run** `pytest tests/test_distill.py -v` → FAIL.

- [ ] **Step 3: Implement** (`src/cvflow/discovery/distill.py`)

```python
"""Stage-1 of discovery: distil one JD into a structured, enum-heavy Crux (Phase 14B).

Engine: Gemini 2.5 Flash via GeminiProvider.generate_structured (thinking off). The
distiller is told never to infer salary or YOE — absent facts become null/"unknown".
"""

from __future__ import annotations

import logging
from typing import Any, Literal, Protocol

from pydantic import BaseModel

from cvflow.discovery import JobPosting

logger = logging.getLogger("cvflow.discovery.distill")

__all__ = ["Crux", "Salary", "Distiller", "distill_all"]


class Salary(BaseModel):
    min_amount: float | None
    max_amount: float | None
    currency: str
    period: Literal["year", "month", "hour", "unknown"]


class Crux(BaseModel):
    job_id: str
    role_family: Literal["devops", "sre", "platform", "infra", "backend", "fullstack",
                         "frontend", "network", "sysadmin", "data", "security", "other"]
    seniority_signal: Literal["fresher", "junior", "mid", "senior", "lead", "unknown"]
    min_years_required: int | None
    max_years_required: int | None
    work_mode: Literal["remote", "hybrid", "onsite", "unknown"]
    location_text: str
    country: Literal["india", "other", "global-remote"]
    stated_salary: Salary | None
    tech_stack: list[str]
    night_shift_only: bool
    app_maintenance_focus: bool
    company_type: Literal["product", "service", "staffing", "unknown"]
    red_flags: list[Literal["unpaid", "commission_only", "vague", "scam"]]
    applicant_instructions: str | None
    one_line: str


_PREAMBLE = (
    "You extract structured facts from ONE job description into the provided schema.\n"
    "Rules:\n"
    "- Use ONLY facts present in the text. If a fact is not stated, output null "
    "(or \"unknown\" for enums).\n"
    "- NEVER infer or estimate salary or years of experience.\n"
    "- role_family / seniority_signal: classify from the DESCRIPTION, not just the title.\n"
    "- night_shift_only: true ONLY if night/rotational-on-call with no day option.\n"
    "- app_maintenance_focus: true ONLY if primarily long-term maintenance of a large "
    "existing application codebase.\n"
    "- company_type: product vs service/consultancy/staffing vs unknown.\n"
    "- tech_stack: up to 8 concrete tools, lowercased, normalized (kubernetes->k8s).\n"
    "- one_line: <=140 char neutral summary.\n"
    "- applicant_instructions: copy any explicit applicant directive verbatim, else null.\n"
    "- job_id MUST equal the provided job_id exactly.\n"
)


class _Provider(Protocol):
    def generate_structured(
        self, prompt: str, *, schema: Any, seed: int, max_output_tokens: int
    ) -> str: ...


class Distiller:
    def __init__(self, provider: _Provider, *, seed: int = 0, max_jd_chars: int = 12_000) -> None:
        self._provider = provider
        self._seed = seed
        self._max_jd_chars = max_jd_chars

    def _build_prompt(self, p: JobPosting) -> str:
        return (
            f"{_PREAMBLE}\n"
            f"job_id: {p.job_id}\n"
            f"title: {p.title}\ncompany: {p.company}\nlocation: {p.location}\n"
            f"experience_range_hint: {p.experience_range or ''}\n"
            f"--- JOB DESCRIPTION ---\n{p.description[: self._max_jd_chars]}\n"
        )

    def distill(self, posting: JobPosting) -> Crux:
        raw = self._provider.generate_structured(
            self._build_prompt(posting), schema=Crux, seed=self._seed, max_output_tokens=512
        )
        crux = Crux.model_validate_json(raw)
        # the model occasionally echoes a wrong job_id; pin it to the real one.
        return crux.model_copy(update={"job_id": posting.job_id})


def distill_all(
    postings: list[JobPosting], store: Any, distiller: Distiller
) -> list[Crux]:
    """Cache-aware distillation. One bad JD is logged + skipped, never aborts the run."""
    out: list[Crux] = []
    for p in postings:
        cached = store.get_crux(p.job_id)
        if cached is not None:
            out.append(Crux.model_validate_json(cached))
            continue
        try:
            crux = distiller.distill(p)
        except Exception as exc:  # noqa: BLE001 — isolate a bad/garbled/over-budget JD
            logger.warning("distill failed for %s: %s", p.job_id, exc)
            continue
        store.save_crux(p.job_id, crux.model_dump_json())
        out.append(crux)
    return out
```

- [ ] **Step 4: Run** `pytest tests/test_distill.py -v` → PASS.
- [ ] **Step 5: Commit** `git commit -am "feat(discovery): Crux schema + Distiller (Gemini structured extraction)"`

---

### Task B3: storage `job_cruxes` table + accessors

**Files:** Modify `src/cvflow/storage/__init__.py`; Test `tests/test_storage.py`.

- [ ] **Step 1: Write the failing test** (append to `tests/test_storage.py`)

```python
def test_crux_cache_roundtrip():
    from cvflow.storage import ApplicationStore
    s = ApplicationStore(":memory:")
    assert s.get_crux("indeed:1") is None
    s.save_crux("indeed:1", '{"job_id":"indeed:1"}')
    assert s.get_crux("indeed:1") == '{"job_id":"indeed:1"}'
    s.save_crux("indeed:1", '{"job_id":"indeed:1","v":2}')  # upsert
    assert s.get_crux("indeed:1") == '{"job_id":"indeed:1","v":2}'
```

- [ ] **Step 2: Run** `pytest tests/test_storage.py::test_crux_cache_roundtrip -v` → FAIL.

- [ ] **Step 3: Implement.** Add to `_SCHEMA` (inside the executescript string):

```python
CREATE TABLE IF NOT EXISTS job_cruxes (
    job_id       TEXT PRIMARY KEY,
    crux_json    TEXT NOT NULL,
    distilled_at TEXT NOT NULL
);
```

Add methods to `ApplicationStore` (near `save_analysis`):

```python
    def save_crux(self, job_id: str, crux_json: str) -> None:
        self._conn.execute(
            "INSERT INTO job_cruxes (job_id, crux_json, distilled_at) VALUES (?, ?, ?) "
            "ON CONFLICT(job_id) DO UPDATE SET crux_json = excluded.crux_json, "
            "distilled_at = excluded.distilled_at",
            (job_id, crux_json, _now()),
        )
        self._conn.commit()

    def get_crux(self, job_id: str) -> str | None:
        row = self._conn.execute(
            "SELECT crux_json FROM job_cruxes WHERE job_id = ?", (job_id,)
        ).fetchone()
        return row["crux_json"] if row is not None else None
```

- [ ] **Step 4: Run** `pytest tests/test_storage.py -v` → PASS.
- [ ] **Step 5: Commit** `git commit -am "feat(storage): job_cruxes cache table"`

---

# PART C — Benchmark, ranking, digest

### Task C1: `NimProvider.generate` extra generation params

**Files:** Modify `src/cvflow/llm/__init__.py`; Test `tests/test_llm.py`.

- [ ] **Step 1: Write the failing test** (append to `tests/test_llm.py`)

```python
def test_nim_generate_sends_seed_and_json_object():
    import json
    from cvflow.llm import NimProvider

    captured = {}

    def post_fn(url, headers, body):
        captured["body"] = json.loads(body)
        return json.dumps({"choices": [{"message": {"content": "[]"}}]})

    prov = NimProvider(base_url="https://x/v1", api_key="k", model="m",
                       max_requests_per_minute=40, post_fn=post_fn)
    prov.generate("p", temperature=0, seed=11, top_p=0.1, max_tokens=200, json_object=True)
    b = captured["body"]
    assert b["temperature"] == 0
    assert b["seed"] == 11
    assert b["top_p"] == 0.1
    assert b["max_tokens"] == 200
    assert b["response_format"] == {"type": "json_object"}


def test_nim_generate_default_no_extra_params():
    import json
    from cvflow.llm import NimProvider
    captured = {}

    def post_fn(url, headers, body):
        captured["body"] = json.loads(body)
        return json.dumps({"choices": [{"message": {"content": "ok"}}]})

    NimProvider(base_url="https://x/v1", api_key="k", model="m",
                max_requests_per_minute=40, post_fn=post_fn).generate("p")
    assert "seed" not in captured["body"]
    assert "response_format" not in captured["body"]
```

- [ ] **Step 2: Run** `pytest tests/test_llm.py::test_nim_generate_sends_seed_and_json_object -v` → FAIL.

- [ ] **Step 3: Implement.** Replace `NimProvider.generate`:

```python
    def generate(
        self,
        prompt: str,
        *,
        temperature: float | None = None,
        seed: int | None = None,
        top_p: float | None = None,
        max_tokens: int | None = None,
        json_object: bool = False,
    ) -> str:
        self._spend_one()
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
        }
        if temperature is not None:
            payload["temperature"] = temperature
        if seed is not None:
            payload["seed"] = seed
        if top_p is not None:
            payload["top_p"] = top_p
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if json_object:
            payload["response_format"] = {"type": "json_object"}
        raw = self._post(self._url, headers, json.dumps(payload))
        try:
            data = json.loads(raw)
            return str(data["choices"][0]["message"]["content"])
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"could not parse NIM reply: {exc}") from exc
```

(`Any` is already imported in this module.)

- [ ] **Step 4: Run** `pytest tests/test_llm.py -v` → PASS.
- [ ] **Step 5: Commit** `git commit -am "feat(llm): NimProvider.generate accepts temperature/seed/top_p/max_tokens/json_object"`

---

### Task C2: `comp_score` + `BenchmarkedJob` + `benchmark_cohort`

**Files:** Create `src/cvflow/discovery/benchmark.py`; Test `tests/test_benchmark.py`.

- [ ] **Step 1: Write the failing test** (`tests/test_benchmark.py`)

```python
"""Hybrid benchmark math (Phase 14C). Pure functions; no network."""

from cvflow.discovery import JobPosting
from cvflow.discovery.benchmark import BenchmarkedJob, FitResult, benchmark_cohort, comp_score


def test_comp_score_anchors():
    assert comp_score(7.0, 7, 40) == 0.0
    assert comp_score(40.0, 7, 40) == 1.0
    assert abs(comp_score(23.0, 7, 40) - 0.4848) < 0.01
    assert comp_score(3.0, 7, 40) == 0.0  # below floor clamps


def _job(jid, max_amount=None, currency=None):
    return JobPosting(job_id=jid, title="T", company="C", location="L", description="d",
                      url=f"https://{jid}", site="indeed", date_posted="2026-06-05",
                      max_amount=max_amount, currency=currency)


def test_benchmark_cohort_M_blends_fit_and_comp_sorted():
    jobs = {"a": _job("a", 2_300_000, "INR"), "b": _job("b", 700_000, "INR")}
    fits = {"a": FitResult(70, "ok", []), "b": FitResult(85, "great", [])}
    out = benchmark_cohort(jobs, fits, cohort="M", fit_weight=0.70, comp_weight=0.30,
                           min_lpa=7, top_lpa=40)
    # a: 0.7*0.70 + 0.3*0.4848 = 0.4900+0.1454=0.6354 -> 64 ; b: 0.7*0.85+0.3*0 = 60
    assert [j.job_id for j in out] == ["a", "b"]
    assert out[0].benchmark == 64 and out[1].benchmark == 60
    assert out[0].cohort == "M"


def test_benchmark_cohort_N_is_fit_only():
    jobs = {"a": _job("a"), "b": _job("b")}
    fits = {"a": FitResult(80, "x", ["PAY_UNKNOWN"]), "b": FitResult(45, "y", [])}
    out = benchmark_cohort(jobs, fits, cohort="N", fit_weight=0.70, comp_weight=0.30,
                           min_lpa=7, top_lpa=40)
    assert [j.job_id for j in out] == ["a", "b"]
    assert out[0].benchmark == 80 and out[1].benchmark == 45
    assert "PAY_UNKNOWN" in out[0].concerns
```

- [ ] **Step 2: Run** `pytest tests/test_benchmark.py -v` → FAIL.

- [ ] **Step 3: Implement** (`src/cvflow/discovery/benchmark.py`)

```python
"""Stage-2 of discovery: hybrid benchmark + ranking (Phase 14C).

LLM scores only `fit` (see fit_scores, Task C3). Code owns `comp` and the blend.
India-focused: no FX, no location score (deferred — see spec 14C §8).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from cvflow.discovery import JobPosting

__all__ = ["FitResult", "BenchmarkedJob", "comp_score", "benchmark_cohort"]


@dataclass(frozen=True)
class FitResult:
    fit_score: int
    fit_reason: str
    concerns: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class BenchmarkedJob:
    posting: JobPosting
    benchmark: int
    fit_score: int
    fit_reason: str
    concerns: list[str]
    cohort: str            # "M" | "N"
    ctc_lpa: float | None = None


def comp_score(ctc_lpa: float, min_lpa: int, top_lpa: int) -> float:
    """0 at the floor, 1 at the top anchor, clamped. INR only (no FX)."""
    span = max(top_lpa - min_lpa, 1)
    return max(0.0, min(1.0, (ctc_lpa - min_lpa) / span))


def _ctc_lpa(posting: JobPosting) -> float | None:
    amount = posting.max_amount if posting.max_amount is not None else posting.min_amount
    if amount is None or (posting.currency or "INR").upper() != "INR":
        return None
    return amount / 100_000


def benchmark_cohort(
    jobs: dict[str, JobPosting],
    fits: dict[str, FitResult],
    *,
    cohort: str,
    fit_weight: float,
    comp_weight: float,
    min_lpa: int,
    top_lpa: int,
) -> list[BenchmarkedJob]:
    """Score + sort one cohort. M blends fit+comp; N is fit only."""
    out: list[BenchmarkedJob] = []
    for job_id, posting in jobs.items():
        fit = fits.get(job_id, FitResult(0, "(no fit score)", ["RANKING_DEGRADED"]))
        lpa = _ctc_lpa(posting)
        if cohort == "M" and lpa is not None:
            c = comp_score(lpa, min_lpa, top_lpa)
            score = round(100 * (fit_weight * fit.fit_score / 100 + comp_weight * c))
        else:
            score = round(100 * (fit.fit_score / 100))
        out.append(
            BenchmarkedJob(
                posting=posting, benchmark=score, fit_score=fit.fit_score,
                fit_reason=fit.fit_reason, concerns=list(fit.concerns), cohort=cohort,
                ctc_lpa=lpa,
            )
        )
    out.sort(key=lambda j: j.benchmark, reverse=True)
    return out
```

- [ ] **Step 4: Run** `pytest tests/test_benchmark.py -v` → PASS.
- [ ] **Step 5: Commit** `git commit -am "feat(discovery): comp_score + benchmark_cohort (hybrid M/N scoring)"`

---

### Task C3: `fit_scores` (one NIM call, prefer_roles rendered) + `build_fingerprint`

**Files:** Modify `src/cvflow/discovery/benchmark.py`; Test `tests/test_benchmark.py`.

- [ ] **Step 1: Write the failing test** (append to `tests/test_benchmark.py`)

```python
def test_build_fingerprint_includes_prefs_and_roles():
    from cvflow.discovery.benchmark import build_fingerprint
    fp = build_fingerprint(prefs_text="HARD: yoe<=1", prefer_roles={"devops": 1.0, "frontend": 0.3})
    assert "yoe<=1" in fp
    assert "devops" in fp and "1.0" in fp


def test_fit_scores_parses_and_renders_prefer_roles():
    import json
    from cvflow.discovery.distill import Crux
    from cvflow.discovery.benchmark import fit_scores

    captured = {}

    class _Prov:
        def generate(self, prompt, **kw):
            captured["prompt"] = prompt
            captured["kw"] = kw
            return json.dumps([
                {"job_id": "indeed:1", "fit_score": 88, "fit_reason": "infra match",
                 "concern_codes": ["SERVICE_COMPANY"]},
                {"job_id": "ghost", "fit_score": 50, "fit_reason": "x", "concern_codes": []},
            ])

    crux = Crux(job_id="indeed:1", role_family="devops", seniority_signal="junior",
                min_years_required=2, max_years_required=4, work_mode="remote",
                location_text="Remote", country="india", stated_salary=None,
                tech_stack=["docker"], night_shift_only=False, app_maintenance_focus=False,
                company_type="service", red_flags=[], applicant_instructions=None,
                one_line="x")
    out = fit_scores([crux], fingerprint="FP", prefer_roles={"devops": 1.0}, provider=_Prov())
    assert out["indeed:1"].fit_score == 88
    assert out["indeed:1"].concerns == ["SERVICE_COMPANY"]
    assert "ghost" not in out                       # unknown job_id dropped
    assert "devops" in captured["prompt"]            # prefer_roles rendered
    assert captured["kw"]["json_object"] is True and captured["kw"]["seed"] is not None


def test_fit_scores_degrades_on_provider_error():
    from cvflow.discovery.distill import Crux
    from cvflow.discovery.benchmark import fit_scores

    class _Boom:
        def generate(self, prompt, **kw):
            raise RuntimeError("nim down")

    crux = Crux(job_id="indeed:1", role_family="devops", seniority_signal="junior",
                min_years_required=1, max_years_required=2, work_mode="remote",
                location_text="Remote", country="india", stated_salary=None, tech_stack=[],
                night_shift_only=False, app_maintenance_focus=False, company_type="product",
                red_flags=[], applicant_instructions=None, one_line="x")
    out = fit_scores([crux], fingerprint="FP", prefer_roles={}, provider=_Boom())
    assert out["indeed:1"].fit_score == 0
    assert "RANKING_DEGRADED" in out["indeed:1"].concerns
```

- [ ] **Step 2: Run** `pytest tests/test_benchmark.py::test_fit_scores_parses_and_renders_prefer_roles -v` → FAIL.

- [ ] **Step 3: Implement.** Add to `src/cvflow/discovery/benchmark.py`:

Add imports at top: `import json`, `import logging`, `from typing import Any, Protocol`. Add `logger = logging.getLogger("cvflow.discovery.benchmark")`. Extend `__all__` with `"fit_scores"`, `"build_fingerprint"`, `"FIT_SEED"`. Add `FIT_SEED = 1409`.

```python
class _Provider(Protocol):
    def generate(self, prompt: str, **kwargs: Any) -> str: ...


def build_fingerprint(*, prefs_text: str, prefer_roles: dict[str, float]) -> str:
    roles = ", ".join(f"{k}={v}" for k, v in sorted(prefer_roles.items()))
    return f"{prefs_text}\nPreferred role families (weight): {roles}\n"


_FIT_PREAMBLE = (
    "You score job FIT (0-100) for a candidate from compact job 'cruxes'.\n"
    "Rubric: 90-100 role weight>=0.8 AND stack overlaps core tools AND seniority "
    "fresher/junior/mid; 70-89 weight>=0.5 or partial stack; 40-69 weight 0.2-0.5 or "
    "little overlap; 0-39 weight<0.2 or unrelated. Modifiers: -10 service/staffing "
    "company; +5 modern infra stack (docker/k8s/ci-cd/cloud). Clamp 0-100.\n"
    'Return ONLY a JSON array of {"job_id","fit_score","fit_reason"(<=120 chars),'
    '"concern_codes"(subset of STACK_MISMATCH,SERVICE_COMPANY,SENIORITY_BORDERLINE,'
    "ROLE_ADJACENT)}. Use only the given job_ids.\n"
)


def _fit_view(crux: Any) -> dict[str, Any]:
    return {
        "job_id": crux.job_id, "role_family": crux.role_family,
        "seniority_signal": crux.seniority_signal, "tech_stack": crux.tech_stack,
        "work_mode": crux.work_mode, "country": crux.country,
        "company_type": crux.company_type, "one_line": crux.one_line,
    }


def fit_scores(
    cruxes: list[Any],
    *,
    fingerprint: str,
    prefer_roles: dict[str, float],
    provider: _Provider,
) -> dict[str, FitResult]:
    """One NIM call over all cruxes. On failure, every job degrades to fit 0 + flag."""
    if not cruxes:
        return {}
    ids = {c.job_id for c in cruxes}
    jobs_json = json.dumps([_fit_view(c) for c in sorted(cruxes, key=lambda c: c.job_id)], indent=2)
    roles = ", ".join(f"{k}={v}" for k, v in sorted(prefer_roles.items()))
    prompt = (
        f"{_FIT_PREAMBLE}\nPreferred role families (weight): {roles}\n\n"
        f"## Candidate\n{fingerprint}\n\n## Jobs\n{jobs_json}\n"
    )
    try:
        raw = provider.generate(prompt, temperature=0, seed=FIT_SEED, top_p=0.1,
                                 max_tokens=min(4096, 60 * len(cruxes) + 200), json_object=True)
        # response_format=json_object may wrap the array in an object; accept either.
        parsed = json.loads(_unwrap_array(raw))
    except Exception as exc:  # noqa: BLE001 — degrade, never lose the cohort
        logger.warning("fit scoring failed (%s); degrading cohort to fit 0", exc)
        return {jid: FitResult(0, "(ranking unavailable)", ["RANKING_DEGRADED"]) for jid in ids}
    out: dict[str, FitResult] = {}
    for entry in parsed:
        jid = str(entry.get("job_id"))
        if jid not in ids:
            continue
        out[jid] = FitResult(
            fit_score=int(entry.get("fit_score", 0) or 0),
            fit_reason=str(entry.get("fit_reason", "")),
            concerns=[str(c) for c in entry.get("concern_codes", [])],
        )
    for jid in ids:  # any job the model omitted still gets a row
        out.setdefault(jid, FitResult(0, "(omitted by ranker)", ["RANKING_DEGRADED"]))
    return out


def _unwrap_array(raw: str) -> str:
    """json_object mode may return {"jobs":[...]} or {"results":[...]}; find the array."""
    raw = raw.strip()
    if raw.startswith("["):
        return raw
    obj = json.loads(raw)
    if isinstance(obj, list):
        return raw
    for v in obj.values():
        if isinstance(v, list):
            return json.dumps(v)
    return "[]"
```

- [ ] **Step 4: Run** `pytest tests/test_benchmark.py -v` → PASS.
- [ ] **Step 5: Commit** `git commit -am "feat(discovery): fit_scores (single NIM call, prefer_roles rendered, degrade-safe) + fingerprint"`

---

### Task C4: rewrite `DiscoveryService.discover()` for the two-stage cohort pipeline

**Files:** Modify `src/cvflow/discovery/__init__.py`; Test `tests/test_discovery.py`.

- [ ] **Step 1: Write the failing test** (append to `tests/test_discovery.py`)

```python
def test_discover_two_stage_partitions_and_benchmarks():
    import json
    from cvflow.storage import ApplicationStore

    store = ApplicationStore(":memory:")
    rows = [
        _row("1", title="DevOps Engineer", min_amount=2_000_000, max_amount=2_300_000,
             currency="INR", description="CI/CD pipelines, 2 years"),       # M
        _row("2", title="Platform Engineer", description="K8s platform, fresher"),  # N
    ]

    def _distill(prompt, *, schema, seed, max_output_tokens):
        # one crux per call, keyed off the job_id embedded in the prompt
        jid = "linkedin:1" if "linkedin:1" in prompt else "linkedin:2"
        return json.dumps({
            "job_id": jid, "role_family": "devops", "seniority_signal": "junior",
            "min_years_required": 2, "max_years_required": 3, "work_mode": "remote",
            "location_text": "Remote", "country": "india", "stated_salary": None,
            "tech_stack": ["k8s"], "night_shift_only": False, "app_maintenance_focus": False,
            "company_type": "product", "red_flags": [], "applicant_instructions": None,
            "one_line": "infra"})

    class _Gem:
        def generate_structured(self, prompt, *, schema, seed, max_output_tokens):
            return _distill(prompt, schema=schema, seed=seed, max_output_tokens=max_output_tokens)

    class _Nim:
        def generate(self, prompt, **kw):
            ids = [x for x in ("linkedin:1", "linkedin:2") if x in prompt]
            return json.dumps([{"job_id": i, "fit_score": 80, "fit_reason": "ok",
                                "concern_codes": []} for i in ids])

    svc = DiscoveryService(
        store=store, ranker=None, search_fn=lambda **k: rows,
        search_terms=["x"], locations=["Remote"], sites=["linkedin"],
        results_wanted_per_site=10, hours_old=72, top_n=5,
        throttle_seconds=0.0, sleep=lambda s: None,
        exclude_title_keywords=["senior"], min_ctc_lpa=7,
        gemini=_Gem(), brain=_Nim(), fingerprint="FP", prefer_roles={"devops": 1.0},
        exclude_when=[], fit_weight=0.70, comp_weight=0.30, top_ctc_lpa=40,
        max_distill_per_cohort=60, top_n_per_cohort=5,
    )
    result = svc.discover()
    m_ids = [j.posting.job_id for j in result["M"]]
    n_ids = [j.posting.job_id for j in result["N"]]
    assert m_ids == ["linkedin:1"]      # has INR salary
    assert n_ids == ["linkedin:2"]      # no salary
    assert result["M"][0].cohort == "M" and result["N"][0].cohort == "N"
    assert store.exists("linkedin:1") and store.exists("linkedin:2")  # persisted as discovered
    assert store.get_crux("linkedin:1") is not None                   # crux cached


def test_discover_drops_jobs_via_exclude_when():
    import json
    from cvflow.storage import ApplicationStore
    store = ApplicationStore(":memory:")
    rows = [_row("1", title="DevOps Engineer", description="night shift only role")]

    class _Gem:
        def generate_structured(self, prompt, *, schema, seed, max_output_tokens):
            return json.dumps({
                "job_id": "linkedin:1", "role_family": "devops", "seniority_signal": "junior",
                "min_years_required": 1, "max_years_required": 2, "work_mode": "onsite",
                "location_text": "X", "country": "india", "stated_salary": None,
                "tech_stack": [], "night_shift_only": True, "app_maintenance_focus": False,
                "company_type": "product", "red_flags": [], "applicant_instructions": None,
                "one_line": "x"})

    class _Nim:
        def generate(self, prompt, **kw):
            return "[]"

    svc = DiscoveryService(
        store=store, ranker=None, search_fn=lambda **k: rows,
        search_terms=["x"], locations=["Remote"], sites=["linkedin"],
        results_wanted_per_site=10, hours_old=72, top_n=5, throttle_seconds=0.0,
        sleep=lambda s: None, gemini=_Gem(), brain=_Nim(), fingerprint="FP",
        prefer_roles={}, exclude_when=[{"field": "night_shift_only", "equals": True}],
        fit_weight=0.70, comp_weight=0.30, top_ctc_lpa=40, max_distill_per_cohort=60,
        top_n_per_cohort=5,
    )
    result = svc.discover()
    assert result["M"] == [] and result["N"] == []  # dropped by exclude_when
```

- [ ] **Step 2: Run** `pytest tests/test_discovery.py::test_discover_two_stage_partitions_and_benchmarks -v` → FAIL.

- [ ] **Step 3: Implement.** In `src/cvflow/discovery/__init__.py`:

Add imports near the top (after existing imports):

```python
from cvflow.discovery.benchmark import (
    BenchmarkedJob,
    benchmark_cohort,
    fit_scores,
)
from cvflow.discovery.distill import Crux, Distiller, distill_all
from cvflow.discovery.rules import crux_excluded
```

Add the new constructor params to `DiscoveryService.__init__` (keyword, all defaulted so older tests still build it):

```python
        gemini: Any = None,
        brain: Any = None,
        fingerprint: str = "",
        prefer_roles: dict[str, float] | None = None,
        exclude_when: list[dict[str, Any]] | None = None,
        fit_weight: float = 0.70,
        comp_weight: float = 0.30,
        top_ctc_lpa: int = 40,
        max_distill_per_cohort: int = 60,
        top_n_per_cohort: int = 5,
        distill_seed: int = 73,
```

Store them in `__init__`:

```python
        self._gemini = gemini
        self._brain = brain
        self._fingerprint = fingerprint
        self._prefer_roles = prefer_roles or {}
        self._exclude_when = exclude_when or []
        self._fit_weight = fit_weight
        self._comp_weight = comp_weight
        self._top_ctc_lpa = top_ctc_lpa
        self._max_distill_per_cohort = max_distill_per_cohort
        self._top_n_per_cohort = top_n_per_cohort
        self._distill_seed = distill_seed
```

Add a helper + replace `discover()`:

```python
    @staticmethod
    def _has_inr_salary(p: JobPosting) -> bool:
        amount = p.max_amount if p.max_amount is not None else p.min_amount
        return amount is not None and (p.currency or "INR").upper() == "INR"

    def _benchmark_cohort(
        self, postings: list[JobPosting], cohort: str
    ) -> list[BenchmarkedJob]:
        if not postings:
            return []
        capped = sorted(postings, key=lambda p: p.date_posted, reverse=True)[
            : self._max_distill_per_cohort
        ]
        distiller = Distiller(self._gemini, seed=self._distill_seed)
        cruxes = distill_all(capped, self._store, distiller)
        kept_cruxes: list[Crux] = []
        for c in cruxes:
            excluded, reason = crux_excluded(c.model_dump(), self._exclude_when)
            if excluded:
                logger.info("exclude_when drop %s: %s", c.job_id, reason)
                continue
            kept_cruxes.append(c)
        by_id = {p.job_id: p for p in capped}
        jobs = {c.job_id: by_id[c.job_id] for c in kept_cruxes}
        fits = fit_scores(
            kept_cruxes, fingerprint=self._fingerprint,
            prefer_roles=self._prefer_roles, provider=self._brain,
        )
        ranked = benchmark_cohort(
            jobs, fits, cohort=cohort, fit_weight=self._fit_weight,
            comp_weight=self._comp_weight, min_lpa=self._min_ctc_lpa,
            top_lpa=self._top_ctc_lpa,
        )
        return ranked[: self._top_n_per_cohort]

    def discover(self) -> dict[str, list[BenchmarkedJob]]:
        postings = self._prefilter(normalize_rows(self._gather_rows()))
        candidates = [p for p in postings if not self._store.exists(p.job_id)]
        logger.info("discovery: %d new candidates after prefilter+dedup", len(candidates))
        m = [p for p in candidates if self._has_inr_salary(p)]
        n = [p for p in candidates if not self._has_inr_salary(p)]
        result = {"M": self._benchmark_cohort(m, "M"), "N": self._benchmark_cohort(n, "N")}
        for cohort in result.values():
            for bj in cohort:
                p = bj.posting
                if not self._store.exists(p.job_id):
                    self._store.add(p.job_id, p.company, p.title, p.url)
        return result
```

Delete the old single-stage body of `discover()` (the `try/except self._ranker.rank(...)` fallback block) and the now-unused `_min_ctc_lpa`-only path. Keep `_prefilter`, `_gather_rows`, `normalize_rows`. Remove `LLMRanker`, `RankedJob`, the `_Ranker` Protocol, and the `ranker` positional from `DiscoveryService` **only after Task C5 updates the callers** — for THIS task, keep `ranker` as an ignored optional param (`ranker: Any = None`) so the diff stays small; its removal is Task C6.

- [ ] **Step 4: Run** `pytest tests/test_discovery.py -v` → PASS (the old `_RecordingRanker`/`LLMRanker` tests from Phase 13 will now fail to match the new return type; **delete the obsolete Phase-13 ranker tests** — `test_ranker_*`, `test_discover_returns_ranked_order_truncated_to_top_n`, `test_discover_falls_back_to_unranked_when_ranking_fails`, `test_discover_persists_presented_jobs_as_discovered`, `test_prefilter_*` that assert on `RankedJob` — and keep `normalize_rows` + the new two-stage tests. Re-add a `_prefilter` unit test if the title/salary prefilter loses coverage).
- [ ] **Step 5: Commit** `git commit -am "feat(discovery): two-stage discover() — distill, exclude_when, benchmark M/N cohorts"`

---

### Task C5: two-section digest + `digest_slots`

**Files:** Modify `src/cvflow/cron.py`; Test `tests/test_cron.py`.

- [ ] **Step 1: Write the failing test** (append to `tests/test_cron.py`)

```python
def test_format_digest_two_sections_and_continuous_numbering():
    from cvflow.cron import format_digest
    from cvflow.discovery import JobPosting
    from cvflow.discovery.benchmark import BenchmarkedJob

    def _bj(jid, cohort, bench, lpa=None, concerns=None):
        p = JobPosting(job_id=jid, title="DevOps", company="Acme", location="Remote",
                       description="d", url=f"https://{jid}", site="indeed", date_posted="x")
        return BenchmarkedJob(posting=p, benchmark=bench, fit_score=bench,
                              fit_reason="infra fit", concerns=concerns or [], cohort=cohort,
                              ctc_lpa=lpa)

    result = {"M": [_bj("indeed:1", "M", 74, lpa=23.0)],
              "N": [_bj("indeed:2", "N", 80, concerns=["PAY_UNKNOWN"])]}
    text = format_digest(result)
    assert "With stated pay" in text and "Pay not stated" in text
    assert "1. " in text and "2. " in text         # continuous numbering across sections
    assert "23 LPA" in text and "74" in text
    assert "PAY_UNKNOWN" in text
    assert "/apply indeed:1" in text and "/apply indeed:2" in text


def test_run_job_discover_sets_slots_in_combined_order():
    from cvflow.cron import run_job
    from cvflow.discovery import JobPosting
    from cvflow.discovery.benchmark import BenchmarkedJob
    from cvflow.storage import ApplicationStore

    def _bj(jid, cohort):
        p = JobPosting(job_id=jid, title="T", company="C", location="L", description="d",
                       url=f"https://{jid}", site="indeed", date_posted="x")
        return BenchmarkedJob(posting=p, benchmark=70, fit_score=70, fit_reason="r",
                              concerns=[], cohort=cohort)

    store = ApplicationStore(":memory:")

    class _Disc:
        def discover(self):
            return {"M": [_bj("indeed:1", "M")], "N": [_bj("indeed:2", "N")]}

    run_job("discover", store=store, discovery=_Disc(), otp=None, notify=lambda m: None)
    assert store.digest_slots() == ["indeed:1", "indeed:2"]  # M first, then N
```

- [ ] **Step 2: Run** `pytest tests/test_cron.py::test_format_digest_two_sections_and_continuous_numbering -v` → FAIL.

- [ ] **Step 3: Implement.** Replace `format_digest` in `src/cvflow/cron.py`:

```python
def format_digest(result: dict[str, Any]) -> str:
    """Render the two-section digest (M = stated pay, N = no pay). Continuously numbered."""
    m, n = result.get("M", []), result.get("N", [])
    if not m and not n:
        return "No new jobs today."
    parts = ["🗞️ cvflow — new jobs today:\n"]
    idx = 1

    def _block(bj: Any, i: int) -> str:
        p = bj.posting
        company = p.company or "Unknown company"
        pay = f" · {round(bj.ctc_lpa)} LPA" if getattr(bj, "ctc_lpa", None) else ""
        concerns = f"  ⚠️ {'; '.join(bj.concerns)}\n" if bj.concerns else ""
        return (
            f"{i}. {p.title} @ {company}  (bench {bj.benchmark} · fit {bj.fit_score}{pay})\n"
            f"  {p.url}\n  {bj.fit_reason}\n{concerns}"
            f"  /apply {p.job_id} | /skip {p.job_id}\n"
        )

    if m:
        parts.append("💰 With stated pay (ranked by value)\n")
        for bj in m:
            parts.append(_block(bj, idx)); idx += 1
    if n:
        parts.append("📋 Pay not stated (ranked by fit)\n")
        for bj in n:
            parts.append(_block(bj, idx)); idx += 1
    parts.append("Reply: /apply 1 2 4  •  /skip 3  •  /apply all")
    return "\n".join(parts)
```

Update the `discover` branch in `run_job`:

```python
    if job == "discover":
        result = discovery.discover()
        ordered = [bj.posting.job_id for bj in result.get("M", []) + result.get("N", [])]
        store.set_digest_slots(ordered)
        notify(format_digest(result))
```

(Update the existing `test_run_job_discover_persists_digest_slots` / `test_run_job_discover_sends_digest` / `test_format_digest_*` Phase-13 tests in `tests/test_cron.py` to the new `{"M":..,"N":..}` shape, or delete the ones superseded by the two new tests above.)

- [ ] **Step 4: Run** `pytest tests/test_cron.py -v` → PASS.
- [ ] **Step 5: Commit** `git commit -am "feat(cron): two-section digest (M/N) with continuous numbering + slots"`

---

### Task C6: wire `build_tools` + `cron._build`; drop dead `LLMRanker`

**Files:** Modify `src/cvflow/mcp/tools.py`, `src/cvflow/cron.py`, `src/cvflow/discovery/__init__.py`.

- [ ] **Step 1:** In `src/cvflow/discovery/__init__.py`, remove the now-dead single-stage ranker: delete `class LLMRanker`, `class RankedJob`, the `_Ranker` Protocol, `format_preferences`, and the `ranker` param from `DiscoveryService.__init__`. Update `__all__` to drop `RankedJob`, `LLMRanker`, `format_preferences` and add `BenchmarkedJob`. Keep `JobPosting`, `normalize_rows`, `DiscoveryService`.

- [ ] **Step 2:** In `src/cvflow/mcp/tools.py` `discover()` method, map `BenchmarkedJob` and both cohorts:

```python
    def discover(self) -> list[dict[str, Any]]:
        """Run discovery and return ranked-job dicts (M cohort first, then N)."""
        result = self._discovery.discover()
        out: list[dict[str, Any]] = []
        for bj in result.get("M", []) + result.get("N", []):
            out.append({
                "job_id": bj.posting.job_id, "title": bj.posting.title,
                "company": bj.posting.company, "location": bj.posting.location,
                "url": bj.posting.url, "cohort": bj.cohort, "benchmark": bj.benchmark,
                "fit_score": bj.fit_score, "fit_reason": bj.fit_reason,
                "concerns": bj.concerns, "ctc_lpa": bj.ctc_lpa,
            })
        return out
```

- [ ] **Step 3:** In `build_tools` (`src/cvflow/mcp/tools.py`), replace the `LLMRanker`/`DiscoveryService` construction with the two-stage wiring:

```python
    from cvflow.discovery import DiscoveryService
    from cvflow.discovery.benchmark import build_fingerprint
    from cvflow.llm import NimProvider

    brain = NimProvider(
        base_url=config.llm.brain.base_url, api_key=config.llm.brain.api_key,
        model=config.llm.brain.model,
        max_requests_per_minute=config.llm.brain.max_requests_per_minute,
    )
    fingerprint = build_fingerprint(
        prefs_text=knowledge.full_context(), prefer_roles=config.preferences.prefer_roles
    )
    discovery = DiscoveryService(
        store, search_terms=config.discovery.search_terms,
        locations=config.discovery.locations, sites=config.discovery.sites,
        results_wanted_per_site=config.discovery.results_wanted_per_site,
        hours_old=config.discovery.hours_old, top_n=config.discovery.top_n_to_present,
        exclude_title_keywords=config.preferences.exclude_title_keywords,
        min_ctc_lpa=config.preferences.min_ctc_lpa,
        country_indeed=config.discovery.country_indeed,
        linkedin_fetch_description=config.discovery.linkedin_fetch_description,
        gemini=tailoring, brain=brain, fingerprint=fingerprint,
        prefer_roles=config.preferences.prefer_roles,
        exclude_when=config.preferences.exclude_when,
        fit_weight=config.preferences.fit_weight, comp_weight=config.preferences.comp_weight,
        top_ctc_lpa=config.preferences.top_ctc_lpa,
        max_distill_per_cohort=config.discovery.max_distill_per_cohort,
        top_n_per_cohort=config.discovery.top_n_per_cohort,
    )
    analyzer = JDAnalyzer(brain)
```

(`tailoring` is the `GeminiProvider` already built earlier in `build_tools`; reuse it for distillation. Remove the old `LLMRanker`/`format_preferences` import lines.)

- [ ] **Step 4:** In `cron._build` (`src/cvflow/cron.py`), mirror the same wiring (construct `GeminiProvider` for `gemini=`, build `fingerprint`, pass all the same kwargs). Remove the `LLMRanker`/`format_preferences` import there.

- [ ] **Step 5: Verify + commit**

Run: `pytest -q && ruff check . && mypy --strict src` → green. Then:
```bash
python -c "from cvflow.config import load_config; from cvflow.mcp.tools import build_tools, TOOL_NAMES; build_tools(load_config('config.yaml')); print('approve absent:', 'approve' not in TOOL_NAMES)"
git commit -am "feat(discovery): wire two-stage engine into build_tools + cron; drop dead LLMRanker"
```

---

# PART D — Analytics & learning memory

### Task D1: `decisions` table + accessors

**Files:** Modify `src/cvflow/storage/__init__.py`; Test `tests/test_storage.py`.

- [ ] **Step 1: Write the failing test** (append to `tests/test_storage.py`)

```python
def test_decisions_log_roundtrip():
    from cvflow.storage import ApplicationStore
    s = ApplicationStore(":memory:")
    s.add_decision(job_id="indeed:1", decision="apply", cohort="M", benchmark=80,
                   fit_score=85, role_family="devops", company="Acme",
                   company_type="product", ctc_lpa=18.0, concerns=["FX"])
    s.add_decision(job_id="indeed:2", decision="skip", cohort="N", benchmark=40,
                   fit_score=40, role_family="frontend", company="Svc",
                   company_type="service", ctc_lpa=None, concerns=[])
    rows = s.recent_decisions(10)
    assert len(rows) == 2
    assert rows[0]["decision"] in ("apply", "skip")
    apply_rows = [r for r in rows if r["decision"] == "apply"]
    assert apply_rows[0]["role_family"] == "devops"
    assert apply_rows[0]["ctc_lpa"] == 18.0
```

- [ ] **Step 2: Run** `pytest tests/test_storage.py::test_decisions_log_roundtrip -v` → FAIL.

- [ ] **Step 3: Implement.** Add to `_SCHEMA`:

```python
CREATE TABLE IF NOT EXISTS decisions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id       TEXT NOT NULL,
    decision     TEXT NOT NULL,
    decided_at   TEXT NOT NULL,
    cohort       TEXT,
    benchmark    INTEGER,
    fit_score    INTEGER,
    role_family  TEXT,
    company      TEXT,
    company_type TEXT,
    ctc_lpa      REAL,
    concerns     TEXT
);
```

Add methods to `ApplicationStore`:

```python
    def add_decision(
        self, *, job_id: str, decision: str, cohort: str | None = None,
        benchmark: int | None = None, fit_score: int | None = None,
        role_family: str | None = None, company: str | None = None,
        company_type: str | None = None, ctc_lpa: float | None = None,
        concerns: list[str] | None = None,
    ) -> None:
        self._conn.execute(
            "INSERT INTO decisions (job_id, decision, decided_at, cohort, benchmark, "
            "fit_score, role_family, company, company_type, ctc_lpa, concerns) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (job_id, decision, _now(), cohort, benchmark, fit_score, role_family,
             company, company_type, ctc_lpa, json.dumps(concerns or [])),
        )
        self._conn.commit()

    def recent_decisions(self, limit: int) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM decisions ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
```

(Add `from typing import Any` to the storage imports if not present.)

- [ ] **Step 4: Run** `pytest tests/test_storage.py -v` → PASS.
- [ ] **Step 5: Commit** `git commit -am "feat(storage): decisions log table + add/recent accessors"`

---

### Task D2: `analytics.summarize` + `record_decision`

**Files:** Create `src/cvflow/analytics.py`; Test `tests/test_analytics.py`.

- [ ] **Step 1: Write the failing test** (`tests/test_analytics.py`)

```python
"""Analytics rollups + decision recording (Phase 14D). No network."""

from cvflow.analytics import record_decision, summarize
from cvflow.discovery import JobPosting
from cvflow.discovery.benchmark import BenchmarkedJob
from cvflow.statemachine import Status
from cvflow.storage import ApplicationStore


def _bj(jid, role, ctype, bench, lpa=None, cohort="N"):
    p = JobPosting(job_id=jid, title="T", company=f"Co-{jid}", location="L", description="d",
                   url=f"https://{jid}", site="indeed", date_posted="x")
    return BenchmarkedJob(posting=p, benchmark=bench, fit_score=bench, fit_reason="r",
                          concerns=[], cohort=cohort, ctc_lpa=lpa)


def test_record_decision_pulls_context_from_benchmarked_job():
    store = ApplicationStore(":memory:")
    bj = _bj("indeed:1", "devops", "product", 80, lpa=18.0, cohort="M")
    record_decision(store, "apply", bj)
    row = store.recent_decisions(1)[0]
    assert row["decision"] == "apply" and row["role_family"] == "devops"
    assert row["cohort"] == "M" and row["ctc_lpa"] == 18.0


def test_summarize_apply_rate_by_role():
    store = ApplicationStore(":memory:")
    for jid, st in [("a", Status.APPLIED), ("b", Status.SKIPPED)]:
        store.add(f"indeed:{jid}", "Co", "Role", "https://x")
    record_decision(store, "apply", _bj("indeed:a", "devops", "product", 80))
    record_decision(store, "skip", _bj("indeed:b", "frontend", "service", 40))
    summary = summarize(store)
    assert summary["totals"]["apply"] == 1 and summary["totals"]["skip"] == 1
    assert summary["apply_rate_by_role"]["devops"] == 1.0
    assert summary["apply_rate_by_role"]["frontend"] == 0.0
```

- [ ] **Step 2: Run** `pytest tests/test_analytics.py -v` → FAIL.

- [ ] **Step 3: Implement** (`src/cvflow/analytics.py`)

```python
"""Analytics rollups + decision recording (Phase 14D).

Pure read/write over the SQLite store — no model calls. This is the dashboard
payload and the input to the weekly learning summary.
"""

from __future__ import annotations

from collections import defaultdict
from statistics import median
from typing import Any


def record_decision(store: Any, decision: str, bj: Any) -> None:
    """Persist an apply/skip with the benchmarked-job context (best-effort)."""
    store.add_decision(
        job_id=bj.posting.job_id, decision=decision, cohort=bj.cohort,
        benchmark=bj.benchmark, fit_score=bj.fit_score, company=bj.posting.company,
        ctc_lpa=bj.ctc_lpa, concerns=list(bj.concerns),
        role_family=getattr(bj, "role_family", None),
        company_type=getattr(bj, "company_type", None),
    )


def summarize(store: Any) -> dict[str, Any]:
    """Aggregate the decisions log into a dashboard-ready dict."""
    rows = store.recent_decisions(10_000)
    totals: dict[str, int] = defaultdict(int)
    by_role: dict[str, list[int]] = defaultdict(list)
    applied_fit: list[int] = []
    skipped_fit: list[int] = []
    skipped_company: dict[str, int] = defaultdict(int)
    for r in rows:
        is_apply = r["decision"] == "apply"
        totals[r["decision"]] += 1
        if r.get("role_family"):
            by_role[r["role_family"]].append(1 if is_apply else 0)
        if r.get("fit_score") is not None:
            (applied_fit if is_apply else skipped_fit).append(r["fit_score"])
        if not is_apply and r.get("company"):
            skipped_company[r["company"]] += 1
    return {
        "totals": dict(totals),
        "apply_rate_by_role": {k: sum(v) / len(v) for k, v in by_role.items()},
        "median_fit_applied": median(applied_fit) if applied_fit else None,
        "median_fit_skipped": median(skipped_fit) if skipped_fit else None,
        "top_skipped_companies": sorted(
            skipped_company.items(), key=lambda kv: kv[1], reverse=True
        )[:10],
    }
```

(Note: `role_family`/`company_type` come through `getattr` because `BenchmarkedJob` doesn't carry them today; Task D3 enriches the call site with the crux. The test passes because those default to `None` and the role test sets them — adjust: in the test, the `_bj` helper has no role_family attr, so `summarize`'s `apply_rate_by_role` would be empty. **Fix the test/impl mismatch now:** have `record_decision` accept an explicit `role_family`/`company_type` override — see Step 4.)

- [ ] **Step 4: Reconcile signature.** Change `record_decision` to take optional explicit crux context so D3 can pass it and tests are deterministic:

```python
def record_decision(
    store: Any, decision: str, bj: Any, *,
    role_family: str | None = None, company_type: str | None = None,
) -> None:
    store.add_decision(
        job_id=bj.posting.job_id, decision=decision, cohort=bj.cohort,
        benchmark=bj.benchmark, fit_score=bj.fit_score, company=bj.posting.company,
        ctc_lpa=bj.ctc_lpa, concerns=list(bj.concerns),
        role_family=role_family, company_type=company_type,
    )
```

Update the first analytics test to pass `role_family=`/`company_type=` explicitly:
`record_decision(store, "apply", _bj(...), role_family="devops", company_type="product")` and likewise the skip. Re-run.

- [ ] **Step 5: Run** `pytest tests/test_analytics.py -v` → PASS.
- [ ] **Step 6: Commit** `git commit -am "feat(analytics): summarize() rollups + record_decision"`

---

### Task D3: gate records each apply/skip decision (best-effort)

**Files:** Modify `src/cvflow/gate/__init__.py`; Test `tests/test_gate.py`.

- [ ] **Step 1: Write the failing test** (append to `tests/test_gate.py`)

```python
def test_apply_records_a_decision():
    from cvflow.gate import handle_gate_command
    s = _pending_store()  # has indeed:a, indeed:b in pending_review + digest slots
    handle_gate_command(command="apply", args="1", user_id=1, authorized_user_id=1, store=s)
    rows = s.recent_decisions(10)
    assert any(r["job_id"] == "indeed:a" and r["decision"] == "apply" for r in rows)


def test_decision_recording_failure_does_not_block_gate(monkeypatch):
    from cvflow.gate import handle_gate_command
    from cvflow.statemachine import Status
    s = _pending_store()

    def boom(**kwargs):
        raise RuntimeError("log write failed")

    monkeypatch.setattr(s, "add_decision", boom)
    res = handle_gate_command(command="apply", args="1", user_id=1, authorized_user_id=1, store=s)
    assert res.handled is True
    assert s.get("indeed:a").status == Status.APPROVED  # gate still worked
```

- [ ] **Step 2: Run** `pytest tests/test_gate.py::test_apply_records_a_decision -v` → FAIL.

- [ ] **Step 3: Implement.** In `handle_gate_command`, inside the `for job_id in targets:` loop, after a successful `ok.append(job_id)`, record the decision best-effort. Add a small helper at module top:

```python
import logging
logger = logging.getLogger("cvflow.gate")
```

In the loop, replace `ok.append(job_id)` with:

```python
            ok.append(job_id)
            _record(store, command, job_id)
```

And add the helper (the store has the crux + last benchmark context; pull what's cheaply available):

```python
def _record(store: ApplicationStore, command: str, job_id: str) -> None:
    """Best-effort decision logging for analytics (Phase 14D). Never blocks the gate."""
    try:
        crux_json = store.get_crux(job_id)
        role_family = company_type = None
        if crux_json:
            import json
            c = json.loads(crux_json)
            role_family, company_type = c.get("role_family"), c.get("company_type")
        app = store.get(job_id)
        store.add_decision(
            job_id=job_id, decision=command, role_family=role_family,
            company_type=company_type, company=app.company if app else None,
        )
    except Exception as exc:  # noqa: BLE001 — analytics must never break approval (invariant 1)
        logger.warning("decision logging failed for %s: %s", job_id, exc)
```

(`command` is "apply" or "skip" — store it verbatim.)

- [ ] **Step 4: Run** `pytest tests/test_gate.py -v` → PASS.
- [ ] **Step 5: Commit** `git commit -am "feat(gate): record apply/skip decisions for analytics (best-effort, never blocks the gate)"`

---

### Task D4: `learn` cron appends a dated learning log

**Files:** Modify `src/cvflow/cron.py`; Test `tests/test_cron.py`.

- [ ] **Step 1: Write the failing test** (append to `tests/test_cron.py`)

```python
def test_run_job_learn_appends_dated_log(tmp_path):
    from cvflow.cron import run_job
    from cvflow.storage import ApplicationStore
    store = ApplicationStore(":memory:")
    for i in range(3):
        store.add(f"indeed:{i}", "Co", "Role", "https://x")
        store.add_decision(job_id=f"indeed:{i}", decision="skip", role_family="frontend",
                           company="Svc", company_type="service", fit_score=40)
    notes = []

    class _Prov:
        def generate(self, prompt, **kw):
            return "Consider down-ranking service companies."

    run_job("learn", store=store, discovery=None, otp=None, notify=notes.append,
            learn_provider=_Prov(), min_decisions=1, learning_dir=str(tmp_path))
    assert any("service companies" in n for n in notes)
    logs = list(tmp_path.glob("*.md"))
    assert len(logs) == 1
    body = logs[0].read_text()
    assert "service companies" in body and "frontend" in body  # suggestion + stats snapshot
```

- [ ] **Step 2: Run** `pytest tests/test_cron.py::test_run_job_learn_appends_dated_log -v` → FAIL.

- [ ] **Step 3: Implement.** Extend `run_job` signature: add `learning_dir: str = "data/learning"`. Replace the `elif job == "learn":` branch:

```python
    elif job == "learn":
        import json
        from datetime import UTC, datetime
        from pathlib import Path

        from cvflow.analytics import summarize
        from cvflow.learning import summarize_decisions

        stats = summarize(store)
        decisions = store.recent_decisions(200)
        applied = [d for d in decisions if d["decision"] == "apply"]
        skipped = [d for d in decisions if d["decision"] == "skip"]
        suggestion = summarize_decisions(
            applied, skipped, provider=learn_provider, min_decisions=min_decisions
        )
        if suggestion:
            day = datetime.now(UTC).date().isoformat()
            Path(learning_dir).mkdir(parents=True, exist_ok=True)
            path = Path(learning_dir) / f"{day}.md"
            entry = (
                f"\n## {datetime.now(UTC).isoformat()}\n\n"
                f"**Stats:** {json.dumps(stats, default=str)}\n\n"
                f"**Suggestion:**\n{suggestion}\n"
            )
            with path.open("a") as fh:
                fh.write(entry)
            notify("💡 Preference suggestions (logged to data/learning/):\n" + suggestion)
```

(The existing `summarize_decisions` from Phase 13C still works; `applied`/`skipped` are now decision dicts, which it json-dumps fine. The `frontend`/stats text lands in the file because `summarize` rolls up `apply_rate_by_role`.)

- [ ] **Step 4: Run** `pytest tests/test_cron.py -v` → PASS.
- [ ] **Step 5: Commit** `git commit -am "feat(cron): learn job appends a dated learning log + stats snapshot (propose-only)"`

---

### Task E: full verification + deploy/docs + build-plan sync

**Files:** Modify `docs/superpowers/plans/2026-06-03-cvflow-build-plan.md`; verify `config.yaml`; ensure `data/learning/` is gitignored (it is — `data/` is gitignored).

- [ ] **Step 1: Full verification** `pytest -q && ruff check . && mypy --strict src` → all green.
- [ ] **Step 2: Live smoke**
```bash
python -c "from cvflow.config import load_config; from cvflow.mcp.tools import build_tools, TOOL_NAMES; build_tools(load_config('config.yaml')); print('approve absent:', 'approve' not in TOOL_NAMES)"
```
Then a real discovery dry-run (long — scrape + distill + benchmark): `python -m cvflow.cron discover` and confirm a two-section digest reaches Telegram with non-zero fit scores. (gateway restart so the live MCP `discover` tool picks up the new engine: `sudo systemctl restart hermes-gateway`.)
- [ ] **Step 3:** Add a `### [x] Phase 14` entry to the build plan after Phase 13, with a dated progress-log entry summarizing A/B/C/D.
- [ ] **Step 4: Commit + push**
```bash
git commit -am "docs(phase-14): mark Phase 14 (discovery relevance v2) complete; sync log"
git push
```

---

## Notes for the executor
- **Gate invariant:** no `approve` tool ever; `handle_gate_command` stays the sole `store.approve` caller. Decision logging is best-effort and wrapped so it can never block or alter approval (invariant 1).
- **Never fabricate (invariant 2):** the distiller is told to emit null for unstated YOE/salary; `exclude_when` never matches an absent field; no FX/estimation anywhere.
- **Never silent (invariant 3):** every deterministic drop (title, exclude_when, distill failure, cap) is logged; a failed cohort fit-call degrades (flagged `RANKING_DEGRADED`) rather than vanishing.
- **Zero cost:** no new dependency; Gemini distill (~≤120/day) stays well under RPD; one NIM call per cohort.
- **Type consistency:** `Crux`/`Salary` (pydantic), `FitResult(fit_score:int, fit_reason:str, concerns:list[str])`, `BenchmarkedJob(posting, benchmark:int, fit_score:int, fit_reason:str, concerns:list[str], cohort:str, ctc_lpa:float|None)`, `DiscoveryService.discover() -> dict[str, list[BenchmarkedJob]]`, `format_digest(result: dict)`, `record_decision(store, decision, bj, *, role_family=None, company_type=None)`.
