# Phase 2 — Hosted multi-user service (Master Plan)

> **For agentic workers (Opus):** This is a **master roadmap** — source of truth for order, scope,
> and exit criteria of Phase 2. Before executing each stage, write a bite-sized TDD sub-plan with
> `superpowers:writing-plans` (save as `docs/superpowers/plans/YYYY-MM-DD-p2<letter>-<name>.md`),
> then execute with `superpowers:subagent-driven-development` or `superpowers:executing-plans`.
> Keep checkboxes + the progress log in sync.
>
> **Authority:** implements `docs/architecture-review-fable.md` (esp. §§1–3bis). Companion:
> `2026-06-10-phase-1-personal-hardening.md` — **all of Phase 1 is a hard precondition.**

**Goal:** Turn single-user cvflow into a hosted freemium service for ~100 users: an owned
Telegram bot + small web app over the unchanged core lib, one shared daily scrape serving
everyone, free tiers until they break, tier enforcement (free digest + 3-run trial / paid /
BYO-key), with the never-fabricate / never-fail-silently invariants intact as product promises.

**Architecture:** Hermes, MCP, and the hooks are retired. Two processes on one box, one SQLite
(WAL): `cvflow-bot` (python-telegram-bot v21 long-polling + APScheduler + task worker + the NL
voice) and `cvflow-web` (FastAPI). The core lib (discovery/analysis/resume/essays/gate/storage)
is called directly — it is already user_id-parameterized and path-resolved from Phase 1C.
Discovery runs ONCE per day for the union of all users' searches; ranking/digests are per-user.

**Tech stack additions:** `python-telegram-bot~=21`, `APScheduler~=3.10`, `fastapi` + `uvicorn`,
`itsdangerous` (web sessions), `pypdf` (onboarding résumé extraction). All OSS, no paid services.
**No server-side browser automation for users — ever in this phase** (review §3bis). No Redis, no
Postgres, no Celery (P3 questions).

**Preconditions (verify before starting; do not start otherwise):**
1. Phase 1 complete: seams (user_id schema, `UserPaths`, `SharedRateLimiter`, `make_notifier`),
   apply kit + `mark_applied`, cron fix, recon decision executed; founder dogfooding ≥2 weeks.
2. **ToS check (recorded in the progress log):** review NIM, Mistral, and Cerebras free-tier terms
   for serving third-party end users from one account. If disallowed for a vendor: free users get
   reduced function on the allowed vendors, or the free tier launches BYOK-only, or that vendor's
   role moves to a cheap paid key immediately (review §3.4 cost table). This changes scope knobs,
   not architecture.
3. Founder decisions recorded: launch audience (geography), invite-only vs public, paid price
   point + workflow quota number.

---

## Standing rules

1. TDD; Conventional Commits; surgical changes (CLAUDE.md).
2. **Gate invariant, restated for P2:** `store.approve()` is reachable only from a human's
   `/apply` in their own chat (bot command handler → `cvflow.gate`), never from the NL tool-router.
   The NL voice gets NO `approve` tool — same defense-in-depth as the MCP allowlist had.
3. **Tenant isolation:** every query/path carries an explicit `user_id`; any new store method
   without one is a review-blocker. Cross-tenant tests accompany every stage.
4. Never fabricate (onboarding extraction must confirm, not invent); never fail silently (every
   per-user failure notifies that user; operator failures notify the admin).
5. Free-tier limits enforced by the shared limiter + per-user quotas; any new cost flagged.
6. PII: per-user data only under `data/users/<id>/` (gitignored); no PII in logs; deletion on
   request must be complete.

## Stage order

```
2A substrate swap (owned bot, founder = user #1, Hermes retired)
2B multi-tenant launch (onboarding, shared discovery, quotas, PII out of git)
2C tiering + web + payments
```
Each stage ships independently; the service is usable at the end of every stage.

---

### [ ] 2A — Substrate swap: the owned bot replaces Hermes

**New package `src/cvflow/app/`** (the only place allowed to know about Telegram/PTB):

| File | Responsibility |
|---|---|
| `app/main.py` | `python -m cvflow.app` entrypoint: build config + services, start APScheduler, task worker thread, PTB long-polling |
| `app/bot.py` | PTB `Application`; command handlers + document upload + plain-text routing; per-chat auth via the `users` table |
| `app/nl.py` | the friendly NL voice (below) |
| `app/scheduler.py` | APScheduler jobs (from config) + the `tasks` table + the single worker thread |
| `app/registry.py` | the tool registry: name → (callable, JSON schema) — the NL voice's only reach into the core lib |
| `app/admin.py` | admin-only commands (stats, broadcast, grant) — minimal at 2A |

**Command handlers (deterministic, mirroring today's hooks — the brain never mediates these):**
- `/apply <n…>` and `/skip <n…>` → `cvflow.gate.handle_gate_command(...)` **unchanged** — the same
  sole-approver function, now called from a bot handler instead of a Hermes hook.
- `/discover` → enqueue a `discover` task for this user (replaces the detached-spawn path; the
  fcntl `runlock` is superseded by task-state for app-spawned runs but keep it during transition).
- `/status` → `status_report` per user. `/start` → onboarding (2B; at 2A greets the owner).
- Unknown users → ignored (2A) / onboarding-gated (2B).

**The NL voice (`app/nl.py`) — preserving the valued conversational UX:**
- `respond(user_id, text, history) -> str`: one NIM `llama-3.3-70b` chat-completions call with
  `tools=[…]` (OpenAI function-calling format) from `app/registry.py`; execute requested tool;
  feed results back; loop max 3 rounds; final content is the reply. History = last ~20 turns from
  a new `chat_history` table (user_id, role, content, ts), trimmed by tokens.
- Registry at 2A (read/prepare only): `discover_status`, `list_applications`, `get_application`,
  `analyze_jd`, `request_review`, `apply_kit`, `mark_applied`, `compose_essay`, `status_report`.
  **No `approve`, no submit-anything** (rule 2).
- Known NIM quirk (from Hermes ops): llama-3.3-70b sometimes emits tool-call JSON as content
  instead of native tool_calls. Mitigate: strict system prompt + a salvage parser that detects a
  single well-formed `{"name": …, "arguments": …}` content blob and executes it; otherwise reply
  asking the user to rephrase. Budget a real sub-plan task for prompt iteration here — this is the
  hardest part of 2A.
- System prompt: warm, concise persona (port the intent of `artifacts/hermes/SOUL.md`); facts only
  from tool results — never invent job data (invariant 2 applies to chat too).

**Scheduler + tasks (`app/scheduler.py`):**
- `tasks` table: `id INTEGER PK AUTOINCREMENT, kind TEXT, user_id TEXT, payload TEXT,
  state TEXT CHECK(state IN ('queued','running','done','failed')), created_at, started_at,
  finished_at, error TEXT`. Worker = one thread: claim with
  `UPDATE tasks SET state='running', started_at=? WHERE id = (SELECT id FROM tasks WHERE
  state='queued' ORDER BY id LIMIT 1) RETURNING *` (SQLite ≥3.35); run; mark done/failed (+notify
  the task's user on failure — never silent). One worker = heavy jobs serialized (2 GB box,
  SQLite-friendly); priority for premium lands in 2C as an `ORDER BY` change.
- APScheduler (BackgroundScheduler): daily shared-discover enqueue (per-user fan-out at 2B),
  heartbeat → admin chat, weekly `learn` per active user, hourly task-janitor (re-queue or fail
  `running` tasks older than a ceiling — crash recovery).
- On boot: mark stale `running` tasks failed + notify (systemd restart story).

**Config & deploy:**
- New optional `config.yaml` section `app:` (`admin_chat_id`, `poll_mode: "polling"`,
  `workers: 1`); `telegram.authorized_user_id` becomes the admin/owner id.
- `notify.backend` default flips to `telegram` (P1C-4 built it); per-user delivery = a notifier
  bound to each user's chat id (factory gains `for_user(user_id)` using the `users` table).
- systemd: `deploy/cvflow-bot.service` (Restart=always, user-level); deploy.md rewritten:
  install → venv → config → `systemctl enable cvflow-bot`. No xvfb (no server browser).
- **Hermes retirement (last step, after parity):** stop/disable `hermes-gateway`, delete the
  4 cron jobs, remove `src/cvflow/mcp/` + hooks/plugins from the live `~/.hermes/`; keep
  `artifacts/hermes/` in git history via `git mv artifacts/hermes archive/hermes`. Keep
  `scripts/install-hermes-cron.sh` working until this step lands (rollback path).

**Parity checklist (the 2A exit bar — every row demonstrated by the founder on the live box):**
daily digest summary arrives · `/discover` works with progress + summary · digest numbering +
`/apply`/`/skip` resolve · NL: "analyze 2 and tailor it" produces PDF + diff in chat ·
apply kit + `mark_applied` · heartbeat to admin · weekly learn · every induced failure (kill a
provider key) reaches chat. **Exit:** parity checklist green for ≥1 week of founder use, Hermes
uninstalled, schedule fully in git/config, full pytest/ruff/mypy green.

### [ ] 2B — Multi-tenant launch

**Users table + identity** (in the main DB via `ApplicationStore` migration):
```sql
CREATE TABLE IF NOT EXISTS users (
    user_id          TEXT PRIMARY KEY,        -- "tg:<telegram numeric id>"; owner keeps 'owner'
    telegram_chat_id INTEGER UNIQUE NOT NULL,
    display_name     TEXT,
    tier             TEXT NOT NULL DEFAULT 'free',   -- free | paid | byok
    runs_used        INTEGER NOT NULL DEFAULT 0,     -- lifetime trial counter (free tier)
    runs_quota       INTEGER,                        -- monthly quota for paid/byok; NULL=free rules
    active           INTEGER NOT NULL DEFAULT 1,
    created_at       TEXT NOT NULL
);
```
Map the founder's row to `user_id='owner'` so existing data needs no rewrite.

**Onboarding conversation (`app/onboarding.py`)** — `/start` for an unknown chat:
1. Consent message first (what is scraped, which LLM vendors process their data, retention,
   `/forgetme`) — explicit accept required; invite-code check if invite-only mode (config flag).
2. Résumé upload (PDF) → `pypdf` text → **Cerebras structured extraction** into draft
   `experience.md`, `skills.md`, `projects.md`, `education.md`, plus a draft
   `candidate_skills.yaml` — every draft echoed back for confirm/edit **before** saving
   (extraction proposes, the human confirms — invariant 2; anything ambiguous is asked, not
   assumed). Saved under `data/users/<id>/profile/` via `UserPaths`.
3. Short prefs interview (one question per message): roles → `prefer_roles`, locations, min CTC,
   YOE, deal-breakers → written to `data/users/<id>/prefs.yaml`.
4. Master résumé: accept a `.tex` upload to `…/resume/master.tex`, else generate a starter master
   from the confirmed profile using the existing modular-section format (`parse_master` must
   accept it — assert in tests), clearly told to the user as a draft they should review.
5. New `UserPrefs` loader (`src/cvflow/userprefs.py`): per-user subset of today's
   `preferences`+`discovery` config blocks (search_terms, locations, prefer_roles, exclude_when,
   min/top CTC, yoe, top_n) with validated defaults; global `config.yaml` keeps vendor keys,
   app settings, and owner paths only.

**Shared discovery refactor (`src/cvflow/discovery/shared.py`)** — the scaling move:
- `build_scrape_plan(all_user_prefs) -> list[tuple[term, location, site]]` — union, deduped,
  ordered (owner/priority terms first — gather order matters for the distill cap).
- Daily flow: ONE `_gather_rows`-equivalent over the plan → `normalize_rows` → distil once into
  the **global** `job_cruxes` (existing cache + `DISTILL_VERSION` honored) → then **per user**:
  prefilter (their keywords/floors) → seen-set (their `applications` rows) → `exclude_when` +
  skill gate (their `candidate_skills.yaml`) → fit call (their fingerprint; one Mistral call per
  user-cohort) → benchmark → top-N (tier-capped) → persist `status=discovered` for that user →
  digest summary to their chat.
- Implementation: extract `DiscoveryService` stages into module-level functions reused by both
  the single-user path (kept for self-hosters) and the shared path; budget math at 100 users:
  distill = shared (~hundreds/day), fit = users×2/day — see review §3.4 table.
- Per-source circuit breaker: `source_health` table (site, consecutive_failures, last_ok); after
  N consecutive empty/failed batches → skip source for the day + admin notice + one line in user
  digests ("Naukri unreachable today") — closes today's silent-Naukri-degradation gap.

**Quotas + budget enforcement:**
- A workflow run = `request_review` (the metered unit: analyze+tailor+kit). Decrement/check in the
  bot layer *before* invoking; free tier: refuse beyond 3 lifetime runs with an upgrade message;
  digest depth capped at 5/run for free users (`top_n` override).
- Per-user daily LLM ceilings (config defaults) enforced in the registry wrapper; vendor-level
  protection is the P1 `SharedRateLimiter`.

**PII out of git:** `git mv profile data/users/owner/profile` (+ master.tex →
`data/users/owner/resume/`), config paths updated, `data/` already gitignored; commit a
`profile.example/` skeleton (schema docs only, no data) for self-hosters; note that history still
contains the founder's PII (repo private — accepted, recorded).

**`/forgetme`:** delete the user's rows (`applications`, `jd_analyses`, `digest_slots`,
`decisions`, `chat_history`, `tasks`, `users`) + `data/users/<id>/` recursively; confirm in chat.

**Exit criteria:** ≥2 real non-founder users onboarded end-to-end without operator help; one
shared scrape produced N distinct correct digests (isolation test extended to the shared path);
trial counter blocks the 4th run; circuit breaker demonstrated (kill Naukri → users informed);
nightly `data/` backup job in place (tar + rclone to a free remote or a second disk — pick at
sub-plan time); founder PII out of the working tree.

### [ ] 2C — Tiering, web app, payments

**Tier enforcement (`src/cvflow/tiers.py`):** one function the bot/web both call:
`check_and_consume(user, action) -> Allowed | Refused(reason, upsell_text)` for actions
`workflow_run | digest_depth | priority`. Free: 5-job digest, 3 lifetime runs. Paid: quota from
`runs_quota` (founder-set), full depth, task priority (worker `ORDER BY` tier). BYOK: paid
behavior; their keys stored Fernet-encrypted (reuse `TokenVault`) in
`data/users/<id>/providers.yaml` and loaded into per-user `ProviderConfig`s — same `NimProvider`
path, so BYOK = config, not code. Vendor failover note: a BYOK user's broken key fails *their*
runs with a clear message, never falls back silently to pooled keys.

**Web app (`src/cvflow/web/`, separate process `cvflow-web.service`, same DB):**
- Auth: **Telegram Login Widget** (no passwords): verify the login payload HMAC-SHA256 against
  the bot token per Telegram's documented scheme; map to the same `user_id`; session = signed
  cookie (`itsdangerous`). Users exist only via bot onboarding first (web is a companion, not a
  second registration path — keeps 2C small).
- Pages (server-rendered Jinja, no SPA): digest history (from `digest_slots` history — add a
  `digests` archive table at sub-plan time), applications table w/ status + proof/kit links,
  tailored-PDF downloads (auth-checked file serving from `UserPaths.output_dir`), prefs editor
  (writes `prefs.yaml` with validation), BYOK key entry (write-only — never display stored keys).
- Bind localhost behind caddy/nginx with HTTPS, or expose via a free Cloudflare tunnel — decide at
  sub-plan time; inbound firewall stays minimal.
- The 4096-char Telegram ceiling stops mattering: long digests/kits link to the web view.

**Payments — manual first, deliberately:** a static pricing note + UPI/Stripe payment link; admin
`/grant <user> paid [quota]` + `/revoke` flip the `users` row; receipts = a `grants` audit table
(admin, user, tier, quota, note, ts). **No billing code, no webhooks** until ≥10 paying users
(P3). Risk of manual lag is acceptable at this scale.

**Admin tooling:** `/admin stats` (users by tier, runs today, vendor budget utilization from the
limiter DB, source health), `/admin broadcast <msg>` (rate-limited, opt-out honored).

**Exit criteria:** a free user hits the trial wall and upgrades via payment-link + `/grant` and
their next run succeeds at paid depth/priority; a BYOK user runs tailoring on their own key
(verified via the limiter's vendor accounting); web login + all pages work for 3 real users;
keys never appear in logs/pages; cross-tenant web test (user A cannot fetch user B's PDF by URL).

---

## Capacity plan (the 2 GB box, honestly)

| Component | Steady RSS | Notes |
|---|---|---|
| cvflow-bot (PTB + APScheduler + worker) | ~120–180 MB | one process |
| cvflow-web (FastAPI/uvicorn) | ~80–120 MB | separate process |
| Discovery run (JobSpy + pandas) | ~200–400 MB peak | serialized by the worker |
| Tectonic compile | ~200–400 MB peak, seconds | serialized; one at a time |
| Playwright/Chromium | **0** | no server browser in P2 |

Serialized heavy jobs keep peak under ~1 GB. If onboarding extraction + tailoring + discovery
contention hurts at ~50 users, the first move is a bigger box (founder pre-approved at P3
threshold), not new architecture. SQLite WAL + busy_timeout + a single writer-ish worker is
sufficient at this write volume; measure before reaching for Postgres.

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| NIM tool-calling flakiness degrades the NL voice | salvage parser + prompt iteration task + deterministic slash-commands always work regardless |
| Free-tier ToS forbids pooled serving | precondition check; BYOK-only free tier or early cheap paid key for that vendor (review §3.4) |
| Pooled key exhausted by one user | per-user quotas (2B) + shared limiter (P1) + admin stats visibility |
| Tenant data leak | rule 3 + isolation tests at every stage + web URL-authz test |
| Scrape sources ban the box | circuit breakers + notices (2B); pre-decided proxy fallback only if a ban persists >1 week (review §3.5) |
| Founder PII in git history | repo private; accepted + recorded; fresh-history mirror only if the repo ever goes public |
| Onboarding extraction fabricates | drafts are confirmed by the human before saving; ambiguities asked |

## Explicit non-goals (do not build in P2)

Server-side form submission for users · LinkedIn automation · CAPTCHA handling · Postgres/Redis/
Celery/K8s · automated billing/webhooks · mobile app · public API · the browser extension
(P3 candidate, per review §3bis).

## Progress log

- 2026-06-10 — plan written (Fable architecture-review session). Preconditions not yet met:
  Phase 1 not started; ToS check not done; founder launch decisions (audience, invite mode,
  price/quota) not recorded.
