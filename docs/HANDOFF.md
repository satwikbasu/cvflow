# cvflow — Session Handoff

Paste the prompt below into a fresh Claude Code session started inside this cloned repo.

## Before you start (human, one-time on this machine)
1. **Secrets** (kept out of git on purpose): create `config.yaml` from `config.example.yaml` and fill in the four secret values — `telegram.bot_token`, `telegram.authorized_user_id`, `llm.brain.api_key` (NVIDIA NIM `nvapi-...`), `llm.tailoring.api_key` (Gemini `AIza...`). All non-secret values in the example are already correct. (Your keys are in the exported transcript `2026-06-03-122714-init.txt`.)
2. **Git push access:** this repo pushes via an SSH host alias `github-personal` (key `~/.ssh/id_ed25519_satwikbasu`). On a new machine, set up your GitHub SSH key and either recreate that alias in `~/.ssh/config` or run `git remote set-url origin git@github.com:satwikbasu/cvflow.git`. Use your **global** git identity (do not set repo-local user/email).

---

## Handoff prompt (paste this)

> I'm continuing work on **cvflow**, an autonomous self-hosted job-application agent. Read `CLAUDE.md` and `docs/superpowers/plans/2026-06-03-cvflow-build-plan.md` in full before doing anything — they are the source of truth for architecture, invariants, and the phase roadmap.
>
> **State of the project (all committed):**
> - Scaffolding, profile knowledge base, and master resume (`resume/master.tex`) are done.
> - **Decisions locked:** substrate = **Hermes Agent**; agent brain = `meta/llama-3.3-70b-instruct` on NVIDIA NIM free tier (Nemotron was rejected — reasoning model, ~3.5 min latency); resume tailoring escalates to **Gemini 2.5 Flash**. cvflow = deterministic skills Hermes calls; the Goal-4 approval gate is deterministic code, never the agent loop.
> - All three API/bot credentials were live-tested OK in the previous session.
> - `config.yaml` exists locally with real values (gitignored). Confirm it's present; if not, I'll create it from `config.example.yaml`.
>
> **Next task: execute Phase 0 (Foundations)** from the build plan. First write the bite-sized TDD sub-plan at `docs/superpowers/plans/<today>-phase-0-foundations.md` (use the `superpowers:writing-plans` skill), then implement it test-first:
> - `pyproject.toml` with pinned deps + `pytest`/`ruff`/`mypy` config
> - `src/cvflow/config.py` — load & validate `config.yaml` into a typed object (TDD)
> - logging setup writing to `logs/`
> - a passing test harness (`pytest` green, `ruff`/`mypy` clean)
>
> Follow the build plan's standing rules (TDD, deterministic gate, never fabricate user facts, never fail silently, respect free-tier limits, secrets never in git, profile PII intentionally committed). Commit with Conventional Commits as you go. Then continue to **Phase 1 (storage + the approval-gate state machine)** — the safety-critical core.

---

## Quick orientation for the new session
- **What/why/architecture:** `CLAUDE.md`
- **Roadmap + locked decisions + open risks:** `docs/superpowers/plans/2026-06-03-cvflow-build-plan.md`
- **Still-open decision:** LLM PII privacy (free tiers may train on submitted data) — resolve before sending real profile data to the cloud (Phase 4+).
- **Profile gaps the agent will ask about:** `essay_answers.md` (all blank), and empty keys in `form_fields.json` (DOB, work authorization, salary, relocation, notice period, postal code).
