# cvflow → freemium tool: architecture review (Fable)

> **Status:** authoritative architectural direction, written 2026-06-10 by the Fable review session.
> Companion build plans (what Opus executes): `docs/superpowers/plans/2026-06-10-phase-1-personal-hardening.md`
> and `docs/superpowers/plans/2026-06-10-phase-2-hosted-service.md`. Those plans supersede
> Phase 12 of the original `2026-06-03-cvflow-build-plan.md` (see §5 here for how).

## §0 Operating model (founder's stated intent, 2026-06-10)

- **Phase 1 — personal:** single user (the founder, maybe a handful), own EC2, own free API keys,
  deployable anywhere per `docs/deploy.md`. Keep this working as-is; harden it.
- **Phase 2 — hosted (~100 users):** one hosted instance, pooled free keys for free users; paid
  keys only for paying users, only once free tiers actually break. Both Telegram and a simple web
  front-end. Built by directed Opus sessions, like everything so far.
- **Phase 3 — only if Phase 2 satisfies 100+ users:** real investment (bigger server, paid
  scraping/APIs). Design for it; build nothing for it.
- **Tier shape (decided):** free = recurring daily digest capped at 5 jobs/run + **3 full workflow
  runs** (analyze → tailor → apply kit) as a one-time trial; paid = subscription with a generous
  workflow quota; BYO-key = modestly discounted subscription (their keys, our infra).

The job of this review: decide the architecture so Phase 2 is a **migration, not a rewrite**, and
say exactly what to build now versus later.

## §1 Verdict

Keep the Phase-1 Hermes deployment running but land every **multi-tenant seam** now (composite-key
schema, per-user path roots, cross-process LLM budget, pluggable notifier); replace Hermes with an
owned thin stack (`python-telegram-bot` + FastAPI + APScheduler) at Phase 2; make scraping and
distillation **shared across users** (scrape once, distil once, rank per-user) — that single
property is what makes 100 users fit on free tiers and a small box. Browser automation: **measure
first, then decide** — instrument which ATSs your real approved jobs land on; harden only
Greenhouse + Lever if they earn it; never run hosted server-side submission for third parties
(§3bis has the full reasoning).

```
        Phase 1 (now, 1 user)                Phase 2 (~100 users, hosted)
 Telegram ◄─► Hermes gateway            Telegram ◄─► python-telegram-bot ┐
              │ MCP / hooks             Web      ◄─► FastAPI             │ two procs,
              ▼                                       │                  │ one DB
        ┌──── cvflow core lib (UNCHANGED across phases) ────┐            │
        │ discovery · analysis · resume · essays · applykit │◄───────────┘
        │ storage (user_id everywhere) · llm (shared budget)│
        └──────────────┬──────────────────────┬─────────────┘
              shared scrape pool         SQLite (WAL):
              (JobSpy + nkparam;         global job_cruxes +
               scrape ONCE for all)      per-user state/prefs/profiles
                Mistral (distil/fit) · Cerebras (tailor/essays) · NIM (chat voice)
```

## §2 Component migration

"When": **P1-seam** = do now while single-user (cheap now, expensive later); P1 = personal-phase
feature; P2 = hosted-phase work. Effort S/M/L.

| Component | Today | Target | When | Effort |
|---|---|---|---|---|
| Chat front-end | Hermes gateway + NIM brain via MCP | keep Hermes for P1; P2 = `python-telegram-bot` v21 long-polling + the NL voice as one NIM chat call with function-calling over a tool registry mirroring today's MCP surface | P2 | M |
| Core lib boundary | already good: `build_tools`/`cron._build` wire everything from config; no singletons | keep the rule: domain modules never read `config.yaml` or fixed paths themselves; all deps injected | standing | — |
| Storage schema | one SQLite; PKs are `job_id` alone; no tenant column | rebuild to composite keys now: `applications`/`jd_analyses` PK `(user_id, job_id)`, `digest_slots` PK `(user_id, slot)`, `decisions` + `user_id` column; every store method takes `user_id: str = "owner"`. `job_cruxes` stays keyed by `(job_id)` — a crux is user-independent and becomes the shared asset | **P1-seam** | M |
| Profile/PII paths | `profile/`, `resume/*.tex` committed in git; paths hardcoded in config | `UserPaths` resolver: `"owner"` → today's config paths (zero behavior change); any other id → `data/users/<id>/{profile,resume,prefs.yaml,tailored}`. PII physically leaves git at P2 | **P1-seam** (resolver), P2 (move) | S |
| LLM rate limiting | per-process window in `NimProvider` — but MCP server, cron, and detached runs are *separate processes sharing the same vendor keys* | SQLite-backed cross-process budget (`SharedRateLimiter`), one row per vendor+minute; `NimProvider` takes an optional limiter. Fixes a real P1 bug and is the P2 pooled-budget enforcement point | **P1-seam** | S |
| Notifier | `HermesNotifier` shells to `hermes send` | `make_notifier(config)`: `notify.backend: hermes\|telegram` (optional key, default hermes); `TelegramNotifier` = stdlib urllib POST to Bot API, never raises. P2 flips the default and adds per-user chat ids | **P1-seam** | S |
| Scheduling | Hermes cron: 120 s script kill (discovery dies daily), schedule only in `~/.hermes/state.db` (not reproducible), `cvflow-learn` unregistered, config schedule fields decorative | P1: detached-spawn wrapper script (mirrors `/discover`'s detach) + `cron print-hermes-schedule` subcommand deriving cron exprs from `config.yaml` + idempotent installer → schedule reproducible from clone, no timeout. P2: APScheduler + a `tasks` table inside the bot process | P1 fix, P2 swap | S / M |
| Discovery pipeline | one user's full pipeline; stages already cleanly separated in `DiscoveryService.discover()` | P2: split into **shared scrape+distil** (union of all users' term×location×site triples, deduped; global crux cache) and **per-user rank** (prefilter → seen-set → gates → fit → digest, all driven by that user's prefs/fingerprint) | P2 | M |
| `/discover` output | full digest + multi-message drop report to chat (too verbose) | full digest + drop report retained at `logs/discover/<timestamp>.md`; chat gets ONE friendly NIM-written summary (≤900 chars), falling back to the full digest on LLM failure | P1 | S |
| Apply workflow end | `fill_application` browser automation (untested vs real portals) | **apply kit**: tailored PDF + grounded answers to the JD's questions (essays module) + applicant instructions + link, formatted for chat; plus a `mark_applied` tool so tracking survives manual application | P1 | M |
| Automation (`automation/`, `auth/`) | built (fixture-tested), never run against a real portal | **defer-and-measure** (§3bis): recon instrumentation now; harden Greenhouse+Lever only if ≥20% of approved jobs land there; archive otherwise. Never hosted for third parties | P1 decision | S–M |
| State machine / gate | un-bypassable `approve()`; `/apply` hook is sole caller | keep in full. Even without automation, `approved` = "user committed to apply" and gates the metered workflow run (tier enforcement reuses the same choke point) | standing | — |
| MCP server / hooks | 12 tools; `cvflow-gate`/`cvflow-discover` hooks | stays while Hermes stays; at P2 deleted — bot command handlers call `cvflow.gate.handle_gate_command` etc. directly (same functions, no Hermes between) | P2 | S |
| `llm.GeminiProvider` | dead code (Gemini dropped) | delete | P1 | S |
| Web front-end | none | FastAPI, separate process, same SQLite (WAL): Telegram-login, digest history, PDF downloads, prefs editor, BYOK key entry | P2 | M |

## §3 The six decisions

**1. Automation: DEFER-AND-MEASURE at P1; apply kit is the shipped path; never hosted server-side
at P2.** Full reasoning in §3bis — this was re-examined deliberately rather than flat-dropped.
One-line rule: instrument the ATS distribution of your real approved jobs for 2–3 weeks; if
Greenhouse+Lever ≥ ~20%, harden exactly those two (gate enforced, disclose-before-submit, proof);
otherwise archive `automation/`+`auth/`. Risk: the recon shows a tempting 19% — resist scope
creep; the bar exists to stop the per-portal maintenance treadmill.

**2. Hermes: KEEP for P1, REPLACE at P2.** It works, it's deployed, and `docs/deploy.md` covers
it; replacing it now is churn. At 100 users it cannot stand: one authorized user, cron killed at
120 s, schedule outside git, config divergence. The P2 stack (`python-telegram-bot` + FastAPI +
APScheduler + direct NIM) reproduces the valued NL voice with one system prompt + function-calling
over the same core lib the MCP server wraps today — cheap **only if** the P1-seams land. Standing
rule meanwhile: no new Hermes-specific logic outside `artifacts/hermes/` and the notifier. Risk:
the conversational loop (multi-turn, clarifications) is real work at P2a — it's budgeted as its own
workstream, not hand-waved.

**3. Multi-tenancy: one SQLite (WAL) + filesystem, schema rebuilt NOW.** Composite PKs
`(user_id, job_id)` cost one idempotent migration today (data is small, tests exist) and erase the
riskiest P2 migration. `job_cruxes` stays global — the crux of a job posting does not depend on who
is looking at it; sharing it is the scaling asset. Per-user `prefs.yaml`/`profile/`/`master.tex`
under `data/users/<id>/` at P2; P1 keeps the committed `profile/` as user `"owner"`. SQLite handles
100 users trivially at this write rate; Postgres is a P3 escape hatch. Risks: tenant leaks — every
store method takes `user_id` explicitly, and a cross-tenant isolation test is part of the P1 seam
work, not P2; write contention — heavy jobs are serialized through the task queue anyway.

**4. LLM cost: pooled free keys until a named threshold breaks; then paid keys routed to paying
users only.** With shared distillation the P2 totals are modest:

| Call | Vendor (free limits) | P2 volume estimate | Breaks when |
|---|---|---|---|
| Distillation (shared) | Mistral mistral-small (5 RPS, ~1B tok/mo) | 300–800 calls/day total | effectively never at ≤100 users |
| Fit ranking (per user) | Mistral | users × 2 cohorts ≈ 200/day | not at 100 users |
| Chat voice + JD analysis | NIM llama-3.3-70b (~40 RPM, no daily cap) | bursty | concurrent chat bursts → queue replies, never parallelize |
| Tailoring + essays | Cerebras gpt-oss-120b (~5 RPM, 2,400/day) | tens/day at ~10 premium | a tailoring rush; first real ceiling |

Cheapest step-up when a ceiling breaks: Mistral pay-as-you-go (mistral-small ≈ $0.1/$0.3 per M
tokens ≈ single-digit $/mo at this scale) and/or a paid Cerebras/other OpenAI-compatible endpoint
for premium tailoring — in both cases it's a per-user `ProviderConfig` swap (base_url+key), zero
code, because everything already routes through `NimProvider`. BYOK uses the same mechanism. Risk:
one abusive user exhausts a pooled key — per-user daily quotas + the shared limiter are P2b launch
requirements, not afterthoughts.

**5. Sourcing: free scraping primary, shared at P2; paid egress only if forced, and not yet.** The
union of users' term×location pairs grows far slower than user count (job-seekers in the same
market want overlapping searches), so one nightly shared scrape plausibly serves 100 users at
roughly today's volume. Failure modes, honestly: datacenter-IP reputation bans (Naukri/Akamai —
already observed; instance restart = new IP recovers it), LinkedIn rate-bans as volume grows,
nkparam key/format rotation (one-line fix class), ToS exposure that grows once this is a product
(accepted, documented risk; never resell raw scraped data). Cheap hardening, in order: pacing
(exists), off-peak scrape window, per-source circuit breaker + a "source down today" notice
(closes a real silent-degradation gap that exists *today* when Naukri 406s), aggressive caching.
First paid step IF forced: a residential/mobile proxy for Naukri traffic only (~$5–8/GB; the JSON
is tiny) — adopt only after a ban persists >1 week across an IP rotation. Held in reserve, not
adopted: hosted job APIs (JSearch/Adzuna class). Risk: a multi-source ban week; the proxy is the
pre-decided insurance line item.

**6. Execution substrate: P1 = fix the current cron in place; P2 = APScheduler + a SQLite `tasks`
table.** P1: a detached-spawn wrapper (the same trick `/discover` already uses) makes the 120 s
Hermes kill irrelevant, and a `print-hermes-schedule` subcommand + idempotent installer makes the
schedule derive from `config.yaml` and survive a fresh clone. P2: APScheduler owns recurring jobs,
a `tasks` table owns on-demand runs (queued/running/done/failed + error), one worker thread
serializes heavy jobs (discovery, tailoring) — which is also the SQLite-contention answer and the
priority-queue hook for premium. No Redis/Celery at ≤100 users; the `tasks` interface is the seam
that makes a real queue a P3 drop-in. Risk: crash kills an in-flight run — systemd `Restart=always`
+ task-state resume on boot.

## §3bis Automation deep-dive (the careful answer)

The founder asked whether auto-apply should be dropped as fragile or pursued as the differentiating
feat. The answer is **neither, yet** — it's a measurement problem first, and a *placement* problem
(server-side vs client-side) second.

### The terrain, portal by portal

| Target | Mechanics | Automatable on this budget? |
|---|---|---|
| **Greenhouse** (`boards.greenhouse.io`, `job-boards.greenhouse.io`) | plain HTML form, stable field names, no login; occasional reCAPTCHA | **Yes** — the single most automatable ATS; detect CAPTCHA → hand off |
| **Lever** (`jobs.lever.co/<co>/<id>/apply`) | plain form POST, stable names, no login; occasional hCaptcha | **Yes**, same class as Greenhouse |
| **Ashby** (`jobs.ashbyhq.com`) | React SPA, public application API | Moderate — only if recon shows volume |
| **Workday / Taleo / SuccessFactors / iCIMS** | account creation, multi-step wizard, per-tenant variation, bot defense | **No.** Hand off, always |
| **Naukri apply** | logged-in, Akamai, datacenter-IP hostile (already proven for search); many listings redirect to company sites anyway | **No** server-side |
| **LinkedIn Easy Apply** | logged-in; LinkedIn aggressively restricts automated accounts (the AIHawk/Auto_Jobs_Applier wave got users banned en masse) | **Never** — burns the user's primary professional account |
| **CAPTCHA anywhere** | reCAPTCHA/hCaptcha on submit | **Never solve programmatically** — paid solvers violate the cost ethos and the line into abuse tooling; policy = pause → manual handoff |

### The blockers, named

1. **ATS heterogeneity** — only the no-login ATSs (Greenhouse/Lever, marginally Ashby) have stable,
   harden-able forms; everything else is a per-tenant maintenance treadmill.
2. **Datacenter-IP reputation** — already bit Naukri search from this box; any server-side browser
   inherits it. Headed+xvfb+stealth helps with fingerprinting, not IP reputation.
3. **Credential custody** — hosted automation for strangers means holding their portal/Google
   credentials. That is a different liability class than holding a résumé. Unacceptable at P2.
4. **Irreversible harm** — a bug that submits a garbled or wrong application damages a real
   person's candidacy with a real employer, unrecoverably. Acceptable risk for the founder on his
   own applications; not a product feature at P2 quality levels.
5. **Scale physics** — one headed Chromium per application on a 2 GB box serves one user, not 100.
6. **ToS/legal** — automated submission violates most boards' terms; tolerable personal risk,
   bad product risk.

### The decision, by phase

- **P1 (founder, own risk): DEFER-AND-MEASURE.** Add zero-browser recon instrumentation: classify
  every presented/approved job's application URL by ATS (domain patterns + redirect resolution),
  persist it, report the distribution after 2–3 weeks of real digests. **Go/no-go rule: if
  Greenhouse+Lever ≥ ~20% of *approved* jobs, execute the scoped hardening** (those two ATSs only;
  gate asserted at entry; every composed answer disclosed before submit; proof captured; manual
  handoff everywhere else — i.e., original Phase 12's reframe, narrowed further). If under the bar,
  archive `automation/` + `auth/` and remove their MCP tools; the apply kit covers 100% of jobs
  either way. This converts an opinion war into one number.
- **P2 (hosted): NO server-side submission for third parties**, regardless of the P1 outcome
  (blockers 2–6 don't soften with success). Tier copy says "apply kit", never auto-apply.
- **P3 (the honest path to the feat): a client-side browser extension** that autofills application
  forms from the user's cvflow data **in the user's own browser, on their IP, with the user
  clicking submit**. This dissolves every blocker at once: no credential custody, no bot detection
  (it's a real human's browser), no liability transfer (they click), no server compute. This is the
  proven model in the market (Simplify-style). It is real engineering (a third front-end) — design
  nothing now, but know it's the destination if automation demand materializes.
- **The gate stays in full** in every branch. With automation: it's the safety invariant. Without:
  `approved` is the deliberate "I'm applying to this" commitment that triggers the metered workflow
  run — the same deterministic choke point becomes the tier-enforcement point at P2.

## §4 Build-plan & expectations review

| Item | Verdict | Why |
|---|---|---|
| Discovery engine (gates-in-code, LLM-ranks-only, crux cache, drop-footer) | KEEP | the product core; P2-ready once scrape/distil is shared |
| Résumé tailoring (unit-selection, no-new-facts line-subset check, Tectonic) | KEEP | the premium feature; already deterministic and safe |
| Never-fabricate + never-fail-silently invariants | KEEP | they become brand promises with customers |
| Essays/clarification loop | KEEP | becomes the apply-kit answer generator |
| Approval gate (deterministic, human-only) | KEEP | safety invariant if automation lives; tier/commitment choke point either way |
| `docs/deploy.md` single-user deploy-anywhere | KEEP | it *is* Phase 1, and the future self-hosted story for technical users |
| Phase 12 (assisted-apply hardening) as written | CHANGE | superseded by §3bis defer-and-measure: recon first, GH+Lever-only hardening behind a ≥20% bar, archive otherwise |
| Zero-cost rule | CHANGE | "free until a named tier threshold breaks; paid keys for paying users only"; §3.4/§3.5 thresholds are the codified triggers |
| Single-tenant assumptions (profile in git, one config, one Telegram user, job_id PKs) | CHANGE | fine to *operate* at P1; the P1-seams remove them from the *schema and code* now |
| Hermes as substrate (Phases 7/11) | CHANGE | keep at P1; replaced at P2a |
| Weekly learning / analytics (`decisions` rollups) | KEEP | becomes a per-user retention feature at P2 |
| MCP layer + hooks | CHANGE | lives exactly as long as Hermes does |
| `GeminiProvider`, unused discovery `brain` wire | DROP | dead code; delete in P1 cleanup |
| OTP coordinator / OAuth vault | CHANGE | automation-era; archived or kept per the §3bis branch (vault outlives it — BYOK keys reuse Fernet at P2) |
| Under-built for P2: onboarding, per-user quotas, web UI, payments, admin tooling | — | all P2 plan workstreams; build none at P1 |

## §5 Roadmap (pointer)

The executable detail lives in the two plan docs; the shape:

1. **P1 — personal hardening + seams** (`2026-06-10-phase-1-personal-hardening.md`):
   cron reproducibility + 120 s fix → digest summarization → apply kit + `mark_applied` →
   multi-tenant seams (schema rebuild, UserPaths, shared limiter, notifier flag) → ATS recon →
   automation go/no-go. *Exit: a fresh clone reproduces the full daily cycle from one install
   script; chat gets friendly summaries; a second synthetic user passes an isolation test; the
   automation decision is made on data.*
2. **P2 — hosted service** (`2026-06-10-phase-2-hosted-service.md`):
   P2a substrate swap (bot+scheduler+NL voice, Hermes retired) → P2b multi-tenant launch
   (onboarding, shared discovery, quotas, PII out of git) → P2c tiering + web + manual payments.
   *Exit per stage in the plan.*
3. **P3 — only on proven demand:** bigger box, Postgres/real queue if metrics demand, paid
   scraping egress if bans force it, the client-side extension if automation demand is real.

## §6 /discover summarization (P1 feature spec)

In `cron.run_job("discover")`, after `format_digest`: (1) write the full digest + the grouped drop
report to `logs/discover/<UTC timestamp>.md` — written *before* any LLM call so retention never
depends on one; (2) ONE call to **NIM llama-3.3-70b** (no daily cap; saves Cerebras quota) via the
existing `NimProvider`: input = the rendered digest + the `_dropped` counts; instructions = warm,
≤10 lines, counts per cohort, top 3 jobs as `n. role @ company — pay — fit`, one-line drop note,
remind `/apply n`, **use the digest's exact numbering and never invent jobs**; (3) send the summary
(+ a "full report: logs/discover/….md" tail) through the notifier. On any LLM failure send the full
digest instead — never silent, never lost. Ordinals always come from `digest_slots`.

## §7 Open questions (not resolvable from the repo)

- **Free-tier ToS:** do NIM/Mistral/Cerebras terms permit serving third-party end users from one
  account? Checked as a P2 precondition; if disallowed, the free tier launches BYOK-only or paid
  keys arrive earlier. (BYOK existing as a tier makes this survivable either way.)
- **Real distillation token volume per run** — aggregate existing logs to confirm the §3.4 headroom
  math before P2b.
- **LinkedIn tolerance** at union-of-users scrape volume — no data beyond single-user runs.
- **Launch audience geography** (India-centric like the founder, or broader?) — drives how small the
  shared-scrape union stays and whether FX/location scoring stays deferred.
- **Distribution intent** (invite-only vs public) — sets how much onboarding abuse-protection P2b
  needs.
