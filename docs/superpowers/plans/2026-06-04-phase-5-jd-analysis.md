# Phase 5 — JD Analysis (Goal 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
> Master roadmap: `2026-06-03-cvflow-build-plan.md` → Phase 5.
> **Exit:** tests on sample JDs extract the structured fields and capture an embedded applicant instruction.

**Goal:** Given a job posting, fetch the full JD text and LLM-extract a structured analysis (required skills, preferred quals, seniority, tone/culture, applicant-specific instructions), persisted linked to the application record.

**Architecture:** New `src/cvflow/analysis/` package. JD fetching is behind an injectable `fetch_fn` (default: `urllib` GET) with a stdlib HTML→text step (no new deps). Extraction reuses the Phase-2 `GeminiProvider` (anything with `generate(prompt) -> str`) behind a `_Provider` Protocol, exactly like `LLMRanker`. Persistence is an additive `jd_analyses` table on the Phase-1 `ApplicationStore`, keyed by `job_id` (FK to the application record) — the safety-critical gate/transition logic is untouched.

**Tech Stack:** Python 3.11+, stdlib `urllib`/`html.parser`, SQLite (existing `ApplicationStore`), Gemini provider (existing). Fully mocked tests — no network, no live LLM.

---

## Standing rules in play
- **Never fabricate (rule 3):** the analyzer extracts only from JD text; absent fields become empty lists / empty strings, never invented. **Applicant instructions** (e.g. "include the word pineapple") are captured verbatim so the downstream resume/automation can honor them.
- **Never fail silently (rule 4):** `fetch_jd` raises `JDFetchError` on a non-200 / empty body rather than returning junk; `analyze` raises `JDAnalysisError` on unparseable LLM output.
- **Free-tier / cost (rules 5,6):** reuses the existing Gemini free tier (RPD-tracked, cached) + stdlib fetch. No new paid surface.
- **Privacy (open risk):** unlike ranking, JD text is not user PII, but the same provider is used; tests stay fully mocked.

## File Structure
- Create `src/cvflow/analysis/__init__.py` — `JDAnalysis` dataclass (+ `to_json`/`from_json`), `fetch_jd`, `_html_to_text`, `JDAnalyzer`, errors.
- Modify `src/cvflow/storage/__init__.py` — additive `jd_analyses` table + `save_analysis` / `get_analysis` (no change to transitions/gate).
- Create `tests/test_analysis.py` — fetch, html-strip, extraction (incl. embedded instruction), persistence round-trip.

---

### Task 1: `JDAnalysis` dataclass + JSON round-trip

**Files:**
- Create: `src/cvflow/analysis/__init__.py`
- Test: `tests/test_analysis.py`

- [ ] **Step 1: Write the failing test**

```python
from cvflow.analysis import JDAnalysis


def test_jdanalysis_json_round_trip() -> None:
    a = JDAnalysis(
        required_skills=["Go", "Kubernetes"],
        preferred_quals=["AWS"],
        seniority="senior",
        tone="fast-paced startup",
        applicant_instructions=["include the word pineapple"],
    )
    assert JDAnalysis.from_json(a.to_json()) == a
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_analysis.py::test_jdanalysis_json_round_trip -v`
Expected: FAIL with `ModuleNotFoundError`/`ImportError` for `cvflow.analysis`.

- [ ] **Step 3: Write minimal implementation**

```python
"""JD analysis (Goal 2): fetch full JD text → LLM-extract structured fields.

Fetching is behind an injectable ``fetch_fn`` (default: stdlib urllib) so tests
need no network. Extraction reuses any provider exposing ``generate(prompt)->str``
(the Phase-2 Gemini client), mirroring discovery's ``LLMRanker`` seam.

Never fabricate (CLAUDE.md invariant 2): fields absent from the JD become empty,
never invented. Applicant-specific instructions (e.g. "include the word pineapple")
are captured verbatim for the downstream resume/automation to honor.

Never fail silently (invariant 3): a bad fetch or unparseable LLM reply raises.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from typing import Protocol
from urllib.request import Request, urlopen

__all__ = [
    "JDAnalysis",
    "JDAnalyzer",
    "JDFetchError",
    "JDAnalysisError",
    "fetch_jd",
]


class JDFetchError(Exception):
    """Raised when a JD page cannot be fetched or is empty."""


class JDAnalysisError(Exception):
    """Raised when the LLM analysis reply cannot be parsed."""


@dataclass(frozen=True)
class JDAnalysis:
    required_skills: list[str]
    preferred_quals: list[str]
    seniority: str
    tone: str
    applicant_instructions: list[str]

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)

    @classmethod
    def from_json(cls, raw: str) -> JDAnalysis:
        d = json.loads(raw)
        return cls(
            required_skills=list(d.get("required_skills", [])),
            preferred_quals=list(d.get("preferred_quals", [])),
            seniority=str(d.get("seniority", "")),
            tone=str(d.get("tone", "")),
            applicant_instructions=list(d.get("applicant_instructions", [])),
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_analysis.py::test_jdanalysis_json_round_trip -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/cvflow/analysis/__init__.py tests/test_analysis.py
git commit -m "feat(analysis): JDAnalysis structured result + JSON round-trip (Phase 5)"
```

---

### Task 2: `_html_to_text` + `fetch_jd`

**Files:**
- Modify: `src/cvflow/analysis/__init__.py`
- Test: `tests/test_analysis.py`

- [ ] **Step 1: Write the failing test**

```python
from cvflow.analysis import JDFetchError, fetch_jd
import pytest


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
    assert ".x{}" not in text   # style body dropped


def test_fetch_jd_raises_on_empty_body() -> None:
    with pytest.raises(JDFetchError):
        fetch_jd("https://x/job/1", fetch_fn=lambda url: "   ")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_analysis.py -k fetch_jd -v`
Expected: FAIL with `ImportError`/`AttributeError` for `fetch_jd`.

- [ ] **Step 3: Write minimal implementation**

Add to `src/cvflow/analysis/__init__.py`:

```python
from collections.abc import Callable

_DROP_TAGS = {"script", "style", "head"}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: object) -> None:
        if tag in _DROP_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in _DROP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0 and data.strip():
            self._parts.append(data.strip())

    def text(self) -> str:
        return "\n".join(self._parts)


def _html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    return parser.text()


def _urllib_fetch(url: str) -> str:
    req = Request(url, headers={"User-Agent": "Mozilla/5.0 cvflow"})
    with urlopen(req, timeout=30) as resp:  # noqa: S310 (http(s) only by config)
        if resp.status != 200:
            raise JDFetchError(f"GET {url} -> HTTP {resp.status}")
        return resp.read().decode("utf-8", errors="replace")


def fetch_jd(url: str, *, fetch_fn: Callable[[str], str] = _urllib_fetch) -> str:
    """Fetch ``url`` and return plain-text JD. Raises :class:`JDFetchError` if empty."""
    text = _html_to_text(fetch_fn(url))
    if not text.strip():
        raise JDFetchError(f"empty JD text from {url}")
    return text
```

Also add `"fetch_jd"` is already in `__all__`; no change needed.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_analysis.py -k fetch_jd -v`
Expected: PASS (both)

- [ ] **Step 5: Commit**

```bash
git add src/cvflow/analysis/__init__.py tests/test_analysis.py
git commit -m "feat(analysis): fetch_jd with stdlib HTML->text and empty-body guard (Phase 5)"
```

---

### Task 3: `JDAnalyzer.analyze`

**Files:**
- Modify: `src/cvflow/analysis/__init__.py`
- Test: `tests/test_analysis.py`

- [ ] **Step 1: Write the failing test**

```python
import json as _json
from cvflow.analysis import JDAnalysis, JDAnalyzer, JDAnalysisError
import pytest


class _FakeProvider:
    def __init__(self, payload: str) -> None:
        self.payload = payload
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.payload


def test_analyze_extracts_fields_and_embedded_instruction() -> None:
    payload = "```json\n" + _json.dumps(
        {
            "required_skills": ["Go", "Kubernetes"],
            "preferred_quals": ["AWS certification"],
            "seniority": "senior",
            "tone": "fast-paced, collaborative",
            "applicant_instructions": ["include the word pineapple in your cover letter"],
        }
    ) + "\n```"
    provider = _FakeProvider(payload)
    analyzer = JDAnalyzer(provider)
    result = analyzer.analyze("Senior Go Engineer ... include the word pineapple ...")
    assert isinstance(result, JDAnalysis)
    assert result.required_skills == ["Go", "Kubernetes"]
    assert result.seniority == "senior"
    assert "include the word pineapple in your cover letter" in result.applicant_instructions
    assert "Senior Go Engineer" in provider.prompts[0]  # JD embedded in prompt


def test_analyze_raises_on_unparseable_reply() -> None:
    analyzer = JDAnalyzer(_FakeProvider("not json at all"))
    with pytest.raises(JDAnalysisError):
        analyzer.analyze("some jd text")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_analysis.py -k analyze -v`
Expected: FAIL with `ImportError`/`AttributeError` for `JDAnalyzer`.

- [ ] **Step 3: Write minimal implementation**

Add to `src/cvflow/analysis/__init__.py` (add `JDAnalyzer` to `__all__`):

```python
class _Provider(Protocol):
    def generate(self, prompt: str) -> str: ...


def _strip_code_fence(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        if t.endswith("```"):
            t = t.rsplit("```", 1)[0]
    return t.strip()


_PROMPT = (
    "You analyze a job description and extract structured fields.\n"
    "Return ONLY a JSON object with keys: required_skills (array of strings), "
    "preferred_quals (array), seniority (string), tone (string), "
    "applicant_instructions (array of any explicit instructions the posting gives "
    'applicants, e.g. "include the word pineapple", a required subject line, or a '
    "portfolio link to add).\n"
    "Extract only what the text states; use empty arrays/strings for anything absent. "
    "Do not invent.\n\n"
    "## Job description\n{jd}\n"
)


class JDAnalyzer:
    """LLM-extracts a :class:`JDAnalysis` from JD text via a generate(prompt)->str provider."""

    def __init__(self, provider: _Provider) -> None:
        self._provider = provider

    def analyze(self, jd_text: str) -> JDAnalysis:
        raw = self._provider.generate(_PROMPT.format(jd=jd_text))
        try:
            return JDAnalysis.from_json(_strip_code_fence(raw))
        except (ValueError, json.JSONDecodeError) as exc:
            raise JDAnalysisError(f"could not parse analysis reply: {exc}") from exc
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_analysis.py -k analyze -v`
Expected: PASS (both)

- [ ] **Step 5: Commit**

```bash
git add src/cvflow/analysis/__init__.py tests/test_analysis.py
git commit -m "feat(analysis): JDAnalyzer LLM extraction with never-invent prompt (Phase 5)"
```

---

### Task 4: Persist analysis linked to the application record

**Files:**
- Modify: `src/cvflow/storage/__init__.py`
- Test: `tests/test_analysis.py`

- [ ] **Step 1: Write the failing test**

```python
from cvflow.analysis import JDAnalysis
from cvflow.storage import ApplicationStore, UnknownJob
import pytest


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_analysis.py -k analysis_ -v` (matches save/get/round_trip names)
Expected: FAIL with `AttributeError: 'ApplicationStore' object has no attribute 'save_analysis'`.

- [ ] **Step 3: Write minimal implementation**

In `src/cvflow/storage/__init__.py`, extend `_SCHEMA` with a second statement and add two methods. Append to `_SCHEMA` string:

```python
_SCHEMA = """
CREATE TABLE IF NOT EXISTS applications (
    job_id            TEXT PRIMARY KEY,
    company           TEXT NOT NULL,
    role              TEXT NOT NULL,
    jd_url            TEXT NOT NULL,
    status            TEXT NOT NULL,
    discovered_at     TEXT NOT NULL,
    applied_at        TEXT,
    tailored_pdf_path TEXT,
    confirmation_ref  TEXT
);
CREATE TABLE IF NOT EXISTS jd_analyses (
    job_id    TEXT PRIMARY KEY REFERENCES applications(job_id),
    analysis  TEXT NOT NULL
);
"""
```

Change the constructor's `self._conn.execute(_SCHEMA)` to `self._conn.executescript(_SCHEMA)` (multiple statements).

Add an import at the top of the file:

```python
from cvflow.analysis import JDAnalysis
```

Add methods to `ApplicationStore` (after `set_confirmation`):

```python
    def save_analysis(self, job_id: str, analysis: JDAnalysis) -> None:
        """Persist the JD analysis linked to an existing application record."""
        self._require(job_id)
        self._conn.execute(
            "INSERT INTO jd_analyses (job_id, analysis) VALUES (?, ?) "
            "ON CONFLICT(job_id) DO UPDATE SET analysis = excluded.analysis",
            (job_id, analysis.to_json()),
        )
        self._conn.commit()

    def get_analysis(self, job_id: str) -> JDAnalysis | None:
        row = self._conn.execute(
            "SELECT analysis FROM jd_analyses WHERE job_id = ?", (job_id,)
        ).fetchone()
        return JDAnalysis.from_json(row["analysis"]) if row is not None else None
```

> Note: `cvflow.analysis` importing nothing from `cvflow.storage` keeps this one-directional (storage → analysis), so no import cycle.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_analysis.py -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add src/cvflow/storage/__init__.py tests/test_analysis.py
git commit -m "feat(storage): persist JD analysis linked to application record (Phase 5)"
```

---

### Task 5: Harness green + roadmap sync

- [ ] **Step 1: Full suite + lint + types**

Run: `pytest && ruff check . && mypy src`
Expected: all green; `mypy --strict` clean (the package config already enforces strict).

- [ ] **Step 2: Tick Phase 5 + progress log**

In `docs/superpowers/plans/2026-06-03-cvflow-build-plan.md`: change `### [ ] Phase 5` to `### [x] Phase 5` and append a progress-log entry dated 2026-06-04 summarizing the deliverable and the embedded-instruction capture.

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/plans/2026-06-03-cvflow-build-plan.md
git commit -m "docs: mark Phase 5 (JD analysis) complete"
```

---

## Self-Review
- **Spec coverage:** fetch full JD (Task 2) ✓; required skills / preferred quals / seniority / tone (Task 3) ✓; applicant-specific instructions incl. embedded "pineapple" (Task 3 test) ✓; persist linked to application record (Task 4) ✓.
- **Type consistency:** `JDAnalysis` fields and `to_json`/`from_json` are used identically across Tasks 1, 3, 4; provider Protocol matches the Phase-2 `generate(prompt)->str` signature.
- **No placeholders:** every code/test step is concrete.

## Out of scope
Resume tailoring + diff (Phase 6); the Telegram presentation of the analysis (Phase 8); wiring the analyzer into the discovery→selection flow (Phase 11 daemon).
