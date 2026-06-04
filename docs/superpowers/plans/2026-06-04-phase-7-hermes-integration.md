# Phase 7 — Hermes Integration (cvflow MCP server) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose cvflow's existing deterministic domain modules to the running Hermes Agent as a local **STDIO MCP server**, so the agent can call cvflow skills from Telegram — while the approval gate stays un-bypassable by construction (no `approve` tool on the surface).

**Architecture:** Two layers. (1) A pure-Python **dispatcher** `CvflowTools` (in `src/cvflow/mcp/tools.py`) that holds the wired-up domain objects (`ApplicationStore`, `KnowledgeBase`, `DiscoveryService`, `JDAnalyzer`, `ResumeTailor`) and exposes one method per skill: `ping`, `discover`, `analyze_jd`, `list_applications`, `get_application`, `request_review`, `submit`. It contains **no `approve` method**. `submit` calls `statemachine.guard_can_submit` and raises unless `status == approved`. (2) A thin **FastMCP server adapter** (`src/cvflow/mcp/server.py`) that registers exactly the methods named in a `TOOL_NAMES` allowlist and runs over stdio. All behaviour tests target the dispatcher (no `mcp` import, no subprocess); the adapter is exercised only by a lightweight registration test.

**Tech Stack:** Python 3.11+, `mcp>=1.0` (official MCP SDK, FastMCP server — open-source library, no per-call cost), existing cvflow modules, pytest.

**Gate invariant (must hold by construction):**
- The agent can *call* `request_review` (moves `discovered → pending_review`) and `submit` (asserts `approved`, else raises).
- There is **no** tool, method, or code path on the MCP surface that produces `approved`. Only the human Telegram approval callback (Phase 8) will call `ApplicationStore.approve()`, which lives off the MCP surface.
- Tests prove: `submit` raises for every non-approved status; `submit` succeeds only after an out-of-band `store.approve()`; `"approve"` is absent from `TOOL_NAMES`; `CvflowTools` has no `approve` attribute.

---

## File Structure

- Create: `src/cvflow/mcp/__init__.py` — package marker; re-exports `CvflowTools`, `TOOL_NAMES`, `build_tools`.
- Create: `src/cvflow/mcp/tools.py` — `CvflowTools` dispatcher + `build_tools(config)` factory + `TOOL_NAMES` allowlist constant.
- Create: `src/cvflow/mcp/server.py` — FastMCP adapter; `build_server(tools)` + `main()` stdio entrypoint.
- Create: `src/cvflow/mcp/__main__.py` — `python -m cvflow.mcp` → `server.main()`.
- Create: `tests/test_mcp_tools.py` — all dispatcher behaviour + gate tests.
- Create: `tests/test_mcp_server.py` — adapter registration / allowlist test.
- Modify: `pyproject.toml` — add `mcp>=1.0` to `dependencies`; add `mcp.*` mypy override if needed.
- Modify: `requirements.txt` — add `mcp>=1.0` (deploy pin list).
- Modify (LIVE, not committed): `~/.hermes/config.yaml` — add `mcp_servers.cvflow` with `tools.include` allowlist.
- Modify: `docs/superpowers/plans/2026-06-03-cvflow-build-plan.md` — tick Phase 7 box + progress log.

---

## Task 1: Add the `mcp` dependency

**Files:**
- Modify: `pyproject.toml`
- Modify: `requirements.txt`

- [ ] **Step 1: Add `mcp` to pyproject dependencies**

In `pyproject.toml`, add to the `dependencies` list:

```toml
    "mcp>=1.0",                # MCP SDK (FastMCP stdio server exposing cvflow skills to Hermes)
```

- [ ] **Step 2: Add `mcp` to requirements.txt**

Append a line mirroring the pin:

```
mcp>=1.0
```

- [ ] **Step 3: Install into the venv**

Run: `.venv/bin/pip install "mcp>=1.0"`
Expected: installs `mcp` and its deps; exits 0.

- [ ] **Step 4: Verify import**

Run: `.venv/bin/python -c "from mcp.server.fastmcp import FastMCP; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml requirements.txt
git commit -m "chore(mcp): add mcp SDK dependency for the Hermes stdio server"
```

---

## Task 2: `ping` health skill (the round-trip proof)

**Files:**
- Create: `src/cvflow/mcp/__init__.py`
- Create: `src/cvflow/mcp/tools.py`
- Test: `tests/test_mcp_tools.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_mcp_tools.py`:

```python
from cvflow.mcp.tools import CvflowTools


def _tools(tmp_path):
    """Build a CvflowTools wired to throwaway in-memory-ish deps for tests.

    Domain objects that need live services (discovery/analysis/resume) are
    injected per-test; ping needs none, so pass None for them here.
    """
    from cvflow.storage import ApplicationStore

    return CvflowTools(
        store=ApplicationStore(":memory:"),
        knowledge=None,
        discovery=None,
        analyzer=None,
        tailor=None,
    )


def test_ping_returns_ok(tmp_path):
    tools = _tools(tmp_path)
    result = tools.ping()
    assert result["status"] == "ok"
    assert result["service"] == "cvflow"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_mcp_tools.py::test_ping_returns_ok -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cvflow.mcp'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/cvflow/mcp/__init__.py`:

```python
"""cvflow's MCP surface — exposes deterministic domain skills to Hermes.

The approval gate stays un-bypassable by construction: this package exposes NO
``approve`` tool. The agent may call ``request_review`` and ``submit`` (which
asserts ``status == approved`` and raises otherwise via
:func:`cvflow.statemachine.guard_can_submit`), but only the human Telegram
approval callback (Phase 8) reaches :meth:`cvflow.storage.ApplicationStore.approve`.
"""

from cvflow.mcp.tools import TOOL_NAMES, CvflowTools, build_tools

__all__ = ["CvflowTools", "TOOL_NAMES", "build_tools"]
```

Create `src/cvflow/mcp/tools.py`:

```python
"""The dispatcher layer: one method per MCP skill, dispatching to real modules.

Pure Python — no MCP transport here, so it is fully unit-testable. The FastMCP
adapter in :mod:`cvflow.mcp.server` registers exactly the methods named in
:data:`TOOL_NAMES`.

Gate invariant: there is intentionally NO ``approve`` method on this class.
"""

from __future__ import annotations

from typing import Any

# The exact set of skills exposed to Hermes. Mirrored by the tools.include
# allowlist in ~/.hermes/config.yaml. "approve" MUST NEVER appear here.
TOOL_NAMES: tuple[str, ...] = (
    "ping",
    "discover",
    "analyze_jd",
    "list_applications",
    "get_application",
    "request_review",
    "submit",
)


class CvflowTools:
    """Wires cvflow's domain objects behind a flat skill surface for Hermes."""

    def __init__(
        self,
        *,
        store: Any,
        knowledge: Any,
        discovery: Any,
        analyzer: Any,
        tailor: Any,
    ) -> None:
        self._store = store
        self._knowledge = knowledge
        self._discovery = discovery
        self._analyzer = analyzer
        self._tailor = tailor

    def ping(self) -> dict[str, str]:
        """Liveness check — proves the Hermes↔cvflow round-trip end to end."""
        return {"status": "ok", "service": "cvflow"}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_mcp_tools.py::test_ping_returns_ok -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cvflow/mcp/__init__.py src/cvflow/mcp/tools.py tests/test_mcp_tools.py
git commit -m "feat(mcp): add CvflowTools dispatcher with ping health skill"
```

---

## Task 3: Storage read skills — `list_applications` + `get_application`

**Files:**
- Modify: `src/cvflow/mcp/tools.py`
- Test: `tests/test_mcp_tools.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_mcp_tools.py`:

```python
from cvflow.statemachine import Status


def test_list_and_get_applications_dispatch_to_store(tmp_path):
    from cvflow.storage import ApplicationStore

    store = ApplicationStore(":memory:")
    store.add("indeed:1", "Acme", "Backend Engineer", "https://x/1")
    store.add("indeed:2", "Globex", "Platform Engineer", "https://x/2")
    tools = CvflowTools(
        store=store, knowledge=None, discovery=None, analyzer=None, tailor=None
    )

    listed = tools.list_applications(status="discovered")
    assert {a["job_id"] for a in listed} == {"indeed:1", "indeed:2"}
    assert listed[0]["company"] in {"Acme", "Globex"}

    one = tools.get_application("indeed:1")
    assert one["role"] == "Backend Engineer"
    assert one["status"] == Status.DISCOVERED.value


def test_get_application_missing_returns_none(tmp_path):
    from cvflow.storage import ApplicationStore

    tools = CvflowTools(
        store=ApplicationStore(":memory:"),
        knowledge=None, discovery=None, analyzer=None, tailor=None,
    )
    assert tools.get_application("nope:0") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_mcp_tools.py -k applications -v`
Expected: FAIL — `AttributeError: 'CvflowTools' object has no attribute 'list_applications'`.

- [ ] **Step 3: Write minimal implementation**

Add to `CvflowTools` in `src/cvflow/mcp/tools.py` (and add `from dataclasses import asdict` import, plus `from cvflow.storage import Application` and `from cvflow.statemachine import Status` at top):

```python
    @staticmethod
    def _app_to_dict(app: "Application") -> dict[str, Any]:
        d = asdict(app)
        d["status"] = app.status.value  # StrEnum → plain string for JSON
        return d

    def list_applications(self, status: str) -> list[dict[str, Any]]:
        """Return application records in a given status (read-only)."""
        apps = self._store.list_by_status(Status(status))
        return [self._app_to_dict(a) for a in apps]

    def get_application(self, job_id: str) -> dict[str, Any] | None:
        """Return one application record by job_id, or None if unknown."""
        app = self._store.get(job_id)
        return self._app_to_dict(app) if app is not None else None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_mcp_tools.py -k applications -v`
Expected: PASS (both tests).

- [ ] **Step 5: Commit**

```bash
git add src/cvflow/mcp/tools.py tests/test_mcp_tools.py
git commit -m "feat(mcp): add list_applications + get_application read skills"
```

---

## Task 4: The gate skills — `request_review` + `submit`

**Files:**
- Modify: `src/cvflow/mcp/tools.py`
- Test: `tests/test_mcp_tools.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_mcp_tools.py`:

```python
import pytest

from cvflow.statemachine import SubmissionBlocked


def _store_with_job():
    from cvflow.storage import ApplicationStore

    store = ApplicationStore(":memory:")
    store.add("indeed:9", "Acme", "Backend Engineer", "https://x/9")
    return store


def test_request_review_moves_to_pending_review():
    store = _store_with_job()
    tools = CvflowTools(
        store=store, knowledge=None, discovery=None, analyzer=None, tailor=None
    )
    tools.request_review("indeed:9")
    assert store.get("indeed:9").status == Status.PENDING_REVIEW


def test_submit_blocks_when_not_approved():
    store = _store_with_job()
    tools = CvflowTools(
        store=store, knowledge=None, discovery=None, analyzer=None, tailor=None
    )
    tools.request_review("indeed:9")  # now pending_review, still NOT approved
    with pytest.raises(SubmissionBlocked):
        tools.submit("indeed:9")


def test_submit_blocks_for_freshly_discovered():
    store = _store_with_job()
    tools = CvflowTools(
        store=store, knowledge=None, discovery=None, analyzer=None, tailor=None
    )
    with pytest.raises(SubmissionBlocked):
        tools.submit("indeed:9")


def test_submit_succeeds_only_after_out_of_band_approval():
    """The ONLY way submit passes the guard is store.approve() — which is NOT
    on the MCP surface (it is the human Telegram callback's sole caller)."""
    store = _store_with_job()
    tools = CvflowTools(
        store=store, knowledge=None, discovery=None, analyzer=None, tailor=None
    )
    tools.request_review("indeed:9")
    store.approve("indeed:9")  # simulates the human approval callback (off-surface)
    result = tools.submit("indeed:9")
    assert result["job_id"] == "indeed:9"
    assert result["status"] == Status.APPROVED.value


def test_cvflowtools_has_no_approve_method():
    store = _store_with_job()
    tools = CvflowTools(
        store=store, knowledge=None, discovery=None, analyzer=None, tailor=None
    )
    assert not hasattr(tools, "approve")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/test_mcp_tools.py -k "review or submit or approve" -v`
Expected: FAIL — `AttributeError: 'CvflowTools' object has no attribute 'request_review'`.

- [ ] **Step 3: Write minimal implementation**

Add to `CvflowTools` (add `from cvflow.statemachine import Status, guard_can_submit` to imports):

```python
    def request_review(self, job_id: str) -> dict[str, Any]:
        """Move an application discovered → pending_review.

        This is as far as the agent can drive status toward submission. It can
        NEVER reach ``approved`` — that is solely the human callback's path.
        """
        self._store.set_status(job_id, Status.PENDING_REVIEW)
        app = self._store.get(job_id)
        return self._app_to_dict(app)

    def submit(self, job_id: str) -> dict[str, Any]:
        """Assert the gate, then return the record cleared for submission.

        Raises :class:`cvflow.statemachine.SubmissionBlocked` unless the record
        is ``approved``. Actual form-filling is wired in Phase 9; this entrypoint
        exists now to prove the gate blocks the agent.
        """
        app = self._store.get(job_id)
        if app is None:
            from cvflow.storage import UnknownJob

            raise UnknownJob(f"no such job_id: {job_id}")
        guard_can_submit(app.status)  # raises unless approved
        return self._app_to_dict(app)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_mcp_tools.py -k "review or submit or approve" -v`
Expected: PASS (all 5).

- [ ] **Step 5: Commit**

```bash
git add src/cvflow/mcp/tools.py tests/test_mcp_tools.py
git commit -m "feat(mcp): add request_review + submit gate skills (submit asserts approved)"
```

---

## Task 5: `discover` skill — dispatch to `DiscoveryService`

**Files:**
- Modify: `src/cvflow/mcp/tools.py`
- Test: `tests/test_mcp_tools.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_mcp_tools.py`:

```python
def test_discover_dispatches_to_discovery_service():
    from cvflow.discovery import JobPosting, RankedJob

    class FakeDiscovery:
        def __init__(self):
            self.called = False

        def discover(self):
            self.called = True
            return [
                RankedJob(
                    posting=JobPosting(
                        job_id="indeed:7", title="Backend Engineer", company="Acme",
                        location="Remote", description="...", url="https://x/7",
                        site="indeed", date_posted="2026-06-04",
                    ),
                    summary="Strong fit",
                    rationale="Python + SQL match",
                )
            ]

    fake = FakeDiscovery()
    tools = CvflowTools(
        store=None, knowledge=None, discovery=fake, analyzer=None, tailor=None
    )
    out = tools.discover()
    assert fake.called is True
    assert out[0]["job_id"] == "indeed:7"
    assert out[0]["title"] == "Backend Engineer"
    assert out[0]["summary"] == "Strong fit"
    assert out[0]["rationale"] == "Python + SQL match"
    assert out[0]["url"] == "https://x/7"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_mcp_tools.py -k discover -v`
Expected: FAIL — `AttributeError: 'CvflowTools' object has no attribute 'discover'`.

- [ ] **Step 3: Write minimal implementation**

Add to `CvflowTools`:

```python
    def discover(self) -> list[dict[str, Any]]:
        """Run daily discovery → ranked top-N. Persists discovered jobs (store)."""
        ranked = self._discovery.discover()
        return [
            {
                "job_id": rj.posting.job_id,
                "title": rj.posting.title,
                "company": rj.posting.company,
                "location": rj.posting.location,
                "url": rj.posting.url,
                "summary": rj.summary,
                "rationale": rj.rationale,
            }
            for rj in ranked
        ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_mcp_tools.py -k discover -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cvflow/mcp/tools.py tests/test_mcp_tools.py
git commit -m "feat(mcp): add discover skill dispatching to DiscoveryService"
```

---

## Task 6: `analyze_jd` skill — fetch + analyze + persist

**Files:**
- Modify: `src/cvflow/mcp/tools.py`
- Test: `tests/test_mcp_tools.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_mcp_tools.py`:

```python
def test_analyze_jd_dispatches_and_persists():
    from cvflow.analysis import JDAnalysis
    from cvflow.storage import ApplicationStore

    store = ApplicationStore(":memory:")
    store.add("indeed:5", "Acme", "Backend Engineer", "https://x/5")

    class FakeAnalyzer:
        def analyze(self, jd_text):
            assert jd_text == "RAW JD TEXT"
            return JDAnalysis(
                required_skills=["python"], preferred_quals=["aws"],
                seniority="mid", tone="casual",
                applicant_instructions=["include the word pineapple"],
            )

    tools = CvflowTools(
        store=store, knowledge=None, discovery=None,
        analyzer=FakeAnalyzer(), tailor=None,
    )
    out = tools.analyze_jd("indeed:5", jd_text="RAW JD TEXT")
    assert out["required_skills"] == ["python"]
    assert out["applicant_instructions"] == ["include the word pineapple"]
    # persisted against the record
    assert store.get_analysis("indeed:5").seniority == "mid"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_mcp_tools.py -k analyze_jd -v`
Expected: FAIL — `AttributeError: ... 'analyze_jd'`.

- [ ] **Step 3: Write minimal implementation**

Add to `CvflowTools` (add `from cvflow.analysis import JDAnalysis, fetch_jd` and `import json` at top; `asdict` already imported):

```python
    def analyze_jd(self, job_id: str, *, jd_text: str | None = None) -> dict[str, Any]:
        """Analyze a JD's text (or fetch it from the record's URL), persist + return.

        Pass ``jd_text`` to analyze provided text; omit it to fetch the stored
        ``jd_url``. Raises if the job is unknown (never fail silently).
        """
        app = self._store.get(job_id)
        if app is None:
            from cvflow.storage import UnknownJob

            raise UnknownJob(f"no such job_id: {job_id}")
        text = jd_text if jd_text is not None else fetch_jd(app.jd_url)
        analysis = self._analyzer.analyze(text)
        self._store.save_analysis(job_id, analysis)
        return json.loads(analysis.to_json())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_mcp_tools.py -k analyze_jd -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cvflow/mcp/tools.py tests/test_mcp_tools.py
git commit -m "feat(mcp): add analyze_jd skill (fetch/analyze/persist)"
```

---

## Task 7: `build_tools(config)` factory

**Files:**
- Modify: `src/cvflow/mcp/tools.py`
- Test: `tests/test_mcp_tools.py`

Wires the real domain objects from a `Config`. Discovery's ranker/search and the LLM providers need keys at *runtime*, so the factory builds the store + knowledge eagerly and defers/optional-izes the live-LLM pieces. For Phase 7 the only live exit-criterion is `ping`; the factory must at minimum produce a `CvflowTools` whose `ping`, storage-read, and gate skills work against the configured DB and KB.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_mcp_tools.py`:

```python
def test_build_tools_wires_store_and_knowledge(tmp_path, monkeypatch):
    """build_tools(config) yields working store + knowledge-backed tools."""
    from cvflow.config import load_config

    # Use the repo's real committed config skeleton path via the live config if present;
    # otherwise this test is skipped (keeps CI hermetic).
    cfg_path = tmp_path / "config.yaml"
    import shutil
    from pathlib import Path

    real = Path("config.yaml")
    if not real.exists():
        import pytest

        pytest.skip("no live config.yaml present")
    shutil.copy(real, cfg_path)

    from cvflow.mcp.tools import build_tools

    cfg = load_config(cfg_path)
    tools = build_tools(cfg)
    assert tools.ping()["status"] == "ok"
    # storage read works against the configured (possibly empty) DB
    assert isinstance(tools.list_applications(status="discovered"), list)
```

> Note: this test is intentionally skip-guarded so the suite stays green on a
> machine without `config.yaml`. On the EC2 box `config.yaml` exists, so it runs.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_mcp_tools.py -k build_tools -v`
Expected: FAIL — `ImportError: cannot import name 'build_tools'` (or AttributeError).

- [ ] **Step 3: Write minimal implementation**

Add to `src/cvflow/mcp/tools.py`:

```python
def build_tools(config: Any) -> CvflowTools:
    """Construct a fully wired :class:`CvflowTools` from a loaded ``Config``.

    Store + knowledge base are built eagerly (no network). Discovery, the JD
    analyzer, and the resume tailor depend on live LLM providers; they are wired
    lazily in later phases. For Phase 7 they are left as ``None`` here and
    injected by tests — ping/storage/gate skills are fully operational, which
    satisfies the Phase-7 exit criterion (agent calls a trivial cvflow skill).
    """
    from cvflow.knowledge import KnowledgeBase
    from cvflow.storage import ApplicationStore

    store = ApplicationStore(config.storage.db_path)
    knowledge = KnowledgeBase.load(
        config.profile.knowledge_base_dir, config.storage.form_fields_path
    )
    return CvflowTools(
        store=store,
        knowledge=knowledge,
        discovery=None,
        analyzer=None,
        tailor=None,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_mcp_tools.py -k build_tools -v`
Expected: PASS (or SKIP if no `config.yaml` — on EC2 it PASSES).

- [ ] **Step 5: Commit**

```bash
git add src/cvflow/mcp/tools.py tests/test_mcp_tools.py
git commit -m "feat(mcp): add build_tools(config) factory wiring store + knowledge"
```

---

## Task 8: FastMCP server adapter + allowlist enforcement

**Files:**
- Create: `src/cvflow/mcp/server.py`
- Create: `src/cvflow/mcp/__main__.py`
- Test: `tests/test_mcp_server.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_mcp_server.py`:

```python
from cvflow.mcp.server import build_server
from cvflow.mcp.tools import TOOL_NAMES, CvflowTools


def _tools():
    from cvflow.storage import ApplicationStore

    return CvflowTools(
        store=ApplicationStore(":memory:"),
        knowledge=None, discovery=None, analyzer=None, tailor=None,
    )


def test_server_registers_exactly_the_allowlist():
    server = build_server(_tools())
    # FastMCP exposes registered tools via list_tools(); names must equal TOOL_NAMES.
    import asyncio

    registered = {t.name for t in asyncio.run(server.list_tools())}
    assert registered == set(TOOL_NAMES)


def test_approve_is_not_exposed():
    assert "approve" not in TOOL_NAMES
    server = build_server(_tools())
    import asyncio

    registered = {t.name for t in asyncio.run(server.list_tools())}
    assert "approve" not in registered
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_mcp_server.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cvflow.mcp.server'`.

- [ ] **Step 3: Write minimal implementation**

Create `src/cvflow/mcp/server.py`:

```python
"""FastMCP stdio adapter — registers exactly the TOOL_NAMES allowlist.

Defense in depth: this server only registers the allowlisted skills, AND the
Hermes config pins the same set under ``tools.include``. ``approve`` appears in
neither, so the agent has no path to satisfy the gate.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from cvflow.mcp.tools import TOOL_NAMES, CvflowTools, build_tools


def build_server(tools: CvflowTools) -> FastMCP:
    """Build a FastMCP server exposing the allowlisted cvflow skills."""
    server = FastMCP("cvflow")

    # Register exactly the allowlist, bound to the dispatcher methods. Each name
    # in TOOL_NAMES must map to a CvflowTools method; we fail loudly otherwise.
    for name in TOOL_NAMES:
        method = getattr(tools, name, None)
        if method is None or not callable(method):
            raise RuntimeError(f"TOOL_NAMES lists {name!r} but CvflowTools has no such method")
        server.add_tool(method, name=name)
    return server


def main() -> None:
    """Entrypoint: build from the live config and serve over stdio."""
    from cvflow.config import load_config

    config = load_config("config.yaml")
    server = build_server(build_tools(config))
    server.run(transport="stdio")
```

Create `src/cvflow/mcp/__main__.py`:

```python
from cvflow.mcp.server import main

if __name__ == "__main__":
    main()
```

> If `FastMCP.add_tool` signature differs in the installed `mcp` version,
> adjust to the available registration API (e.g. `server.tool(name=name)(method)`).
> Verify with `.venv/bin/python -c "from mcp.server.fastmcp import FastMCP; help(FastMCP.add_tool)"`
> during Step 3 before finalizing.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_mcp_server.py -v`
Expected: PASS (both tests).

- [ ] **Step 5: Commit**

```bash
git add src/cvflow/mcp/server.py src/cvflow/mcp/__main__.py tests/test_mcp_server.py
git commit -m "feat(mcp): add FastMCP stdio adapter registering only the allowlist"
```

---

## Task 9: Full suite + lint + type-check gate

**Files:** none (verification task)

- [ ] **Step 1: Run the full test suite**

Run: `.venv/bin/pytest -q`
Expected: all tests pass (67 prior + the new MCP tests).

- [ ] **Step 2: Lint**

Run: `.venv/bin/ruff check .`
Expected: clean. Fix any findings, re-run.

- [ ] **Step 3: Type-check**

Run: `.venv/bin/mypy src`
Expected: clean. If `mcp.*` lacks stubs, add to `pyproject.toml`:

```toml
[[tool.mypy.overrides]]
module = ["mcp.*"]
ignore_missing_imports = true
```

Re-run mypy; expected clean.

- [ ] **Step 4: Commit any config/lint fixups**

```bash
git add -A
git commit -m "chore(mcp): mypy override for mcp.* + lint fixups"
```

---

## Task 10: Smoke-test the stdio server standalone

**Files:** none (manual verification)

- [ ] **Step 1: Verify the server starts and lists tools over stdio**

Run a one-shot MCP client handshake using the SDK's stdio client:

```bash
.venv/bin/python - <<'PY'
import asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def main():
    params = StdioServerParameters(
        command=".venv/bin/python", args=["-m", "cvflow.mcp"]
    )
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = sorted(t.name for t in tools.tools)
            print("TOOLS:", names)
            res = await session.call_tool("ping", {})
            print("PING:", res.content)

asyncio.run(main())
PY
```

Expected: `TOOLS:` lists the 7 allowlisted names (no `approve`); `PING:` shows `status=ok, service=cvflow`. Run from the repo root so `config.yaml` resolves.

- [ ] **Step 2: (no commit — verification only)**

---

## Task 11: Wire the server into Hermes (LIVE, not committed)

**Files:**
- Modify: `~/.hermes/config.yaml` (live host config — gitignored, never committed)

- [ ] **Step 1: Editable-install cvflow into the venv + back up config**

Run: `.venv/bin/pip install -e .` (so `cvflow` imports in the spawned subprocess without PYTHONPATH hacks — chosen approach).
Then: `cp ~/.hermes/config.yaml ~/.hermes/config.yaml.bak.phase7`

- [ ] **Step 2: Add the `mcp_servers.cvflow` block with the allowlist**

Add to `~/.hermes/config.yaml` (top-level key):

```yaml
mcp_servers:
  cvflow:
    command: /home/ubuntu/cvflow/.venv/bin/python
    args: ["-m", "cvflow.mcp"]
    cwd: /home/ubuntu/cvflow
    tools:
      include: [ping, discover, analyze_jd, list_applications, get_application, request_review, submit]
```

> The `tools.include` list is the Hermes-side allowlist (docs recommend it for
> sensitive systems). It pins the same 7 skills the server registers — `approve`
> is absent on both sides. `cwd` makes the server resolve `config.yaml`.
> `cvflow` is importable because of the editable install in Step 1 (no PYTHONPATH).
> If Hermes's stdio MCP schema does not support `cwd`, the editable install still
> makes the package importable; pass the config path via an absolute default in
> `main()` instead.

- [ ] **Step 3: Reload MCP in the running gateway**

In the Hermes CLI/Telegram session run `/reload-mcp`, or restart `hermes gateway`.
Expected: Hermes reports the `cvflow` server connected and 7 tools discovered.

- [ ] **Step 4: Prove the Phase-7 exit from Telegram**

From the authorized Telegram chat, ask Hermes to call the cvflow ping skill, e.g.:

```text
Use the cvflow ping tool and tell me the result.
```

Expected: the agent invokes `cvflow` `ping` and reports `status=ok, service=cvflow`.
This is the Phase-7 EXIT proof (agent calls a trivial cvflow skill end-to-end via the running gateway, brain handles the tool-calling round-trip).

- [ ] **Step 5: (no commit — live host config is gitignored)**

---

## Task 12: Update the master build plan

**Files:**
- Modify: `docs/superpowers/plans/2026-06-03-cvflow-build-plan.md`

- [ ] **Step 1: Tick the Phase 7 checkbox**

Change `### [ ] Phase 7 — Hermes integration (substrate setup) — DECIDED` to `### [x] Phase 7 ...`.

- [ ] **Step 2: Add a progress-log entry**

Append under "## Progress log" a dated bullet summarizing: MCP server built (dispatcher + FastMCP adapter), gate invariant proven (no `approve` tool; submit blocks unless approved; only out-of-band `store.approve()` satisfies it), wired into Hermes via `mcp_servers.cvflow` + `tools.include` allowlist, and Phase-7 exit proven from Telegram (`ping` round-trip). Note test count delta and ruff/mypy clean.

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/plans/2026-06-03-cvflow-build-plan.md docs/superpowers/plans/2026-06-04-phase-7-hermes-integration.md
git commit -m "docs: mark Phase 7 (Hermes MCP integration) complete; add sub-plan"
```

---

## Self-Review notes

- **Spec coverage:** trivial `ping` (Task 2 ✔), discovery/analysis/resume/storage read skills (Tasks 3,5,6 ✔ — resume read deferred: Phase-7 exit needs only ping + a representative dispatch set; resume tailoring needs the live Gemini provider wired in Phase 8, so a resume MCP skill is intentionally out of scope here and noted), gate skills request_review + submit (Task 4 ✔), dispatch-to-real-modules proof (Tasks 3,5,6 ✔), submit-raises-unless-approved (Task 4 ✔), no exposed path to approved (Tasks 4 + 8 ✔), live wiring with allowlist (Task 11 ✔), Telegram exit proof (Task 11 ✔).
- **Resume skill note:** the build-plan Phase-7 line says "resume" among registered modules. A read-only resume skill (e.g. compile master / produce diff) depends on the live tailoring provider; deferring it to the Phase 8 gate-flow wiring keeps Phase 7 hermetic and avoids throwaway wiring. Recorded here so it is a conscious scope decision, not a gap.
- **Type consistency:** `_app_to_dict`, `TOOL_NAMES`, `build_tools`, `build_server` names are consistent across tasks. `CvflowTools.__init__` keyword args (`store/knowledge/discovery/analyzer/tailor`) are identical in every test.
- **Placeholder scan:** no TBD/TODO; every code step shows full code.
</content>
</invoke>
