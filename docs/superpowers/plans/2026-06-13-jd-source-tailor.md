# JD-source fix + deterministic `/tailor` + tool-surface cleanup — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make analysis + résumé tailoring work end-to-end on data already in the DB (no login-walled
fetch), behind a config toggle, triggered by a deterministic single-job `/tailor` command, with the
dead automation tools removed from the brain.

**Architecture:** Persist the scraped JD text + fit/benchmark at discovery. `request_review` builds
its `JDAnalysis` either from the cached crux (default) or by re-analyzing the stored JD text
(`config.resume.use_jd_analysis`). A new detached `python -m cvflow.cron tailor <job_id>` runs it; a
new `/tailor` Hermes hook (single ordinal only) spawns that run. The four automation MCP tools and
their wiring are removed; the brain allowlist drops to 6 read/Q&A tools.

**Tech Stack:** Python 3.11, SQLite (`cvflow.storage`), pydantic crux JSON, `NimProvider`
(Mistral/Cerebras), Tectonic, Hermes command hooks, pytest + ruff + mypy --strict.

**Spec:** `docs/superpowers/specs/2026-06-13-jd-source-tailor-design.md`. Scope: **private repo
only.** Standing rules: TDD (failing test → watch fail → minimal code → pass → commit), Conventional
Commits, new config keys optional-with-default, gate invariant untouched (no `approve` tool), never
fail silently.

---

## File structure

| File | Responsibility | Task |
|---|---|---|
| `src/cvflow/storage/__init__.py` | new columns + `job_descriptions` table + meta/text methods | 1 |
| `src/cvflow/discovery/__init__.py` | persist stage writes meta + JD text | 2 |
| `src/cvflow/config.py`, `config.example.yaml` | `resume.use_jd_analysis` toggle | 3 |
| `src/cvflow/analysis/__init__.py` | `resolve_tailoring_analysis` (crux ↔ analysis) | 4 |
| `src/cvflow/mcp/tools.py`, `src/cvflow/mcp/server.py` | remove 4 automation tools; `request_review` toggle + half-state fix; `get_application` enrich | 5,6,7 |
| `src/cvflow/runlock.py` | `tailor_lock` | 8 |
| `src/cvflow/cron.py` | `run_tailor` + `tailor` subcommand | 8 |
| `src/cvflow/tailor_command.py` | pure `/tailor` handler (single ordinal) | 9 |
| `artifacts/hermes/hooks/cvflow-tailor/`, `artifacts/hermes/plugins/cvflow-tailor/` | Hermes hook + plugin | 9 |
| `artifacts/hermes/hermes-config.yaml`, `docs/deploy.md` | allowlist → 6 tools, register hook | 10 |

---

### Task 1: Storage — persist JD text + fit/benchmark

**Files:**
- Modify: `src/cvflow/storage/__init__.py` (dataclass `Application`, `_SCHEMA`, `_migrate`,
  `_row_to_app`, new methods)
- Test: `tests/test_storage.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_storage.py`:

```python
def test_migration_adds_meta_columns_and_jd_table(tmp_path):
    import sqlite3
    db = tmp_path / "old.db"
    # Simulate a pre-this-change DB: applications without the new columns, no job_descriptions.
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE applications (job_id TEXT PRIMARY KEY, company TEXT NOT NULL, "
        "role TEXT NOT NULL, jd_url TEXT NOT NULL, status TEXT NOT NULL, discovered_at TEXT NOT NULL);"
        "INSERT INTO applications (job_id, company, role, jd_url, status, discovered_at) "
        "VALUES ('x:1','Acme','Dev','http://u','discovered','2026-01-01T00:00:00+00:00');"
    )
    conn.commit(); conn.close()

    from cvflow.storage import ApplicationStore
    store = ApplicationStore(db)               # runs _migrate
    app = store.get("x:1")
    assert app is not None and app.benchmark is None and app.fit_score is None
    # second open is a no-op (idempotent)
    ApplicationStore(db)


def test_set_discovery_meta_and_jd_text_roundtrip(tmp_path):
    from cvflow.storage import ApplicationStore
    store = ApplicationStore(tmp_path / "db.sqlite")
    store.add("x:1", "Acme", "Backend Engineer", "http://u")
    store.set_discovery_meta(
        "x:1", benchmark=82, fit_score=7, fit_reason="strong Go match",
        concerns=["onsite"], cohort="N", ctc_lpa=12.5,
    )
    app = store.get("x:1")
    assert app.benchmark == 82 and app.fit_score == 7 and app.cohort == "N"
    assert app.fit_reason == "strong Go match" and app.ctc_lpa == 12.5
    import json
    assert json.loads(app.concerns) == ["onsite"]

    assert store.get_jd_text("x:1") is None
    store.set_jd_text("x:1", "full JD text here")
    assert store.get_jd_text("x:1") == "full JD text here"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_storage.py::test_migration_adds_meta_columns_and_jd_table tests/test_storage.py::test_set_discovery_meta_and_jd_text_roundtrip -v`
Expected: FAIL (`Application` has no `benchmark`; `set_discovery_meta`/`set_jd_text`/`get_jd_text` undefined).

- [ ] **Step 3: Extend the `Application` dataclass**

In `src/cvflow/storage/__init__.py`, add fields after `otp_deadline` (line ~64):

```python
    otp_deadline: str | None = None
    benchmark: int | None = None
    fit_score: int | None = None
    fit_reason: str | None = None
    concerns: str | None = None          # JSON-encoded list[str]
    cohort: str | None = None            # "M" | "N"
    ctc_lpa: float | None = None
```

- [ ] **Step 4: Add the `job_descriptions` table to `_SCHEMA`**

Append inside the `_SCHEMA` string (after the `decisions` table, before the closing `"""`):

```sql
CREATE TABLE IF NOT EXISTS job_descriptions (
    job_id      TEXT PRIMARY KEY,
    description TEXT NOT NULL,
    scraped_at  TEXT NOT NULL
);
```

- [ ] **Step 5: Add the new columns to `_migrate` and read them in `_row_to_app`**

In `_migrate`, extend the `added_columns` dict:

```python
        added_columns = {
            "tailored_pdf_path": "TEXT",
            "confirmation_ref": "TEXT",
            "proof_url": "TEXT",
            "proof_screenshot_path": "TEXT",
            "proof_page_title": "TEXT",
            "otp_deadline": "TEXT",
            "benchmark": "INTEGER",
            "fit_score": "INTEGER",
            "fit_reason": "TEXT",
            "concerns": "TEXT",
            "cohort": "TEXT",
            "ctc_lpa": "REAL",
        }
```

In `_row_to_app`, add the new fields to the `Application(...)` construction:

```python
            otp_deadline=row["otp_deadline"],
            benchmark=row["benchmark"],
            fit_score=row["fit_score"],
            fit_reason=row["fit_reason"],
            concerns=row["concerns"],
            cohort=row["cohort"],
            ctc_lpa=row["ctc_lpa"],
```

- [ ] **Step 6: Add the three new methods**

Add to `ApplicationStore` (near `set_tailored_pdf`):

```python
    def set_discovery_meta(
        self, job_id: str, *, benchmark: int | None, fit_score: int | None,
        fit_reason: str | None, concerns: list[str] | None, cohort: str | None,
        ctc_lpa: float | None,
    ) -> None:
        """Persist discovery ranking signals (additive; never touches status/approval)."""
        import json
        self._require(job_id)
        self._conn.execute(
            "UPDATE applications SET benchmark = ?, fit_score = ?, fit_reason = ?, "
            "concerns = ?, cohort = ?, ctc_lpa = ? WHERE job_id = ?",
            (benchmark, fit_score, fit_reason, json.dumps(concerns or []), cohort, ctc_lpa, job_id),
        )
        self._conn.commit()

    def set_jd_text(self, job_id: str, description: str) -> None:
        """Store the raw scraped JD text for later analysis / Q&A (upsert)."""
        self._conn.execute(
            "INSERT INTO job_descriptions (job_id, description, scraped_at) VALUES (?, ?, ?) "
            "ON CONFLICT(job_id) DO UPDATE SET description = excluded.description, "
            "scraped_at = excluded.scraped_at",
            (job_id, description, _now()),
        )
        self._conn.commit()

    def get_jd_text(self, job_id: str) -> str | None:
        row = self._conn.execute(
            "SELECT description FROM job_descriptions WHERE job_id = ?", (job_id,)
        ).fetchone()
        return row["description"] if row is not None else None
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `pytest tests/test_storage.py -v`
Expected: PASS (all storage tests, including the two new ones).

- [ ] **Step 8: Commit**

```bash
git add src/cvflow/storage/__init__.py tests/test_storage.py
git commit -m "feat(storage): persist JD text + fit/benchmark; idempotent migration"
```

---

### Task 2: Discovery persist stage writes the new data

**Files:**
- Modify: `src/cvflow/discovery/__init__.py` (the persist loop, currently `~:498-502`)
- Test: `tests/test_discovery.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_discovery.py` (follow the file's existing `DiscoveryService` construction
helper / fixtures; this test asserts persistence after a stubbed `discover`):

```python
def test_persist_stage_writes_meta_and_jd_text(discovery_service_with_one_ranked_job):
    # Fixture builds a DiscoveryService whose pipeline yields one BenchmarkedJob in cohort "N"
    # with posting.description set, benchmark=70, fit_score=6, fit_reason="ok", concerns=["x"],
    # ctc_lpa=None, and a real ApplicationStore at `store`.
    svc, store, job_id = discovery_service_with_one_ranked_job
    svc.discover(progress=lambda _m: None)
    app = store.get(job_id)
    assert app.fit_score == 6 and app.cohort == "N" and app.fit_reason == "ok"
    assert store.get_jd_text(job_id) == "the scraped JD description"
```

> If no such fixture exists, build the service inline mirroring the existing discovery tests
> (stub `_gather_rows`/distiller/fit to produce one `BenchmarkedJob`). Reuse the existing test's
> construction pattern — do not invent a new harness.

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_discovery.py::test_persist_stage_writes_meta_and_jd_text -v`
Expected: FAIL (`fit_score` is `None`; `get_jd_text` returns `None`).

- [ ] **Step 3: Update the persist loop**

In `src/cvflow/discovery/__init__.py`, replace the persist loop (currently):

```python
        for cohort in (result["M"], result["N"]):
            for bj in cohort:
                p = bj.posting
                if not self._store.exists(p.job_id):
                    self._store.add(p.job_id, p.company, p.title, p.url)
```

with:

```python
        for cohort in (result["M"], result["N"]):
            for bj in cohort:
                p = bj.posting
                if not self._store.exists(p.job_id):
                    self._store.add(p.job_id, p.company, p.title, p.url)
                # Persist ranking signals (upsert every run — scores can change) and the raw
                # JD text once, so tailoring/get_application work without re-fetching walled URLs.
                self._store.set_discovery_meta(
                    p.job_id, benchmark=bj.benchmark, fit_score=bj.fit_score,
                    fit_reason=bj.fit_reason, concerns=bj.concerns, cohort=bj.cohort,
                    ctc_lpa=bj.ctc_lpa,
                )
                if p.description and self._store.get_jd_text(p.job_id) is None:
                    self._store.set_jd_text(p.job_id, p.description)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_discovery.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cvflow/discovery/__init__.py tests/test_discovery.py
git commit -m "feat(discovery): persist fit/benchmark + raw JD text at the persist stage"
```

---

### Task 3: Config — the `resume.use_jd_analysis` toggle

**Files:**
- Modify: `src/cvflow/config.py` (`ResumeConfig` dataclass + the `resume=ResumeConfig(...)` loader
  at line ~346), `config.example.yaml`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_config.py`:

```python
def test_use_jd_analysis_defaults_false_when_absent(tmp_path):
    # Load the committed example (which omits the key) and assert the default.
    from cvflow.config import load_config
    cfg = load_config("config.example.yaml")
    assert cfg.resume.use_jd_analysis is False


def test_use_jd_analysis_reads_true(tmp_path, write_config):
    # write_config: existing helper that writes a full valid config dict to a temp file.
    cfg = write_config(resume_extra={"use_jd_analysis": True})
    assert cfg.resume.use_jd_analysis is True
```

> If `write_config` / a config-builder helper does not exist, set the key directly in the
> example-derived dict the file already uses for round-trip tests; reuse the existing pattern.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_config.py::test_use_jd_analysis_defaults_false_when_absent -v`
Expected: FAIL (`ResumeConfig` has no `use_jd_analysis`).

- [ ] **Step 3: Add the field + loader**

In `src/cvflow/config.py`, add to `ResumeConfig`:

```python
@dataclass(frozen=True)
class ResumeConfig:
    master_tex_path: str
    output_dir: str
    latex_compiler: str
    use_jd_analysis: bool = False
```

In the `resume=ResumeConfig(...)` construction (line ~346), add the optional load:

```python
        resume=ResumeConfig(
            master_tex_path=_get_str(res, "master_tex_path", "resume."),
            output_dir=_get_str(res, "output_dir", "resume."),
            latex_compiler=_get_str(res, "latex_compiler", "resume."),
            use_jd_analysis=_opt_bool(res, "use_jd_analysis", "resume.", False),
        ),
```

- [ ] **Step 4: Document the key in `config.example.yaml`**

Under the `resume:` block in `config.example.yaml`, add:

```yaml
  # false = tailor from the cached discovery crux (no extra LLM call, works on every ranked job);
  # true  = re-analyze the stored JD text per job for richer required/preferred fields.
  use_jd_analysis: false
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_config.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/cvflow/config.py config.example.yaml tests/test_config.py
git commit -m "feat(config): add optional resume.use_jd_analysis toggle (default false)"
```

---

### Task 4: `resolve_tailoring_analysis` (crux ↔ analysis, with crux fallback)

**Files:**
- Modify: `src/cvflow/analysis/__init__.py` (new function; add to `__all__`)
- Test: `tests/test_analysis.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_analysis.py`:

```python
import json
from cvflow.analysis import JDAnalysis, resolve_tailoring_analysis


class _FakeStore:
    def __init__(self, *, crux=None, jd_text=None, app=None):
        self._crux = crux; self._jd_text = jd_text; self._app = app
        self.saved = None
    def get_crux(self, job_id): return json.dumps(self._crux) if self._crux else None
    def get_jd_text(self, job_id): return self._jd_text
    def get(self, job_id): return self._app
    def save_analysis(self, job_id, analysis): self.saved = analysis


class _FakeAnalyzer:
    def __init__(self, result): self._result = result; self.calls = 0
    def analyze(self, text): self.calls += 1; return self._result


def test_crux_path_maps_three_fields():
    store = _FakeStore(crux={
        "must_have_skills": ["go", "python"],
        "tech_stack": ["go", "python", "postgresql", "redis"],
        "seniority_signal": "junior", "applicant_instructions": "mention OSS",
    })
    ana = resolve_tailoring_analysis(
        "x:1", store=store, analyzer=_FakeAnalyzer(None), use_jd_analysis=False,
    )
    assert ana.required_skills == ["go", "python", "postgresql", "redis"]
    assert ana.preferred_quals == ["postgresql", "redis"]
    assert ana.seniority == "junior"
    assert ana.applicant_instructions == ["mention OSS"]


def test_analysis_path_uses_stored_text_without_fetch():
    expected = JDAnalysis(required_skills=["go"], preferred_quals=[], seniority="mid",
                          tone="", applicant_instructions=[])
    analyzer = _FakeAnalyzer(expected)
    store = _FakeStore(jd_text="full JD text", crux={"tech_stack": ["x"]})
    ana = resolve_tailoring_analysis(
        "x:1", store=store, analyzer=analyzer, use_jd_analysis=True,
        fetch_fn=lambda url: (_ for _ in ()).throw(AssertionError("should not fetch")),
    )
    assert analyzer.calls == 1 and ana is expected and store.saved is expected


def test_analysis_path_falls_back_to_crux_and_notifies_on_failure():
    def boom(text): raise RuntimeError("LLM down")
    store = _FakeStore(jd_text="text", crux={"tech_stack": ["go"], "seniority_signal": "junior"})
    notices = []
    ana = resolve_tailoring_analysis(
        "x:1", store=store, analyzer=_FakeAnalyzer(None), use_jd_analysis=True,
        notify=notices.append,
        fetch_fn=lambda url: "text",
    )
    # _FakeAnalyzer(None).analyze returns None -> tailor.plan would fail, but here the analyzer
    # raising is simulated by patching analyze:
    store._analyzer = None
    assert ana.required_skills == ["go"]      # crux fallback
    assert notices and "falling back" in notices[0].lower()
```

> Note: in `test_analysis_path_falls_back...`, make the analyzer raise — replace `_FakeAnalyzer`
> with one whose `analyze` raises `RuntimeError`. (Adjust the fake; the assertion is: crux result +
> a notice containing "falling back".)

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_analysis.py -k tailoring_analysis -v`
Expected: FAIL (`resolve_tailoring_analysis` undefined).

- [ ] **Step 3: Implement the function**

Add to `src/cvflow/analysis/__init__.py` (and add `"resolve_tailoring_analysis"` to `__all__`):

```python
def _crux_to_analysis(job_id: str, store: Any) -> JDAnalysis:
    """Build a tailoring JDAnalysis from the cached crux JSON (no network, no LLM)."""
    crux_json = store.get_crux(job_id)
    if not crux_json:
        raise JDAnalysisError(
            f"no crux or JD analysis for {job_id}; run discovery first"
        )
    c = json.loads(crux_json)
    must = list(c.get("must_have_skills") or [])
    tech = list(c.get("tech_stack") or [])
    required = list(dict.fromkeys(must + tech))
    must_set = set(must)
    preferred = [t for t in tech if t not in must_set]
    instr = c.get("applicant_instructions")
    return JDAnalysis(
        required_skills=required,
        preferred_quals=preferred,
        seniority=str(c.get("seniority_signal") or ""),
        tone="",
        applicant_instructions=[instr] if instr else [],
    )


def resolve_tailoring_analysis(
    job_id: str, *, store: Any, analyzer: Any, use_jd_analysis: bool,
    notify: Callable[[str], None] | None = None, fetch_fn: Callable[[str], str] = fetch_jd,
) -> JDAnalysis:
    """Return the JDAnalysis driving tailoring.

    ``use_jd_analysis=False`` (default): derive from the cached crux — no network, no LLM call,
    works on every ranked job. ``True``: re-analyze the stored JD text (falling back to fetching
    the URL only if no text is stored). On any analysis-path failure, log + notify and fall back to
    the crux (never fail silently — invariant 3). Raises JDAnalysisError only if neither source
    exists.
    """
    if use_jd_analysis:
        try:
            text = store.get_jd_text(job_id)
            if not text:
                app = store.get(job_id)
                if app is None:
                    raise JDAnalysisError(f"no such job: {job_id}")
                text = fetch_fn(app.jd_url)
            analysis = analyzer.analyze(text)
            store.save_analysis(job_id, analysis)
            return analysis
        except Exception as exc:  # noqa: BLE001 — fall back to crux, never silent
            logger.warning("JD analysis failed for %s: %s; using crux", job_id, exc)
            if notify is not None:
                notify(f"⚠️ JD analysis failed for {job_id}; falling back to the cached crux.")
    return _crux_to_analysis(job_id, store)
```

Ensure the module imports near the top include `import json`, `from typing import Any`,
`from collections.abc import Callable`, and a module `logger` (add
`logger = logging.getLogger("cvflow.analysis")` + `import logging` if absent).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_analysis.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cvflow/analysis/__init__.py tests/test_analysis.py
git commit -m "feat(analysis): resolve_tailoring_analysis (crux-derived or JD-text, crux fallback)"
```

---

### Task 5: Remove the four automation MCP tools + their wiring

**Files:**
- Modify: `src/cvflow/mcp/tools.py` (`TOOL_NAMES`, the 4 methods, `__init__`, `build_tools`,
  the `guard_can_submit` import)
- Test: `tests/test_mcp_tools.py`
- Note: `src/cvflow/mcp/server.py` registers from `TOOL_NAMES` already — no change needed.

- [ ] **Step 1: Write the failing guard test**

Add to `tests/test_mcp_tools.py`:

```python
def test_tool_names_excludes_automation_and_approve():
    from cvflow.mcp.tools import TOOL_NAMES
    forbidden = {"approve", "submit", "fill_application", "resume_application", "submit_otp"}
    assert forbidden.isdisjoint(TOOL_NAMES)
    assert set(TOOL_NAMES) == {
        "ping", "discover", "analyze_jd", "list_applications", "get_application",
        "request_review", "compose_essay", "status_report",
    }
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_mcp_tools.py::test_tool_names_excludes_automation_and_approve -v`
Expected: FAIL (`submit`/`fill_application`/`resume_application`/`submit_otp` still present).

- [ ] **Step 3: Trim `TOOL_NAMES`**

In `src/cvflow/mcp/tools.py`:

```python
TOOL_NAMES: tuple[str, ...] = (
    "ping",
    "discover",
    "analyze_jd",
    "list_applications",
    "get_application",
    "request_review",
    "compose_essay",
    "status_report",
)
```

- [ ] **Step 4: Delete the four methods + their ctor params**

Delete the `submit`, `fill_application`, `resume_application`, `submit_otp` methods. In `__init__`,
remove the `automator` and `otp_coordinator` parameters and the `self._automator` / `self._otp`
assignments. Change the top import to drop `guard_can_submit`:

```python
from cvflow.statemachine import Status
```

- [ ] **Step 5: Remove the automation/auth wiring from `build_tools`**

Delete from `build_tools` the entire block that imports and constructs `Automator`, `FormFiller`,
`SessionManager`, `OtpCoordinator`, `TokenVault`, and the `HermesNotifier`-for-automator (the code
between `from cvflow.automation import ...` and the `return CvflowTools(...)`). Update the final
return to drop `automator=` and `otp_coordinator=`:

```python
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

(The `automation/` and `auth/` modules stay on disk — archival is deferred to the public-release
cut — but nothing in `mcp/` imports them after this step.)

- [ ] **Step 6: Run the tests to verify they pass**

Run: `pytest tests/test_mcp_tools.py tests/test_mcp_tools_phase8.py tests/test_mcp_server.py -v`
Expected: the new guard test PASSES. Delete or adjust any pre-existing test that asserted the four
removed tools/methods (e.g. phase-8 submit/fill tests) — those modules are gone by design.

- [ ] **Step 7: Verify lint/types + build_tools smoke**

Run: `ruff check src/cvflow/mcp/tools.py && mypy src/cvflow/mcp/tools.py`
Run: `.venv/bin/python -c "from cvflow.config import load_config; from cvflow.mcp.tools import build_tools; build_tools(load_config('config.yaml')); print('ok')"`
Expected: clean; prints `ok` (no Playwright/Automator import).

- [ ] **Step 8: Commit**

```bash
git add src/cvflow/mcp/tools.py tests/test_mcp_tools.py tests/test_mcp_tools_phase8.py
git commit -m "refactor(mcp): remove automation tools (submit/fill/resume/submit_otp) + wiring"
```

---

### Task 6: `request_review` — use the toggle + fix the half-state bug

**Files:**
- Modify: `src/cvflow/mcp/tools.py` (`request_review`, `__init__`, `build_tools`)
- Test: `tests/test_mcp_tools.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_mcp_tools.py` (use the file's existing `CvflowTools` construction helper; these
use fakes for store/analyzer/tailor):

```python
def test_request_review_tailors_via_crux_without_analysis(tools_with_fakes):
    # tools_with_fakes: CvflowTools whose store has a DISCOVERED job "x:1" with a crux but
    # NO jd_analyses row; use_jd_analysis=False; tailor returns a fake plan + pdf + diff.
    tools, store = tools_with_fakes
    out = tools.request_review("x:1")
    assert out["status"] == "pending_review"
    assert out["pdf_path"].endswith(".pdf")
    assert store.get("x:1").status.value == "pending_review"


def test_request_review_does_not_orphan_status_on_failure(tools_with_failing_tailor):
    # tailor.plan raises; status must stay DISCOVERED (the half-state bug fix).
    tools, store = tools_with_failing_tailor
    import pytest
    with pytest.raises(Exception):
        tools.request_review("x:1")
    assert store.get("x:1").status.value == "discovered"
```

> Build the fakes mirroring the existing `test_mcp_tools.py` helpers. The key behaviours:
> `store.get_crux` returns a crux JSON; `tailor.plan`/`compile_tailored`/`diff` are stubbed; the
> failing variant raises in `tailor.plan`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_mcp_tools.py -k request_review -v`
Expected: FAIL (today `request_review` requires a `jd_analyses` row and sets status before failing).

- [ ] **Step 3: Add ctor params**

In `CvflowTools.__init__`, add (after `essay_provider`):

```python
        essay_provider: Any = None,
        use_jd_analysis: bool = False,
        notify: Any = None,
    ) -> None:
        ...
        self._essay_provider = essay_provider
        self._use_jd_analysis = use_jd_analysis
        self._notify = notify
```

- [ ] **Step 4: Rewrite `request_review`**

```python
    def request_review(self, job_id: str, *, feedback: str | None = None) -> dict[str, Any]:
        """Tailor + compile the PDF, then move to PENDING_REVIEW. Never approves.

        Builds the JDAnalysis from the cached crux (default) or the stored JD text, per
        config.resume.use_jd_analysis. Status flips only AFTER a successful compile, so a tailoring
        failure never orphans the job in pending_review.
        """
        from cvflow.analysis import resolve_tailoring_analysis

        app = self._store.get(job_id)
        if app is None:
            raise UnknownJob(job_id)
        analysis = resolve_tailoring_analysis(
            job_id, store=self._store, analyzer=self._analyzer,
            use_jd_analysis=self._use_jd_analysis, notify=self._notify,
        )
        plan = self._tailor.plan(analysis, feedback=feedback)
        pdf = self._tailor.compile_tailored(plan, self._output_dir)
        if app.status is Status.DISCOVERED:
            self._store.set_status(job_id, Status.PENDING_REVIEW)
        self._store.set_tailored_pdf(job_id, str(pdf))
        return {
            "job_id": job_id,
            "status": Status.PENDING_REVIEW.value,
            "pdf_path": str(pdf),
            "diff": self._tailor.diff(plan),
            "analysis_summary": json.loads(analysis.to_json()),
            "instructions": (
                f"Reply /apply {job_id} to approve & apply, or /skip {job_id} to skip."
            ),
        }
```

- [ ] **Step 5: Wire `build_tools`**

In `build_tools`, pass the new args to the final `CvflowTools(...)`:

```python
        essay_provider=tailoring,
        use_jd_analysis=config.resume.use_jd_analysis,
        notify=HermesNotifier(),
```

Add `from cvflow.notify import HermesNotifier` to `build_tools`'s imports if not present.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `pytest tests/test_mcp_tools.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/cvflow/mcp/tools.py tests/test_mcp_tools.py
git commit -m "feat(mcp): request_review tailors via the configured JD source; fix pending_review orphan"
```

---

### Task 7: `get_application` — ordinal + scores + crux summary

**Files:**
- Modify: `src/cvflow/mcp/tools.py` (`get_application`)
- Test: `tests/test_mcp_tools.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_get_application_by_ordinal_with_scores_and_crux(tools_with_scored_job):
    # store has job "x:1" (fit_score=7, benchmark=80, cohort="N", concerns ['onsite'], a crux),
    # and digest_slots maps slot 4 -> "x:1".
    tools, store = tools_with_scored_job
    out = tools.get_application("4")              # ordinal
    assert out["job_id"] == "x:1"
    assert out["fit_score"] == 7 and out["benchmark"] == 80 and out["cohort"] == "N"
    assert out["concerns"] == ["onsite"]
    assert out["crux"]["role_family"] == "backend"
    assert out["crux"]["one_line"]


def test_get_application_unknown_ordinal_returns_none(tools_with_scored_job):
    tools, _ = tools_with_scored_job
    assert tools.get_application("99") is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_mcp_tools.py -k get_application -v`
Expected: FAIL (ordinal not resolved; scores/crux absent).

- [ ] **Step 3: Rewrite `get_application`**

```python
    def get_application(self, ref: str) -> dict[str, Any] | None:
        """Get / show ONE job's details — by job_id (e.g. "naukri:080626501910") OR by its digest
        ordinal (e.g. "4" → "details for job 4"). Returns status, link, company, role, dates, the
        fit/benchmark scores, and a crux summary (role family, must-have skills, one-line) so you
        can decide whether to /tailor it. None if not found.
        """
        job_id = ref
        if ":" not in str(ref):
            try:
                slot = int(ref)
            except (TypeError, ValueError):
                return None
            resolved = self._store.get_digest_slot(slot)
            if resolved is None:
                return None
            job_id = resolved
        app = self._store.get(job_id)
        if app is None:
            return None
        out: dict[str, Any] = {
            "job_id": app.job_id,
            "company": app.company,
            "role": app.role,
            "status": app.status.value,
            "jd_url": app.jd_url,
            "discovered_at": app.discovered_at,
        }
        for k in ("tailored_pdf_path", "applied_at", "confirmation_ref",
                  "benchmark", "fit_score", "fit_reason", "cohort", "ctc_lpa"):
            v = getattr(app, k, None)
            if v is not None:
                out[k] = v
        if app.concerns:
            try:
                out["concerns"] = json.loads(app.concerns)
            except (ValueError, TypeError):
                pass
        crux_json = self._store.get_crux(job_id)
        if crux_json:
            c = json.loads(crux_json)
            out["crux"] = {
                k: c.get(k) for k in
                ("role_family", "seniority_signal", "must_have_skills", "company_type", "one_line")
            }
        return out
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_mcp_tools.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cvflow/mcp/tools.py tests/test_mcp_tools.py
git commit -m "feat(mcp): get_application accepts an ordinal and surfaces scores + crux summary"
```

---

### Task 8: `tailor_lock` + `cron tailor <job_id>` subcommand

**Files:**
- Modify: `src/cvflow/runlock.py` (`tailor_lock`), `src/cvflow/cron.py` (`run_tailor` + `main` branch)
- Test: `tests/test_cron.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_cron.py`:

```python
def test_run_tailor_posts_pdf_and_diff(tmp_path):
    from cvflow.cron import run_tailor
    from contextlib import nullcontext

    class _Tools:
        def request_review(self, job_id):
            return {"job_id": job_id, "status": "pending_review",
                    "pdf_path": "/x/r.pdf", "diff": "Section order: experience → projects"}
    notices = []
    run_tailor("x:1", tools=_Tools(), notify=notices.append, lock=nullcontext())
    assert any("/x/r.pdf" in m for m in notices)
    assert any("/apply x:1" in m for m in notices)


def test_run_tailor_notifies_on_failure(tmp_path):
    from cvflow.cron import run_tailor
    from contextlib import nullcontext

    class _Tools:
        def request_review(self, job_id): raise RuntimeError("compile failed")
    notices = []
    import pytest
    with pytest.raises(RuntimeError):
        run_tailor("x:1", tools=_Tools(), notify=notices.append, lock=nullcontext())
    assert any("failed" in m.lower() for m in notices)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_cron.py -k run_tailor -v`
Expected: FAIL (`run_tailor` undefined).

- [ ] **Step 3: Add `tailor_lock` to `runlock.py`**

In `src/cvflow/runlock.py`, add to `__all__` (`"tailor_lock"`) and define:

```python
@contextmanager
def tailor_lock(path: str = "data/tailor.lock") -> Iterator[None]:
    """Serialize tailoring runs (one /tailor at a time; protects Tectonic + LLM concurrency)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(p), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise AlreadyRunning(f"tailor lock held: {path}") from exc
        yield
    finally:
        os.close(fd)
```

- [ ] **Step 4: Add `run_tailor` + the `tailor` subcommand to `cron.py`**

Add the function:

```python
def run_tailor(job_id: str, *, tools: Any, notify: Any, lock: Any = None) -> None:
    """Tailor one job (detached entrypoint for /tailor). Posts PDF + diff; errors notify."""
    from cvflow.runlock import AlreadyRunning, tailor_lock

    cm = lock if lock is not None else tailor_lock()
    acquired = False
    try:
        with cm:
            acquired = True
            try:
                result = tools.request_review(job_id)
                notify(
                    f"✂️ Tailored — {job_id}\n📄 {result['pdf_path']}\n\n{result['diff']}\n\n"
                    f"Reply /apply {job_id} to approve, or /skip {job_id} to skip."
                )
            except Exception as exc:  # noqa: BLE001 — never silent
                notify(f"⚠️ Tailoring failed for {job_id}: {exc}")
                raise
    except AlreadyRunning:
        if acquired:
            raise
        notify("⏳ A tailoring run is already in progress — try again shortly.")
```

In `main`, add this branch immediately after the `print-hermes-schedule` branch:

```python
    if job == "tailor":
        if len(args) < 2:
            raise SystemExit("usage: python -m cvflow.cron tailor <job_id>")
        from cvflow.mcp.tools import build_tools
        from cvflow.notify import HermesNotifier

        cfg = load_config("config.yaml")
        run_tailor(args[1], tools=build_tools(cfg), notify=HermesNotifier())
        return
```

Update the usage string at the top of `main` to include `tailor <job_id>`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `pytest tests/test_cron.py -k run_tailor -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/cvflow/runlock.py src/cvflow/cron.py tests/test_cron.py
git commit -m "feat(cron): tailor_lock + run_tailor + 'cron tailor <job_id>' subcommand"
```

---

### Task 9: `/tailor` command handler + Hermes hook/plugin

**Files:**
- Create: `src/cvflow/tailor_command.py`
- Create: `artifacts/hermes/hooks/cvflow-tailor/HOOK.yaml`,
  `artifacts/hermes/hooks/cvflow-tailor/handler.py`,
  `artifacts/hermes/plugins/cvflow-tailor/plugin.yaml`
- Test: `tests/test_tailor_command.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_tailor_command.py`:

```python
from cvflow.tailor_command import handle_tailor_command


class _Store:
    def __init__(self, slots): self._slots = slots
    def get_digest_slot(self, n): return self._slots.get(n)
    def digest_slots(self): return list(self._slots.values())


def _store(): return _Store({1: "a:1", 2: "b:2", 4: "d:4"})


def test_single_ordinal_spawns():
    spawned = []
    res = handle_tailor_command(
        args="4", user_id="u", authorized_user_id="u", store=_store(),
        spawn=spawned.append, is_locked=lambda: False,
    )
    assert res.handled and spawned == ["d:4"]


def test_multiple_ordinals_rejected():
    spawned = []
    res = handle_tailor_command(
        args="1 2", user_id="u", authorized_user_id="u", store=_store(),
        spawn=spawned.append, is_locked=lambda: False,
    )
    assert res.handled and not spawned and "one job" in res.message.lower()


def test_unauthorized_not_handled():
    res = handle_tailor_command(
        args="1", user_id="intruder", authorized_user_id="u", store=_store(),
        spawn=lambda j: None, is_locked=lambda: False,
    )
    assert res.handled is False


def test_locked_does_not_spawn():
    spawned = []
    res = handle_tailor_command(
        args="4", user_id="u", authorized_user_id="u", store=_store(),
        spawn=spawned.append, is_locked=lambda: True,
    )
    assert res.handled and not spawned and "in progress" in res.message.lower()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_tailor_command.py -v`
Expected: FAIL (`cvflow.tailor_command` undefined).

- [ ] **Step 3: Implement `tailor_command.py`**

```python
"""Deterministic ``/tailor`` trigger — single job, off the agent loop.

A Hermes ``command:tailor`` hook calls :func:`handle_tailor_command` in the gateway process. It
resolves exactly ONE digest ordinal to a job_id and spawns a detached tailoring run; it never
approves. ``spawn`` and ``is_locked`` are injected so tests never fork or touch a real lock.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from cvflow.gate import resolve_targets

__all__ = ["TailorResult", "handle_tailor_command"]


@dataclass(frozen=True)
class TailorResult:
    handled: bool
    message: str


def _same_user(a: object, b: object) -> bool:
    return str(a).strip() == str(b).strip()


def handle_tailor_command(
    *,
    args: str,
    user_id: object,
    authorized_user_id: object,
    store: object,
    spawn: Callable[[str], None],
    is_locked: Callable[[], bool],
) -> TailorResult:
    if not _same_user(user_id, authorized_user_id):
        return TailorResult(handled=False, message="")
    if not args.strip():
        return TailorResult(handled=True, message="⚠️ Usage: /tailor <number> — one job, e.g. /tailor 4")
    targets, unknown = resolve_targets(args, store)
    if unknown or len(targets) != 1:
        return TailorResult(handled=True, message="⚠️ One job at a time, e.g. /tailor 4")
    if is_locked():
        return TailorResult(
            handled=True, message="⏳ A tailoring run is already in progress — try again shortly."
        )
    spawn(targets[0])
    return TailorResult(handled=True, message=f"✂️ Tailoring {targets[0]} — I'll post the PDF + diff here.")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_tailor_command.py -v`
Expected: PASS.

- [ ] **Step 5: Create the Hermes hook + plugin (mirrors cvflow-discover)**

`artifacts/hermes/hooks/cvflow-tailor/HOOK.yaml`:

```yaml
name: cvflow-tailor
description: Deterministic single-job résumé tailoring trigger for cvflow (/tailor).
events:
  - command:tailor
```

`artifacts/hermes/plugins/cvflow-tailor/plugin.yaml`:

```yaml
name: cvflow-tailor
version: 1.0.0
description: "Registers cvflow's /tailor slash command so the gateway treats it as a known command. The cvflow-tailor HOOK intercepts command:tailor and spawns a detached single-job tailoring run — a trigger, never an approval."
author: "cvflow"
kind: standalone
```

`artifacts/hermes/hooks/cvflow-tailor/handler.py`:

```python
"""Hermes command hook: routes /tailor to cvflow's deterministic single-job tailoring trigger.

Runs in the gateway process, OUTSIDE the agent loop. Resolves ONE ordinal and spawns a detached
`python -m cvflow.cron tailor <job_id>` (systemd --user transient unit, like /discover), then acks.
The detached run posts the PDF + diff via HermesNotifier. Unauthorized users -> {} (fall through).
This is a trigger, never an approval — the gate invariant is untouched.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from typing import Any


def _repo_root() -> str:
    return os.environ.get("CVFLOW_ROOT", os.path.expanduser("~/cvflow"))


def _ensure_import() -> None:
    root_src = os.path.join(_repo_root(), "src")
    if root_src not in sys.path:
        sys.path.insert(0, root_src)


def _build_store():
    _ensure_import()
    from cvflow.config import load_config
    from cvflow.storage import ApplicationStore

    root = _repo_root()
    cfg = load_config(os.path.join(root, "config.yaml"))
    db_path = cfg.storage.db_path
    if not os.path.isabs(db_path):
        db_path = os.path.join(root, db_path)
    return ApplicationStore(db_path)


def _authorized_user_id():
    _ensure_import()
    from cvflow.config import load_config

    return load_config(os.path.join(_repo_root(), "config.yaml")).telegram.authorized_user_id


def _is_locked() -> bool:
    _ensure_import()
    from cvflow.runlock import is_locked

    return is_locked(os.path.join(_repo_root(), "data", "tailor.lock"))


def _spawn(job_id: str) -> None:
    root = _repo_root()
    python = os.path.join(root, ".venv", "bin", "python")
    os.makedirs(os.path.join(root, "data"), exist_ok=True)
    ts = int(time.time())
    log_path = os.path.join(root, "data", f"tailor-{ts}.log")
    uid = os.getuid()
    env = dict(os.environ)
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{uid}")
    env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path=/run/user/{uid}/bus")
    inner = f"cd {root!r} && exec {python!r} -m cvflow.cron tailor {job_id!r} >> {log_path!r} 2>&1"
    try:
        subprocess.run(  # noqa: S603 — fixed argv, no shell injection (paths/ids are ours)
            ["systemd-run", "--user", "--collect", f"--unit=cvflow-tailor-{ts}",
             "bash", "-c", inner],
            env=env, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
        )
    except (OSError, subprocess.CalledProcessError):
        with open(log_path, "a") as log:
            log.write("systemd-run --user failed; falling back to detached Popen\n")
            log.flush()
            subprocess.Popen(  # noqa: S603 — fixed argv, no shell
                [python, "-m", "cvflow.cron", "tailor", job_id],
                cwd=root, start_new_session=True, stdout=log, stderr=subprocess.STDOUT,
            )


async def handle(event_type: str, context: dict[str, Any]) -> dict[str, Any]:
    try:
        _ensure_import()
        from cvflow.tailor_command import handle_tailor_command

        res = handle_tailor_command(
            args=str(context.get("args", "")),
            user_id=context.get("user_id"),
            authorized_user_id=_authorized_user_id(),
            store=_build_store(),
            spawn=_spawn,
            is_locked=_is_locked,
        )
        if not res.handled:
            return {}
        return {"decision": "handled", "message": res.message}
    except Exception:  # noqa: BLE001 — never leak a stack trace; fall through
        return {}
```

- [ ] **Step 6: Commit**

```bash
git add src/cvflow/tailor_command.py tests/test_tailor_command.py artifacts/hermes/hooks/cvflow-tailor artifacts/hermes/plugins/cvflow-tailor
git commit -m "feat(tailor): deterministic single-job /tailor command + Hermes hook/plugin"
```

---

### Task 10: Brain allowlist + deploy docs

**Files:**
- Modify: `artifacts/hermes/hermes-config.yaml` (`tools.include` lines 15-17; `plugins.enabled`),
  `docs/deploy.md`
- Test: none (config/docs); verified live at deploy.

- [ ] **Step 1: Trim the allowlist**

In `artifacts/hermes/hermes-config.yaml`, replace the `tools.include` list (lines 15-17) with the
6 read/Q&A tools (tailoring is deterministic-only now):

```yaml
    tools:
      include: [ping, discover, list_applications, get_application,
            compose_essay, status_report]
```

- [ ] **Step 2: Register the `/tailor` plugin**

In the `plugins.enabled` block (line ~631):

```yaml
plugins:
  enabled:
    - cvflow-gate
    - cvflow-discover
    - cvflow-tailor
```

- [ ] **Step 3: Document the flow + deploy steps in `docs/deploy.md`**

Add a short section: the new flow `/discover → ask "details for job N" → /tailor N → /apply N`;
the `resume.use_jd_analysis` toggle (default false = crux); and the live-box deploy steps:
copy `artifacts/hermes/hooks/cvflow-tailor/` + `artifacts/hermes/plugins/cvflow-tailor/` into
`~/.hermes/`, set `tools.include` + `plugins.enabled` in the live `~/.hermes/config.yaml`, and
`sudo systemctl restart hermes-gateway` (+ `/new` the Telegram session if the brain config changed).
Note: back up `data/cvflow.db` before the first run so the additive migration is reversible.

- [ ] **Step 4: Full suite + lint + types**

Run: `pytest && ruff check . && mypy src`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add artifacts/hermes/hermes-config.yaml docs/deploy.md
git commit -m "feat(hermes): allowlist down to 6 read tools; register /tailor; deploy docs"
```

---

## Self-review

**Spec coverage:**
- JD-source toggle (both paths) → Tasks 3, 4, 6. ✓
- Persist raw JD text + fit/benchmark → Tasks 1, 2. ✓
- `request_review` half-state fix → Task 6. ✓
- `get_application` ordinal + scores + crux summary → Task 7. ✓
- Deterministic single-job `/tailor`, detached → Tasks 8, 9. ✓
- Remove 4 automation tools + wiring; brain allowlist → Tasks 5, 10. ✓
- Gate invariant untouched (no `approve`) → guard test in Task 5; `statemachine`/`gate` unchanged. ✓
- Config optional-with-default → Task 3 (`_opt_bool`, default false; example loads). ✓

**Type consistency:** `resolve_tailoring_analysis(job_id, *, store, analyzer, use_jd_analysis,
notify, fetch_fn)` is defined in Task 4 and called identically in Task 6. `run_tailor(job_id, *,
tools, notify, lock)` defined and called consistently (Task 8). `handle_tailor_command(*, args,
user_id, authorized_user_id, store, spawn, is_locked)` defined (Task 9) and invoked identically by
the hook. `set_discovery_meta` / `set_jd_text` / `get_jd_text` signatures match between Task 1
(definition) and Task 2/7 (use). `get_application(ref)` rename is reflected in the Task 10 docstring
intent and the Task 7 tests.

**Placeholder scan:** the two test-fixture notes (Task 2, Task 4 fallback analyzer) instruct reuse
of existing harness patterns rather than leaving code blank; all code steps contain real code.

## Notes for the executor
- One live job is currently stuck in `pending_review` with no analysis (the pre-fix half-state);
  do **not** mutate it — Task 6 prevents new ones.
- The Hermes hook (`artifacts/hermes/hooks/cvflow-tailor/handler.py`) is thin wiring mirroring the
  tested `cvflow-discover`/`cvflow-gate` hooks; the testable logic lives in `tailor_command.py`.
- After merge, deploy per Task 10 step 3 and verify live: `/discover` → `get_application` shows
  scores+crux → `/tailor N` returns PDF+diff under both `use_jd_analysis` values → `/apply N` works.
