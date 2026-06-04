# cvflow Build Plan (Master Roadmap)

> **For agentic workers:** This is the **master roadmap** — the source of truth for *order, scope, and exit criteria*. It is intentionally phase-level. Before executing a phase, write a detailed bite-sized TDD sub-plan for it using `superpowers:writing-plans`, save it as `docs/superpowers/plans/YYYY-MM-DD-phase-N-<name>.md`, then execute with `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans`. Keep the phase checkboxes below in sync as phases complete.

**Goal:** Build cvflow — an autonomous, self-hosted, zero-SaaS-cost job-application agent operated entirely through Telegram, with a mandatory human approval gate before any application is submitted.

**Architecture:** **Hermes Agent** is the always-on substrate (Telegram, scheduling, browser runtime, LLM brain). cvflow supplies **deterministic domain skills** Hermes calls — discovery, JD analysis, resume tailoring, storage, and the approval gate. The Goal-4 approval gate and the "never fabricate facts" rule are **deterministic code**, never delegated to the agent loop.

**Tech Stack:** Python 3.11+, Hermes Agent, NVIDIA NIM `meta/llama-3.3-70b-instruct` (brain) + Gemini 2.5 Flash (tailoring), SQLite, JobSpy, Playwright, modular LaTeX + TeX Live, systemd, Fernet.

---

## Standing rules (apply to EVERY phase — non-negotiable)

1. **TDD.** Write the failing test, watch it fail, implement minimally, watch it pass, commit. Frequent small commits, Conventional Commits style.
2. **The approval gate is deterministic.** Only path to `approved` is the Telegram approval callback; the submission entrypoint asserts `status == approved` and raises otherwise. An agent may *call* the gate but never satisfy it.
3. **Never fabricate user facts.** All facts come from `profile/`. Unanswerable field → clarification loop, never a guess.
4. **Never fail silently.** Every skip / timeout / error is logged *and* reported to the user via Telegram.
5. **Respect free-tier limits by design** (Gemini RPD, NIM RPM, JobSpy throttle). Cache and batch LLM calls.
6. **Flag any path to cost.** Any change introducing a paid API / proxy / host / quota overage must be called out explicitly in the PR/commit.
7. **Credentials never hit git; profile PII is intentionally committed to this PRIVATE repo** (so deploy = `git clone`). `config.yaml`, `data/`, `logs/`, and the CV PDF stay gitignored; `profile/` (incl. `form_fields.json`) and the master resume source `resume/*.tex` are tracked. Tokens/cookies are Fernet-encrypted at rest, `chmod 600`. Git identity = global config (never repo-local).
8. **DRY, YAGNI, surgical changes.** (See CLAUDE.md behavioral guidelines.)

---

## Phase dependency order

Domain phases (1–5) are **independent of the orchestration-substrate decision** and are built first. The substrate decision (Phase 6) is made *before* building the interface/automation/orchestration glue (Phases 7–10), so we don't write throwaway code.

```
0 Foundations
  └─ 1 Storage + State Machine (the gate core)
       ├─ 2 LLM layer (Gemini + NIM)
       │    ├─ 4 Discovery (JobSpy + dedup + ranking)
       │    ├─ 5 JD Analysis
       │    └─ 6 Resume Tailoring (LaTeX)
       └─ 3 Knowledge-base loader
                 ↓
       7 Hermes integration (substrate setup; brain = NIM meta/llama-3.3-70b-instruct)
                 ↓
       8 Telegram interface (wires the gate)
       9 Browser automation (Playwright)
       10 Auth (Google OAuth + email OTP)
       11 Scheduler + daemon wiring + systemd deploy
       12 End-to-end dry run on a throwaway account
```

---

## Phases

### [x] Phase 0 — Foundations
**Delivers:** runnable package skeleton, config loading, logging, test harness.
- `src/cvflow/config.py` — load & validate `config.yaml` against `config.example.yaml` schema; typed config object.
- Logging setup writing to `logs/` (rotating), also surfaced to Telegram later.
- `pyproject.toml`/`requirements.txt` pinned; `pytest`, `ruff`, `mypy` configured.
- `playwright install chromium`; document TeX Live install.
**Exit:** `pytest` runs (even if just config tests); `ruff`/`mypy` clean; config loads from a sample `config.yaml`.

### [x] Phase 1 — Storage + State Machine (safety-critical core)
**Delivers:** the durable tracking store and the un-bypassable gate.
- `src/cvflow/storage/` — SQLite schema & models for the application record (job ID, company, role, JD URL, discovery ts, application ts, status, tailored-PDF path, confirmation ref); `form_fields` JSON loader with populated-vs-empty-key semantics.
- `src/cvflow/statemachine/` — explicit states (`discovered → pending_review → approved → applied`; plus `otp_timeout`, `skipped`, `failed`); legal-transition table; `approve()` is the *only* setter of `approved`; a `guard_can_submit(app)` that raises unless `status == approved`.
**Exit:** tests prove illegal transitions raise, `approved` cannot be reached except via `approve()`, and `guard_can_submit` blocks every non-approved status. Dedup keyed by stable job ID is enforced.

### [x] Phase 2 — LLM layer (tailoring client)
**Delivers:** the Gemini client for the resume-tailoring escalation. (The agent brain — NIM `meta/llama-3.3-70b-instruct` — is configured inside Hermes in Phase 7, not here.)
- `src/cvflow/llm/` — `GeminiProvider` (google-genai) with RPD tracking + response caching, used by the resume module.
**Exit:** tests (mocked HTTP) prove the call path, RPD accounting, and cache hits. No live key needed for tests.

### [x] Phase 3 — Knowledge-base loader
**Delivers:** profile ingestion.
- Loader that reads all `profile/*.md` **and `profile/projects/*.md`** (recurse) plus `form_fields.json` into a structured context object loaded in full for downstream prompts.
**Exit:** tests load the real committed `profile/` and expose experience/skills/essays/form-fields; missing-field detection returns a pending list (no guessing). *(Private single-user repo: real data committed directly; the `*.example` data templates were removed 2026-06-04 — the loader still defensively skips any `*.example.md`.)*

### [x] Phase 4 — Discovery
**Delivers:** Goal 1.
- `src/cvflow/discovery/` — JobSpy search across configured sites; throttling; cross-day dedup via SQLite; LLM ranking against the profile (uses Phase 2 + 3); produce top 5–10 with title/company/summary/rationale/link.
**Exit:** tests (mocked JobSpy output) prove dedup across runs, ranking ordering, and top-N selection.
**FOLLOW-UP (user requirement, added 2026-06-04):** add a **salary-hike filter** — user's current CTC is **4 LPA**; only surface/apply to jobs offering **≥20% hike (floor 4.8 LPA)**. Jobs with stated pay below the floor are filtered out; jobs with **no stated salary are NOT silently dropped** — surface/flag them (invariant 3). Configurable via `config.yaml` (`discovery.current_ctc_lpa`, `discovery.min_hike_pct`).

### [x] Phase 5 — JD Analysis
**Delivers:** Goal 2.
- `src/cvflow/analysis/` — fetch full JD; LLM-extract required skills, preferred quals, seniority, tone/culture, and **applicant-specific instructions** (e.g. "include the word pineapple"); persist analysis linked to the application record.
**Exit:** tests on sample JDs extract the structured fields and capture an embedded applicant instruction.

### [x] Phase 6 — Resume Tailoring
**Delivers:** Goal 3 + the diff for Goal 4.
- `src/cvflow/resume/` — operate on modular master (`master.tex` + `sections/*.tex`); reorder/emphasize sections & bullets per JD **without adding facts**; compile via **Tectonic** (locked decision — not `latexmk`); generate plain-language diff (section reorder + bullet changes + promoted/demoted skills) narrated by the LLM.
- **Project selection (user requirement, 2026-06-04):** pick which projects appear from the JD's required skills + preferred quals, and show **at most 2 projects** per tailored resume (e.g. Java/Spring Boot JD → include **CRUDbot**). Selection/emphasis of existing `profile/projects/*.md` only — never adds facts (invariant 2).
**Exit:** tests prove a tailored `.tex` compiles to PDF, no new factual claims vs master (assert against KB), ≤2 JD-relevant projects are selected, and a human-readable diff is produced.

### [x] Phase 7 — Hermes integration (substrate setup) — DECIDED
**Decision (locked):** substrate = **Hermes Agent**. Brain = `meta/llama-3.3-70b-instruct` (NIM free tier); resume tailoring escalates to Gemini 2.5 Flash.
**Delivers:** a running Hermes that can call cvflow skills.
- Install Hermes; `hermes model` → configure NIM `meta/llama-3.3-70b-instruct` brain; configure Telegram (single authorized user).
- Register cvflow's domain modules (discovery, analysis, resume, storage, gate) as Hermes **skills/tools**; confirm Hermes invokes them and that the deterministic gate skill cannot be bypassed by the agent loop.
**Exit:** Hermes responds on Telegram, calls a trivial cvflow skill, and the brain handles a tool-calling round-trip. Security posture for token/PII handling reviewed.

### [x] Phase 8 — Approval-gate skill + chat flows (Goals 1,4,7,8,9,10 surface)
**Delivers:** the **gate wiring** and chat interactions as Hermes skills (Hermes provides the messaging transport).
- Approval-gate skill: sends the tailored PDF + diff + JD analysis and presents **approve & apply / request edits / skip**; the approve path is the sole caller of `statemachine.approve()`.
- Skills for: daily digest + selection, OTP request, clarification Q&A, status reports. Single authorized Telegram user enforced.
- **Essay / free-text auto-answering (user requirement, 2026-06-04):** compose answers to `profile/essay_answers.md` and free-text application fields from a **personality fingerprint** — the user's real experience/skills/projects/personality, molded (voice + emphasis) to the job role. This is synthesis of EXISTING facts, not invention. **Escalate to the user via the clarification loop ONLY when** a question needs a fact genuinely absent from the profile (e.g. an unrecorded preference or a specific number it cannot derive); such fields are marked pending, never guessed. Refines (does not weaken) invariant 2.
**Exit:** tests (mocked transport) prove the approve path is the only route to `approved`, unauthorized users are ignored, the review message carries PDF/diff/analysis, an essay answer is grounded in cited profile content, and an ungroundable question triggers the clarification loop instead of a guess.

### [ ] Phase 9 — Browser automation skill (Goals 5, 8)
**Delivers:** form filling + proof, as a Hermes skill.
- `src/cvflow/automation/` — Playwright `launch_persistent_context` (headed under xvfb, stealth); multi-page forms, uploads, dropdowns, checkboxes, free-text from KB/form-fields; pause→clarify→resume preserving state; capture proof (URL/confirmation#/screenshot/title). (Evaluate Hermes's native browser tool vs our own Playwright skill; default to our own for deterministic gate enforcement.)
**Exit:** tests against a local fixture form prove field filling, file upload, pause/resume, and proof capture. `guard_can_submit` is asserted at entry.

### [ ] Phase 10 — Auth (Goals 6, 7)
**Delivers:** login flows.
- `src/cvflow/auth/` — Google OAuth (stored, Fernet-encrypted tokens; documented risks; burner account); email-OTP fallback that messages the user, waits up to timeout, marks `otp_timeout` + pauses on timeout (never silent).
**Exit:** tests prove token encryption round-trip, OTP-wait timeout → `otp_timeout` + notification, and OTP success → resume.

### [ ] Phase 11 — Scheduling + deploy (via Hermes)
**Delivers:** Goal 10 + production operation.
- Configure Hermes's scheduler for the daily discovery trigger + heartbeat (no hand-built daemon/APScheduler). systemd unit running Hermes as a dedicated unprivileged user (auto-restart, xvfb).
**Exit:** Hermes runs the daily schedule, sends a heartbeat to Telegram; systemd unit documented in `docs/deploy.md`.

### [ ] Phase 12 — End-to-end dry run
**Delivers:** confidence.
- Full flow on a throwaway account against a test/sandbox posting; verify the gate truly blocks until approval and proof is captured.
**Exit:** one complete discovery→approval→submission cycle logged & reported, with the gate demonstrably blocking pre-approval.

---

## Key decisions & rationale (settled)

- **Telegram** over Discord: long-polling needs no public IP, 50 MB PDF sends, native inline keyboards.
- **JobSpy** for discovery: maintained, multi-board, free. **(Re-evaluated 2026-06-03 vs alternatives — DECIDED to keep, behind an adapter seam.)** fantastic.jobs (better data: hourly refresh, 200k+ career sites, no scraping breakage) is **hosted-SaaS-only**, metered ($1–9/1k jobs) with an undisclosed free allowance → breaches the zero-cost invariant unless a standing free tier is confirmed; SerpApi Google Jobs free tier (100 searches/mo) is too small for multi-term daily search (≈540/mo) and scales to paid; Coresignal/JobsPikr are paid-only. Mitigation for JobSpy's endpoint-breakage risk: discovery is built against an **injectable `search_fn`** so a fantastic.jobs/SerpApi adapter can slot into the same seam later with no rework to dedup/ranking/storage.
- **Playwright** (persistent context) over Selenium: session reuse, auto-wait, WebSocket.
- **SQLite** tracking + **JSON** form-fields + **markdown** KB.
- **Modular LaTeX** + TeX Live over regex-on-monolith: deterministic reorder & meaningful diff.
- **Orchestration substrate = Hermes Agent** (DECIDED): less glue code, lower trust surface than OpenClaw, built for messaging + autonomous multi-step + browser + BYO-LLM. cvflow = deterministic skills Hermes calls.
- **LLM (DECIDED):** brain = `meta/llama-3.3-70b-instruct` (NIM free tier, fast ~1.5–2s, non-reasoning, tool-calling, 128k ctx, ~40 RPM / no hard daily cap), configured in Hermes. Resume tailoring escalates to **Gemini 2.5 Flash** (low volume → under RPD, higher quality). *Nemotron Super 49B v1.5 tested & rejected: reasoning model, ~3.5 min latency on free tier.*
- **Codex-via-ChatGPT-Go: rejected** — Go lacks the full Codex agent; subscription-OAuth as a headless backend violates OpenAI ToS; Codex moved to token billing. Do not reintroduce.
- **Approval gate as a state machine**, not a convention.

## Open decisions / risks (must resolve where noted)

- **LLM PII privacy (RESOLVED 2026-06-04 — informed consent / burner data):** the user explicitly accepts that both free tiers (NIM + Gemini) may train on the submitted profile PII and chooses to treat this profile as **expendable burner data**. No redaction/local-model/paid-key mitigation is required; stop flagging PII egress to the free-tier LLMs as a blocker. (The zero-cost invariant is preserved.)
- **Anti-bot / ToS:** LinkedIn/Indeed/Glassdoor resist scraping & auto-apply; expect breakage and treat platform accounts as expendable. JobSpy endpoints drift.
- **Token/cookie theft & PII exposure:** mitigate with burner Google account, unprivileged user, Fernet at rest, chmod 600, host firewall.
- **Browser session preservation** on pause is best-effort; on form timeout → log + notify.

## Progress log

- 2026-06-04: **Phase 8 done (approval-gate + chat flows).** The deterministic human-approval path is live and proven by construction. **Gate:** pure `cvflow.gate.handle_gate_command` (`GateResult`) is the SOLE `store.approve()` caller; unauthorized users ignored (handled=False); every authorized outcome returns a message (never silent). Wired via a Hermes **`command:approve`/`command:skip` hook** (`artifacts/hermes/hooks/cvflow-gate/`) that runs in the gateway process, returns `decision:"handled"` and short-circuits before the brain — plus a **plugin** (`artifacts/hermes/plugins/cvflow-gate/`) registering `/skip` (`/approve` is a Hermes built-in; the hook intercepts it). Collision fix: a **bare `/approve` (no job_id) falls through** to Hermes' built-in confirm flow; only `/approve <job_id>` is the cvflow gate. The MCP tool surface still has **no `approve` tool** (9 tools: ping, discover, analyze_jd, list_applications, get_application, request_review, submit, **compose_essay, status_report**). **Review message:** `request_review(job_id, feedback=None)` → moves to pending_review, **wires real Gemini tailoring** → `compile_tailored` (new resume method: assembles preamble + reordered `\input`s + selected projects, Tectonic-compiles) → returns `{pdf_path, diff, analysis_summary, instructions:"/approve <id> | /skip <id>"}`. `/edit` is brain-mediated via `request_review(feedback=...)` (PDF send + Gemini/Tectonic can't live in the 30s text-only hook). **Essays:** `cvflow.essays.compose_answer` grounds answers in real KB docs and validates citations against `KnowledgeBase.doc_keys()`; ungroundable/phantom-citation → `needs_clarification` (never a guess), exposed as `compose_essay`. **Live:** hook loaded (`Loaded hook 'cvflow-gate' for events: [command:approve, command:skip]`), `/skip` + `/approve` both known commands, MCP server rebuilds with Gemini wired (9 tools, no approve). **103 tests pass; ruff + mypy --strict clean.** Sub-plan: `2026-06-04-phase-8-approval-gate-chat-flows.md`; design: `specs/2026-06-04-phase-8-approval-gate-chat-flows-design.md`. **Known follow-up (pre-existing from Phase 7, NOT a Phase-8 regression):** `discover`/`analyze_jd` MCP tools error live (`'NoneType' has no attribute 'discover'`) because `build_tools` leaves `discovery`/`analyzer` as `None` — the brain (NIM) has no in-code provider class; these cvflow-internal LLM calls need an OpenAI-compatible provider (wire in a later phase). **Acceptance demo (user-run):** from the authorized chat, drive analyze_jd → request_review on a job, receive the PDF/diff, send `/approve <id>`, confirm `✅ Approved` + status `approved`. **Next: Phase 9 (browser automation).**
- 2026-06-04: **Phase 8 brainstorm + sub-plan written (pre-execution).** Nailed the deterministic human-approval mechanism by reading Hermes source: the gate = a `command:approve`/`command:skip` **hook** (`gateway/run.py:8003`, returns `decision:"handled"`, short-circuits before the brain; hook ctx carries `user_id`+`args`) registered via a thin Hermes **plugin** (makes the commands "known"). Rejected inline buttons (custom `callback_data` needs a Hermes fork) and the `clarify` primitive for the gate (resolves back into the agent loop — fine for OTP/clarification, not the gate). Gate logic lives in a pure `cvflow.gate.handle_gate_command` — the SOLE `store.approve()` caller. Decisions confirmed with user: slash-command gate; `/edit` re-tailors via `request_review(feedback=...)` (brain-mediated, since PDF send + Gemini/Tectonic can't live in the 30s text-only hook); essay composer module + MCP skill; wire real Gemini tailoring into `request_review` now. Design: `docs/superpowers/specs/2026-06-04-phase-8-approval-gate-chat-flows-design.md`; sub-plan: `2026-06-04-phase-8-approval-gate-chat-flows.md` (7 TDD tasks). **Next: execute.**
- 2026-06-04: **Phase 7 done (Hermes integration).** Built cvflow's MCP surface as a local **stdio MCP server** under `src/cvflow/mcp/`: a pure-Python dispatcher `CvflowTools` (`tools.py`, no MCP import → fully unit-testable) exposing 7 skills — `ping`, `discover`, `analyze_jd`, `list_applications`, `get_application`, `request_review`, `submit` — each dispatching to the existing domain modules; plus a thin **FastMCP** adapter (`server.py` + `__main__.py`) that registers exactly the `TOOL_NAMES` allowlist over stdio. **Gate invariant proven by construction:** there is NO `approve` tool/method on the surface (`TOOL_NAMES` excludes it; `hasattr(tools,"approve")` asserted False); `submit` calls `guard_can_submit` and raises `SubmissionBlocked` for every non-approved status; a test proves `submit` succeeds *only* after an out-of-band `store.approve()` (the future human Telegram callback, off-surface). 13 new tests (80 total), ruff + mypy(strict) clean. **Wired live:** `mcp_servers.cvflow` added to `~/.hermes/config.yaml` with a `tools.include` allowlist (same 7 skills); `cvflow` editable-installed into `.venv` for subprocess import; the running `hermes gateway` (PID 525) auto-reloaded and spawned the server; `hermes mcp test cvflow` → ✓ connected (912ms), 7 tools discovered, no `approve`; a standalone MCP stdio-client handshake returned `ping → {status: ok}`. Sub-plan: `2026-06-04-phase-7-hermes-integration.md`. **Resume MCP skill deferred to Phase 8** (needs the live Gemini tailoring provider — conscious scope decision, recorded in the sub-plan). **Phase-7 acceptance demo (user-initiated):** from the authorized Telegram chat, ask Hermes to call the cvflow `ping` tool — the brain's tool-calling round-trip to the deterministic skill is the final exit proof. **Next: Phase 8 (approval-gate skill + chat flows).**
- 2026-06-04: **Phase 7 prep — substrate mechanism nailed down + hosting moves to EC2.** Verified Hermes Agent is real (Nous Research, MIT, released Feb 2026) and that it extends **via MCP servers, not a proprietary Python plugin API**. **Locked integration design for Phase 7:** cvflow exposes its deterministic skills (discovery / analysis / resume / storage / **approval-gate**) as a **local stdio MCP server**; Hermes connects via `mcp_servers.cvflow` in `~/.hermes/config.yaml` with a `tools.include` **allowlist** (docs recommend allowlists for sensitive systems). Brain via `model.base_url=https://integrate.api.nvidia.com/v1`, `model=meta/llama-3.3-70b-instruct`, key in `~/.hermes/.env`. Telegram via `telegram.token` + `telegram.authorized_users:[<id>]`. **Gate invariant preserved by construction:** the exposed MCP tool surface never includes an `approve` tool — the agent can call `request_review`/`submit` (which asserts `status==approved` and raises) but only the human Telegram approval callback reaches `statemachine.approve()`. Hermes commands: `hermes model`, `hermes gateway telegram`, `hermes chat`, `/reload-mcp`. **PII privacy risk RESOLVED** (user accepts burner-data risk — see Open decisions). Hosting: user provisioned a **t3.small / 2 vCPU / 30 GB** EC2 box (SSH from any IP, no inbound http/https — fine, Telegram is outbound long-polling). **Phase 7 will be executed by a fresh Claude Code session ON the EC2 box** (it writes the `2026-06-04-phase-7-hermes-integration.md` TDD sub-plan first, then builds the MCP server + wires Hermes live). Memory files transferred to the box out-of-band.
- 2026-06-03: Repo initialized, project scaffolded, stack & decisions recorded. Build plan written. Profile + master resume populated. **Substrate locked = Hermes; brain = NIM meta/llama-3.3-70b-instruct (Nemotron rejected for latency); tailoring = Gemini 2.5 Flash. All three keys live-tested OK.**
- 2026-06-03: **Phase 0 done.** `pyproject.toml` (pinned deps + pytest/ruff/mypy config), `src/cvflow/config.py` (typed/validated frozen `Config`, `ConfigError`, api-key stripping, HH:MM validation), `src/cvflow/logging_setup.py` (rotating file logging, idempotent). 9 tests green, ruff + mypy(strict) clean; loader verified against the real gitignored `config.yaml`. Sub-plan: `2026-06-03-phase-0-foundations.md`.
- 2026-06-03: **Phase 1 done (safety-critical core).** `statemachine/` — `Status` StrEnum + legal-transition table; `transition()` refuses `approved` as a target; `approve()` is the sole producer of `approved` (only from `pending_review`); `guard_can_submit()` raises `SubmissionBlocked` for every non-approved status. `storage/` — `ApplicationStore` (SQLite, `:memory:`-capable) keyed on stable `job_id` (dedup → `DuplicateJob`); `set_status` routes through the state machine so it can never write `approved`; `approve()` is the only store path to it; `FormFields` loader with populated/missing/`require` never-guess semantics. 31 tests green (22 new), ruff + mypy(strict) clean. Sub-plan: `2026-06-03-phase-1-storage-statemachine.md`.
- 2026-06-03: **Phase 2 done.** `llm/` — `GeminiProvider` (google-genai 2.7) for the tailoring escalation: injectable client + clock for tests; per-day RPD tracking with day-rollover reset; raises `RpdExceeded` before touching the API when over budget; in-memory response cache (cache hits don't consume RPD). `LLMError`/`RpdExceeded`. 5 new tests (36 total), all mocked — no live key/network; ruff + mypy(strict) clean. Sub-plan: `2026-06-03-phase-2-llm-tailoring-client.md`.
- 2026-06-03: **Phase 3 done.** `knowledge/` — `KnowledgeBase.load()` recursively reads `profile/**/*.md` (incl. `projects/*`), excluding `*.example.md` templates and `README.md`; keys docs by relative stem; loads `form_fields.json` via the Phase-1 `FormFields`. Accessors: `skills`/`experience`/`essays`/`education`/`personality` (raise `KeyError` if absent — no silent default), `project_docs()`, `missing_form_fields()`, `full_context()` for full-context prompting. 7 new tests (43 total) incl. loading the committed `*.example.md` templates; ruff + mypy(strict) clean. Sub-plan: `2026-06-03-phase-3-knowledge-base.md`.
- 2026-06-03: **Job-source re-evaluated** (user asked re: fantastic.jobs / SerpApi). DECIDED: keep JobSpy as the only zero-cost self-hosted default, behind an injectable `search_fn` adapter seam (fantastic.jobs is hosted-SaaS-only + metered w/ undisclosed free tier; SerpApi free tier 100/mo too small; both breach zero-cost as primary). See Key-decisions entry.
- 2026-06-04: **Phase 6 done (Goal 3 + diff for Goal 4).** `resume/` — `parse_master()` reads the modular master into an ordered section list + selectable per-project blocks; `ResumeTailor.plan()` has the LLM (same `generate(prompt)->str` seam) pick section order + projects, then deterministically validates against real units (drops fabricated ids) and **caps projects at `MAX_PROJECTS=2`** (user requirement; JD-relevant, e.g. Java/Spring Boot → CRUDbot); `render()` reorders sections + emits only selected project blocks; `assert_no_new_facts()` is an exact subset check (every tailored content line must exist in the master — invariant 2 enforced deterministically, since tailoring only selects/reorders whole units, never rewrites text); `diff()` produces a plain-language summary + LLM narration for the gate; `compile_master()` shells to Tectonic (`CompileError` on failure). 8 tests (67 total); the real-master Tectonic compile test passes (skips when `tectonic` absent). ruff + mypy(strict) clean. Sub-plan: `2026-06-04-phase-6-resume-tailoring.md`. **Deferred to later phases:** essay/free-text auto-answer from the personality fingerprint → Phase 8; salary-hike discovery filter → Phase 4 follow-up; assembling preamble + tailored body into a compiled *tailored* PDF → Phase 8/11 wiring. **Next: Phase 7 (Hermes integration).**
- 2026-06-04: **Phase 6 prep + new user requirements.** Split `resume/master.tex` into modular `resume/sections/{experience,education,projects,skills,certifications}.tex` (`\input` in default order; content byte-identical). Installed **Tectonic 0.16.9** and confirmed the modular master **compiles to PDF**; fixed a pdfTeX/XeTeX incompatibility (guarded `\input{glyphtounicode}` + `\pdfgentounicode=1` with `\ifdefined\pdfgentounicode`) — PDF text verified ATS/AI-extractable; fixed a header contact-line margin overflow (`\small`→`\footnotesize`). **Template decision:** keep Jake Gutierrez's template (the r/developersIndia-recommended ATS standard). **Three new user requirements wired into the plan (Phases 4/6/8) and saved to memory:** (1) resume tailoring selects JD-relevant projects, **max 2** (e.g. Java/Spring Boot → CRUDbot); (2) AI auto-answers essays/free-text from a **personality fingerprint** (synthesis of real profile facts), escalating to the user only when a fact is genuinely absent; (3) **salary-hike filter** — current CTC 4 LPA, only apply to **≥20% hike (floor 4.8 LPA)**, never silently dropping no-salary postings.
- 2026-06-04: **Phase 5 done (Goal 2).** `analysis/` — `JDAnalysis` frozen dataclass (`to_json`/`from_json`); `fetch_jd()` behind an injectable `fetch_fn` (default stdlib `urllib`) with a stdlib `html.parser` HTML→text step that drops `script`/`style`/`head` bodies (no new deps), raising `JDFetchError` on empty body (never fail silently); `JDAnalyzer.analyze()` LLM-extracts required_skills / preferred_quals / seniority / tone / **applicant_instructions** via the Phase-2 provider seam (`generate(prompt)->str`), strips code fences, raises `JDAnalysisError` on unparseable replies — never-invent prompt (absent → empty). Persistence: additive `jd_analyses` table on `ApplicationStore` (`save_analysis`/`get_analysis`, FK `job_id`, `UnknownJob` if no record) — the gate/transition core is untouched. 8 new tests (60 total) incl. capturing an embedded "include the word pineapple" instruction; ruff + mypy(strict) clean. Sub-plan: `2026-06-04-phase-5-jd-analysis.md`. **Next: Phase 6 (Resume Tailoring).**
- 2026-06-03: **Phase 4 done (Goal 1).** `discovery/` — `normalize_rows()` (stable `site:id` job_id, url-sha1 fallback, within-batch dedup); `LLMRanker` (reorders real postings only, parses fenced JSON, **drops LLM-fabricated ids**, top-N truncation); `DiscoveryService.discover()` (search across `terms×locations` with injectable throttle/sleep → cross-day dedup via `ApplicationStore.exists()` → rank → persist presented jobs as `discovered` → return `RankedJob`s). JobSpy wrapped as the default `search_fn` behind the adapter seam. 9 new tests (52 total), fully mocked — no live scraping/LLM; ruff + mypy(strict) clean (added `jobspy.*` import override). Sub-plan: `2026-06-03-phase-4-discovery.md`. **OPEN RISK reiterated: ranking sends profile PII to the free-tier LLM — resolve consent/redaction before live runs.** **Next: Phase 5 (JD Analysis).**
