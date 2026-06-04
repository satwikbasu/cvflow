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

**Orchestration substrate = Hermes Agent** (Nous Research, self-hosted, MIT-ish). Hermes is the always-on runtime and provides the **Telegram interface, scheduling, the LLM brain, and the browser runtime**. cvflow's job is to supply **deterministic domain skills** that Hermes calls — discovery, JD analysis, resume tailoring, storage, and crucially the **approval gate**. The agent's probabilistic loop may *invoke* these skills but can never substitute for them.

**LLM:** brain = `meta/llama-3.3-70b-instruct` on the NVIDIA NIM free tier (fast ~1.5–2s, non-reasoning, good tool-calling, 128k ctx, ~40 RPM / no hard daily cap), configured inside Hermes via `hermes model`. Resume tailoring escalates to **Gemini 2.5 Flash** (low volume, higher quality). *(Nemotron Super 49B v1.5 was tested and rejected — as a reasoning model its latency hit ~3.5 min on the free tier, unusable for an agent.)*

| Subpackage (`src/cvflow/`) | Responsibility | Goals |
|---|---|---|
| `config.py` | load & validate `config.yaml` | — |
| `statemachine/` | application status state machine — **the un-bypassable review gate** | 4 |
| `storage/` | SQLite tracking store + `form_fields.json` loader (populated vs empty keys) | 9 |
| `llm/` | thin client for the tailoring escalation to Gemini (the brain itself is configured in Hermes) | 3 |
| `discovery/` | JobSpy multi-board search + cross-day dedup + LLM ranking → top 5–10 | 1 |
| `analysis/` | fetch & parse full JD (skills, quals, seniority, tone, applicant instructions) | 2 |
| `resume/` | tailor modular LaTeX per job, compile PDF, generate plain-language diff vs master | 3,4 |
| `automation/` | form filling (multi-page, uploads), pause/resume, proof capture — runs as a Hermes skill | 5,8 |
| `auth/` | Google OAuth + email-OTP fallback | 6,7 |

Hermes supplies messaging (Telegram), scheduling, the heartbeat, and the browser runtime, so there is **no hand-built `bot/` or `scheduler/`** — those surface as Hermes config + thin skills (e.g. an approval-gate skill that sends the PDF/diff and awaits a structured reply).

**Data flow:** scheduler → discovery (dedup+rank) → Telegram digest → user selects → analysis → resume tailoring → **approval gate (Telegram)** → automation (+ auth, clarification, OTP) → proof captured → storage updated → status report.

**Knowledge base:** `profile/*.md` is the single source of truth about the user, loaded in full as LLM context. `profile/form_fields.json` holds recurring form values; an empty key is reported to the user, never guessed.

## Tech stack

Python 3.11+ · **Hermes Agent** (runtime: Telegram + scheduling + browser + LLM routing) · NVIDIA NIM `llama-3.3-70b-instruct` brain + Gemini 2.5 Flash (tailoring) · SQLite · JobSpy · Playwright · modular LaTeX + TeX Live · systemd · Fernet. Rationale for each choice is in the build plan.

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

Install Hermes (single-curl) and run it as a dedicated unprivileged user under a **systemd** unit (auto-restart); the browser runs headed under **xvfb**; Hermes owns the daily schedule + heartbeat; cvflow's skills are registered with Hermes. Use a **burner** Google account for OAuth. Details land in `docs/deploy.md` during Phase 11.

## Conventions

- **Layout:** package `cvflow` under `src/`; one concern per subpackage (table above). Prefer small, focused files.
- **TDD:** failing test → watch it fail → minimal implementation → watch it pass → commit. Frequent small commits.
- **Commits:** Conventional Commits (`feat:`, `fix:`, `chore:`, `docs:`…). Git identity comes from **global** git config — never set repo-local user/email.
- **Secrets/PII:** private single-user repo — real `profile/` + `resume/*.tex` PII is committed directly (invariant 5). No `*.example` data templates; the only committed example is `config.example.yaml` (secrets-free config skeleton, since `config.yaml` is gitignored).

---

## Behavioral guidelines

Bias toward caution over speed; for trivial tasks, use judgment.

**1. Think before coding.** State assumptions; if multiple interpretations exist, surface them rather than picking silently; if a simpler approach exists, say so; if something is unclear, stop and ask.

**2. Simplicity first.** Minimum code that solves the problem. No speculative features, abstractions for single-use code, unrequested configurability, or error handling for impossible scenarios. If 200 lines could be 50, rewrite.

**3. Surgical changes.** Touch only what the task requires. Don't refactor or reformat adjacent code that isn't broken; match existing style. Remove only the orphans *your* changes created; mention pre-existing dead code, don't delete it. Every changed line should trace to the request.

**4. Goal-driven execution.** Turn tasks into verifiable goals ("add validation" → "write tests for invalid inputs, then make them pass"). For multi-step work, state a brief plan with a verification check per step. Strong success criteria let you loop without constant clarification.
