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

### [ ] Phase 2 — LLM layer (tailoring client)
**Delivers:** the Gemini client for the resume-tailoring escalation. (The agent brain — NIM `meta/llama-3.3-70b-instruct` — is configured inside Hermes in Phase 7, not here.)
- `src/cvflow/llm/` — `GeminiProvider` (google-genai) with RPD tracking + response caching, used by the resume module.
**Exit:** tests (mocked HTTP) prove the call path, RPD accounting, and cache hits. No live key needed for tests.

### [ ] Phase 3 — Knowledge-base loader
**Delivers:** profile ingestion.
- Loader that reads all `profile/*.md` **and `profile/projects/*.md`** (recurse) plus `form_fields.json` into a structured context object loaded in full for downstream prompts.
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

### [ ] Phase 7 — Hermes integration (substrate setup) — DECIDED
**Decision (locked):** substrate = **Hermes Agent**. Brain = `meta/llama-3.3-70b-instruct` (NIM free tier); resume tailoring escalates to Gemini 2.5 Flash.
**Delivers:** a running Hermes that can call cvflow skills.
- Install Hermes; `hermes model` → configure NIM `meta/llama-3.3-70b-instruct` brain; configure Telegram (single authorized user).
- Register cvflow's domain modules (discovery, analysis, resume, storage, gate) as Hermes **skills/tools**; confirm Hermes invokes them and that the deterministic gate skill cannot be bypassed by the agent loop.
**Exit:** Hermes responds on Telegram, calls a trivial cvflow skill, and the brain handles a tool-calling round-trip. Security posture for token/PII handling reviewed.

### [ ] Phase 8 — Approval-gate skill + chat flows (Goals 1,4,7,8,9,10 surface)
**Delivers:** the **gate wiring** and chat interactions as Hermes skills (Hermes provides the messaging transport).
- Approval-gate skill: sends the tailored PDF + diff + JD analysis and presents **approve & apply / request edits / skip**; the approve path is the sole caller of `statemachine.approve()`.
- Skills for: daily digest + selection, OTP request, clarification Q&A, status reports. Single authorized Telegram user enforced.
**Exit:** tests (mocked transport) prove the approve path is the only route to `approved`, unauthorized users are ignored, and the review message carries PDF/diff/analysis.

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
- **JobSpy** for discovery: maintained, multi-board, free.
- **Playwright** (persistent context) over Selenium: session reuse, auto-wait, WebSocket.
- **SQLite** tracking + **JSON** form-fields + **markdown** KB.
- **Modular LaTeX** + TeX Live over regex-on-monolith: deterministic reorder & meaningful diff.
- **Orchestration substrate = Hermes Agent** (DECIDED): less glue code, lower trust surface than OpenClaw, built for messaging + autonomous multi-step + browser + BYO-LLM. cvflow = deterministic skills Hermes calls.
- **LLM (DECIDED):** brain = `meta/llama-3.3-70b-instruct` (NIM free tier, fast ~1.5–2s, non-reasoning, tool-calling, 128k ctx, ~40 RPM / no hard daily cap), configured in Hermes. Resume tailoring escalates to **Gemini 2.5 Flash** (low volume → under RPD, higher quality). *Nemotron Super 49B v1.5 tested & rejected: reasoning model, ~3.5 min latency on free tier.*
- **Codex-via-ChatGPT-Go: rejected** — Go lacks the full Codex agent; subscription-OAuth as a headless backend violates OpenAI ToS; Codex moved to token billing. Do not reintroduce.
- **Approval gate as a state machine**, not a convention.

## Open decisions / risks (must resolve where noted)

- **LLM PII privacy (still open):** both free tiers (NIM + Gemini) may train on submitted data, including the user's PII. Options: informed consent / PII redaction / local model (needs server GPU/VRAM specs) / paid key (breaks zero-cost). Resolve before loading real profile data into live calls (Phase 4+).
- **Anti-bot / ToS:** LinkedIn/Indeed/Glassdoor resist scraping & auto-apply; expect breakage and treat platform accounts as expendable. JobSpy endpoints drift.
- **Token/cookie theft & PII exposure:** mitigate with burner Google account, unprivileged user, Fernet at rest, chmod 600, host firewall.
- **Browser session preservation** on pause is best-effort; on form timeout → log + notify.

## Progress log

- 2026-06-03: Repo initialized, project scaffolded, stack & decisions recorded. Build plan written. Profile + master resume populated. **Substrate locked = Hermes; brain = NIM meta/llama-3.3-70b-instruct (Nemotron rejected for latency); tailoring = Gemini 2.5 Flash. All three keys live-tested OK.** **Next: Phase 0.**
