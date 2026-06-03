# cvflow — Session Handoff

Paste the prompt below into a fresh Claude Code session started inside this cloned repo.

## Before you start (human, on the OTHER machine)
1. **⚠️ Pull first — this machine is behind.** The last session (on the other system) added Phases 0–4
   plus docs and pushed them. Run `git pull` before anything so you have all commits, the updated build
   plan, the new sub-plans, and this handoff. Verify with `git log --oneline -8` (newest should be the
   `docs:` hosting/Tectonic commits, then `feat(discovery)…`).
2. **Secrets** (kept out of git on purpose): ensure `config.yaml` exists here — copy from
   `config.example.yaml` and fill the four secret values: `telegram.bot_token`,
   `telegram.authorized_user_id`, `llm.brain.api_key` (NVIDIA NIM `nvapi-...`),
   `llm.tailoring.api_key` (Gemini `AIza...`). All non-secret values in the example are already correct.
   (Your keys are in the earlier exported transcript on the original machine.) `chmod 600 config.yaml`.
3. **Python env:** `python3.11 -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]"`
   (pyproject now drives deps/tooling). Sanity check: `pytest -q` → **52 passing**; `ruff check .` and
   `mypy src` → clean.
4. **Git push access:** pushes via SSH alias `github-personal` (key `~/.ssh/id_ed25519_satwikbasu`). On a new
   machine, set up your GitHub SSH key and recreate that alias in `~/.ssh/config`, or
   `git remote set-url origin git@github.com:satwikbasu/cvflow.git`. Use your **global** git identity
   (never repo-local). **Commit messages: plain Conventional Commits, NO `Co-Authored-By` trailer**
   (match existing history).

---

## Handoff prompt (paste this)

> I'm continuing work on **cvflow**, an autonomous self-hosted job-application agent. Read `CLAUDE.md` and
> `docs/superpowers/plans/2026-06-03-cvflow-build-plan.md` in full before doing anything — they are the source
> of truth for architecture, invariants, and the phase roadmap. The per-phase TDD sub-plans live alongside it
> in `docs/superpowers/plans/`.
>
> **Completed and committed (Phases 0–4):**
> - **Phase 0 — Foundations:** `pyproject.toml` (pinned deps + pytest/ruff/mypy-strict), `src/cvflow/config.py`
>   (typed/validated frozen `Config`, `ConfigError`, api-key stripping, HH:MM validation), `logging_setup.py`
>   (rotating, idempotent).
> - **Phase 1 — Storage + State Machine (safety-critical core):** `statemachine/` — deterministic gate:
>   `transition()` refuses `approved`, `approve()` is the sole producer (only from `pending_review`),
>   `guard_can_submit()` blocks every non-approved status. `storage/` — `ApplicationStore` (SQLite, dedup on
>   stable `job_id`), `FormFields` never-guess loader.
> - **Phase 2 — LLM (tailoring):** `llm/` `GeminiProvider` — RPD tracking w/ day-rollover, `RpdExceeded`,
>   in-memory response cache (cache hits don't spend budget). Brain (NIM) stays in Hermes (Phase 7).
> - **Phase 3 — KB loader:** `knowledge/` `KnowledgeBase` — full-context profile ingestion (excludes
>   `*.example.md`/README), `missing_form_fields()`.
> - **Phase 4 — Discovery (Goal 1):** `discovery/` — `normalize_rows()` (stable `site:id` job_id), `LLMRanker`
>   (reorders only real postings, drops LLM-fabricated ids), `DiscoveryService.discover()` (cross-day dedup via
>   the store, throttle, persist presented jobs as `discovered`). **Job source is behind an injectable
>   `search_fn` seam; JobSpy is the zero-cost default** (fantastic.jobs/SerpApi rejected as primary — hosted-SaaS/
>   metered or free tier too small; the seam lets one slot in later).
>
> **Health:** `pytest` → 52 passing; `ruff` + `mypy --strict` clean. Each phase has a TDD sub-plan; the master
> plan's checkboxes + progress log are in sync.
>
> **Decisions locked:** substrate = **Hermes Agent**; brain = `meta/llama-3.3-70b-instruct` (NIM free tier);
> tailoring = **Gemini 2.5 Flash**. Approval gate = deterministic code, never the agent loop.
> **Hosting:** dev stays local through ~Phase 10; host at Phase 7/11 — AWS `t3.small` as a 6-month credit
> stepping stone, then migrate to Oracle Always Free; **never allocate an Elastic IP** (see
> `docs/hosting-aws-ec2.md`). **LaTeX:** use **Tectonic** (tiny, local, private), not `texlive-full` or a hosted
> LaTeX API — set `resume.latex_compiler: "tectonic"` when Phase 6 wires the compile call.
>
> **Next task: execute Phase 5 (JD Analysis, Goal 2).** First write the bite-sized TDD sub-plan at
> `docs/superpowers/plans/<today>-phase-5-jd-analysis.md` (use the `superpowers:writing-plans` skill), then
> implement it test-first in `src/cvflow/analysis/`: fetch the full JD, LLM-extract required skills, preferred
> quals, seniority, tone/culture, and **applicant-specific instructions** (e.g. "include the word pineapple");
> persist the analysis linked to the application record. Keep the LLM call behind an injectable provider so
> tests stay fully mocked (no live API). Exit: tests on sample JDs extract the structured fields and capture an
> embedded applicant instruction.
>
> Follow the standing rules (TDD; deterministic gate; never fabricate user facts; never fail silently; respect
> free-tier limits; secrets never in git; profile PII intentionally committed). **Commit with Conventional
> Commits and NO Co-Authored-By trailer.** Continue into Phase 6 (Resume Tailoring) if time permits, then
> **update this `docs/HANDOFF.md` and push to remote at the end of the session.**

---

## Quick orientation for the new session
- **What/why/architecture:** `CLAUDE.md`
- **Roadmap + locked decisions + open risks:** `docs/superpowers/plans/2026-06-03-cvflow-build-plan.md`
- **Per-phase TDD sub-plans:** `docs/superpowers/plans/2026-06-03-phase-{0,1,2,3,4}-*.md`
- **Hosting/deploy strategy + EC2 runbook + Tectonic:** `docs/hosting-aws-ec2.md`
- **OPEN RISK — LLM PII privacy:** ranking (Phase 4) and analysis/tailoring (5/6) send profile PII to the
  free-tier LLMs, which may train on it. Tests are mocked; resolve consent/redaction before any **live** run.
- **Profile gaps the agent will ask about:** `essay_answers.md` (blank), and empty keys in `form_fields.json`
  (DOB, work authorization, salary, relocation, notice period, postal code).

> **Note:** Claude's file-based memory is per-machine and does NOT sync between the two systems. This
> `HANDOFF.md`, the build plan, and git history are the only cross-machine link — keep decisions there.
