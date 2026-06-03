# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

**cvflow** is an autonomous, self-hosted job-application agent that runs continuously on an Ubuntu server and is operated **only** through a Telegram chat. Once a day it discovers and ranks job postings against the user's profile, then — for jobs the user picks — analyzes the JD, tailors a LaTeX resume, and (after **explicit human approval in chat**) fills and submits the application via browser automation, asking the user for OTPs and clarifications when stuck. **Hard constraint: zero per-call / SaaS cost** — every component is open-source or free-tier; the only allowed ongoing cost is the server.

## Build plan — read this first

The roadmap, phase order, exit criteria, and full decision log live in **`docs/superpowers/plans/2026-06-03-cvflow-build-plan.md`**. Always check it before starting work to find the current phase. Before executing a phase, write its bite-sized TDD sub-plan (`superpowers:writing-plans`) and keep the phase checkboxes and the plan's progress log in sync.

**Current status:** scaffolded; next is Phase 0 (Foundations). No application code exists yet under `src/cvflow/`.

## Critical invariants (never violate)

1. **The approval gate is deterministic, not a convention.** The only path to status `approved` is the Telegram approval callback; the submission entrypoint asserts `status == approved` and raises otherwise. An agent/LLM may *call* the gate but must never be able to satisfy it. (Goal 4.)
2. **Never fabricate facts about the user.** All facts come from `profile/`. A field unanswerable from the knowledge base triggers the clarification loop — never a guess. (Goals 3, 8.)
3. **Never fail silently.** Every skip, OTP timeout, and error is logged *and* reported to the user via Telegram. (Goals 7, 8, 9.)
4. **Respect free-tier limits by design** (Gemini RPD, NIM RPM, JobSpy throttle) and **flag any change that introduces a cost** (paid API/proxy/host/overage).
5. **Credentials never enter git; PII is intentionally committed to this PRIVATE repo.** `config.yaml`, `data/`, `logs/`, and the CV PDF (tokens, keys, cookies, runtime data) stay gitignored. The `profile/` knowledge base + `form_fields.json` and the **master resume LaTeX source** (`resume/*.tex`) ARE committed on purpose (PII, no secrets) so the system deploys by `git clone` — do **not** re-ignore them. Tokens/cookies are Fernet-encrypted at rest, `chmod 600`.

## Architecture (big picture)

A single long-lived Python daemon owns scheduling, the Telegram interface, and browser automation so clarification/OTP loops can pause and resume a browser session while still receiving chat replies. Domain logic is path-independent of the orchestration substrate (a possible agent-framework swap is deferred — see plan Phase 7).

| Subpackage (`src/cvflow/`) | Responsibility | Goals |
|---|---|---|
| `config.py` | load & validate `config.yaml` | — |
| `statemachine/` | application status state machine — **the un-bypassable review gate** | 4 |
| `storage/` | SQLite tracking store + `form_fields.json` loader (populated vs empty keys) | 9 |
| `llm/` | provider-agnostic wrapper: Gemini (quality) + NVIDIA NIM (bulk/fallback), rate-limited, cached | 1,2,3,8 |
| `discovery/` | JobSpy multi-board search + cross-day dedup + LLM ranking → top 5–10 | 1 |
| `analysis/` | fetch & parse full JD (skills, quals, seniority, tone, applicant instructions) | 2 |
| `resume/` | tailor modular LaTeX per job, compile PDF, generate plain-language diff vs master | 3,4 |
| `bot/` | Telegram: digests, approval prompts, OTP, clarifications, reports, heartbeat | 1,4,7,8,9,10 |
| `automation/` | Playwright form filling (multi-page, uploads), pause/resume, proof capture | 5,8 |
| `auth/` | Google OAuth + email-OTP fallback | 6,7 |
| `scheduler/` | APScheduler daily trigger + heartbeat | 10 |

**Data flow:** scheduler → discovery (dedup+rank) → Telegram digest → user selects → analysis → resume tailoring → **approval gate (Telegram)** → automation (+ auth, clarification, OTP) → proof captured → storage updated → status report.

**Knowledge base:** `profile/*.md` is the single source of truth about the user, loaded in full as LLM context. `profile/form_fields.json` holds recurring form values; an empty key is reported to the user, never guessed.

## Tech stack

Python 3.11+ · SQLite · python-telegram-bot (Telegram: no public IP, 50 MB files, inline keyboards) · JobSpy · Playwright (persistent context) · Gemini free tier + NVIDIA NIM free tier (provider-agnostic `llm/`) · modular LaTeX + TeX Live · APScheduler · systemd · Fernet. Rationale for each choice is in the build plan.

## Commands

```bash
# Setup
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
sudo apt install texlive-full          # LaTeX compilation
cp config.example.yaml config.yaml     # then fill secrets; populate profile/ and resume/master.tex

# Develop
pytest                                  # all tests
pytest path/to/test_file.py::test_name -v   # a single test
ruff check .                            # lint
mypy src                                # type-check
```
(Test/lint config is established in Phase 0; the daemon entrypoint is wired in Phase 11.)

## Deployment (Ubuntu)

Run as a dedicated unprivileged user under a **systemd** unit (auto-restart across reboots/crashes); the browser runs headed under **xvfb**; APScheduler owns the daily trigger; a heartbeat reports liveness to Telegram. Use a **burner** Google account for OAuth. Details land in `docs/deploy.md` during Phase 11.

## Conventions

- **Layout:** package `cvflow` under `src/`; one concern per subpackage (table above). Prefer small, focused files.
- **TDD:** failing test → watch it fail → minimal implementation → watch it pass → commit. Frequent small commits.
- **Commits:** Conventional Commits (`feat:`, `fix:`, `chore:`, `docs:`…). Git identity comes from **global** git config — never set repo-local user/email.
- **Secrets/PII:** only `*.example.*` templates committed (see invariant 5).

---

## Behavioral guidelines

Bias toward caution over speed; for trivial tasks, use judgment.

**1. Think before coding.** State assumptions; if multiple interpretations exist, surface them rather than picking silently; if a simpler approach exists, say so; if something is unclear, stop and ask.

**2. Simplicity first.** Minimum code that solves the problem. No speculative features, abstractions for single-use code, unrequested configurability, or error handling for impossible scenarios. If 200 lines could be 50, rewrite.

**3. Surgical changes.** Touch only what the task requires. Don't refactor or reformat adjacent code that isn't broken; match existing style. Remove only the orphans *your* changes created; mention pre-existing dead code, don't delete it. Every changed line should trace to the request.

**4. Goal-driven execution.** Turn tasks into verifiable goals ("add validation" → "write tests for invalid inputs, then make them pass"). For multi-step work, state a brief plan with a verification check per step. Strong success criteria let you loop without constant clarification.
