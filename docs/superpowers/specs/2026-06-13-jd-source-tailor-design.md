# JD-source fix + deterministic `/tailor` + tool-surface cleanup — design

> **Status:** approved design (2026-06-13 brainstorming session). Scope: **private repo only**.
> Next step: a TDD implementation plan via `superpowers:writing-plans`.
> Companion direction: `docs/architecture-review-fable.md` (this advances P1 tailoring usability;
> it does not touch the multi-tenant seams or the public release).

## Problem

After `/discover`, asking the brain to "analyze job 2 and tailor it" fails with *"access to the
job description is restricted."* Root cause (verified in code, not a wiring gap):

- `analyze_jd(job_id)` with no `jd_text` calls `fetch_jd(app.jd_url)` (`mcp/tools.py:257`), a stdlib
  urllib GET against the **listing URL** (LinkedIn/Indeed/Naukri). Those are login-walled → empty/
  restricted page → analysis fails.
- The scraped JD `description` **is** captured at discovery (`discovery/__init__.py:154`) and
  distilled into a `Crux` that **is** persisted (`job_cruxes`), but the **raw description is never
  persisted** and `analyze_jd` ignores the crux.
- `request_review` (the tailoring step) needs a `JDAnalysis`, which only `analyze_jd` produces — so
  with `analyze_jd` broken, tailoring cannot run. Live DB confirms it: `jd_analyses = 0` rows.
- Secondary bug: `request_review` sets `PENDING_REVIEW` **before** the analysis check, so a failure
  orphans the job in `pending_review` with no PDF (one such row exists live).

Operating philosophy (founder, 2026-06-13): the brain LLM is for **reading/answering** about jobs;
**discovery, tailoring, and apply are deterministic operations** the user triggers explicitly after
reviewing with the brain. Tailoring should therefore move off the brain onto a deterministic
command, and must work from data already in the DB.

## Spike evidence (why crux-derived tailoring is viable)

A throwaway comparison on a real Go/Python backend role (matched to the committed master résumé),
same JD text through both extraction paths, then `Tailorer.plan` on each:

- `Tailorer.plan` consumes **only** `required_skills`, `preferred_quals`, `seniority` from
  `JDAnalysis` (it ignores `tone`/`applicant_instructions`). The real selection work is the Cerebras
  tailorer + the `assert_no_new_facts` guard.
- Crux-derived inputs (`required_skills = must_have_skills + tech_stack`, `seniority =
  seniority_signal`) produced a **tailoring plan of equivalent quality** to the full `analyze_jd`
  path. The analysis path's only genuine extras were soft `preferred_quals` (e.g. "observability")
  and a free-text seniority string — neither changed the outcome materially.
- Crux-derived tailoring **ran on the actual stored InCommon job with zero fetch and zero extra LLM
  call**; the analysis path cannot run on any of the 73 already-discovered jobs (no stored text,
  walled URLs).

Conclusion: build **both** paths behind a config toggle (default = crux), since the second LLM call
is cheap and only happens on explicit user choice; the founder tunes which to use based on results.

## Goals

1. Tailoring + analysis work end-to-end on data already in the DB, no login-walled fetch.
2. A boolean `config.yaml` toggle selects crux-derived vs re-distilled/analysis tailoring inputs.
3. Persist all discovery-derived data (raw JD text, fit/benchmark) for current display and future
   enhancements.
4. `get_application` surfaces enough (scores + crux summary) and is ordinal-addressable, so the user
   can decide what to tailor.
5. A deterministic single-job `/tailor N` command produces the PDF + diff; tailoring leaves the
   brain's reach.
6. Remove the dead automation/auth tool surface from the brain.

## Non-goals (this change)

- Multi-tenant seams (P1C), apply-kit (P1D), public-release work — untouched.
- Physically archiving `automation/`+`auth/` directories or removing the `automation:`/`auth:`/
  `security:` config blocks — **deferred to the public-release W1 cut** (avoids touching `config.py`
  validation twice; lower risk to live config loading). This change only removes the tool surface +
  `build_tools` wiring.
- Job-specific conversational Q&A ("does job X mention Y") — deferred (LLM calls are stateless;
  `get_application`'s crux summary is the read surface for now).
- The `/apply` "— submitting." wording fix — deferred to the public cut.
- `discover` stays on the brain allowlist as-is (the inline-call 120 s footgun is accepted; the
  detached `/discover` is the real path).
- No mutation of the existing orphaned live `pending_review` row.

## Design

### 1. Storage — persist everything (idempotent migrations)

`src/cvflow/storage/__init__.py`. Migrations stay idempotent: `_SCHEMA` uses
`CREATE TABLE IF NOT EXISTS`; `_migrate()` adds columns only when `PRAGMA table_info` shows them
missing (mirrors the existing `crux_version` ALTER pattern).

- **`applications` new columns** (small decision-support data): `benchmark INTEGER`,
  `fit_score INTEGER`, `fit_reason TEXT`, `concerns TEXT` (JSON array), `cohort TEXT`,
  `ctc_lpa REAL`. Extend the `Application` dataclass with matching optional fields.
- **New table** `job_descriptions(job_id TEXT PRIMARY KEY, description TEXT NOT NULL,
  scraped_at TEXT NOT NULL)` — raw scraped JD text, kept out of `applications` so `list_by_status`
  never drags blobs.
- **New methods** (all take `user_id: str = "owner"` for the future seam; default = no behavior
  change today):
  - `set_discovery_meta(job_id, *, benchmark, fit_score, fit_reason, concerns, cohort, ctc_lpa)`
  - `set_jd_text(job_id, description)` / `get_jd_text(job_id) -> str | None`
- Tests: migration adds columns + table on an old fixture DB and survives data; a second
  `_migrate()` is a no-op; round-trips for both new methods.

### 2. Discovery persist stage writes the new data

`src/cvflow/discovery/__init__.py`, the persist loop (currently `~:498-502`, only
`store.add(job_id, company, title, url)`):

- For every presented `BenchmarkedJob bj`, also call `set_discovery_meta(...)` (upsert each run —
  scores can change) and `set_jd_text(bj.posting.job_id, bj.posting.description)` (write once;
  skip if already present / empty).
- Keep the existing `if not exists: add(...)` for the base row; the meta/text writes are
  upsert-style and run regardless so re-ranked jobs refresh their scores.
- Test: after a stubbed discover, `get_application` (below) returns the persisted scores and a
  stored description exists.

### 3. Tailoring source — both paths behind a toggle

- **Config:** add `use_jd_analysis: bool` to `ResumeConfig` (`config.py`), loaded with
  `_opt_bool(resume, "use_jd_analysis", "resume.", default=False)` so an absent key keeps the live
  config loading. Add it (commented, default `false`) to `config.example.yaml` with a short doc
  line. The founder adds/flips it in the live `config.yaml`.
- **New pure function** `resolve_tailoring_analysis(job_id, *, store, analyzer, use_jd_analysis,
  notify=None, fetch_fn=fetch_jd) -> JDAnalysis`, in `src/cvflow/analysis/__init__.py` (it produces
  a `JDAnalysis`, so it belongs with the analysis module):
  - `use_jd_analysis is False` (**crux path, default**): read crux via `store.get_crux(job_id)`;
    map → `JDAnalysis(required_skills=dedupe(must_have_skills + tech_stack),
    preferred_quals=[t for t in tech_stack if t not in set(must_have_skills)],
    seniority=seniority_signal, tone="", applicant_instructions=[crux.applicant_instructions]
    if set else [])`. No network, no LLM call.
  - `use_jd_analysis is True` (**analysis path**): `text = store.get_jd_text(job_id) or
    fetch_fn(url)`; `analyzer.analyze(text)`; persist via `store.save_analysis`.
  - **Universal fallback (never fail silently, invariant 3):** in analysis mode, if there is no
    stored text *and* the fetch fails, log + `notify("falling back to crux for <job>")` and use the
    crux path. The crux exists for every ranked job, so tailoring always has a source.
  - If neither crux nor text exists (un-distilled job): raise a clear error naming the missing
    prerequisite.
- Tests: crux mapping correctness; analysis path uses stored text without fetching; fetch-failure
  fallback to crux notifies and succeeds; missing-everything raises.

### 4. `request_review` — use the toggle + fix the half-state bug

`src/cvflow/mcp/tools.py::request_review`:

- Replace the `get_analysis() or raise` block with
  `analysis = resolve_tailoring_analysis(job_id, store=self._store, analyzer=self._analyzer,
  use_jd_analysis=self._use_jd_analysis, notify=self._notify)`.
- Wire `use_jd_analysis` + a notifier into `CvflowTools.__init__` and `build_tools`
  (`config.resume.use_jd_analysis`, `make/HermesNotifier`).
- **Bug fix:** build the plan + compile the PDF **first**, then set `PENDING_REVIEW` and
  `tailored_pdf_path` only on success — so a tailoring failure never orphans status.
- Return payload unchanged in shape (`job_id`, `status`, `pdf_path`, `diff`, `analysis_summary`,
  `instructions`).
- Tests: both toggle modes produce a plan; a forced failure leaves status unchanged (no orphan).

### 5. `get_application` — richer + ordinal-addressable

`src/cvflow/mcp/tools.py::get_application(ref: str)`:

- `ref` accepts a `job_id` (contains `:`) **or** a digest ordinal — resolve an ordinal to a
  `job_id` via `store.get_digest_slot(int(ref))` (same source `/apply` uses), so "details for job 4"
  works.
- Returns today's compact fields **plus**: `benchmark`, `fit_score`, `fit_reason`, `concerns`,
  `cohort`, `ctc_lpa` (from `applications`), and a `crux` summary block
  (`role_family`, `seniority_signal`, `must_have_skills`, `company_type`, `one_line`) read from
  `job_cruxes` when present. Omit keys that are null to keep the reply compact.
- Update the docstring so the brain knows it can be called with an ordinal.
- Tests: ordinal and job_id both resolve; scores + crux summary appear when present; unknown ref →
  `None`.

### 6. `/tailor N` — deterministic, single-job, detached

- **New CLI subcommand** `python -m cvflow.cron tailor <job_id>`: builds services (reuses
  `cron._build`), runs `request_review(job_id)`, and sends **PDF path + diff** through the notifier;
  any error notifies (never silent). Guarded by a new `tailor` runlock (sibling of
  `discovery_lock`) so a tailor and a discover/another tailor don't collide.
- **New Hermes command hook** `artifacts/hermes/hooks/cvflow-tailor/handler.py` (mirrors
  `cvflow-gate/handler.py`): authorized-user check → `resolve_targets(args, store)` → **reject if
  not exactly one resolved ordinal** (reply: "one job at a time, e.g. `/tailor 4`"). On a single
  target, spawn `python -m cvflow.cron tailor <job_id>` **detached** using the same
  `systemd-run --user --collect` / `setsid` pattern as the `/discover` hook (`handler.py::_spawn`),
  and reply that tailoring has started.
- Register the hook in the live `~/.hermes/` plugins + reference copy under `artifacts/hermes/`;
  document in `docs/deploy.md`.
- Tests (pure, hook-independent where possible): single-ordinal resolves to one job_id; multi/range/
  `all` is rejected with the one-at-a-time message; unauthorized user → not handled; the `cron tailor`
  subcommand calls `request_review` and notifies with the PDF + diff (notifier + request_review
  stubbed).

### 7. Tool surface + brain allowlist

- **`mcp/tools.py`:** remove `submit`, `fill_application`, `resume_application`, `submit_otp` from
  `TOOL_NAMES` (→ 8) and delete their methods; drop the `automator` and `otp_coordinator` ctor
  params. **`mcp/server.py`** registration follows `TOOL_NAMES` (verify no hardcoded list).
- **`build_tools`:** remove the `Automator`/`SessionManager`/`FormFiller`/`OtpCoordinator`/
  `TokenVault` construction and their imports; add `use_jd_analysis` + notifier wiring. The
  `automation/`+`auth/` modules and the `automation:`/`auth:`/`security:` config blocks stay on
  disk/in config (archival deferred — see non-goals); nothing imports them after this change.
- **Brain allowlist** (`artifacts/hermes/hermes-config.yaml` reference + live
  `~/.hermes/config.yaml` `tools.include`): exactly `ping`, `discover`, `list_applications`,
  `get_application`, `status_report`, `compose_essay` (6). `request_review` and `analyze_jd` remain
  in the dispatcher (called by `/tailor` and internally) but are **not** brain-exposed.
- **Gate invariant untouched:** no `approve` tool; `/apply` stays the sole `store.approve` caller;
  `statemachine` unchanged.
- Tests: an allowlist/`TOOL_NAMES` guard test asserting none of `approve`, `submit`,
  `fill_application`, `resume_application`, `submit_otp` are present; `build_tools` smoke-constructs
  without Automation/Auth imports.

## Resulting user flow

```
/discover                       → digest, jobs 1..N (status discovered, scores + JD text persisted)
brain: "details for job 4"      → get_application(4): scores + crux summary → user decides
/tailor 4                       → detached request_review(job 4) → PDF + diff posted to chat
                                  (crux-derived or re-distilled per config.resume.use_jd_analysis)
/apply 4   (or /skip 4)         → pending_review → approved (commitment; gate unchanged)
```

## Config change

`config.yaml` (live) + `config.example.yaml`:
```yaml
resume:
  # ...existing keys...
  use_jd_analysis: false   # false = tailor from the cached discovery crux (no extra LLM call);
                           # true  = re-analyze the stored JD text per job (richer soft-quals)
```
Optional-with-default (`_opt_bool`, default `false`) — absent key keeps the live config loading.

## Testing strategy (TDD, all via pytest; ruff + mypy --strict stay clean)

Per-section tests listed above. Plus a full-suite green run with no edits to pre-existing tests
except those that referenced the four removed tools (delete/adjust those). Live verification after
deploy: `/discover` → `get_application` shows scores+crux → `/tailor N` returns PDF+diff in chat
under both toggle values → `/apply N` works.

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| Migration alters live DB | additive `ALTER`/new table only; idempotent; migration test on an old-shape fixture; back up `data/cvflow.db` before deploy |
| Crux fields too coarse for some jobs | the toggle flips to the analysis path per the founder's judgement; crux remains the universal fallback |
| Analysis path can't fetch (walled URL, no stored text) | fall back to crux + notify (never silent) |
| Detached `/tailor` dies invisibly | inherits the `cron` catch-all notify-then-raise; the runlock turns a collision into a loud `AlreadyRunning` notice |
| New required-config breaks the live box | `use_jd_analysis` is optional-with-default; test loading a config without it |
| Removing tools breaks MCP registration | `server.py` follows `TOOL_NAMES`; guard test + `build_tools` smoke |

## Open questions

None blocking. The founder will tune `use_jd_analysis` empirically after first runs.
