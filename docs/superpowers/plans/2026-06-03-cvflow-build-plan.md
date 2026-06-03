# cvflow Build Plan (Master Roadmap)

> **For agentic workers:** This is the **master roadmap** — the source of truth for *order, scope, and exit criteria*. It is intentionally phase-level. Before executing a phase, write a detailed bite-sized TDD sub-plan for it using `superpowers:writing-plans`, save it as `docs/superpowers/plans/YYYY-MM-DD-phase-N-<name>.md`, then execute with `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans`. Keep the phase checkboxes below in sync as phases complete.

**Goal:** Build cvflow — an autonomous, self-hosted, zero-SaaS-cost job-application agent operated entirely through Telegram, with a mandatory human approval gate before any application is submitted.

**Architecture:** A single long-lived Python daemon (or an agent-framework substrate, decision in Phase 6) owns scheduling, the Telegram interface, and browser automation. Domain logic (discovery, JD analysis, resume tailoring, tracking) is built as path-independent modules. The Goal-4 approval gate and the "never fabricate facts" rule are **deterministic code**, never delegated to an LLM/agent loop.

**Tech Stack:** Python 3.11+, SQLite, python-telegram-bot, JobSpy, Playwright, Gemini + NVIDIA NIM (provider-agnostic), modular LaTeX + TeX Live, APScheduler, systemd, Fernet.

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
       7 Orchestration-substrate DECISION (spike: Hermes / NanoClaw vs standalone)
                 ↓
       8 Telegram interface (wires the gate)
       9 Browser automation (Playwright)
       10 Auth (Google OAuth + email OTP)
       11 Scheduler + daemon wiring + systemd deploy
       12 End-to-end dry run on a throwaway account
```

---

## Phases

### [ ] Phase 0 — Foundations
**Delivers:** runnable package skeleton, config loading, logging, test harness.
- `src/cvflow/config.py` — load & validate `config.yaml` against `config.example.yaml` schema; typed config object.
- Logging setup writing to `logs/` (rotating), also surfaced to Telegram later.
- `pyproject.toml`/`requirements.txt` pinned; `pytest`, `ruff`, `mypy` configured.
- `playwright install chromium`; document TeX Live install.
**Exit:** `pytest` runs (even if just config tests); `ruff`/`mypy` clean; config loads from a sample `config.yaml`.

### [ ] Phase 1 — Storage + State Machine (safety-critical core)
**Delivers:** the durable tracking store and the un-bypassable gate.
- `src/cvflow/storage/` — SQLite schema & models for the application record (job ID, company, role, JD URL, discovery ts, application ts, status, tailored-PDF path, confirmation ref); `form_fields` JSON loader with populated-vs-empty-key semantics.
- `src/cvflow/statemachine/` — explicit states (`discovered → pending_review → approved → applied`; plus `otp_timeout`, `skipped`, `failed`); legal-transition table; `approve()` is the *only* setter of `approved`; a `guard_can_submit(app)` that raises unless `status == approved`.
**Exit:** tests prove illegal transitions raise, `approved` cannot be reached except via `approve()`, and `guard_can_submit` blocks every non-approved status. Dedup keyed by stable job ID is enforced.

### [ ] Phase 2 — LLM layer
**Delivers:** provider-agnostic LLM access with rate limiting.
- `src/cvflow/llm/` — one interface; `GeminiProvider` (google-genai) and `NimProvider` (OpenAI-compatible); router (quality→Gemini, bulk→NIM) with cross-provider fallback on rate-limit; per-provider RPD/RPM tracking; response caching.
**Exit:** tests (mocked HTTP) prove routing, fallback-on-429, RPD/RPM accounting, and cache hits. No live key needed for tests.

### [ ] Phase 3 — Knowledge-base loader
**Delivers:** profile ingestion.
- Loader that reads all `profile/*.md` (+ `form_fields.json`) into a structured context object loaded in full for downstream prompts.
**Exit:** tests load the `*.example.md` templates and expose experience/skills/essays/form-fields; missing-field detection returns a pending list (no guessing).

### [ ] Phase 4 — Discovery
**Delivers:** Goal 1.
- `src/cvflow/discovery/` — JobSpy search across configured sites; throttling; cross-day dedup via SQLite; LLM ranking against the profile (uses Phase 2 + 3); produce top 5–10 with title/company/summary/rationale/link.
**Exit:** tests (mocked JobSpy output) prove dedup across runs, ranking ordering, and top-N selection.

### [ ] Phase 5 — JD Analysis
**Delivers:** Goal 2.
- `src/cvflow/analysis/` — fetch full JD; LLM-extract required skills, preferred quals, seniority, tone/culture, and **applicant-specific instructions** (e.g. "include the word pineapple"); persist analysis linked to the application record.
**Exit:** tests on sample JDs extract the structured fields and capture an embedded applicant instruction.

### [ ] Phase 6 — Resume Tailoring
**Delivers:** Goal 3 + the diff for Goal 4.
- `src/cvflow/resume/` — operate on modular master (`master.tex` + `sections/*.tex`); reorder/emphasize sections & bullets per JD **without adding facts**; compile via `latexmk`; generate plain-language diff (section reorder + bullet changes + promoted/demoted skills) narrated by the LLM.
**Exit:** tests prove a tailored `.tex` compiles to PDF, no new factual claims vs master (assert against KB), and a human-readable diff is produced.

### [ ] Phase 7 — Orchestration-substrate DECISION (spike)
**Delivers:** the Path A (standalone) vs Path B (agent framework) decision — see CLAUDE.md / Open Decisions below.
- Time-boxed spike: trial **Hermes** and a sandboxed option (**NanoClaw**) on a throwaway account; verify (a) can enforce the deterministic gate by calling cvflow tools, (b) security posture for holding OAuth tokens + PII, (c) Telegram + Playwright integration quality.
**Exit:** documented decision committed; Phases 8–11 scoped to the chosen path. Default fallback = Path A (standalone daemon).

### [ ] Phase 8 — Telegram interface (Goals 1,4,7,8,9,10 surface)
**Delivers:** the chat interface and the **gate wiring**.
- `src/cvflow/bot/` — long-polling bot; daily digest with selection; **approval prompt with inline keyboard (approve & apply / request edits / skip)** whose approve callback is the sole caller of `statemachine.approve()`; OTP request flow; clarification Q&A flow; status reports; heartbeat. Authorized to a single Telegram user ID.
**Exit:** tests (mocked Telegram) prove the approve callback is the only route to `approved`, unauthorized users are ignored, and PDFs/diffs/analysis are sent in the review message.

### [ ] Phase 9 — Browser automation (Goals 5, 8)
**Delivers:** form filling + proof.
- `src/cvflow/automation/` — Playwright `launch_persistent_context` (headed under xvfb, stealth); multi-page forms, uploads, dropdowns, checkboxes, free-text from KB/form-fields; pause→clarify→resume preserving state; capture proof (URL/confirmation#/screenshot/title).
**Exit:** tests against a local fixture form prove field filling, file upload, pause/resume, and proof capture. `guard_can_submit` is asserted at entry.

### [ ] Phase 10 — Auth (Goals 6, 7)
**Delivers:** login flows.
- `src/cvflow/auth/` — Google OAuth (stored, Fernet-encrypted tokens; documented risks; burner account); email-OTP fallback that messages the user, waits up to timeout, marks `otp_timeout` + pauses on timeout (never silent).
**Exit:** tests prove token encryption round-trip, OTP-wait timeout → `otp_timeout` + notification, and OTP success → resume.

### [ ] Phase 11 — Scheduler + daemon wiring + deploy
**Delivers:** Goal 10 + production operation.
- `src/cvflow/scheduler/` — APScheduler daily trigger + heartbeat; daemon entrypoint wiring all modules end-to-end; systemd unit (dedicated unprivileged user, auto-restart, xvfb).
**Exit:** daemon boots, schedules discovery, sends heartbeat; systemd unit documented in `docs/deploy.md`.

### [ ] Phase 12 — End-to-end dry run
**Delivers:** confidence.
- Full flow on a throwaway account against a test/sandbox posting; verify the gate truly blocks until approval and proof is captured.
**Exit:** one complete discovery→approval→submission cycle logged & reported, with the gate demonstrably blocking pre-approval.

---

## Key decisions & rationale (settled)

- **Telegram** over Discord: long-polling needs no public IP, 50 MB PDF sends, native inline keyboards.
- **JobSpy** for discovery: maintained, multi-board, free.
- **Playwright** (persistent context) over Selenium: session reuse, auto-wait, WebSocket.
- **SQLite** tracking + **JSON** form-fields + **markdown** KB.
- **Modular LaTeX** + TeX Live over regex-on-monolith: deterministic reorder & meaningful diff.
- **Gemini free tier primary, NVIDIA NIM free tier secondary**, behind a provider-agnostic `llm/` wrapper (quality→Gemini, bulk/triage→NIM, fallback on rate-limit).
- **Codex-via-ChatGPT-Go: rejected** — Go lacks the full Codex agent; subscription-OAuth as a headless backend violates OpenAI ToS; Codex moved to token billing. Do not reintroduce.
- **Approval gate as a state machine**, not a convention.

## Open decisions / risks (must resolve where noted)

- **Orchestration substrate (Phase 7):** standalone daemon vs Hermes/NanoClaw/TrustClaw/OpenClaw. Default fallback = standalone. Decide before Phase 8.
- **LLM PII privacy:** both free tiers may train on submitted data. Options: informed consent / PII redaction / **local model** (needs server GPU/VRAM specs — get from user) / paid key (breaks zero-cost). Resolve before loading real PII (latest: before Phase 4 uses real profile data).
- **Anti-bot / ToS:** LinkedIn/Indeed/Glassdoor resist scraping & auto-apply; expect breakage and treat platform accounts as expendable. JobSpy endpoints drift.
- **Token/cookie theft & PII exposure:** mitigate with burner Google account, unprivileged user, Fernet at rest, chmod 600, host firewall.
- **Browser session preservation** on pause is best-effort; on form timeout → log + notify.

## Progress log

- 2026-06-03: Repo initialized, project scaffolded, stack & decisions recorded. Build plan written. **Next: Phase 0.**
