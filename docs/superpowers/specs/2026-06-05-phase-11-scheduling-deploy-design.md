# Phase 11 — Scheduling + daemon wiring + systemd deploy — Design

Date: 2026-06-05
Status: approved (brainstorm), pre-implementation
Related: build plan Phase 11 (Goal 10); `[[hermes-runtime-ops]]`, `[[approval-gate-wiring]]`;
closes the Phase-10 carry-forward (wire the OTP sweep).

## Goal
Make cvflow run unattended: a daily discovery digest, a recurring OTP-timeout sweep, and a
heartbeat — all pushed to Telegram — scheduled by Hermes's own scheduler, with the deployment
(systemd + xvfb + cron jobs + tool allowlist) documented in `docs/deploy.md`.

## Decisions (locked)
- **Deterministic `cvflow.cron` entrypoint** runs each scheduled job directly (no agent loop); the
  brain is only involved later when the user replies to select/approve.
- **Notifications via `hermes send`** (no LLM, no running gateway required for Telegram) — one
  notification path for all cvflow-originated notices (mid-flow crash/OTP + scheduled digest/
  heartbeat/sweep).
- **`hermes cron` fires jobs automatically** while the gateway runs (verified: `hermes cron status`
  → "Gateway is running — cron jobs will fire automatically"). No separate tick timer needed.

## Components

### 1. `src/cvflow/notify.py` — `HermesNotifier` (replaces the logging-only stub)
- Callable: `HermesNotifier(target="telegram", runner=subprocess.run)`; `__call__(message: str)`
  runs `hermes send --to <target> --quiet <message>`.
- **Never raises** (invariant 3 must not become "crash the caller"): on a non-zero exit or an
  `OSError`/`CalledProcessError`, it logs the failure and returns. The send is the primary path; the
  log is the safety net. Injectable `runner` for tests.
- Replaces `_telegram_notify` in `build_tools`, so `Automator` crash alerts and `OtpCoordinator`
  OTP request/expiry notices now actually reach Telegram.

### 2. `src/cvflow/cron.py` — deterministic pipeline entrypoint (`python -m cvflow.cron <job>`)
- `format_digest(ranked) -> str` — pure, unit-tested. Each job: title @ company, **URL** (user
  requirement), one-line rationale, and `/apply <job_id> | /skip <job_id>`. Empty list →
  `"No new jobs today."` (never silent).
- `run_job(job, *, store, discovery, otp, notify) -> None` — pure dispatch (testable with fakes):
  - `discover` → `discovery.discover()` → `notify(format_digest(...))`.
  - `sweep-otp` → `otp.expire_overdue()` (per-expiry notices fire inside via the notifier).
  - `heartbeat` → `notify("💓 cvflow alive — " + per-status counts from store.list_by_status)`.
- `main(argv=None)` — parse the job name, build the minimal services from `config.yaml`
  (`ApplicationStore`, `NimProvider`→`LLMRanker`→`DiscoveryService`, `OtpCoordinator`,
  `HermesNotifier`), call `run_job`. **Does NOT build `SessionManager`/`Automator`** — a sweep must
  not launch Chromium.

### 3. Scheduling (ops — `hermes cron`, tracked wrapper scripts)
- Thin wrappers under `artifacts/hermes/scripts/` (installed to `~/.hermes/scripts/`):
  `cvflow-discover.sh`, `cvflow-sweep-otp.sh`, `cvflow-heartbeat.sh`, each `exec`-ing
  `/home/ubuntu/cvflow/.venv/bin/python -m cvflow.cron <job>` from the repo dir. They print nothing
  (the entrypoint sends via the notifier), so cron stays silent and there is no double-delivery.
- Created with `hermes cron create <schedule> --no-agent --script <wrapper>` — discovery at
  `schedule.daily_discovery_time`, `sweep-otp` every 5 min, `heartbeat` every
  `schedule.heartbeat_interval_minutes`. Documented in `docs/deploy.md`.

### 4. Fix the stale tool allowlist
Update `mcp_servers.cvflow.tools.include` in **both** the tracked
`artifacts/hermes/hermes-config.yaml` and the live `~/.hermes/config.yaml` to the full current set:
`ping, discover, analyze_jd, list_applications, get_application, request_review, submit,
compose_essay, status_report, fill_application, resume_application, submit_otp` — **still no
`approve`**. Without this the brain cannot drive the Phase 8–10 features live.

### 5. `docs/deploy.md`
Document: the (already-running) `hermes-gateway.service` systemd unit; xvfb for the headed browser
(`Xvfb :99` + `DISPLAY=:99`, set `automation.headless: false` in prod for stealth); the
unprivileged-user / `chmod 600` / burner-account posture; the `hermes cron create` commands; the
tool allowlist; the `.env`/`config.yaml` secret checklist. Satisfies the exit criterion.

## Error handling (invariant 3)
The notifier never raises; cron jobs always emit a message (digest, "no new jobs", heartbeat, or an
expiry notice). A failed `hermes send` is logged.

## Testing (no network, no browser, no live Hermes)
- `HermesNotifier`: builds the exact argv; success path; `runner` raising / non-zero exit → logged,
  not raised (mock `runner`).
- `format_digest`: every job's URL + `/apply`/`/skip` present; empty → `"No new jobs today."`.
- `run_job`: `discover` calls `discovery.discover` then `notify` with the digest; `sweep-otp` calls
  `otp.expire_overdue`; `heartbeat` notifies with status counts — all with injected fakes.
- Ops smoke (manual, documented): one real `hermes send` test message; `hermes cron list` shows the
  three jobs.

## Exit criteria (build plan)
Hermes runs the daily schedule and sends a heartbeat to Telegram; the systemd unit is documented in
`docs/deploy.md`. (Plus: the OTP sweep is now scheduled — Phase-10 carry-forward closed.)

## Accepted trade-offs
1. **Template digest, not brain-composed** — deterministic, cheap, resilient if the brain is flaky;
   conscious choice.
2. **Notifier depends on the `hermes` CLI on PATH** (true on this host); the failure path logs.
3. **Live OTP-page *detection* still deferred** to Phase 12 — Phase 11 schedules the sweep (the
   timeout half) but the trigger that *starts* an OTP wait on a real site is the dry-run's concern.
4. **xvfb/headed-browser config is documented, not code** — it's host setup; the automation code is
   already headless-capable for tests.

## Out of scope (Phase 12)
The end-to-end dry run on a throwaway account; live OTP-page detection; any anti-bot hardening.
