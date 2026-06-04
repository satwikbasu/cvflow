# Phase 9 — Browser Automation + Closing the Discovery/Analyzer Gap (Design)

Date: 2026-06-04
Status: approved (brainstorm), pre-implementation
Related: build plan Phase 9; `[[approval-gate-wiring]]`, `[[hermes-runtime-ops]]`, `[[essay-auto-answer-policy]]`

This design covers two pieces of work, executed in order:

1. **Part 1 — Close the discovery/analyzer gap** (pre-existing from Phase 7): build the
   missing OpenAI-compatible brain provider and wire `discovery` + `analyzer` in `build_tools`.
2. **Part 2 — Phase 9 browser automation skill** (`src/cvflow/automation/`).

---

## Part 1 — Close the discovery/analyzer gap

### Problem
`build_tools` (`src/cvflow/mcp/tools.py`) passes `discovery=None`, `analyzer=None`, so the
`discover` and `analyze_jd` MCP tools error live with `'NoneType' object has no attribute ...`.
Both `LLMRanker` (discovery) and `JDAnalyzer` (analysis) consume any object exposing
`generate(prompt) -> str` — the same seam `GeminiProvider` implements — but the NIM brain has
no in-code provider class.

### Solution: `NimProvider`
A new member of `cvflow/llm` exposing `generate(prompt: str) -> str`, built from
`config.llm.brain` (`base_url`, `api_key`, `model`, `max_requests_per_minute`).

- **Transport:** stdlib `urllib` POST to `{base_url}/chat/completions` (OpenAI-compatible),
  `Authorization: Bearer <api_key>`, body `{model, messages:[{role:"user",content:prompt}]}`.
  Chosen over the `openai` SDK / `httpx` to match the codebase's minimal-deps style
  (`analysis/fetch_jd` already uses stdlib `urllib`) and because we hand-roll the rate budget
  anyway (the SDK's main value-add). **Zero new dependency — zero-cost invariant preserved.**
- **Injectable seam:** a `post_fn(url, headers, body) -> str` parameter (default = the urllib
  impl) so tests run with **no network** — mirrors `JDAnalyzer`'s `fetch_fn` and
  `GeminiProvider`'s `client` injection.
- **Rate budget:** per-minute request tracking with a minute-rollover reset; raise
  `RpmExceeded(LLMError)` before calling out when over `max_requests_per_minute` (mirrors
  `GeminiProvider`'s `RpdExceeded` / invariant 4). No response cache (ranking/JD prompts are
  one-shot per run; YAGNI).
- **Parsing:** read `choices[0].message.content`; strip code fences via the existing helper
  pattern; raise `LLMError` on a malformed/empty reply (never fail silently).

### Wiring `build_tools`
Replace the two `None`s:

```python
brain = NimProvider(
    base_url=config.llm.brain.base_url,
    api_key=config.llm.brain.api_key,
    model=config.llm.brain.model,
    max_requests_per_minute=config.llm.brain.max_requests_per_minute,
)
ranker = LLMRanker(brain, knowledge.full_context())
discovery = DiscoveryService(
    store, ranker,
    search_terms=config.discovery.search_terms,
    locations=config.discovery.locations,
    sites=config.discovery.sites,
    results_wanted_per_site=config.discovery.results_wanted_per_site,
    hours_old=config.discovery.hours_old,
    top_n=config.discovery.top_n_to_present,
)
analyzer = JDAnalyzer(brain)
```

### URLs always shown (user requirement)
`CvflowTools.discover` already returns `url` per ranked job. Add a test asserting **every**
returned dict carries a non-empty `url`, and document the return contract so the brain's digest
always renders the link. (No silent drop — invariant 3.)

### Part 1 exit
`discover` and `analyze_jd` work end-to-end (mocked HTTP in tests; live demo from Telegram).
`ruff` + `mypy --strict` clean.

---

## Part 2 — Phase 9 browser automation (`src/cvflow/automation/`)

### Goals
Goal 5 (form filling + submit) and Goal 8 (clarification loop, never-silent failures), as a
deterministic Hermes skill — **our own Playwright**, not Hermes's native browser tool, so the
approval gate is enforced in our code.

### Session model — Option C (hybrid)
- **On-disk persistence:** `launch_persistent_context(user_data_dir=<config.automation.storage_state_dir>/<job_id>)`
  so login cookies / 2FA-session survive a process restart (the expensive thing to lose —
  Phase-10 territory). Headed under xvfb; stealth args (`--disable-blink-features=AutomationControlled`,
  realistic UA). `headless`/`use_stealth` from `config.automation`.
- **In-process live session:** a module-level `SessionManager` (the MCP server process is
  long-lived) keyed by `job_id` holds the live `BrowserContext`/`Page` between MCP calls, so a
  pause→clarify→resume continues on the **same page** with typed values intact.
- **Restart trade-off (accepted, made visible):** a gateway crash mid-form loses that one
  form's in-flight typed values (the form restarts) but never the login. On crash the app is
  marked `failed` and the user is notified (below) — it never silently resumes into a bad state.

### Entry gate (invariant 2 / Goal 4)
Every automation entrypoint (`fill_application`, `resume_application`) calls
`guard_can_submit(app.status)` **first** → raises `SubmissionBlocked` for any non-`approved`
status. Automation is unreachable before the human `/apply`. A test asserts a non-approved job
cannot reach a single browser action.

### `FormFiller` — deterministic-first field resolution
Operates on a Playwright `Page`. Discovers fillable fields (label/name/type/options). For each:

1. **Lookup** — normalize the field label/name to a key and check `FormFields`
   (`is_filled`/`require` / `populated`). Hit → type the literal value. Source = `form_fields`.
2. **Compose** — if the field is free-text/essay-like (textarea / long-answer), call
   `essays.compose_answer(question, knowledge, provider=essay_provider)` — reusing the
   `essay_provider` (Gemini/tailoring) already wired into `CvflowTools` in Phase 8. Grounded → fill.
   Source = `composed`. Ungroundable → treat as unresolved (→ step 3).
3. **Escalate / skip** — if still unresolved: **required** → `NeedsClarification` (pause); 
   **optional** → skip + log (never guess — invariant 2).

Field-type handlers: text/textarea (`fill`), `<select>` dropdown (`select_option`, fuzzy match
to closest option), checkbox/radio (`check`), file upload (`set_input_files` with the tailored
PDF / requested doc), multi-page navigation (find & click Next/Continue; final Submit is a
distinct, gated step). An unmappable required field type also escalates rather than guessing.

### Field-source tagging + composed-answer disclosure (user requirement)
Every fill is recorded as a `FieldFill(label, value, source)` where source ∈
`{form_fields, composed, clarified}`. Before the **final submit click**, all `composed` fills are
reported to the user via Telegram: the question, the generated answer, and the grounding doc
keys. (Minimum = notify; design leaves the hook to elevate to confirm later.) This satisfies
"I should be asked or at least let known about LLM-composed fields."

### Pause → clarify → resume
- `fill_application(job_id)` runs the filler until completion or the first unresolved **required**
  field, then returns either a completion/proof payload or
  `{"needs_clarification": true, "job_id", "question"}`. The live session stays open in
  `SessionManager`.
- The agent relays the question to the user (existing clarification chat flow).
- `resume_application(job_id, answer)` records the answer as a `clarified` `FieldFill`, fills the
  pending field, and continues — looping if another unresolved field appears.

### Final submit + proof capture
When all required fields are resolved and composed answers disclosed, the filler clicks the
gated Submit, waits for confirmation, and captures **proof**: final URL, confirmation
number/text (heuristic scrape), screenshot (PNG under `config.automation.storage_state_dir`),
and page title. Proof is persisted on the application record; status → `applied`.

### Storage additions (additive, gate core untouched)
The `applications` table gains nullable proof columns: `proof_url`, `proof_screenshot_path`,
`proof_page_title`. (`confirmation_ref` already exists.) New setters
`set_proof(job_id, *, url, screenshot_path, page_title)`; reuse `set_confirmation`. Additive
columns only — the state machine / `approve()` path is not touched (mirrors Phase 5's additive
`jd_analyses` table).

### Never-silent failure (user requirement / invariant 3)
Any exception during fill/submit → app status `failed`, the live session is closed/cleaned, and
a Telegram alert is emitted naming the **job (company/role) and its post URL**. Returned to the
caller as a structured error (so the agent reports it). OTP-specific waits are Phase 10; here a
hard timeout/error still routes through this same notify-and-fail path.

### MCP surface
Add `fill_application` and `resume_application` to `TOOL_NAMES`, both gated by
`guard_can_submit`. The existing `submit` guard is unchanged. **Still no `approve` tool** — the
gate invariant holds by construction (`hasattr(tools, "approve")` stays False).

### Hermes toolsets — deviation from the original task note (user-approved)
We **leave Hermes's native `browser`/`computer_use` toolsets disabled**, contrary to the
original Phase-9 note. Rationale: (1) we use our own deterministic Playwright skill precisely so
the agent loop cannot drive a browser around the approval gate; (2) re-enabling adds context /
latency on the already-strained NIM free tier (`[[hermes-runtime-ops]]`). No change to
`agent.disabled_toolsets`.

### Testing (against a local fixture form)
A committed fixture HTML form (text, textarea, `<select>`, checkbox, file input, two pages) is
served via `file://` / a tiny local server. Tests prove: field filling (each type), file upload,
dropdown/checkbox, **pause/resume** (unresolved required field → `NeedsClarification` →
`resume_application` completes), **proof capture**, composed-answer disclosure, and the **entry
gate** (non-approved job → `SubmissionBlocked`, zero browser actions). Tests **skip cleanly** if
Playwright browsers aren't installed (mirrors the Tectonic-absent skip in Phase 6). The essay
provider and Telegram-notify are injected/mocked — no live brain, no live network.

### Part 2 exit (build-plan criteria)
Tests against the local fixture form prove field filling, file upload, pause/resume, and proof
capture, with `guard_can_submit` asserted at entry. `ruff` + `mypy --strict` clean.

---

## Module layout
```
src/cvflow/llm/__init__.py        # + NimProvider, RpmExceeded
src/cvflow/automation/__init__.py # SessionManager, FormFiller, FieldFill, NeedsClarification, proof, notify hook
src/cvflow/mcp/tools.py           # wire discovery/analyzer; + fill_application/resume_application
src/cvflow/storage/__init__.py    # + proof columns/setter (additive)
tests/fixtures/form/*.html        # local fixture form
```

## Out of scope (later phases)
Google OAuth + email-OTP waits (Phase 10); scheduler/heartbeat + systemd (Phase 11); live
end-to-end dry run (Phase 12).
