# cvflow — architecture & workflow reference

A precise, code-grounded description of the whole system: every wired component, what runs
inside Hermes vs. outside it, the end-to-end workflow, the cron jobs, the LLM providers, and
every `config.yaml` parameter. Written from a full read of `src/cvflow/**`, `artifacts/hermes/**`,
and `config.example.yaml` as of 2026-06-10. For the discovery pipeline's internal mechanics
(scrapers, gates, ranking math) see the companion `docs/discovery-engine.md`; this doc is the
system-level picture around it.

---

## 1. What cvflow is, in one paragraph

cvflow is a single-user, self-hosted job-application agent operated entirely through Telegram.
Once a day it scrapes job boards, ranks postings against the user's committed profile, and
sends a digest. For jobs the user picks it analyzes the JD, tailors a LaTeX résumé, and — only
after an explicit human approval in chat — drives the application via browser automation,
asking the user for OTPs and unanswerable fields. The hard constraints are **zero per-call
cost** (every model/service is free-tier or self-hosted) and a **deterministic, un-bypassable
approval gate** (no code path lets the LLM approve its own submission).

---

## 2. The two-process model: Hermes vs. cvflow

cvflow is not one program. It is **a set of deterministic Python skills** plus **the Hermes
Agent runtime** that calls them. Understanding the split is the key to the whole system.

### Hermes owns (the "substrate")
- **The Telegram interface** — inbound messages and outbound `hermes send`.
- **The LLM brain** — `meta/llama-3.3-70b-instruct` (NVIDIA NIM), configured in
  `~/.hermes/config.yaml` (`model.default`). This is what reads the user's chat messages,
  decides which tools to call, and writes conversational replies.
- **The MCP client** — Hermes spawns cvflow's MCP server as a subprocess and exposes its 12
  tools to the brain.
- **The scheduler** — Hermes' own cron (`hermes cron`, state in `~/.hermes/state.db`) fires the
  daily/periodic jobs.
- **The browser runtime** — available but **deliberately disabled** (see §9); cvflow drives its
  own Playwright instead, to keep the agent loop away from the gate.
- **Process supervision** — the gateway runs under systemd (`hermes-gateway.service`).

### cvflow owns (the "domain logic")
- The **MCP server** (`src/cvflow/mcp/`) exposing 12 deterministic skills.
- The **state machine + storage** — the un-bypassable gate and the SQLite tracking DB.
- **Discovery, JD analysis, résumé tailoring, essays, browser automation, auth/OTP.**
- The **command hooks** (`/apply`, `/skip`, `/discover`) — cvflow code that Hermes runs in the
  gateway process *outside* the brain.
- The **cron job bodies** — `python -m cvflow.cron <job>`, invoked by Hermes' scheduler.

The mental model: **Hermes is the body (senses, voice, clock, hands); cvflow is the
deterministic brainstem** that makes the safety-critical decisions the probabilistic LLM is not
allowed to make alone.

---

## 3. The end-to-end workflow (daily cycle)

```
┌─ Hermes scheduler (cron) ──────────────────────────────────────────────┐
│  daily → cvflow-discover.sh → python -m cvflow.cron discover           │
└────────────────────────────────────────────────────────────────────────┘
        │
        ▼  (no LLM brain involved — pure deterministic job)
  DiscoveryService.discover()
    scrape (JobSpy: linkedin/indeed/google  +  Naukri adapter)
    → normalize_rows → _prefilter (title/intern/salary/YOE)
    → cross-day dedup (SQLite) → cap (max_distill_per_cohort)
    → distil each JD → Crux         [Mistral mistral-small]
    → gate: exclude_when rules + must-have skill coverage + salary floor
    → partition into 💰 M (stated INR pay) / 📋 N (no pay)
    → fit-score each cohort         [Mistral mistral-small]
    → benchmark (code: fit×comp blend) → top-N per cohort
    → persist presented jobs as status=discovered
    → format_digest → HermesNotifier (`hermes send`) → Telegram
        │
        ▼  user reads digest in Telegram, replies
  ┌──────────────────────────────────────────────────────────────────────┐
  │ "/skip 3"   → command:skip hook  (gateway, OUTSIDE brain)              │
  │             → cvflow.gate → store.set_status(SKIPPED)                  │
  │ natural-language "tailor #1 for me" → brain calls MCP tools:          │
  │   analyze_jd(job) [NIM] → request_review(job):                        │
  │     status discovered→pending_review, tailor résumé [Cerebras],       │
  │     compile PDF [Tectonic], send PDF + plain-language diff            │
  │ "/apply 1"  → command:apply hook (gateway, OUTSIDE brain)             │
  │             → cvflow.gate → store.approve()  (pending_review→approved)│
  └──────────────────────────────────────────────────────────────────────┘
        │  (now status=approved — the ONLY way to reach it)
        ▼  brain calls fill_application(job)  (gated: asserts approved)
  Automator: open browser → discover fields → resolve each field
     (form_fields lookup → grounded essay [Cerebras] → else clarify/skip)
     pause→ask user for any required-but-unknown field (clarification loop)
     OTP needed → OtpCoordinator.request → user replies → submit_otp
     disclose every composed answer to the user → submit → capture proof
     → status=applied (or failed + Telegram alert; never silent)
```

Three independent periodic jobs run alongside the daily cycle: an **OTP-timeout sweep**, a
**heartbeat**, and a **weekly preference-learning** suggestion (see §7).

---

## 4. Component-by-component (every `src/cvflow/` module)

| Module | Purpose | Key types / functions | Depends on |
|---|---|---|---|
| `config.py` | Load + strictly validate `config.yaml` into a frozen typed `Config`; fail loudly on any missing/mistyped key. Validates `fit_weight+comp_weight==1.0` and `HH:MM`. | `load_config`, `Config`, `ConfigError`, the per-section dataclasses | PyYAML |
| `statemachine/` | The un-bypassable gate. `Status` enum + legal-transition table. `approve()` is the **sole** producer of `approved` (only from `pending_review`); `transition()` *refuses* `approved` as a target; `guard_can_submit()` raises unless `approved`. | `Status`, `approve`, `transition`, `guard_can_submit`, `SubmissionBlocked` | — |
| `storage/` | SQLite tracking store keyed by stable `job_id`; routes every status write through the state machine (so it can never write `approved` except via `approve()`). Also the `FormFields` loader (empty value = ask, never guess). Idempotent column migration for deploy-by-clone. | `ApplicationStore`, `Application`, `FormFields`; tables: `applications`, `jd_analyses`, `digest_slots`, `job_cruxes`, `decisions` | statemachine, analysis |
| `discovery/` | The daily pipeline (see `discovery-engine.md`). Scrape → normalize → prefilter → dedup → cap → distil → gate → partition → fit → benchmark → top-N. | `DiscoveryService.discover()`, `JobPosting`, `DropRecord`, `normalize_rows` | distill, benchmark, rules, skills, naukri |
| `discovery/distill.py` | One JD → a structured `Crux` (enums + nullable facts; never infers salary/YOE). Version-gated cache. | `Crux`, `Salary`, `Distiller`, `distill_all`, `DISTILL_VERSION` | Mistral provider, pydantic |
| `discovery/benchmark.py` | Code-side ranking: `comp_score`, `benchmark_cohort` (M = fit×comp blend, N = fit only), the `fit_scores` LLM call, `effective_lpa`, deterministic concern codes (`PAY_UNKNOWN`/`YOE_UNKNOWN`). | `fit_scores`, `benchmark_cohort`, `BenchmarkedJob`, `FitResult` | Mistral provider |
| `discovery/rules.py` | Declarative `exclude_when` evaluator over crux fields (config = rules, code = evaluator). Null field never matches. | `crux_excluded` | — |
| `discovery/skills.py` | Deterministic must-have-skill coverage gate; drops a job when the candidate lacks >50% of its must-haves, matched against `profile/candidate_skills.yaml`. | `coverage_drop`, `load_skill_profile` | PyYAML |
| `discovery/naukri.py` | nkparam-signed Naukri API adapter (JobSpy's Naukri 406s); IP-fragile (see discovery-engine §10). | `search_naukri`, `make_nkparam` | pycryptodome |
| `analysis/` | Fetch a JD page (stdlib urllib + HTML→text) and LLM-extract structured fields (skills, quals, seniority, tone, **applicant_instructions**). Never invents. | `JDAnalyzer`, `JDAnalysis`, `fetch_jd` | NIM provider |
| `resume/` | Tailor the modular master `.tex` by **selecting + reordering whole units** (≤2 projects), never rewriting — so "no new facts" is a deterministic line-subset check. Renders, asserts, diffs, Tectonic-compiles. | `ResumeTailor`, `parse_master`, `Master`, `MAX_PROJECTS=2` | Cerebras provider, Tectonic |
| `essays/` | Compose free-text/essay answers from the profile in the user's voice; any ungroundable answer or bad citation → `needs_clarification` (never a guess). | `compose_answer`, `EssayAnswer` | Cerebras provider |
| `automation/` | Deterministic browser form-filling: discover fields → `resolve_field` (form_fields → grounded essay → clarify/skip) → disclose composed answers → submit → capture proof. `guard_can_submit` gates every entry. Crash → `failed` + Telegram alert. | `Automator`, `FormFiller`, `SessionManager`, `resolve_field` | Playwright, essays, statemachine |
| `auth/` | Fernet token vault (secrets at rest, `chmod 600`) + non-blocking email-OTP coordinator (deadline, lazy + proactive expiry, never silent). | `TokenVault`, `OtpCoordinator` | cryptography |
| `llm/` | OpenAI-compatible chat client `NimProvider` (used for **all** providers — brain/distill/tailoring) with per-minute budget guard and a `generate_structured` (json_schema) path. Also a `GeminiProvider` class that is **no longer wired** (legacy; see §8). | `NimProvider`, `GeminiProvider`, `RpmExceeded`, `RpdExceeded` | stdlib urllib / google-genai |
| `gate/` | The deterministic `/apply`·`/skip` handler — the **sole** caller of `store.approve()`. Resolves digest ordinals/ranges/`all`; batches; logs each decision for analytics. | `handle_gate_command`, `resolve_targets`, `GateResult` | storage, statemachine, analytics |
| `discover_command.py` | The `/discover` trigger handler (gate's twin): authorized + not-locked → spawn a detached run + ack. A trigger, never an approval. | `handle_discover_command`, `DiscoverResult` | — |
| `runlock.py` | `fcntl` single-run guard so the daily cron and a manual `/discover` never double-run. | `discovery_lock`, `is_locked`, `AlreadyRunning` | — |
| `cron.py` | The deterministic job entrypoint `python -m cvflow.cron <job>`. Builds services from config (`_build`), dispatches `discover`/`sweep-otp`/`heartbeat`/`learn`, renders the digest + drop report, notifies via `hermes send`. | `main`, `run_job`, `format_digest`, `format_drop_report` | most modules |
| `notify.py` | `HermesNotifier` — the single outbound path; shells to `hermes send`. Resolves the binary absolutely (systemd PATH gap). **Never raises.** | `HermesNotifier` | subprocess |
| `analytics.py` | Read/write rollups over the `decisions` table (apply-rate by role/company-type/cohort, medians, top-skipped) for the weekly learning input. | `summarize`, `record_decision` | storage |
| `learning.py` | Weekly: summarize apply/skip history into **proposed** preference edits (never auto-applies). | `summarize_decisions` | NIM provider |
| `mcp/` | The MCP surface. `tools.py` = pure dispatcher (`CvflowTools`, the 12-tool `TOOL_NAMES`, and `build_tools` which wires everything from config); `server.py` = FastMCP stdio adapter registering exactly the allowlist. | `CvflowTools`, `build_tools`, `build_server` | everything |

`bot/` and `scheduler/` are **empty** by design — Hermes provides messaging and scheduling, so
there is no hand-built bot or scheduler.

---

## 5. The MCP tool surface (the 12 skills the brain can call)

Defined in `mcp/tools.py::TOOL_NAMES`, registered by `mcp/server.py`, allow-listed again in
`~/.hermes/config.yaml` (`mcp_servers.cvflow.tools.include`). **`approve` is in neither list —
by construction the agent has no approve tool.**

| Tool | What it does | LLM used |
|---|---|---|
| `ping` | health check `{status: ok}` | — |
| `discover` | run discovery, return ranked job dicts (the brain can trigger it conversationally; the daily run is the cron) | Mistral (distil+fit) |
| `analyze_jd` | fetch + extract structured JD fields, persist | NIM |
| `list_applications` / `get_application` | read tracking records | — |
| `request_review` | discovered→pending_review, tailor + compile PDF, return PDF path + diff + analysis | Cerebras |
| `compose_essay` | grounded free-text answer or a clarification flag | Cerebras |
| `status_report` | counts per status | — |
| `submit` | asserts `guard_can_submit` (approved only) — raises otherwise | — |
| `fill_application` | drive the browser to fill the approved app (gated) | Cerebras (field essays) |
| `resume_application` | continue a paused fill with a user clarification answer | Cerebras |
| `submit_otp` | resolve a user OTP; on time, resume; expired → `otp_timeout` | — |

---

## 6. Slash commands / hooks / plugins (deterministic, OUTSIDE the brain)

These run in the gateway process when the user types a known slash command, and **short-circuit
before the brain sees the message** (`{"decision":"handled"}`). Each is a `hooks/<name>/`
(handler) + `plugins/<name>/` (registers the command). Installed to `~/.hermes/{hooks,plugins}/`.

| Command | Hook → handler | What it does |
|---|---|---|
| `/apply <n…>` | `cvflow-gate` → `cvflow.gate.handle_gate_command` | The approval path. Calls `store.approve()` (the sole caller). **Only valid from `pending_review`** → `approved`. |
| `/skip <n…>` | `cvflow-gate` → same | `discovered`/`pending_review` → `skipped`. |
| `/discover` | `cvflow-discover` → `cvflow.discover_command` | Spawns a detached `cron discover --progress`, acks instantly, streams progress + the digest + ONE LLM-summarized drop note (full digest + per-job drops retained to `logs/discover/<ts>.md`; daily cron is digest-only). |

`/apply` is used (not the Hermes built-in `/approve`) to avoid collision. Unauthorized users →
`{}` (ignored). The hooks import cvflow from `$CVFLOW_ROOT` (default `~/cvflow`) at runtime.

> **Note on the apply transition:** `store.approve()` requires the job to be in
> `pending_review`. A freshly *discovered* job is not — it must first pass through
> `request_review` (the brain's tailoring step). So `/apply` directly off the digest only
> succeeds after a review has been prepared for that job; otherwise it returns
> "couldn't apply: … (discovered)". `/skip` works straight from the digest. (See §13.)

---

## 7. The cron jobs (4) — purpose, body, current state

All four run as Hermes `--no-agent` scripts under `~/.hermes/scripts/`, each executing
`python -m cvflow.cron <job>` and reporting to Telegram via `hermes send`. The schedule lives in
Hermes' `state.db` (set at `hermes cron create` time), **not** in `config.yaml`.

| Job | Schedule (as registered) | Body (`cron.run_job`) | Purpose |
|---|---|---|---|
| `cvflow-discover` | `0 8 * * *` (UTC) | `DiscoveryService.discover()` → digest | The daily job: scrape, rank, send the two-section digest. |
| `cvflow-sweep-otp` | `every 5m` | `OtpCoordinator.expire_overdue()` | Enforces OTP timeouts while idle (marks overdue `approved` apps `otp_timeout` + notifies). |
| `cvflow-heartbeat` | `every 30m` | status counts | "I'm alive" message with per-status counts. |
| `cvflow-learn` | `every 168h` (weekly) | `analytics.summarize` + `learning.summarize_decisions` | Proposes (never applies) preference edits from apply/skip history; logs to `data/learning/`. |

**Three known problems with the current cron wiring (the reason this doc was written):**
1. **Not reproducible after a `git clone`.** The schedule lives only in `~/.hermes/state.db`
   (never committed). A fresh box has an empty registry; the four jobs must be re-created by
   hand. `cvflow-learn` is currently **not registered** on this box.
2. **`config.yaml` schedule fields are decorative.** `schedule.heartbeat_interval_minutes` and
   `daily_discovery_time` are read only by cvflow's Python when *composing* messages; the actual
   firing cadence is whatever was typed at `hermes cron create`. They have diverged (config says
   heartbeat 360–720 min; the live job fires every 30 min).
3. **Discovery times out.** Hermes cron kills any script at ~120 s (no `--timeout` flag), but a
   full discovery run takes 10–25 min, so `cvflow-discover` dies every day
   (`error: Script timed out after 120s`).

(A fix — config-derived schedules, an idempotent install script, and a detached discovery
wrapper — is being designed separately.)

---

## 8. The LLM providers (4 roles, 3 vendors, all free-tier)

All providers are OpenAI-compatible and go through one client class, `llm.NimProvider`
(`POST /chat/completions`). The vendor differences (`random_seed` vs `seed`, browser
User-Agent for Cerebras/Cloudflare, json_schema support) are handled inside it.

| Role | Vendor / model | Where it's used in code | Why this one |
|---|---|---|---|
| **Brain** | NVIDIA NIM `meta/llama-3.3-70b-instruct` | Hermes agent loop (all chat + tool-calling); cvflow `analyze_jd` (`JDAnalyzer`) | Fast, non-reasoning, good tool-calling, 128k ctx, ~40 RPM, no hard daily cap |
| **Distillation** | Mistral `mistral-small-2506` | `discovery/distill.py` (JD → Crux) | Strict `json_schema`, very high TPM, 5 RPS |
| **Fit ranking** | Mistral `mistral-small-2506` | `discovery/benchmark.py::fit_scores` | Needs a per-job JSON **array**; NIM can't emit it reliably |
| **Tailoring** | Cerebras `gpt-oss-120b` | `resume/` (plan + diff), `essays/` (compose), `automation/` (form free-text) | High quality, 65k ctx, low volume |

**Important accuracy notes:**
- The `llm.brain` provider object *is* constructed and passed into `DiscoveryService`, but
  discovery's fit call uses the **distillation** provider (Mistral), not the brain. The brain's
  only cvflow use is `analyze_jd`.
- **Gemini is no longer wired.** `llm/__init__.py` still contains a `GeminiProvider` class, but
  `build_tools` and `cron._build` construct `NimProvider` for all three config blocks, and
  `config.example.yaml` sets `tailoring.provider: cerebras`. The README's "tailoring escalates to
  Gemini 2.5 Flash" line is stale. (Google gutted the Gemini free tier — see discovery-engine §11.)
- "Zero per-call cost" holds because every tier is free; the trade-off is the free-tier rate
  limits (NIM 40 RPM, Mistral 5 RPS, Cerebras 5 RPM / 2400/day), respected by the per-minute
  guard in `NimProvider` and the discovery distil cap.

---

## 9. How Hermes fits — and what is deliberately disabled

`~/.hermes/config.yaml` highlights:
- `model.default: meta/llama-3.3-70b-instruct`, `provider: nvidia` — the brain. The real key
  lives in `~/.hermes/.env` (`NVIDIA_API_KEY`), **not** cvflow's `config.yaml`.
- `mcp_servers.cvflow` — spawns `python -m cvflow.mcp` (cwd `/home/ubuntu/cvflow`) with the
  12-tool `tools.include` allowlist.
- `agent.tool_use_enforcement: force` — required so the NIM/llama brain emits native tool-calls
  (with `auto` it leaks raw JSON into chat and double-calls).
- `agent.disabled_toolsets: [browser, computer_use, image_gen, tts, vision, session_search,
  cronjob, delegation, code_execution]` — **the browser/computer_use disablement is a safety
  decision**: if the agent could drive a browser itself, it could submit an application around
  the gate. cvflow drives its own Playwright via the gated `fill_application` tool instead.
- `agent.gateway_timeout: 1800`, `clarify_timeout: 600` — long-running tool tolerance and the
  clarification-loop wait.

**Runs inside Hermes:** the brain, Telegram I/O, the MCP client (which spawns cvflow's server),
the command hooks (in the gateway process), the cron scheduler. **Runs as cvflow code outside
the brain:** the MCP server process, the cron job bodies, the detached discovery run, and the
hook handlers' logic (they execute in the gateway process but are pure cvflow functions the LLM
never mediates). `notify.py` reaches Telegram via `hermes send` **without** needing the gateway
or the brain — that's how cron jobs and the detached discovery run message the user.

---

## 10. Data model (SQLite, `data/cvflow.db`)

| Table | Holds |
|---|---|
| `applications` | one row per job_id: company, role, jd_url, **status**, timestamps, tailored-PDF path, proof (url/screenshot/title/confirmation), `otp_deadline` |
| `jd_analyses` | the extracted `JDAnalysis` JSON per job |
| `digest_slots` | the ordinal→job_id map for the last digest (so `/apply 1` resolves) |
| `job_cruxes` | cached distilled `Crux` per job, version-gated by `DISTILL_VERSION` |
| `decisions` | every apply/skip with role/company/cohort/fit context — the analytics + learning input |

The status lifecycle: `discovered → pending_review → approved → applied`, with branches to
`skipped`, `otp_timeout`, `failed`. `approved` is reachable **only** via `approve()`.

---

## 11. The approval-gate invariant, defended in layers

1. **State machine:** `transition()` refuses `approved`; `approve()` is the only producer and
   only from `pending_review`.
2. **Storage:** `set_status()` routes through `transition()`, so the persistence layer also
   cannot write `approved` except via `ApplicationStore.approve()`.
3. **MCP surface:** `TOOL_NAMES` has no `approve`; `submit`/`fill_application` assert
   `guard_can_submit`. The brain can *prepare* (`request_review`) and *attempt* (`submit`) but
   cannot approve.
4. **Hermes allowlist:** `tools.include` independently omits `approve` (defense in depth).
5. **The only approver:** the `/apply` command hook, running outside the brain, calling
   `store.approve()`. A human in Telegram is the sole path to `approved`.

---

## 12. Config + secrets summary

`config.yaml` (gitignored, `chmod 600`) holds all cvflow secrets and tuning. `~/.hermes/.env`
(gitignored) holds Hermes' secrets — crucially `NVIDIA_API_KEY` (the brain key that actually
matters), `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_USERS`. The Fernet key
(`data/.fernet_key`) is auto-created `chmod 600`. The `profile/` knowledge base, `resume/*.tex`,
and `form_fields.json` are **intentionally committed** (PII, no secrets) so a clone deploys
without templates. See §13 of `config.example.yaml` walkthrough below.

### Every `config.yaml` parameter

**telegram** — `bot_token` (from @BotFather), `authorized_user_id` (the only Telegram user the
bot obeys; everything else is ignored).

**schedule** — `daily_discovery_time` (HH:MM, interpreted in `timezone`), `timezone` (IANA, e.g.
`Asia/Kolkata`), `heartbeat_interval_minutes`. *(Today these are read only when composing
messages, not by the scheduler — see §7 problem 2.)*

**discovery** — `search_terms` (the scrape matrix rows; cast wide, ranking filters by fit not
title), `locations`, `sites` (`linkedin/indeed/google` via JobSpy + `naukri` via the custom
adapter), `results_wanted_per_site` (raw-volume dial), `hours_old` (freshness window),
`country_indeed` (pins Indeed/Naukri to a country), `linkedin_fetch_description` (fetch full
LinkedIn JD text so distillation has content), `max_distill_per_cohort` (total JDs distilled per
run — coverage vs. speed), `top_n_per_cohort` (jobs shown per 💰/📋 section),
`reconsider_discovered` (testing toggle; keep `false` in prod).

**preferences** — `yoe_have` (candidate years), `min_ctc_lpa` (salary floor: M/N split +
prefilter), `exclude_title_keywords` (hard title drops), `yoe_buffer` (tolerance above stated
YOE before the Naukri experience gate fires; ceiling = `yoe_have + yoe_buffer`), `top_ctc_lpa`
(comp-score top anchor), `fit_weight`/`comp_weight` (M-cohort blend, must sum to 1.0),
`prefer_roles` (role_family→weight, dominates the fit band), `exclude_when` (declarative
deal-breaker rules over crux fields: seniority, min_years, country, night-shift, maintenance,
red-flags).

**llm.brain / llm.distillation / llm.tailoring** — each: `provider`, `api_key`, `base_url`,
`model`, `max_requests_per_minute`. (Brain is also configured inside Hermes; the cvflow copy is
used for `analyze_jd` and as the discovery service's nominal brain.)

**resume** — `master_tex_path` (the modular master), `output_dir` (compiled PDFs),
`latex_compiler` (Tectonic regardless).

**automation** — `headless` (false → headed under xvfb for stealth), `use_stealth`,
`storage_state_dir` (Playwright persistent context: cookies/session, also screenshot dir),
`form_timeout_minutes`.

**auth** — `google_account_email` (burner for SSO), `application_email` (OTP destination),
`otp_timeout_minutes`.

**storage** — `db_path` (SQLite), `form_fields_path`. **security** — `fernet_key_path`.
**profile** — `knowledge_base_dir` (all `*.md` loaded in full as LLM context).

---

## 13. Observations worth knowing (not bugs in scope, but real)

- **`/apply` from a discovered job fails.** The digest advertises `/apply N`, but
  `store.approve()` requires `pending_review`. A job must go through `request_review` (the
  tailoring step, brain-mediated) before `/apply` works. In practice the user expresses interest
  → brain runs `analyze_jd` + `request_review` → PDF arrives → user `/apply`s. A discovered job
  `/apply`'d before review returns "couldn't apply (discovered)". Worth confirming this is the
  intended UX vs. smoothing it.
- **Discovery's nominal `brain` is unused for ranking** — fit runs on Mistral. Harmless but a
  small dead wire.
- **`GeminiProvider` is dead code** — kept in `llm/__init__.py` but not constructed anywhere.
- **No `min_fit_score` floor** — by user choice (protects volume); the 💰 cohort can surface
  low-fit salaried roles that list no must-haves.
- **Naukri is IP-fragile** — best-effort; blocked when the datacenter IP is Akamai-flagged.
- **Single-tenant by construction** — one config, one DB, one profile in the repo, one Telegram
  user, one Hermes gateway, one Fernet key, one set of crons (see the productization review).
