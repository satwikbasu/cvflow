# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

# Part A — Project: cvflow

## What this is

**cvflow** is an autonomous, self-hosted job-application agent that runs continuously on an Ubuntu server and talks to its single human operator **only** through a Telegram chat. Once a day it discovers and ranks job postings, then — for jobs the user picks — it analyzes the JD, tailors a LaTeX resume, and (after explicit human approval) fills and submits the application via browser automation, asking the user for OTPs or clarifications mid-flow when it gets stuck. It tracks every application's status in a queryable store. **Hard constraint: zero per-call / SaaS cost** — every component is open-source, free-tier, or self-hosted; the only allowed ongoing cost is the Ubuntu server itself.

## The 10 goals (authoritative; summarized but unambiguous)

1. **Daily discovery** — once/day at a configured time, search multiple boards, dedup across days, score/rank against the user profile, present **top 5–10** (title, company, role summary, relevance rationale, link). User replies to select.
2. **JD analysis** — for each selected job, fetch & deeply parse the full JD: required skills, preferred quals, seniority, tone/culture, and any applicant-specific instructions (e.g. "include the word pineapple" — attention-to-detail tests). Stored, drives tailoring & form-filling.
3. **Resume tailoring** — edit the user's master LaTeX resume per job: reorder sections, adjust bullet emphasis, align keywords to the JD **without fabricating facts**. Compile to PDF on the server.
4. **Human review gate (MANDATORY, must be architecturally un-bypassable)** — before any application action, send the user: the compiled PDF, a plain-language diff vs master, the JD analysis, and an inline prompt with ≥3 options (approve & apply / request edits / skip). No proceed without explicit approval.
5. **Autonomous submission** — after approval, drive the application form via browser automation: multi-page forms, file uploads, dropdowns, checkboxes, free-text. Capture proof (URL / confirmation # / screenshot / title).
6. **Auth: Google OAuth** — prefer "Sign in with Google" when offered, using a user-designated Google account and stored tokens. Security of stored creds must be documented.
7. **Auth: email OTP fallback** — when no Google sign-in, register/login with the designated email; when an OTP arrives, message the user (naming the platform), wait up to a configurable timeout (default **15 min**). On timeout: mark `OTP_TIMEOUT`, notify, pause for user instruction. **Never skip silently.**
8. **Mid-form clarification loop** — on any field unanswerable from the knowledge base, pause (preserve form state if possible), send the exact question + options, wait, then resume. If state can't be preserved and the form times out, log & notify.
9. **Status tracking** — persistent record per application: job ID, company, role, JD URL, discovery ts, application ts, status (`discovered / pending_review / approved / applied / otp_timeout / skipped / failed`), tailored-PDF path, confirmation ref. User can request a report anytime; send a full summary after each batch.
10. **Scheduling & proactive operation** — daily discovery runs on a schedule unattended; a heartbeat/health-check lets the user confirm it's alive. The user must never SSH, run commands, or open a dashboard during normal operation.

## Decided tech stack (with one-line justifications)

| Concern | Choice | Why |
|---|---|---|
| Language | **Python 3.11+** | All chosen libs (JobSpy, Playwright, python-telegram-bot, Gemini SDK) are first-class in Python. |
| Messaging | **Telegram Bot API** via `python-telegram-bot` | Long-polling needs **no public IP / TLS** (ideal behind home NAT); native inline keyboards; sends PDFs up to **50 MB** (> Discord's free 25 MB); single bot token. |
| Job discovery | **JobSpy** (`python-jobspy`) | Actively maintained (16k★), one API across LinkedIn/Indeed/Glassdoor/Google/ZipRecruiter; free & open-source. More stable than hand-rolled scrapers. |
| Browser automation | **Playwright (Python)** + `launch_persistent_context` | Persistent context reuses cookies/session across runs (Goals 5–7); auto-wait reduces flakiness; WebSocket transport. Run headed under `xvfb` on the server. Stealth plugin to reduce bot detection. |
| LLM inference (primary) | **Google Gemini API free tier** (Gemini 2.5 Flash) | Genuinely free, no card; ~**1,500 req/day** (Flash-Lite) / lower for Flash/Pro, ~1M TPM. Best quality of the free options — reserve for quality-sensitive steps (resume tailoring, clarification/essay drafting). **See privacy caveat below.** |
| LLM inference (secondary/fallback) | **NVIDIA NIM** free tier (`build.nvidia.com`, `nvapi-` key) | Free, no card, **OpenAI-compatible**, ~40 RPM (upgradable to 200), 100+ open-weight models (Llama 3.3, Nemotron, Gemma…). Slightly lower quality than Gemini 2.5 Flash but adds headroom: use for high-volume/low-stakes work (bulk JD pre-filter & ranking triage) so Gemini's daily RPD is spent only where quality matters. |
| Resume editing | Modular **LaTeX** (`\input` section files) + **TeX Live** (`latexmk`/`pdflatex`) | Structured edits swap/reorder section files instead of fragile regex on a monolith; reliable server-side PDF compile. |
| Diff summary | `git diff` on `.tex` + section/bullet set comparison, narrated by Gemini | Plain-language, factual change summary for the review gate (Goal 4). |
| App tracking store | **SQLite** | ACID, queryable, single-file, zero-cost; perfect for single-user durable status records (Goal 9). |
| Form-fields store | **JSON file** (`profile/form_fields.json`) | Matches spec; human-editable; explicit populated vs empty-key semantics. |
| Knowledge base | **Markdown files** in `profile/` | Human-authored source of truth; loaded in full as LLM context. |
| Scheduling | **APScheduler** inside the daemon + **systemd** service | One long-lived process owns the daily trigger and heartbeat; systemd keeps it alive across reboots/crashes. (Plain cron is an acceptable fallback.) |
| Secrets at rest | **Fernet** (`cryptography`) encryption + filesystem perms | Encrypt OAuth tokens / cookies; key kept outside the repo, files `chmod 600`. No paid secrets manager. |

### LLM provider strategy & privacy caveat (partly deferred)
- **`llm/` must be provider-agnostic** — a thin wrapper exposing one interface so Gemini and NVIDIA NIM (and later a paid key or local model) are swappable per-call. Route quality-sensitive calls to Gemini, bulk/triage calls to NIM, and let the wrapper fall back across providers when one hits its rate limit.
- **Privacy:** the free Gemini tier (and likely NIM's free tier) may use submitted data to improve products, and this system feeds it the user's resume and PII. Resolution options, preferred order: (a) accept with informed consent, (b) redact PII before sending where feasible, (c) **self-hosted local model** (zero per-call cost *and* no data leaves the server) if the Ubuntu box has the hardware, (d) paid API key (breaks the zero-cost rule — flag if proposed). **Not settled — confirm before loading real PII.**
- **Codex-via-ChatGPT-Go is NOT a viable brain here** (evaluated & rejected): ChatGPT Go does *not* include the full Codex agent / Agent Mode (only a mobile review-preview), and driving a ChatGPT-subscription OAuth as an unattended headless backend violates OpenAI's usage terms (account-ban risk). Codex also moved to API-token billing in 2026. Don't reintroduce this path.

## Cost constraints — how each piece stays free
JobSpy / Playwright / python-telegram-bot / SQLite / TeX Live / APScheduler / Fernet are all open-source (server-only cost). Gemini API and Telegram Bot API are free tiers. **The architecture must respect free-tier rate limits by design** — batch and cache LLM calls, keep daily volume under Gemini RPD, throttle JobSpy to avoid IP bans. **Flag explicitly in code/PRs any change that creates a realistic path to cost** (paid API, paid proxy, paid host, exceeding a free tier).

## Planned directory structure

```
cvflow/
├── CLAUDE.md                     # this file — project memory
├── README.md                     # human-facing overview & setup
├── config.example.yaml           # every config var, documented, no secrets
├── requirements.txt
├── src/cvflow/
│   ├── config.py                 # load/validate config.yaml
│   ├── statemachine/             # application status state machine — THE review gate
│   ├── bot/                      # Telegram interface: digests, approvals, OTP, clarifications, reports
│   ├── discovery/                # JobSpy search + cross-day dedup + LLM ranking
│   ├── analysis/                 # JD fetch + parse (skills, quals, tone, applicant instructions)
│   ├── resume/                   # LaTeX tailoring + compile + diff summary
│   ├── automation/               # Playwright form filling + proof capture
│   ├── auth/                     # Google OAuth + email OTP fallback
│   ├── storage/                  # SQLite models + form_fields loader
│   ├── llm/                      # Gemini client wrapper w/ rate limiting + caching
│   └── scheduler/                # APScheduler daily trigger + heartbeat
├── profile/                      # user knowledge base (real files gitignored)
│   ├── *.example.md              # templates: experience, skills, projects, education, personality, essays
│   └── form_fields.example.json  # form-field store template
├── resume/                       # master.example.tex (real master gitignored)
├── data/                         # gitignored: cvflow.db, generated PDFs, tokens, cookies
└── logs/                         # gitignored
```

## Orchestration substrate — OPEN decision (evaluate before heavy build)

Two paths for the daemon/orchestration layer (the messaging interface + multi-step task driving + browser automation glue):

- **Path A — Standalone daemon** (what the current scaffold assumes): hand-built `python-telegram-bot` + Playwright + APScheduler. Maximum control, smallest/most auditable trust surface, but the most code to write.
- **Path B — Build on an existing self-hosted agent framework** that already provides messaging + browser automation + multi-step autonomy + markdown/YAML memory. cvflow then contributes only its **domain tools** (JobSpy discovery, JD analysis, LaTeX tailoring, SQLite tracker) and the **deterministic approval gate**, which the framework calls. Far less glue code; depends on a young, fast-moving project that would hold OAuth tokens + PII.

Candidates evaluated (all self-hosted, MIT-ish, Telegram-capable, BYO-LLM):
| Framework | Note |
|---|---|
| **OpenClaw** | Biggest ecosystem & local orchestration, but largest trust surface — app-level checks in a shared-memory process (a bug/exploit can reach everything on the box). |
| **Hermes Agent** (Nous Research, Feb 2026) | Persistent memory + closed skill-learning loop, 16+ messaging platforms, single-curl install, lower setup friction & security surface than OpenClaw. Strong first alternative to trial. |
| **NanoClaw** | OS-level **container isolation** (Docker), per-group isolated filesystems — best blast-radius containment; attractive precisely because we handle tokens/PII. |
| **TrustClaw** | Security-focused, sandboxed execution, OAuth-based tools, easier deploy. |

**Standing decision regardless of path:** the Goal-4 approval gate and the "never fabricate facts" rule are **deterministic cvflow code**, never delegated to an agent's probabilistic loop. An agent framework may *call* the gate tool but can never satisfy it on its own. **Recommended next step:** trial **Hermes** (and a sandboxed option like **NanoClaw**) on a throwaway account before committing; if neither earns trust for token/PII handling, fall back to Path A.

## Key architectural decisions

- **The review gate is a state machine, not a convention (Goal 4).** Applications live in `statemachine/`. The only legal path to `APPLYING`/`applied` is through `approved`, and `approved` is set **exclusively** by the Telegram approval callback handler. The submission entrypoint in `automation/` asserts `status == approved` and raises otherwise. This makes accidental bypass impossible — there is one choke point.
- **Never invent facts about the user.** Resume tailoring and form-filling read only from `profile/`. A field unanswerable from the knowledge base triggers the clarification loop (Goal 8) — it must never guess. Empty `form_fields.json` keys go to a pending list reported to the user; they do **not** block an application if the markdown KB can answer.
- **Single long-lived daemon.** The Telegram bot, scheduler, and heartbeat share one process so clarification/OTP loops can pause and resume browser sessions while still receiving chat replies. Mind the single event loop (don't let long-poll ingress starve other work).
- **Modular LaTeX over string-replacement.** Master resume is split into `\input` section files so tailoring reorders/swaps whole sections deterministically and the diff is meaningful.
- **Dedup is permanent.** A job surfaced once is never re-shown unless explicitly requested (keyed by a stable job ID in SQLite).

## Human-in-the-loop checkpoints & enforcement
1. **Job selection** (Goal 1) — user picks from the daily digest; nothing analyzed/tailored without selection.
2. **Resume approval gate** (Goal 4) — enforced by the state machine above; un-bypassable.
3. **OTP request** (Goal 7) — blocks the specific application up to the timeout, then `otp_timeout` + pause-for-instruction; never silent.
4. **Mid-form clarification** (Goal 8) — pauses the browser session for the exact question, resumes on reply.

## Known limitations / risks / deferred decisions
- **Orchestration substrate (Path A vs B)** — undecided; trial Hermes/NanoClaw before heavy build (see Orchestration section).
- **LLM free-tier privacy** (Gemini and NIM may train on submitted PII) — unresolved (see caveat above).
- **Anti-bot / ToS** — LinkedIn/Indeed/Glassdoor resist scraping and automated applying; expect breakage and account-risk. JobSpy endpoints drift (e.g. Glassdoor's Next.js migration). Keep scraping throttled; treat platform login accounts as expendable.
- **OAuth-token / cookie theft** = full access to the linked Google/application accounts; **PII files** = identity exposure. Mitigations (no cost): dedicated **burner** Google account, dedicated unprivileged Linux user, Fernet encryption at rest, `chmod 600`, host firewall, secrets never in git. Document these wherever creds are stored.
- **Free-tier rate limits** (Gemini RPD/RPM, JobSpy IP bans) bound daily throughput — design within them, don't assume unlimited.
- **Browser session preservation** on clarification/OTP pause is best-effort; if the form times out, log + notify (Goals 7–8).

## Development conventions
- **Layout:** `src/` package `cvflow`; one concern per subpackage (above).
- **Commits:** Conventional Commits (`feat:`, `fix:`, `chore:`, `docs:`…). Git identity comes from **global** git config — do not set repo-local user/email.
- **Secrets/PII:** only `*.example.*` templates are committed; real `config.yaml`, `profile/*`, `resume/*`, `data/`, `logs/` are gitignored. Never commit a real token, cookie, resume, or profile.
- **Run locally:** create a venv, `pip install -r requirements.txt`, `playwright install chromium`, install TeX Live, copy `config.example.yaml`→`config.yaml`, populate `profile/`, then run the daemon (entrypoint TBD as `src/cvflow` is built).
- **Deploy to Ubuntu:** run as a dedicated unprivileged user under a **systemd** unit (auto-restart); browser runs headed under `xvfb`; APScheduler owns the daily trigger; heartbeat reports liveness to Telegram.
- **Respect the rules in Part B** for all code changes.

---

# Part B — Behavioral guidelines

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.
