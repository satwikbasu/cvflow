# cvflow — Session Handoff

Paste the prompt below into a fresh Claude Code session running **on the EC2 host** inside this repo
(`/home/ubuntu/cvflow`). This handoff resumes **Phase 9 (Browser automation)** mid-execution.

## Before you start
1. **Pull / verify HEAD.** Newest commit on `main` should be
   `feat(storage): additive submission-proof columns + set_proof` (`b1dbf05`). Run `git log --oneline -8`.
2. **Env:** `source .venv/bin/activate`. Sanity: `pytest -q` → **108 passing**; `ruff check .` and
   `mypy --strict src` → clean.
3. **Playwright browser:** Phase 9's browser tests need Chromium. Run `playwright install chromium`
   (and on a headless box the system libs: `playwright install-deps chromium` if available). The browser
   tests are written to **skip cleanly** if Chromium is absent, so the suite stays green either way — but
   you must install it to actually *prove* the form-filling tasks.
4. **Commits:** plain Conventional Commits, **NO `Co-Authored-By` trailer**. Commit straight to `main`
   (single-operator deploy-by-clone repo — no feature branches). Global git identity only.
5. **Spend limit note:** the previous session could not spawn implementer subagents (account hit a monthly
   spend limit) and executed tasks **inline** instead. If your subagent dispatch also fails with a spend-limit
   error, just execute inline — the plan is fully self-contained.

---

## Handoff prompt (paste this)

> I'm continuing **cvflow**, an autonomous self-hosted job-application agent, running ON its EC2 host at
> `/home/ubuntu/cvflow`. Read `CLAUDE.md` and `docs/superpowers/plans/2026-06-03-cvflow-build-plan.md` IN FULL
> first — they are the source of truth (architecture, invariants, phase roadmap). Honor the memory files under
> `~/.claude/projects/-home-ubuntu-cvflow/memory/` (especially `approval-gate-wiring`, `hermes-runtime-ops`,
> `essay-auto-answer-policy`, `resume-tailoring-rules`).
>
> **State:** Phases 0–8 done & pushed. **Phase 9 is IN PROGRESS.** HEAD = `b1dbf05`. 108 tests pass; ruff +
> mypy --strict clean.
>
> **THE APPROVAL GATE (locked, never weaken):** the ONLY producer of status `approved` is a human Telegram
> `/apply <job_id>` slash command, routed by a Hermes hook OUTSIDE the agent loop. The MCP surface exposes NO
> `approve` tool. `submit`/automation entrypoints assert `guard_can_submit` and raise `SubmissionBlocked`
> otherwise. See the `approval-gate-wiring` memory.
>
> **What I am building (Phase 9) and the design behind it — READ THESE TWO FIRST:**
> - Design spec: `docs/superpowers/specs/2026-06-04-phase-9-browser-automation-design.md`
> - TDD sub-plan (10 bite-sized tasks, full code in every step):
>   `docs/superpowers/plans/2026-06-04-phase-9-browser-automation.md`
>
> **Already completed this session (Tasks 1–3 of the sub-plan, committed):**
> - **Task 1 (`53c3b41`)** — `NimProvider` in `src/cvflow/llm/__init__.py`: OpenAI-compatible
>   `/chat/completions` client built from `config.llm.brain`, stdlib-urllib transport with an injectable
>   `post_fn` seam, per-minute budget guard `RpmExceeded` (mirrors `GeminiProvider`'s `RpdExceeded`). Exposes
>   `generate(prompt)->str`. Tests: `tests/test_llm_nim.py` (no network).
> - **Task 2 (`e9c7757`)** — wired the previously-`None` `discovery` + `analyzer` in `build_tools`
>   (`src/cvflow/mcp/tools.py`): `NimProvider` → `LLMRanker`/`DiscoveryService` and `JDAnalyzer`. This closes
>   the long-standing live gap where `discover`/`analyze_jd` errored with `'NoneType' has no attribute ...`.
>   Verified `build_tools(load_config('config.yaml'))` constructs both (no network) and still has no `approve`
>   tool. Added a test asserting `discover()` returns a non-empty `url` for every job (user requirement: always
>   show job URLs).
> - **Task 3 (`b1dbf05`)** — additive submission-proof columns on `ApplicationStore`
>   (`proof_url`, `proof_screenshot_path`, `proof_page_title`) + `set_proof(...)`. State-machine / gate core
>   untouched. Test in `tests/test_storage.py`.
>
> **YOUR JOB: execute Tasks 4→10 of the sub-plan, in order, test-first.** The sub-plan contains the exact
> failing test, the exact implementation, the run command + expected output, and the commit message for each
> step — follow it verbatim unless reality contradicts it (if so, prefer the surrounding code's actual style,
> as I did in Task 3 where the store uses `execute()+commit()`, not a `with self._conn:` block). After EACH
> task: run `pytest -q && ruff check . && mypy --strict src` (all green), self-review against the spec and the
> invariants, then commit. Keep the gate invariant true: NEVER add an `approve` tool/method;
> `hasattr(tools, "approve")` must stay False.
>
> **Remaining tasks (summary — full detail in the sub-plan):**
> - **Task 4 — Local fixture form.** Create `tests/fixtures/form/page1.html` + `page2.html` (two-page form with
>   text, file, select, checkbox, textarea, a required field, a Next link, and a Submit button that sets a
>   confirmation # + page title). No test of its own — used by Tasks 6–7. Commit.
> - **Task 5 — Pure deterministic-first field resolver.** Create `src/cvflow/automation/__init__.py` with
>   `FieldSpec`, `FieldFill`, `FieldResolution`, `NeedsClarification`, and `resolve_field(...)`. This is the
>   invariant-2 core (never guess): (1) `form_fields.json` exact/normalized lookup → literal value; (2)
>   `textarea` → grounded `essays.compose_answer`; (3) required+unresolved → clarify, optional+unresolved →
>   skip. Fully unit-tested with NO browser (`tests/test_automation_resolve.py`).
> - **Task 6 — `SessionManager` + `FormFiller`.** Append to `automation/__init__.py`. `SessionManager` =
>   Option-C hybrid: `launch_persistent_context(user_data_dir=<storage_state_dir>/<job_id>)` (login/cookies
>   survive restarts) held in-process keyed by `job_id` (live page survives pause→resume). `FormFiller` reads
>   the form (`discover_fields`) and fills each type (`apply`). Real-Playwright tests in
>   `tests/test_automation_browser.py` that **skip if Chromium absent** (`pytest.importorskip` + try/except).
> - **Task 7 — Proof capture.** Add `Proof` dataclass + `FormFiller.capture_proof(path)` (url / title /
>   screenshot / confirmation #). Browser test.
> - **Task 8 — Gated `Automator` orchestration.** Append to `automation/__init__.py`. `fill(job_id)` asserts
>   `guard_can_submit` FIRST, opens the session, resolves+fills each field, **pauses** on a required unknown
>   (returns `needs_clarification`), discloses every `composed` answer to the user via the injected `notify`
>   BEFORE the final submit, submits, captures+persists proof, sets status `applied`. `resume(job_id, answer)`
>   continues on the same live page. **Any crash → status `failed` + `notify` naming the job and its URL**
>   (user requirements: disclose composed answers; alert with URL on crash). Tested with a FAKE page/filler
>   (no browser) in `tests/test_automation_orchestration.py` — verifies gate, pause/resume, disclosure,
>   crash-notify. (Verified legal transitions: `approved→applied` and `approved→failed` are both allowed.)
> - **Task 9 — MCP `fill_application` / `resume_application`.** Add both to `TOOL_NAMES` and `CvflowTools`
>   (delegating to an injected `automator`); wire a real `Automator` (with `SessionManager`, `FormFiller`, and a
>   `_telegram_notify` that logs) in `build_tools`. STILL no `approve` tool. Test in `tests/test_mcp_tools.py`.
> - **Task 10 — Full verification + build-plan sync.** `pytest -q && ruff check . && mypy --strict src` green;
>   with Chromium installed run `tests/test_automation_browser.py` and confirm the browser tests actually PASS
>   (not skip). Tick Phase 9 in the build plan and add a dated progress-log entry. Commit.
>
> **Deviation already decided & approved by the user (do NOT revert):** we use our OWN deterministic Playwright
> skill and **leave Hermes's native `browser`/`computer_use` toolsets DISABLED** (contrary to the original
> Phase-9 task note) — so the agent loop can't drive a browser around the gate, and to spare NIM
> context/latency. Do not re-enable them in `agent.disabled_toolsets`.
>
> **Standing rules:** TDD; deterministic gate; never fabricate user facts (clarify, don't guess); never fail
> silently (log + Telegram); respect free-tier limits; zero per-call/SaaS cost (no new paid deps — automation
> uses Playwright which is already a dependency; `NimProvider` is stdlib-only); secrets never in git, profile
> PII intentionally committed. Conventional Commits, NO `Co-Authored-By`. Commit straight to `main`.
>
> **After Phase 9 is complete:** use `superpowers:finishing-a-development-branch`, then **update this
> `docs/HANDOFF.md` and `git push`.** Next phases after 9 are: Phase 10 (Auth — Google OAuth + email-OTP),
> Phase 11 (scheduling + systemd deploy), Phase 12 (end-to-end dry run).

---

## Quick orientation for the new session
- **What/why/architecture + invariants:** `CLAUDE.md`
- **Roadmap + locked decisions + progress log:** `docs/superpowers/plans/2026-06-03-cvflow-build-plan.md`
- **Phase 9 design + sub-plan (READ BOTH):**
  `docs/superpowers/specs/2026-06-04-phase-9-browser-automation-design.md` and
  `docs/superpowers/plans/2026-06-04-phase-9-browser-automation.md`
- **Memory files** (`~/.claude/projects/-home-ubuntu-cvflow/memory/`): `approval-gate-wiring` (the gate +
  the now-CLOSED discovery/analyzer gap), `hermes-runtime-ops` (gateway/MCP/systemd, NIM latency,
  disabled_toolsets — note the Phase-9 deviation above), `essay-auto-answer-policy`, `resume-tailoring-rules`,
  `salary-hike-filter`, `pii-free-tier-consent`, `git-use-global-identity`.
- **PII privacy:** RESOLVED — burner-data consent; do NOT re-flag free-tier LLM PII egress as a blocker.
- **Gate self-check before you commit anything in Phase 9:** there is no `approve` tool; every automation
  entrypoint calls `guard_can_submit` first; crash paths notify with the job URL and set status `failed`.

> **Note:** Claude's file-based memory is per-machine. This `HANDOFF.md`, the build plan, the Phase-9
> design/sub-plan, and git history are the durable record — keep decisions there.
