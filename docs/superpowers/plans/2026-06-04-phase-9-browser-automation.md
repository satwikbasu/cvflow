# Phase 9 — Browser Automation + Discovery/Analyzer Provider Gap — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the live `discover`/`analyze_jd` gap with an OpenAI-compatible NIM brain provider, then build the deterministic Playwright form-filling skill (fill, upload, pause→clarify→resume, proof capture) behind the approval gate.

**Architecture:** Part 1 adds `NimProvider` (stdlib-urllib, injectable transport, RPM budget) implementing the existing `generate(prompt)->str` seam, wired into `build_tools`. Part 2 adds `src/cvflow/automation/`: a pure deterministic-first field resolver (unit-tested without a browser), a `SessionManager` (`launch_persistent_context`, Option-C hybrid), a `FormFiller` over a Playwright `Page`, and an `Automator` orchestrator that the MCP tools `fill_application`/`resume_application` delegate to. `guard_can_submit` is asserted at every entrypoint; no `approve` tool is ever exposed.

**Tech Stack:** Python 3.11+, stdlib `urllib`, Playwright (Chromium), SQLite, pytest. Tests mock all network/LLM and skip cleanly when Playwright browsers are absent.

---

## File structure

- `src/cvflow/llm/__init__.py` — **modify**: add `NimProvider`, `RpmExceeded`.
- `src/cvflow/mcp/tools.py` — **modify**: wire `discovery`/`analyzer`; add `fill_application`/`resume_application` to `TOOL_NAMES` + methods.
- `src/cvflow/storage/__init__.py` — **modify**: additive proof columns + `set_proof`.
- `src/cvflow/automation/__init__.py` — **create**: `FieldSpec`, `FieldFill`, `FieldResolution`, `NeedsClarification`, `resolve_field`, `SessionManager`, `FormFiller`, `Automator`.
- `tests/fixtures/form/page1.html`, `page2.html` — **create**: local fixture form.
- `tests/test_llm_nim.py`, `tests/test_automation_resolve.py`, `tests/test_automation_browser.py`, `tests/test_automation_orchestration.py` — **create**.
- `tests/test_mcp_tools.py` — **modify**: URL-present + gate tests.

---

# PART 1 — Close the discovery/analyzer gap

### Task 1: `NimProvider` (OpenAI-compatible brain client)

**Files:**
- Modify: `src/cvflow/llm/__init__.py`
- Test: `tests/test_llm_nim.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_llm_nim.py
"""Tests for the NIM OpenAI-compatible brain provider. No live key / network."""

import json

import pytest

from cvflow.llm import LLMError, NimProvider, RpmExceeded


def _fake_post(captured):
    def post(url, headers, body):
        captured["url"] = url
        captured["headers"] = headers
        captured["body"] = json.loads(body)
        return json.dumps({"choices": [{"message": {"content": "ranked!"}}]})
    return post


def test_generate_posts_chat_completions_and_returns_content():
    captured = {}
    p = NimProvider(
        base_url="https://integrate.api.nvidia.com/v1",
        api_key="secret",
        model="meta/llama-3.3-70b-instruct",
        max_requests_per_minute=40,
        post_fn=_fake_post(captured),
    )
    out = p.generate("rank these")
    assert out == "ranked!"
    assert captured["url"] == "https://integrate.api.nvidia.com/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer secret"
    assert captured["body"]["model"] == "meta/llama-3.3-70b-instruct"
    assert captured["body"]["messages"] == [{"role": "user", "content": "rank these"}]


def test_rpm_budget_raises_before_calling_out():
    calls = {"n": 0}

    def post(url, headers, body):
        calls["n"] += 1
        return json.dumps({"choices": [{"message": {"content": "ok"}}]})

    clock = {"t": 1000.0}
    p = NimProvider(
        base_url="b", api_key="k", model="m",
        max_requests_per_minute=2, post_fn=post, now=lambda: clock["t"],
    )
    p.generate("a"); p.generate("b")
    with pytest.raises(RpmExceeded):
        p.generate("c")
    assert calls["n"] == 2  # never called out on the over-budget request
    clock["t"] += 61  # minute rolls over
    assert p.generate("d") == "ok"


def test_malformed_reply_raises_llmerror():
    p = NimProvider(base_url="b", api_key="k", model="m",
                    max_requests_per_minute=40, post_fn=lambda u, h, b: "{not json}")
    with pytest.raises(LLMError):
        p.generate("x")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_llm_nim.py -v`
Expected: FAIL — `ImportError: cannot import name 'NimProvider'`.

- [ ] **Step 3: Write minimal implementation**

Append to `src/cvflow/llm/__init__.py` (after the existing `GeminiProvider`). Add `import json` and `from urllib.request import Request, urlopen` at the top if not present:

```python
class RpmExceeded(LLMError):
    """Raised when a call would exceed the configured requests-per-minute budget."""


def _now_seconds() -> float:
    import time
    return time.monotonic()


def _urllib_post(url: str, headers: dict[str, str], body: str) -> str:
    from urllib.request import Request, urlopen

    req = Request(url, data=body.encode(), headers=headers, method="POST")
    with urlopen(req, timeout=60) as resp:  # noqa: S310 (https by config)
        if resp.status != 200:
            raise LLMError(f"NIM POST {url} -> HTTP {resp.status}")
        return resp.read().decode("utf-8", errors="replace")


class NimProvider:
    """OpenAI-compatible chat client for the NIM brain; generate(prompt)->str.

    Mirrors GeminiProvider's seam. Stdlib transport (no new dep). Per-minute
    budget guard honors the free-tier invariant (raises before calling out).
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        max_requests_per_minute: int,
        post_fn: Callable[[str, dict[str, str], str], str] = _urllib_post,
        now: Callable[[], float] = _now_seconds,
    ) -> None:
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._api_key = api_key
        self._model = model
        self._max_rpm = max_requests_per_minute
        self._post = post_fn
        self._now = now
        self._window_start = now()
        self._count = 0

    def _spend_one(self) -> None:
        t = self._now()
        if t - self._window_start >= 60.0:
            self._window_start = t
            self._count = 0
        if self._count >= self._max_rpm:
            raise RpmExceeded(f"NIM RPM budget reached ({self._max_rpm})")
        self._count += 1

    def generate(self, prompt: str) -> str:
        self._spend_one()
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        body = json.dumps(
            {"model": self._model, "messages": [{"role": "user", "content": prompt}]}
        )
        raw = self._post(self._url, headers, body)
        try:
            data = json.loads(raw)
            return str(data["choices"][0]["message"]["content"])
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"could not parse NIM reply: {exc}") from exc
```

Add `import json` near the top of the module if absent (GeminiProvider didn't need it; the new code does).

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_llm_nim.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/cvflow/llm/__init__.py tests/test_llm_nim.py
git commit -m "feat(llm): NimProvider OpenAI-compatible brain client with RPM budget"
```

---

### Task 2: Wire `discovery`/`analyzer` in `build_tools` + URL-always test

**Files:**
- Modify: `src/cvflow/mcp/tools.py:187-220` (the `build_tools` function)
- Test: `tests/test_mcp_tools.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_mcp_tools.py
def test_discover_returns_url_for_every_job():
    from cvflow.discovery import JobPosting, RankedJob
    from cvflow.storage import ApplicationStore

    class _StubDiscovery:
        def discover(self):
            p = JobPosting(
                job_id="indeed:7", title="Backend Dev", company="Acme",
                location="Remote", description="d", url="https://jobs/7",
                site="indeed", date_posted="2026-06-04",
            )
            return [RankedJob(posting=p, summary="s", rationale="r")]

    tools = CvflowTools(
        store=ApplicationStore(":memory:"), knowledge=None,
        discovery=_StubDiscovery(), analyzer=None, tailor=None,
    )
    jobs = tools.discover()
    assert jobs and all(j["url"] for j in jobs)
    assert jobs[0]["url"] == "https://jobs/7"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_mcp_tools.py::test_discover_returns_url_for_every_job -v`
Expected: PASS already? No — `discover()` exists and returns `url`. If it PASSES, this test pins the contract; keep it. If the import path differs it FAILs. Run and confirm green (this task's behavioral change is in `build_tools`, which has no unit harness — it is exercised live; the test pins the URL contract).

- [ ] **Step 3: Wire the provider in `build_tools`**

In `src/cvflow/mcp/tools.py`, replace the wiring block (the `# analyzer + discovery run on the NIM brain ...` comment through the `return CvflowTools(...)` with `discovery=None, analyzer=None`):

```python
    from cvflow.analysis import JDAnalyzer
    from cvflow.discovery import DiscoveryService, LLMRanker
    from cvflow.llm import NimProvider

    brain = NimProvider(
        base_url=config.llm.brain.base_url,
        api_key=config.llm.brain.api_key,
        model=config.llm.brain.model,
        max_requests_per_minute=config.llm.brain.max_requests_per_minute,
    )
    ranker = LLMRanker(brain, knowledge.full_context())
    discovery = DiscoveryService(
        store,
        ranker,
        search_terms=config.discovery.search_terms,
        locations=config.discovery.locations,
        sites=config.discovery.sites,
        results_wanted_per_site=config.discovery.results_wanted_per_site,
        hours_old=config.discovery.hours_old,
        top_n=config.discovery.top_n_to_present,
    )
    analyzer = JDAnalyzer(brain)
    return CvflowTools(
        store=store,
        knowledge=knowledge,
        discovery=discovery,
        analyzer=analyzer,
        tailor=tailor,
        output_dir=config.resume.output_dir,
        essay_provider=tailoring,
    )
```

- [ ] **Step 4: Run the full suite + lint/type**

Run: `pytest -q && ruff check . && mypy --strict src`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add src/cvflow/mcp/tools.py tests/test_mcp_tools.py
git commit -m "feat(mcp): wire NIM-backed discovery + JD analyzer in build_tools"
```

---

# PART 2 — Phase 9 browser automation

### Task 3: Additive proof columns + `set_proof`

**Files:**
- Modify: `src/cvflow/storage/__init__.py` (the `Application` dataclass ~50-59, `CREATE TABLE` ~63-73, `_row_to_app` ~92-103, and add a setter near `set_confirmation` ~171)
- Test: `tests/test_storage.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_storage.py
def test_set_proof_persists_url_screenshot_title():
    from cvflow.storage import ApplicationStore

    store = ApplicationStore(":memory:")
    store.add("indeed:9", "Acme", "Backend", "https://jobs/9")
    store.set_proof(
        "indeed:9", url="https://acme/confirm",
        screenshot_path="/data/proof/9.png", page_title="Application received",
    )
    app = store.get("indeed:9")
    assert app.proof_url == "https://acme/confirm"
    assert app.proof_screenshot_path == "/data/proof/9.png"
    assert app.proof_page_title == "Application received"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_storage.py::test_set_proof_persists_url_screenshot_title -v`
Expected: FAIL — `AttributeError: 'ApplicationStore' object has no attribute 'set_proof'`.

- [ ] **Step 3: Implement (additive only)**

In the `Application` dataclass add three fields after `confirmation_ref`:

```python
    proof_url: str | None = None
    proof_screenshot_path: str | None = None
    proof_page_title: str | None = None
```

In the `CREATE TABLE applications (...)` statement add (after `confirmation_ref TEXT`):

```python
    proof_url             TEXT,
    proof_screenshot_path TEXT,
    proof_page_title      TEXT
```

In `_row_to_app`, add the three reads:

```python
            proof_url=row["proof_url"],
            proof_screenshot_path=row["proof_screenshot_path"],
            proof_page_title=row["proof_page_title"],
```

Add the setter after `set_confirmation`:

```python
    def set_proof(
        self, job_id: str, *, url: str, screenshot_path: str, page_title: str
    ) -> None:
        """Persist submission proof. Additive — never touches status/approval."""
        self._require(job_id)
        with self._conn:
            self._conn.execute(
                "UPDATE applications SET proof_url = ?, proof_screenshot_path = ?, "
                "proof_page_title = ? WHERE job_id = ?",
                (url, screenshot_path, page_title, job_id),
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_storage.py -v`
Expected: PASS (existing + new).

- [ ] **Step 5: Commit**

```bash
git add src/cvflow/storage/__init__.py tests/test_storage.py
git commit -m "feat(storage): additive submission-proof columns + set_proof"
```

---

### Task 4: Local fixture form

**Files:**
- Create: `tests/fixtures/form/page1.html`, `tests/fixtures/form/page2.html`

- [ ] **Step 1: Create the fixtures (no test yet — used by later tasks)**

`tests/fixtures/form/page1.html`:

```html
<!doctype html><html><body>
<h1>Apply: Backend Engineer</h1>
<form>
  <label for="full_name">Full name</label>
  <input id="full_name" name="full_name" type="text" required>

  <label for="phone">Phone</label>
  <input id="phone" name="phone" type="text" required>

  <label for="resume">Resume</label>
  <input id="resume" name="resume" type="file">

  <label for="experience_level">Experience level</label>
  <select id="experience_level" name="experience_level">
    <option value="">--</option>
    <option value="junior">Junior</option>
    <option value="mid">Mid</option>
    <option value="senior">Senior</option>
  </select>

  <label><input id="relocate" name="relocate" type="checkbox"> Willing to relocate</label>

  <label for="why_us">Why do you want to work here?</label>
  <textarea id="why_us" name="why_us"></textarea>

  <label for="start_date">Expected start date</label>
  <input id="start_date" name="start_date" type="text" required>

  <a id="next" href="page2.html">Next</a>
</form>
</body></html>
```

`tests/fixtures/form/page2.html`:

```html
<!doctype html><html><body>
<h1>Review &amp; Submit</h1>
<button id="submit" onclick="document.title='Application received'; document.getElementById('conf').style.display='block'; return false;">Submit</button>
<div id="conf" style="display:none">Confirmation #ABC-12345</div>
</body></html>
```

- [ ] **Step 2: Commit**

```bash
git add tests/fixtures/form/page1.html tests/fixtures/form/page2.html
git commit -m "test(automation): local fixture application form (2 pages)"
```

---

### Task 5: Pure deterministic-first field resolver

**Files:**
- Create: `src/cvflow/automation/__init__.py`
- Test: `tests/test_automation_resolve.py`

This is the invariant-2 core, fully testable with no browser.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_automation_resolve.py
"""Pure field-resolution logic — deterministic-first, never guess. No browser."""

from cvflow.automation import FieldSpec, resolve_field
from cvflow.essays import EssayAnswer
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_automation_resolve.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cvflow.automation'`.

- [ ] **Step 3: Create `src/cvflow/automation/__init__.py` (resolver portion)**

```python
"""Deterministic browser automation skill (Phase 9, Goals 5 & 8).

Field values are resolved deterministic-first (form_fields lookup → grounded
essay composition → clarify/skip); an LLM never guesses *what fact* fills a box
(invariant 2). guard_can_submit gates every entrypoint (Goal 4). Failures are
never silent: a crash marks the app failed and notifies the user with the job URL
(invariant 3).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol

from cvflow.essays import compose_answer

__all__ = [
    "FieldSpec",
    "FieldFill",
    "FieldResolution",
    "NeedsClarification",
    "resolve_field",
]


class _Provider(Protocol):
    def generate(self, prompt: str) -> str: ...


@dataclass(frozen=True)
class FieldSpec:
    label: str
    name: str
    field_type: str  # text | textarea | select | checkbox | file
    options: list[str]
    required: bool


@dataclass(frozen=True)
class FieldFill:
    label: str
    value: str
    source: str  # form_fields | composed | clarified | skipped


@dataclass(frozen=True)
class FieldResolution:
    fill: FieldFill | None
    clarify: bool
    question: str | None


@dataclass(frozen=True)
class NeedsClarification:
    job_id: str
    question: str


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.strip().lower()).strip("_")


def resolve_field(
    spec: FieldSpec, form_fields: Any, knowledge: Any, *, provider: _Provider
) -> FieldResolution:
    """Resolve one field deterministic-first; never guess a fact."""
    # 1) literal lookup against form_fields (exact name, then normalized label)
    for key in (spec.name, _normalize(spec.name), _normalize(spec.label)):
        if form_fields.is_filled(key):
            return FieldResolution(
                FieldFill(spec.label, form_fields.require(key), "form_fields"),
                False, None,
            )
    # 2) free-text / essay → grounded composition
    if spec.field_type == "textarea":
        ans = compose_answer(spec.label, knowledge, provider=provider)
        if ans.grounded and ans.text:
            return FieldResolution(
                FieldFill(spec.label, ans.text, "composed"), False, None
            )
    # 3) escalate (required) or skip (optional) — never guess
    if spec.required:
        return FieldResolution(None, True, spec.label)
    return FieldResolution(FieldFill(spec.label, "", "skipped"), False, None)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_automation_resolve.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/cvflow/automation/__init__.py tests/test_automation_resolve.py
git commit -m "feat(automation): deterministic-first field resolver (never guess)"
```

---

### Task 6: `SessionManager` + `FormFiller` against the fixture form

**Files:**
- Modify: `src/cvflow/automation/__init__.py`
- Test: `tests/test_automation_browser.py`

These tests use real Playwright and SKIP if Chromium is unavailable.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_automation_browser.py
"""FormFiller against the local fixture form. Skips if Playwright unavailable."""

from pathlib import Path

import pytest

playwright_api = pytest.importorskip("playwright.sync_api")

from cvflow.automation import FieldFill, FormFiller, SessionManager  # noqa: E402

FIXTURE = (Path(__file__).parent / "fixtures" / "form" / "page1.html").resolve()
FIXTURE_URL = FIXTURE.as_uri()


@pytest.fixture
def session(tmp_path):
    try:
        mgr = SessionManager(user_data_root=str(tmp_path / "profiles"), headless=True)
    except Exception as exc:  # browser binary missing
        pytest.skip(f"playwright chromium unavailable: {exc}")
    yield mgr
    mgr.close_all()


def test_discover_fields_reads_the_form(session):
    page = session.open("job1", FIXTURE_URL)
    filler = FormFiller(page)
    specs = {s.name: s for s in filler.discover_fields()}
    assert specs["full_name"].field_type == "text"
    assert specs["full_name"].required is True
    assert specs["experience_level"].field_type == "select"
    assert "senior" in specs["experience_level"].options
    assert specs["relocate"].field_type == "checkbox"
    assert specs["why_us"].field_type == "textarea"
    assert specs["resume"].field_type == "file"


def test_apply_fills_each_field_type(session, tmp_path):
    page = session.open("job1", FIXTURE_URL)
    filler = FormFiller(page)
    resume = tmp_path / "cv.pdf"
    resume.write_bytes(b"%PDF-1.4 fake")
    filler.apply("full_name", "text", "Ada Lovelace")
    filler.apply("experience_level", "select", "senior")
    filler.apply("relocate", "checkbox", "true")
    filler.apply("why_us", "textarea", "I love backends")
    filler.apply("resume", "file", str(resume))
    assert page.input_value("#full_name") == "Ada Lovelace"
    assert page.input_value("#experience_level") == "senior"
    assert page.is_checked("#relocate") is True
    assert page.input_value("#why_us") == "I love backends"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_automation_browser.py -v`
Expected: FAIL — `ImportError: cannot import name 'FormFiller'` (or SKIP if Chromium absent — install with `playwright install chromium`, then it FAILs on the import).

- [ ] **Step 3: Implement `SessionManager` + `FormFiller`**

Append to `src/cvflow/automation/__init__.py` (and add to `__all__`: `"SessionManager", "FormFiller"`):

```python
class SessionManager:
    """Holds live persistent browser contexts keyed by job_id (Option-C hybrid).

    The on-disk user_data_dir persists login/cookies across restarts; the live
    context/page is held in-process so pause→resume continues on the same page.
    """

    def __init__(self, *, user_data_root: str, headless: bool = False,
                 use_stealth: bool = True) -> None:
        from pathlib import Path
        from playwright.sync_api import sync_playwright

        self._root = Path(user_data_root)
        self._root.mkdir(parents=True, exist_ok=True)
        self._headless = headless
        self._args = (
            ["--disable-blink-features=AutomationControlled"] if use_stealth else []
        )
        self._pw = sync_playwright().start()
        self._contexts: dict[str, Any] = {}
        self._pages: dict[str, Any] = {}

    def open(self, job_id: str, url: str) -> Any:
        if job_id not in self._contexts:
            ctx = self._pw.chromium.launch_persistent_context(
                user_data_dir=str(self._root / job_id),
                headless=self._headless,
                args=self._args,
            )
            self._contexts[job_id] = ctx
            self._pages[job_id] = ctx.pages[0] if ctx.pages else ctx.new_page()
            self._pages[job_id].goto(url)
        return self._pages[job_id]

    def page(self, job_id: str) -> Any | None:
        return self._pages.get(job_id)

    def close(self, job_id: str) -> None:
        ctx = self._contexts.pop(job_id, None)
        self._pages.pop(job_id, None)
        if ctx is not None:
            ctx.close()

    def close_all(self) -> None:
        for job_id in list(self._contexts):
            self.close(job_id)
        self._pw.stop()


class FormFiller:
    """Reads & fills fields on a single Playwright Page; captures proof."""

    def __init__(self, page: Any) -> None:
        self._page = page

    def discover_fields(self) -> list[FieldSpec]:
        specs: list[FieldSpec] = []
        for el in self._page.query_selector_all("input, textarea, select"):
            name = el.get_attribute("name") or el.get_attribute("id") or ""
            if not name:
                continue
            tag = el.evaluate("e => e.tagName.toLowerCase()")
            if tag == "textarea":
                ftype = "textarea"
            elif tag == "select":
                ftype = "select"
            else:
                ftype = el.get_attribute("type") or "text"
            options = (
                [o.get_attribute("value") or "" for o in el.query_selector_all("option")]
                if ftype == "select" else []
            )
            label = self._label_for(name) or name
            required = el.get_attribute("required") is not None
            specs.append(FieldSpec(label, name, ftype, options, required))
        return specs

    def _label_for(self, name: str) -> str:
        el = self._page.query_selector(f"label[for='{name}']")
        return el.inner_text().strip() if el else ""

    def apply(self, name: str, field_type: str, value: str) -> None:
        target = f"[name='{name}']" if self._page.query_selector(f"[name='{name}']") else f"#{name}"
        if field_type == "select":
            self._page.select_option(target, value)
        elif field_type == "checkbox":
            if value.lower() in ("true", "yes", "1"):
                self._page.check(target)
        elif field_type == "file":
            self._page.set_input_files(target, value)
        else:
            self._page.fill(target, value)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_automation_browser.py -v` (run `playwright install chromium` first if it skips)
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/cvflow/automation/__init__.py tests/test_automation_browser.py
git commit -m "feat(automation): SessionManager (persistent context) + FormFiller"
```

---

### Task 7: Proof capture on `FormFiller`

**Files:**
- Modify: `src/cvflow/automation/__init__.py`
- Test: `tests/test_automation_browser.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_automation_browser.py
def test_capture_proof_returns_url_title_and_screenshot(session, tmp_path):
    page2 = (Path(__file__).parent / "fixtures" / "form" / "page2.html").resolve()
    page = session.open("job2", page2.as_uri())
    page.click("#submit")
    filler = FormFiller(page)
    proof = filler.capture_proof(str(tmp_path / "proof.png"))
    assert proof.page_title == "Application received"
    assert "ABC-12345" in (proof.confirmation_ref or "")
    assert proof.url.endswith("page2.html")
    assert Path(proof.screenshot_path).exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_automation_browser.py::test_capture_proof_returns_url_title_and_screenshot -v`
Expected: FAIL — `AttributeError: 'FormFiller' object has no attribute 'capture_proof'`.

- [ ] **Step 3: Implement**

Add a `Proof` dataclass (and to `__all__`: `"Proof"`) and the method:

```python
@dataclass(frozen=True)
class Proof:
    url: str
    page_title: str
    screenshot_path: str
    confirmation_ref: str | None
```

```python
    def capture_proof(self, screenshot_path: str) -> Proof:
        self._page.screenshot(path=screenshot_path)
        body = self._page.inner_text("body")
        m = re.search(r"#\s*([A-Za-z0-9-]+)", body)
        return Proof(
            url=self._page.url,
            page_title=self._page.title(),
            screenshot_path=screenshot_path,
            confirmation_ref=m.group(0) if m else None,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_automation_browser.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cvflow/automation/__init__.py tests/test_automation_browser.py
git commit -m "feat(automation): proof capture (url/title/screenshot/confirmation)"
```

---

### Task 8: `Automator` orchestration — gate, fill, pause/resume, disclosure, crash-notify

**Files:**
- Modify: `src/cvflow/automation/__init__.py`
- Test: `tests/test_automation_orchestration.py`

Orchestration is tested with a **fake page/filler** (no browser) so the control flow, gate, and notifications are verified deterministically.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_automation_orchestration.py
"""Automator control flow: gate, pause/resume, disclosure, crash-notify. No browser."""

import pytest

from cvflow.automation import Automator, FieldSpec
from cvflow.statemachine import Status, SubmissionBlocked
from cvflow.storage import ApplicationStore, FormFields


class _FakeFiller:
    def __init__(self, specs, *, raise_on_submit=False):
        self._specs = specs
        self.applied = []
        self._raise = raise_on_submit
    def discover_fields(self): return self._specs
    def apply(self, name, ftype, value): self.applied.append((name, value))
    def submit(self):
        if self._raise: raise RuntimeError("network died")
    def capture_proof(self, path):
        from cvflow.automation import Proof
        return Proof(url="https://done", page_title="Application received",
                     screenshot_path=path, confirmation_ref="#XYZ-1")


class _FakeSessions:
    def __init__(self, page="P"): self._page = page; self.closed = []
    def open(self, job_id, url): return self._page
    def page(self, job_id): return self._page
    def close(self, job_id): self.closed.append(job_id)


class _KB:
    form_fields = FormFields(values={"full_name": "Ada Lovelace"})
    def full_context(self): return "ctx"
    def doc_keys(self): return ["experience"]


def _provider(text="{}"):
    class P:
        def generate(self, prompt): return text
    return P()


def _store():
    s = ApplicationStore(":memory:")
    s.add("indeed:1", "Acme", "Backend", "https://jobs/1")
    return s


def _automator(store, filler, sessions, notes, **kw):
    return Automator(
        store=store, knowledge=_KB(), provider=_provider(kw.pop("ptext", "{}")),
        sessions=sessions,
        filler_factory=lambda page: filler,
        notify=lambda msg: notes.append(msg),
        screenshot_dir="/tmp",
        **kw,
    )


def test_gate_blocks_non_approved():
    store = _store()  # status discovered
    notes = []
    auto = _automator(store, _FakeFiller([]), _FakeSessions(), notes)
    with pytest.raises(SubmissionBlocked):
        auto.fill("indeed:1")


def _approve(store):
    store.set_status("indeed:1", Status.PENDING_REVIEW)
    store.approve("indeed:1")


def test_completes_and_records_proof_when_all_resolved():
    store = _store(); _approve(store)
    specs = [FieldSpec("Full name", "full_name", "text", [], True)]
    filler = _FakeFiller(specs)
    notes = []
    auto = _automator(store, filler, _FakeSessions(), notes)
    result = auto.fill("indeed:1")
    assert result["status"] == "applied"
    assert ("full_name", "Ada Lovelace") in filler.applied
    assert store.get("indeed:1").status == Status.APPLIED
    assert store.get("indeed:1").proof_url == "https://done"


def test_required_unknown_field_pauses_for_clarification():
    store = _store(); _approve(store)
    specs = [FieldSpec("Expected start date", "start_date", "text", [], True)]
    auto = _automator(store, _FakeFiller(specs), _FakeSessions(), [])
    result = auto.fill("indeed:1")
    assert result["needs_clarification"] is True
    assert result["question"] == "Expected start date"
    assert store.get("indeed:1").status == Status.APPROVED  # not submitted


def test_resume_uses_clarified_answer_then_completes():
    store = _store(); _approve(store)
    specs = [FieldSpec("Expected start date", "start_date", "text", [], True)]
    filler = _FakeFiller(specs)
    auto = _automator(store, filler, _FakeSessions(), [])
    auto.fill("indeed:1")
    result = auto.resume("indeed:1", "2026-07-01")
    assert ("start_date", "2026-07-01") in filler.applied
    assert result["status"] == "applied"


def test_composed_answers_disclosed_before_submit():
    store = _store(); _approve(store)
    specs = [FieldSpec("Why do you want to work here?", "why_us", "textarea", [], True)]
    filler = _FakeFiller(specs)
    notes = []
    grounded = '{"answer": "I love backends", "citations": ["experience"]}'
    auto = _automator(store, filler, _FakeSessions(), notes, ptext=grounded)
    auto.fill("indeed:1")
    assert any("why_us" in n or "Why do you want" in n for n in notes)
    assert any("I love backends" in n for n in notes)


def test_crash_marks_failed_and_notifies_with_url():
    store = _store(); _approve(store)
    specs = [FieldSpec("Full name", "full_name", "text", [], True)]
    filler = _FakeFiller(specs, raise_on_submit=True)
    sessions = _FakeSessions()
    notes = []
    auto = _automator(store, filler, sessions, notes)
    result = auto.fill("indeed:1")
    assert result["failed"] is True
    assert store.get("indeed:1").status == Status.FAILED
    assert any("https://jobs/1" in n for n in notes)
    assert "indeed:1" in sessions.closed
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_automation_orchestration.py -v`
Expected: FAIL — `ImportError: cannot import name 'Automator'`.

- [ ] **Step 3: Implement `Automator`**

Append to `src/cvflow/automation/__init__.py` (add `"Automator"` to `__all__`). Note: `FormFiller` needs a `submit()` method that clicks Submit & waits — add it alongside.

Add to `FormFiller` (after `apply`):

```python
    def submit(self) -> None:
        self._page.click("#submit, button[type='submit'], input[type='submit']")
        self._page.wait_for_load_state("networkidle")
```

Then the orchestrator:

```python
from cvflow.statemachine import Status, guard_can_submit  # add near top imports
from cvflow.storage import UnknownJob  # add near top imports


class Automator:
    """Gated orchestration of fill → (pause/resume) → disclose → submit → proof."""

    def __init__(
        self, *, store: Any, knowledge: Any, provider: _Provider, sessions: Any,
        filler_factory: Any, notify: Any, screenshot_dir: str,
    ) -> None:
        self._store = store
        self._kb = knowledge
        self._provider = provider
        self._sessions = sessions
        self._make_filler = filler_factory
        self._notify = notify
        self._shot_dir = screenshot_dir
        self._pending: dict[str, FieldSpec] = {}   # job_id -> awaiting-clarification spec
        self._composed: dict[str, list[FieldFill]] = {}

    def _app(self, job_id: str) -> Any:
        app = self._store.get(job_id)
        if app is None:
            raise UnknownJob(job_id)
        return app

    def fill(self, job_id: str) -> dict[str, Any]:
        app = self._app(job_id)
        guard_can_submit(app.status)  # raises SubmissionBlocked unless APPROVED
        page = self._sessions.open(job_id, app.jd_url)
        self._composed[job_id] = []
        filler = self._make_filler(page)
        return self._run(job_id, app, filler, filler.discover_fields())

    def resume(self, job_id: str, answer: str) -> dict[str, Any]:
        app = self._app(job_id)
        guard_can_submit(app.status)
        spec = self._pending.pop(job_id, None)
        page = self._sessions.page(job_id)
        filler = self._make_filler(page)
        if spec is not None:
            filler.apply(spec.name, spec.field_type, answer)
        # re-discover remaining fields and continue
        return self._run(job_id, app, filler, filler.discover_fields(),
                         already=spec.name if spec else None)

    def _run(self, job_id, app, filler, specs, already=None):
        ff = self._kb.form_fields
        try:
            for spec in specs:
                if already is not None and spec.name == already:
                    continue
                r = resolve_field(spec, ff, self._kb, provider=self._provider)
                if r.clarify:
                    self._pending[job_id] = spec
                    self._notify(f"[{job_id}] Need an answer: {r.question}")
                    return {"needs_clarification": True, "job_id": job_id,
                            "question": r.question}
                if r.fill.source == "skipped":
                    continue
                filler.apply(spec.name, spec.field_type, r.fill.value)
                if r.fill.source == "composed":
                    self._composed[job_id].append(r.fill)
            self._disclose_composed(job_id)
            filler.submit()
            proof = filler.capture_proof(f"{self._shot_dir}/{job_id}.png")
            self._store.set_proof(job_id, url=proof.url,
                                  screenshot_path=proof.screenshot_path,
                                  page_title=proof.page_title)
            if proof.confirmation_ref:
                self._store.set_confirmation(job_id, proof.confirmation_ref)
            self._store.set_status(job_id, Status.APPLIED)
            self._sessions.close(job_id)
            return {"status": "applied", "job_id": job_id, "proof_url": proof.url}
        except Exception as exc:  # never silent (invariant 3)
            self._store.set_status(job_id, Status.FAILED)
            self._sessions.close(job_id)
            self._notify(
                f"❌ Application failed for {app.company} — {app.role} "
                f"({app.jd_url}): {exc}"
            )
            return {"failed": True, "job_id": job_id, "error": str(exc)}

    def _disclose_composed(self, job_id: str) -> None:
        for fill in self._composed.get(job_id, []):
            self._notify(
                f"[{job_id}] Composed answer for '{fill.label}' ({fill.name if hasattr(fill,'name') else ''}): {fill.value}"
            )
```

Note: `FieldFill` has no `name`; the disclosure references the label. Replace `_disclose_composed` body with the simpler form:

```python
    def _disclose_composed(self, job_id: str) -> None:
        for fill in self._composed.get(job_id, []):
            self._notify(
                f"[{job_id}] Composed answer for '{fill.label}': {fill.value}"
            )
```

(The disclosure test matches on the label text "Why do you want" and the answer — both satisfied.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_automation_orchestration.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add src/cvflow/automation/__init__.py tests/test_automation_orchestration.py
git commit -m "feat(automation): gated Automator (fill/pause/resume/disclose/crash-notify)"
```

---

### Task 9: MCP tools `fill_application` / `resume_application` + wiring

**Files:**
- Modify: `src/cvflow/mcp/tools.py` (`TOOL_NAMES`, `CvflowTools.__init__`, new methods, `build_tools`)
- Test: `tests/test_mcp_tools.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_mcp_tools.py
def test_fill_application_is_gated_and_has_no_approve_tool():
    from cvflow.mcp.tools import TOOL_NAMES
    from cvflow.statemachine import Status, SubmissionBlocked
    from cvflow.storage import ApplicationStore

    assert "approve" not in TOOL_NAMES
    assert "fill_application" in TOOL_NAMES
    assert "resume_application" in TOOL_NAMES

    store = ApplicationStore(":memory:")
    store.add("indeed:1", "Acme", "Backend", "https://jobs/1")

    class _Auto:
        def fill(self, job_id):
            from cvflow.statemachine import guard_can_submit
            guard_can_submit(store.get(job_id).status)
            return {"status": "applied"}
        def resume(self, job_id, answer): return {"status": "applied"}

    tools = CvflowTools(
        store=store, knowledge=None, discovery=None, analyzer=None,
        tailor=None, automator=_Auto(),
    )
    with pytest.raises(SubmissionBlocked):
        tools.fill_application("indeed:1")
    assert not hasattr(tools, "approve")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_mcp_tools.py::test_fill_application_is_gated_and_has_no_approve_tool -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'automator'`.

- [ ] **Step 3: Implement**

In `src/cvflow/mcp/tools.py`: add to `TOOL_NAMES` (before the closing paren): `"fill_application", "resume_application",`.

Add `automator: Any = None` to `CvflowTools.__init__` params and `self._automator = automator`.

Add the two methods (near `submit`):

```python
    def fill_application(self, job_id: str) -> dict[str, Any]:
        """Fill the approved application; returns proof or a clarification request.

        Gated: the underlying Automator asserts guard_can_submit; only an
        APPROVED job (set out-of-band by the human /apply) reaches the browser.
        """
        return self._automator.fill(job_id)

    def resume_application(self, job_id: str, answer: str) -> dict[str, Any]:
        """Continue a paused application with the user's clarification answer."""
        return self._automator.resume(job_id, answer)
```

In `build_tools`, after `analyzer = JDAnalyzer(brain)`, construct the automator and pass it:

```python
    from cvflow.automation import Automator, FormFiller, SessionManager

    sessions = SessionManager(
        user_data_root=config.automation.storage_state_dir,
        headless=config.automation.headless,
        use_stealth=config.automation.use_stealth,
    )
    automator = Automator(
        store=store, knowledge=knowledge, provider=tailoring, sessions=sessions,
        filler_factory=FormFiller, notify=_telegram_notify,
        screenshot_dir=config.automation.storage_state_dir,
    )
```

and add `automator=automator,` to the `return CvflowTools(...)` call.

For `_telegram_notify`: add a module-level fallback that logs (the live Telegram transport is Hermes's; the hook/skill relays). Minimal:

```python
def _telegram_notify(message: str) -> None:
    """Surface a user-facing notice. Hermes relays MCP tool returns to Telegram;
    this also logs so a crash notice is never lost (invariant 3)."""
    import logging
    logging.getLogger("cvflow.automation").warning("NOTIFY: %s", message)
```

> Note: `SessionManager` starts Playwright at construction. If that's too eager for environments without Chromium, `build_tools` is only called in the live gateway where Chromium is installed; unit tests construct `CvflowTools` directly with a fake automator (as above), so they never hit this path.

- [ ] **Step 4: Run the full suite + lint/type**

Run: `pytest -q && ruff check . && mypy --strict src`
Expected: all green (Playwright browser tests skip if Chromium absent).

- [ ] **Step 5: Commit**

```bash
git add src/cvflow/mcp/tools.py tests/test_mcp_tools.py
git commit -m "feat(mcp): gated fill_application/resume_application tools (no approve)"
```

---

### Task 10: Full-suite verification + plan/build-plan sync

**Files:**
- Modify: `docs/superpowers/plans/2026-06-03-cvflow-build-plan.md` (Phase 9 checkbox + progress log)

- [ ] **Step 1: Run the complete verification**

Run: `pytest -q && ruff check . && mypy --strict src`
Expected: all green; new automation browser tests pass (or skip cleanly if Chromium absent — note which in the commit).

- [ ] **Step 2: End-to-end browser proof (with Chromium installed)**

Run: `playwright install chromium && pytest tests/test_automation_browser.py -v`
Expected: PASS — field fill, file upload, dropdown/checkbox, proof capture all green against the fixture form.

- [ ] **Step 3: Sync the build plan**

Tick `### [x] Phase 9 — Browser automation skill` and add a Progress-log entry dated 2026-06-04 summarizing: NimProvider closing the discovery/analyzer gap; the automation module (Option-C hybrid session, deterministic-first resolver, pause/resume, composed-answer disclosure, crash→failed+notify, proof capture); gate asserted at entry; Hermes native browser left disabled (approved deviation); test counts.

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/plans/2026-06-03-cvflow-build-plan.md
git commit -m "docs(phase-9): mark Phase 9 complete; sync progress log"
```

---

## Notes for the executor
- **Gate invariant:** never add an `approve` tool/method. `fill_application`/`resume_application` must reach the browser only via `Automator` which calls `guard_can_submit` first. Keep `hasattr(tools, "approve") is False`.
- **Never guess:** all "what fact goes here" logic lives in `resolve_field`; do not let the LLM pick raw values outside grounded `compose_answer`.
- **Never silent:** every crash path calls `notify` with the job URL and sets status `FAILED`.
- **Zero cost:** no new paid dependency; `NimProvider` uses stdlib only.
- **Skips:** Playwright browser tests use `pytest.importorskip` + a try/except skip when Chromium is missing — mirror Phase 6's Tectonic-absent skip; do not let them hard-fail CI on a browserless box.
