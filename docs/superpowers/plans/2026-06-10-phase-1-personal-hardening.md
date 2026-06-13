# Phase 1 — Personal hardening + multi-tenant seams (Master Plan)

> **For agentic workers (Opus):** This is a **master roadmap** — source of truth for order, scope,
> and exit criteria of Phase 1. Before executing each workstream, write a bite-sized TDD sub-plan
> with `superpowers:writing-plans` (save as `docs/superpowers/plans/YYYY-MM-DD-p1<letter>-<name>.md`),
> then execute with `superpowers:subagent-driven-development` or `superpowers:executing-plans`.
> Keep the checkboxes here and the progress log at the bottom in sync.
>
> **Authority:** this plan + `2026-06-10-phase-2-hosted-service.md` implement
> `docs/architecture-review-fable.md` and **supersede Phase 12** of
> `2026-06-03-cvflow-build-plan.md` (workstream 1E below replaces it).

**Goal:** Harden cvflow for daily personal use (fix the broken cron, tame the verbose digest, ship
the apply kit) while landing every multi-tenant seam — schema, paths, rate budget, notifier — so
Phase 2 (hosted, ~100 users) is a migration, not a rewrite. Decide automation's fate with data.

**Architecture:** No substrate change — Hermes stays exactly as deployed (`docs/deploy.md`).
Every change is either inside the cvflow core lib or in `artifacts/hermes/` scripts. Zero behavior
change for workstream 1C (seams) is a hard requirement: all existing tests must pass unmodified.

**Tech stack:** unchanged (Python 3.11, Hermes, NIM/Mistral/Cerebras via `NimProvider`, SQLite,
JobSpy + nkparam, Tectonic).

**Current state this plan picks up from (verified 2026-06-10):**
- 251 tests green; Phases 0–11, 13, 14 done; Phase 12 open (superseded here).
- Known cron problems (`docs/architecture-and-workflow.md` §7): Hermes kills scripts at ~120 s so
  the daily `cvflow-discover` dies every day; the schedule lives only in `~/.hermes/state.db`
  (not reproducible from clone); `cvflow-learn` is unregistered; `config.yaml` schedule fields are
  decorative.
- `/discover` works (hook + detached run + fcntl lock) but floods chat with the full digest + a
  multi-message drop report.
- `automation/` + `auth/` exist, fixture-tested, never run against a real portal.
- Single-tenant schema: `applications`/`jd_analyses` PK = `job_id`; `digest_slots` PK = `slot`;
  no `user_id` anywhere; profile paths hardcoded via config.

---

## Standing rules (from CLAUDE.md — apply to every workstream)

1. TDD: failing test → watch fail → minimal code → pass → commit (Conventional Commits).
2. The approval gate stays deterministic and human-only. **No workstream may add an `approve`
   tool or weaken `statemachine`.**
3. Never fabricate user facts; never fail silently (every new failure path logs *and* notifies).
4. New `config.yaml` keys must be **optional with defaults** — the live box's config must keep
   loading without edits (config.py fails loudly on missing keys, so additions must not become
   required). Update `config.example.yaml` + docs for every new key.
5. Surgical changes; no speculative P2 features (P2 builds on the seams, it is not built now).

## Workstream order & dependencies

```
1A cron reproducibility + 120s fix      (ops pain — do first)
1B /discover + daily digest summary     (depends on nothing; quick UX win)
1C multi-tenant seams                   (schema rebuild first, then paths/limiter/notifier)
1D apply kit + mark_applied             (depends on 1C only trivially; can run parallel to 1C)
1E ATS recon (start its data collection right after 1A; decide at the end of P1)
1F cleanup + docs
```

---

### [x] 1A — Cron reproducibility + the 120 s fix

> **DONE 2026-06-11** (sub-plan `2026-06-10-p1a-cron-fix.md`). `cvflow-discover.sh` is now a
> sub-second detached spawner (`nohup setsid … &`); `cron.py` gained `to_utc_cron` +
> `format_hermes_schedule` + a `print-hermes-schedule` subcommand (config-derived, UTC:
> `12:00 Asia/Kolkata` → `30 6 * * *`); committed idempotent `scripts/install-hermes-cron.sh`
> copies the scripts, clears `cvflow-*` jobs, re-creates all four (registers the missing
> `cvflow-learn`); `docs/deploy.md` uses the installer. Commits `4ad62b3`/`7b9fcaa`/`5cb1c84`/
> `9620792`. Tests `tests/test_cron_schedule.py` (6, lock-independent).
> **DEPLOYED to the live box 2026-06-11:** ran `install-hermes-cron.sh` (4 jobs now registered with
> config-derived UTC schedule — discover `30 6 * * *`, heartbeat `720m`, **`cvflow-learn` finally
> registered**), copied the updated `/discover` hook, restarted the gateway.
> **Extra hardening (`410d97e`):** the detached run now launches in a **transient user systemd unit**
> (`systemd-run --user --collect`) so it lives under `user@.service`'s cgroup, not the gateway's —
> a `systemctl restart hermes-gateway` (KillMode=mixed) had SIGKILLed a 15-min run mid-flight on
> 2026-06-11. Survives both the 120 s kill and a gateway restart; falls back to `setsid` if the user
> manager is unreachable. Requires `loginctl enable-linger` (done on the box; documented in
> deploy.md). Both the daily script and the `/discover` hook were hardened. Verified live: the daily
> script exits <1 s and the run shows up under `user@1000.service` scraping normally.
> **Log convention locked down:** every run's **live** raw log is a per-run file in `data/`
> (`discover-manual-<ts>.log` for `/discover`, `discover-cron-<ts>.log` for cron — the earlier
> `logs/cron-discover.log` single-file choice was wrong and reverted); the **finished** digest is
> archived to `logs/discover/<ts>.md` on completion. Documented in `data/README.md`,
> `logs/README.md`, `deploy.md`, and `architecture-and-workflow.md`.

**Problem being fixed:** Hermes cron kills any `--no-agent` script at ~120 s; a discovery run takes
10–25 min, so the daily digest has been dying with `error: Script timed out after 120s`. And the
whole schedule exists only in `~/.hermes/state.db`, so a fresh clone has no jobs.

**Files:**
- Modify: `artifacts/hermes/scripts/cvflow-discover.sh` — become a detached spawner.
- Create: `scripts/install-hermes-cron.sh` (committed, idempotent installer).
- Modify: `src/cvflow/cron.py` — add a `print-hermes-schedule` subcommand.
- Modify: `docs/deploy.md` — replace the four manual `hermes cron create` lines with the installer.
- Tests: `tests/test_cron_schedule.py` (new).

**Design:**
1. **Detached daily run.** `cvflow-discover.sh` no longer runs discovery inline; it spawns it
   detached and exits in <1 s (the same pattern `/discover`'s `discover_command.py` already uses —
   reuse its lessons: redirect stdio, close the parent log fd, `setsid`):
   ```bash
   #!/usr/bin/env bash
   cd "${CVFLOW_ROOT:-$HOME/cvflow}" || exit 1
   mkdir -p logs
   nohup setsid .venv/bin/python -m cvflow.cron discover \
       >> logs/cron-discover.log 2>&1 < /dev/null &
   ```
   The existing `discovery_lock` (`runlock.py`) already guarantees the daily spawn and a manual
   `/discover` can't double-run; the digest reaches Telegram from inside the detached process via
   the notifier (which needs no gateway). The 120 s kill now only ever sees a sub-second script.
2. **Schedule derived from config.** New subcommand:
   `python -m cvflow.cron print-hermes-schedule` prints exactly the four registration commands,
   computing the discover cron expression from `schedule.daily_discovery_time` + `schedule.timezone`
   (zoneinfo; convert today's HH:MM in the configured tz to UTC `M H * * *`) and the heartbeat
   interval from `schedule.heartbeat_interval_minutes`:
   ```
   hermes cron create '30 2 * * *' --no-agent --script cvflow-discover.sh  --name cvflow-discover
   hermes cron create 'every 5m'   --no-agent --script cvflow-sweep-otp.sh --name cvflow-sweep-otp
   hermes cron create 'every 360m' --no-agent --script cvflow-heartbeat.sh --name cvflow-heartbeat
   hermes cron create 'every 168h' --no-agent --script cvflow-learn.sh     --name cvflow-learn
   ```
   This makes the config fields authoritative (fixes "decorative schedule" divergence) and is
   pure-Python testable (inject a fake config; assert the emitted lines).
3. **Idempotent installer.** `scripts/install-hermes-cron.sh`: copies
   `artifacts/hermes/scripts/cvflow-*.sh` to `~/.hermes/scripts/` (chmod +x), deletes any existing
   `cvflow-*` cron entries (`hermes cron list` → `hermes cron delete <name>`), then evals the
   output of `print-hermes-schedule`. Running it twice yields the same four jobs.
4. Register the missing `cvflow-learn` on the live box by running the installer.

**Exit criteria:**
- `pytest tests/test_cron_schedule.py` proves: correct UTC conversion (e.g. 08:00 Asia/Kolkata →
  `30 2 * * *`), heartbeat minutes from config, all four lines emitted.
- On the live box: `hermes cron list` shows all 4 jobs; the next daily discover completes (digest
  arrives) with no 120 s error in Hermes logs; re-running the installer is a no-op-equivalent.
- A fresh-clone deploy needs exactly: clone → venv → config → `scripts/install-hermes-cron.sh`
  (deploy.md updated to say so).

### [x] 1B — Digest summarization (chat gets a friendly summary; the log keeps everything)

> **DONE 2026-06-10** (sub-plan `2026-06-10-p1b-digest-summary.md`). Refined from the original
> design: the **digest is unchanged**; what gets LLM-summarized is the **drop report** (compressed
> first, then Cerebras), and **only on manual `/discover`** — the daily cron stays digest-only. Full
> digest + per-job drops always retained to `logs/discover/<ts>.md`. All knobs in `discovery.*`
> config. Commits `a8ea8a5`/`fd1c407`/`899a51c`/`f1b381d`.

**Files:**
- Modify: `src/cvflow/cron.py` — `run_job("discover")` writes the log file and sends the summary;
  new functions `write_discover_log(...)` and `summarize_digest(...)`.
- Modify: `src/cvflow/cron.py::main` / `_build` — pass the brain provider into `run_job` for the
  summary call (it already passes `learn_provider=brain`; reuse that provider).
- Tests: extend `tests/test_cron*.py`.

**Design:**
1. **Retention first, LLM second.** Before any LLM call, write the full
   `format_digest(result)` output plus all `format_drop_report(...)` chunks to
   `logs/discover/<UTC ISO timestamp>.md` (new param `digest_log_dir: str = "logs/discover"`).
2. **One NIM call.** `summarize_digest(digest_text: str, dropped: dict[str, int] | None,
   provider) -> str | None`:
   - Prompt rules: warm and friendly; ≤10 lines / ≤900 chars; report counts per cohort; show the
     top 3 jobs as `n. role @ company — pay — fit`; one-line note on what was filtered; end with
     “Reply /apply n or /skip n.”; **use the digest's exact numbering verbatim; never invent or
     renumber jobs**.
   - `provider.generate(prompt, temperature=0.3, max_tokens=350)`. Catch `LLMError`/`RpmExceeded`
     → return `None`.
3. **Wiring in `run_job`:** summary is sent when available, suffixed with
   `\n📄 Full report: logs/discover/<file>`; on `None`, send the full digest exactly as today
   (never silent). The drop report goes **only** to the log file now — both for the daily run and
   the manual `/discover` (`report_drops` keeps controlling whether drop *records* are collected,
   but chat delivery of chunks is removed). Manual-run per-stage progress streaming is unchanged.
4. `digest_slots` behavior unchanged — slots are set from the full result regardless of what chat
   shows, so `/apply 7` still works even if the summary only showed the top 3.

**Exit criteria:** tests prove (a) log file written even when the provider raises; (b) summary sent
when the provider succeeds, full digest sent when it fails; (c) drop chunks no longer go to chat;
(d) slots cover the full presented list. Live: next digest arrives as a short summary; the log file
exists and contains everything.

### [ ] 1C — Multi-tenant seams (zero behavior change; all 251+ tests pass unmodified)

This is the workstream that makes Phase 2 cheap. Four independent seams; land in this order.

#### 1C-1 Schema rebuild: `user_id` everywhere it belongs

**Files:** `src/cvflow/storage/__init__.py`; tests `tests/test_storage*.py` (additions only).

- Extend `_migrate()` with an idempotent **table rebuild** (SQLite can't alter PKs): when
  `applications` lacks a `user_id` column —
  ```sql
  CREATE TABLE applications_new (
      user_id TEXT NOT NULL DEFAULT 'owner',
      job_id  TEXT NOT NULL,
      -- …all existing columns verbatim…
      PRIMARY KEY (user_id, job_id)
  );
  INSERT INTO applications_new (user_id, job_id, company, role, jd_url, status,
      discovered_at, applied_at, tailored_pdf_path, confirmation_ref, proof_url,
      proof_screenshot_path, proof_page_title, otp_deadline)
      SELECT 'owner', job_id, company, role, jd_url, status, discovered_at, applied_at,
             tailored_pdf_path, confirmation_ref, proof_url, proof_screenshot_path,
             proof_page_title, otp_deadline FROM applications;
  DROP TABLE applications; ALTER TABLE applications_new RENAME TO applications;
  ```
  Same treatment: `jd_analyses` → PK `(user_id, job_id)`; `digest_slots` → PK `(user_id, slot)`;
  `decisions` → plain `user_id TEXT NOT NULL DEFAULT 'owner'` column (keep AUTOINCREMENT id).
  **`job_cruxes` is untouched** — deliberately global (per `architecture-review-fable.md` §3.3).
  Update `_SCHEMA` so fresh DBs are created in the new shape directly.
- Every `ApplicationStore` method gains a keyword `user_id: str = "owner"` and scopes its
  SQL by it (`WHERE user_id = ? AND job_id = ?` etc.). `Application` dataclass gains a
  `user_id: str = "owner"` field. Existing callers don't change (default applies).
- Enable WAL + busy_timeout at connect (`PRAGMA journal_mode=WAL`,
  `PRAGMA busy_timeout=5000`) — multiple processes already share this DB today.
- **New isolation test:** two users add the same `job_id`; approving for user A leaves user B
  `discovered`; `digest_slots`/`list_by_status`/`recent_decisions` never cross users.
- **Migration test:** open a pre-rebuild fixture DB (created with the old `_SCHEMA`), assert data
  survives under `user_id='owner'` and a second `_migrate()` is a no-op. Back up the live
  `data/cvflow.db` before first deploy of this change.

#### 1C-2 `UserPaths` resolver

**Files:** create `src/cvflow/userpaths.py`; modify `cron._build` + `mcp/tools.py::build_tools`;
tests `tests/test_userpaths.py`.

```python
@dataclass(frozen=True)
class UserPaths:
    user_id: str
    knowledge_base_dir: Path   # profile/*.md + candidate_skills.yaml live here
    form_fields_path: Path
    master_tex_path: Path
    output_dir: Path           # tailored PDFs

def resolve_user_paths(user_id: str, config: Config, users_root: Path = Path("data/users")) -> UserPaths: ...
```
- `"owner"` → exactly the paths config names today (zero behavior change).
- Any other id → `data/users/<id>/profile/`, `…/profile/form_fields.json`,
  `…/resume/master.tex`, `…/tailored/` (created on demand by callers, not here).
- `_build` and `build_tools` construct knowledge/tailor/output_dir through
  `resolve_user_paths("owner", config)` instead of reading config fields directly.

#### 1C-3 Cross-process shared LLM budget

**Why now:** the MCP server, the cron job, and a detached `/discover` run are **three separate
processes** sharing the same vendor keys; each has its own in-process RPM window in `NimProvider`,
so collectively they can exceed a vendor's limit today. This is a real P1 bug and the P2
pooled-budget enforcement point.

**Files:** create `src/cvflow/llm/budget.py`; modify `src/cvflow/llm/__init__.py` (NimProvider),
`cron._build`, `build_tools`, `src/cvflow/config.py` (+ `config.example.yaml`);
tests `tests/test_llm_budget.py`.

- `SharedRateLimiter(db_path: str | Path, vendor: str, max_rpm: int)` — own sqlite conn
  (WAL, busy_timeout), table `budget(vendor TEXT, window_start INTEGER, count INTEGER,
  PRIMARY KEY (vendor, window_start))`; `acquire()` does an atomic
  upsert-and-check inside one transaction and raises the existing `RpmExceeded` when the current
  60 s window is full. Old windows are deleted opportunistically.
- `NimProvider.__init__` gains `limiter: SharedRateLimiter | None = None`; `_spend_one` delegates
  to the limiter when present, else keeps the current in-process window (tests unaffected).
- Optional config key `storage.llm_budget_db_path` (default `"data/llm_budget.db"`); `_provider()`
  helpers in `cron._build`/`build_tools` construct one limiter per vendor (keyed by
  `cfg.provider`) and pass it in.
- Test: two limiter instances on the same file (simulating two processes) — combined acquires
  beyond `max_rpm` raise in the second instance.

#### 1C-4 Notifier backend flag

**Files:** modify `src/cvflow/notify.py`, `src/cvflow/config.py` (+ example); tests extend
`tests/test_notify*.py`.

- `TelegramNotifier(bot_token: str, chat_id: int | str, post_fn=_urllib_post_form)` — POST to
  `https://api.telegram.org/bot<token>/sendMessage` (`chat_id`, `text`; chunk text at 4000 chars).
  **Never raises** (mirror `HermesNotifier`'s log-and-continue contract).
- `make_notifier(config) -> Callable[[str], None]` reading optional key
  `notify.backend: "hermes" | "telegram"` (default `"hermes"`); telegram backend uses
  `config.telegram.bot_token` + `config.telegram.authorized_user_id`.
- `_build`/`build_tools` call `make_notifier(config)` instead of `HermesNotifier()` directly.
- P1 stays on `hermes`; flipping to `telegram` on the live box once is a worthwhile smoke test,
  then flip back (or keep — both must work).

**1C exit criteria:** full suite green with no edits to pre-existing tests; the new isolation,
migration, budget-contention, and notifier tests pass; live box runs one full daily cycle after
deploying the schema rebuild (with a `data/cvflow.db` backup taken first).

### [ ] 1D — Apply kit + `mark_applied` (the universal apply path)

**What it is:** for any job with an analysis + tailored résumé, produce everything needed to apply
manually in two minutes: the PDF, grounded answers to the JD's likely questions, the JD's
applicant instructions, and the link. This is the product's answer on every portal automation
can't (or shouldn't) drive.

**Files:**
- Create: `src/cvflow/applykit.py`.
- Modify: `src/cvflow/mcp/tools.py` (two new tools → `TOOL_NAMES` becomes 14),
  `src/cvflow/mcp/server.py` (register), `artifacts/hermes/hermes-config.yaml` reference copy +
  live `~/.hermes/config.yaml` `tools.include` (+ `docs/deploy.md` allowlist text).
- Tests: `tests/test_applykit.py`, extend `tests/test_mcp_tools.py`.

**Design:**
1. ```python
   @dataclass(frozen=True)
   class ApplyKit:
       job_id: str
       jd_url: str
       pdf_path: str
       answers: list[tuple[str, str]]      # (question, grounded answer)
       unanswered: list[str]               # needs the user — never guessed
       applicant_instructions: str | None  # from JDAnalysis (e.g. "mention pineapple")

   def build_apply_kit(job_id, *, store, knowledge, essay_provider, user_id="owner") -> ApplyKit
   def format_apply_kit(kit: ApplyKit) -> str   # one Telegram message, ≤4000 chars
   ```
   Question sources, in order: `JDAnalysis.applicant_instructions` (verbatim, highlighted);
   common-field block from `FormFields.populated` (name/phone/notice period/CTC…, rendered as a
   copy-paste block); free-text questions found in the analysis answered via
   `essays.compose_answer` — any `needs_clarification` answer goes to `unanswered`, never guessed
   (invariant 2). Requires `tailored_pdf_path` set (i.e., after `request_review`); raise a clear
   error naming the missing prerequisite otherwise.
2. **MCP tool `apply_kit(job_id)`** → returns the kit dict + the formatted message (the brain
   relays it; users ask "give me the kit for 2" or it's offered after `/apply`).
3. **MCP tool `mark_applied(job_id, confirmation_ref: str | None = None)`** → requires current
   status `approved` (use `store.set_status(job_id, Status.APPLIED)`, which the transition table
   already permits only from `approved` — verify and test, don't bypass); stores the optional
   confirmation ref. This closes the tracking loop for manual applications: digest → `/apply n`
   (commitment) → kit → user applies → "mark 2 applied" → status `applied`.
4. **Gate untouched:** neither tool can approve; `mark_applied` only works *after* the human
   `/apply`. State machine unchanged.
5. Update the `/apply` hook's success reply text to mention the kit ("ask me for the apply kit").

**Exit criteria:** tests prove kit grounding (a question answerable from the profile is answered
with citations; an unanswerable one lands in `unanswered`), prerequisite errors, formatting under
4000 chars, `mark_applied` blocked from non-approved states; live: full cycle digest → review →
`/apply` → kit in chat → `mark_applied` reflected in `status_report`.

### [ ] 1E — ATS recon → the automation go/no-go (supersedes Phase 12)

**Decision rule (from `architecture-review-fable.md` §3bis):** collect ≥2–3 weeks of real digests;
if **Greenhouse + Lever ≥ ~20% of *approved* jobs**, execute branch GO; else branch NO-GO. Never:
LinkedIn Easy Apply, CAPTCHA solving, credentialed portals (Workday/Naukri login), hosted
submission for anyone but the founder.

**1E-1 Instrumentation (build immediately; zero browser):**
> **DONE 2026-06-13** (sub-plan `2026-06-11-p1e1-ats-recon.md`). `src/cvflow/recon.py` ships
> `classify_ats(url, fetch_redirect=False)` (host-pattern match over GH/Lever/Ashby/Workday/Taleo/
> SuccessFactors/iCIMS/Naukri/LinkedIn/Indeed → else `other`) + a never-raising one-hop
> `_resolve_one_hop` (stdlib urllib HEAD, 10 s, failures fall back to the original URL); storage
> gained a nullable `ats` column (additive `_migrate`, fresh `_SCHEMA`) + `set_ats`. Discovery's
> persist stage tags each newly-added job (no network); `analyze_jd` upgrades it with
> `fetch_redirect=True`. `python -m cvflow.recon report` prints per-ATS presented/approved counts +
> the GH+Lever approved share. Commits `cbac315`/`075ef73`/`96df990`/`939dcd4`/`851f0fe`. The 2–3
> week data clock starts now; 1E-2 (the go/no-go decision) waits on the window. deploy.md documents
> the report command.
- Create `src/cvflow/recon.py`:
  `classify_ats(url: str, fetch_redirect: bool = False) -> str` returning one of
  `greenhouse | lever | ashby | workday | taleo | successfactors | icims | naukri | linkedin |
  indeed | other` by domain/path patterns (`boards.greenhouse.io`, `job-boards.greenhouse.io`,
  `jobs.lever.co`, `jobs.ashbyhq.com`, `myworkdayjobs.com`, …); with `fetch_redirect=True`,
  resolve one hop of redirects via stdlib urllib (timeout 10 s, failures → classify the original).
- `storage`: `_migrate()` adds nullable `ats TEXT` to `applications`; `set_ats(job_id, ats,
  user_id="owner")`. Discovery persist stage classifies (no network) each presented job; the
  `analyze_jd` path upgrades the classification with `fetch_redirect=True` since it fetches anyway.
- `python -m cvflow.recon report` → table: per ATS, count + % of (a) presented, (b) approved jobs,
  reading the `decisions` + `applications` tables. Document running it in deploy.md.

**Instrument-accuracy caveat (raised 2026-06-13 — must inform 1E-2):** `classify_ats` is
**URL-only**. Many "company custom" career pages are really Greenhouse/Lever underneath, but they
load the ATS form via a JS apply-handoff or an iframe *without* an HTTP redirect, so the one-hop
resolve can't see it; aggregator (Indeed/Naukri) listing URLs hide the real ATS the same way. The
result: **GH+Lever is under-counted — the reported share is a floor.** A one-shot health-check
(`python -m cvflow.recon healthcheck`, scheduled via user crontab for 2026-06-20) flags whether new
jobs are being classified at all. **Before reading 1E-2 as NO-GO**, confirm the share isn't an
artifact of URL-only blindness; if it is, upgrade to DOM-level classification (open the apply page
with the existing Playwright runtime) before deciding.

**1E-2 The decision (after the data window):** run the report; record the numbers and the chosen
branch in this plan's progress log. Then execute exactly one branch:

- **Branch GO (GH+Lever ≥ 20% of approved):** write a dedicated TDD sub-plan scoped to:
  field-mapping + upload + submit for Greenhouse and Lever portals only; `guard_can_submit`
  asserted at entry (existing); every composed answer disclosed in chat before submit (existing
  Automator contract); proof captured; CAPTCHA detected → clean pause + apply-kit handoff; any
  other ATS → apply-kit handoff without launching a browser (use the `ats` column to route).
  Success bar: one real Greenhouse and one real Lever submission on the founder's burner account
  with proof, plus a demonstrated pre-approval block.
- **Branch NO-GO (< 20%):** `git mv src/cvflow/automation src/cvflow/auth archive/` (history
  preserved) with a README stating why + the §3bis client-side-extension future path; remove
  `fill_application`, `resume_application`, `submit_otp` from `TOOL_NAMES`/server/allowlist;
  remove the `sweep-otp` cron job from the installer + live box; delete their tests; keep
  `TokenVault` only if nothing else uses it (it returns at P2 for BYOK — note that in the README).
  The statemachine keeps `otp_timeout`/`failed` statuses (harmless, historical rows may exist).

**Exit criteria:** instrumentation tested (URL fixtures per ATS; report math); decision recorded
with numbers; chosen branch fully executed with green suite.

### [ ] 1F — Cleanup + docs

- Delete `GeminiProvider` from `src/cvflow/llm/__init__.py` (dead since the Cerebras migration)
  and its tests; `grep -r Gemini` to catch stale README/CLAUDE.md/build-plan mentions and fix the
  "tailoring escalates to Gemini" line wherever it survives.
- Remove the unused `brain` param from `DiscoveryService` **only if** the diff stays trivial;
  otherwise leave and note it (surgical-changes rule).
- Update: `README.md` status, `CLAUDE.md` (LLM table: Cerebras not Gemini; Phase-12 superseded
  note), `docs/architecture-and-workflow.md` §7 (cron fixed) + §13, `docs/deploy.md` (installer,
  new tools, notifier flag), `2026-06-03-cvflow-build-plan.md` (mark Phase 12 “superseded by
  2026-06-10 P1 plan §1E”, link here).
- Memory hygiene: this plan's completion entries go in the progress log below.

**Exit criteria:** no Gemini references outside history/changelogs; all docs name the current
reality; `ruff check .`, `mypy src`, full pytest green.

---

## Phase-1 definition of done

1. Fresh clone → venv → `config.yaml` → `scripts/install-hermes-cron.sh` reproduces the entire
   daily cycle; the daily digest arrives (no 120 s death) as a friendly summary with the full log
   retained on disk.
2. The apply-kit flow works end-to-end in chat and `mark_applied` keeps tracking truthful.
3. All four seams landed with zero pre-existing-test edits; the two-user isolation test passes;
   the live DB migrated with a verified backup.
4. The automation decision is **made and executed** on recon data, recorded below.
5. Founder dogfoods ≥2 weeks post-1B with no silent failures (every error reached Telegram).

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| Schema rebuild corrupts the live DB | backup `data/cvflow.db` first; migration test runs against a copy of the real DB before deploy |
| Detached daily run dies invisibly | it inherits `cron.main`'s catch-all notify-then-raise; plus the run lock means a stuck run blocks the next one *loudly* (`AlreadyRunning` notice) |
| Summary LLM hallucinates job numbers | prompt pins numbering to the digest text; fallback to full digest on any failure; slots always set from the full result |
| New required-config breakage on the live box | rule 4: every new key optional-with-default; test loading the **committed example** and a config missing all new keys |
| Recon sample too thin (few approvals) | extend the window to 4 weeks before deciding; presented-jobs share is the tiebreaker datum |

## Progress log

- 2026-06-10 — plan written (Fable architecture-review session). Nothing executed yet.
- 2026-06-11 — **1A shipped** (sub-plan `2026-06-10-p1a-cron-fix.md`): detached `cvflow-discover.sh`,
  config-derived `print-hermes-schedule` (UTC conversion via zoneinfo), idempotent
  `install-hermes-cron.sh`, deploy.md updated. Commits `4ad62b3`/`7b9fcaa`/`5cb1c84`/`9620792`.
  Live-box install + next-digest verification still pending. Noted: `tests/test_cron.py`'s 9
  `run_job("discover")` tests share the real `data/discover.lock` (no injected lock), so they fail
  while any live `/discover` runs — a pre-existing P1B test-isolation flake, not 1A.
- 2026-06-13 — **1E-1 shipped** (sub-plan `2026-06-11-p1e1-ats-recon.md`): `cvflow.recon`
  (`classify_ats` + one-hop redirect resolve), nullable `ats` column + `set_ats`, discovery/
  analyze_jd tagging, `python -m cvflow.recon report`. Commits `cbac315`/`075ef73`/`96df990`/
  `939dcd4`/`851f0fe`. Started the automation-decision data clock; 1E-2 pending the 2–3 week window.
  Live deploy needs a gateway restart to respawn the MCP server.
- 2026-06-13 — **recon health-check added** (`5ef8c2e`): `python -m cvflow.recon healthcheck` +
  `scripts/recon-healthcheck.sh`, scheduled once via the user crontab (`0 9 20 6 *` → 2026-06-20)
  to verify the URL classifier actually tags *new* (post-tagging) jobs. Baseline run 2026-06-13
  reads SUSPICIOUS as expected (the 69 in-window jobs pre-date tagging). Recorded the **URL-only
  under-counting caveat** under 1E above; gateway restarted by the founder. **Action 2026-06-20:**
  review `~/recon-healthcheck-2026-06-20.txt`, then delete the crontab line.
