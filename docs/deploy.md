# cvflow Deployment (Ubuntu / EC2)

## Runtime
Hermes runs under systemd as `hermes-gateway.service` (`User=ubuntu`, `Restart=always`); the unit
source is tracked at `artifacts/hermes/hermes-gateway.service`. While the gateway runs,
`hermes cron` jobs fire automatically (`hermes cron status` → "Gateway is running — cron jobs will
fire automatically"), so no separate tick timer is needed.

```bash
systemctl status hermes-gateway        # running?
sudo systemctl restart hermes-gateway  # after editing ~/.hermes/config.yaml
```

## Secrets / config (never committed)
`config.yaml` (repo root) + `~/.hermes/.env` hold the tokens. Required: `telegram.bot_token`,
`telegram.authorized_user_id`, `llm.brain.api_key` (NVIDIA NIM `nvapi-…`), `llm.tailoring.api_key`
(Gemini `AIza…`). `chmod 600 config.yaml`. The Fernet key (`security.fernet_key_path`, default
`data/.fernet_key`) is auto-created `chmod 600` on first run.

## MCP tool allowlist
`~/.hermes/config.yaml` → `mcp_servers.cvflow.tools.include` must list all 12 cvflow tools:
`ping, discover, analyze_jd, list_applications, get_application, request_review, submit,
compose_essay, status_report, fill_application, resume_application, submit_otp`.
It must NEVER include `approve` — the approval gate is the `/apply` hook + plugin under
`artifacts/hermes/` (installed to `~/.hermes/{hooks,plugins}/`), enforced outside the agent loop.
Verify: `hermes mcp test cvflow` → "Tools discovered: 12", no `approve`. After editing the live
config, reload the gateway so it re-reads the server (`sudo systemctl restart hermes-gateway`).

## Command hooks + plugins (deterministic, off the agent loop)
cvflow's slash commands run as Hermes **command hooks** in the gateway process, *outside* the
brain — the gate (`/apply`, `/skip`) and the manual discovery trigger (`/discover`). Each is a
`hooks/<name>/` (the handler) plus a `plugins/<name>/` (registers the command so the gateway
fires the hook). Install both into the live Hermes dirs and enable the plugins:
```bash
cp -r artifacts/hermes/hooks/cvflow-gate       ~/.hermes/hooks/cvflow-gate
cp -r artifacts/hermes/plugins/cvflow-gate     ~/.hermes/plugins/cvflow-gate
cp -r artifacts/hermes/hooks/cvflow-discover   ~/.hermes/hooks/cvflow-discover
cp -r artifacts/hermes/plugins/cvflow-discover ~/.hermes/plugins/cvflow-discover
# ~/.hermes/config.yaml → plugins.enabled must list BOTH:
#   plugins:
#     enabled:
#     - cvflow-gate
#     - cvflow-discover
sudo systemctl restart hermes-gateway
```
Verify in the gateway log: `Loaded hook 'cvflow-gate' for events: ['command:apply', 'command:skip']`
and `Loaded hook 'cvflow-discover' for events: ['command:discover']`. The hooks import cvflow from
`$CVFLOW_ROOT` (default `~/cvflow`) at runtime — set `CVFLOW_ROOT` in `~/.hermes/.env` if the repo
lives elsewhere.
- **`/apply` / `/skip`** route to `cvflow.gate.handle_gate_command` (the sole `approve()` caller).
  NEVER add an `approve` command/tool — the gate is human-only by construction.
- **`/discover`** triggers a discovery run on demand: it spawns a detached
  `python -m cvflow.cron discover --progress` and acks instantly, then streams per-stage progress,
  the digest, and ONE LLM-summarized note of the dropped jobs to the chat (the full digest +
  per-job drop report is always retained to `logs/discover/<timestamp>.md`; the daily cron run
  stays digest-only). Tuned via `discovery.{log_dir,summarize_drops,drop_summary_provider,
  drop_summary_max_chars,drop_summary_samples_per_bucket,cron_sends_drops}` in `config.yaml`.
  A `fcntl` run lock (`data/discover.lock`) guarantees the daily cron and a manual `/discover` never
  double-run.

## Headed browser under xvfb (stealth, prod only)
Tests run headless. For production stealth set `automation.headless: false` and give the gateway a
virtual display:
```bash
sudo apt-get install -y xvfb
Xvfb :99 -screen 0 1920x1080x24 &        # or a small systemd unit
# add to the gateway unit (drop-in) so automation inherits it:
#   Environment="DISPLAY=:99"
```

## Scheduled jobs (deterministic, no agent loop)
Each job runs `python -m cvflow.cron <job>` via a `--no-agent` wrapper and notifies Telegram through
`hermes send` (the wrapper prints nothing, so cron does not double-deliver).
```bash
# One-time: let the user manager persist across sessions so detached discovery runs (launched as
# transient user units) survive a `systemctl restart hermes-gateway`. Without this they fall back
# to a plain detached run that escapes the 120 s kill but not a gateway restart.
loginctl enable-linger "$USER"

# Reproduce the entire cron schedule from config.yaml (idempotent — safe to re-run).
# Copies the cvflow-*.sh scripts into ~/.hermes/scripts/, clears any old cvflow-* jobs, and
# re-creates all four (discover / sweep-otp / heartbeat / learn) with schedules derived from
# schedule.daily_discovery_time + schedule.timezone (converted to UTC) and
# schedule.heartbeat_interval_minutes.
scripts/install-hermes-cron.sh
```
- The schedule is **derived from `config.yaml`**, not typed by hand: the daily time comes from
  `schedule.daily_discovery_time` + `schedule.timezone` (Hermes interprets cron in UTC and the box
  runs UTC, so e.g. `12:00 Asia/Kolkata` → `30 6 * * *`), and the heartbeat from
  `schedule.heartbeat_interval_minutes`. Inspect the lines before they run with
  `.venv/bin/python -m cvflow.cron print-hermes-schedule`.
- The daily `cvflow-discover` run is **detached** — the script spawns the 10–25 min discovery and
  returns in under a second, so Hermes' ~120 s `--no-agent` script kill never interrupts it; the
  digest arrives from the detached process. Verify registration with `hermes cron list` (expect all
  four `cvflow-*` jobs, including `cvflow-learn`).
- **`cvflow-sweep-otp` (every 5 min) enforces OTP timeouts** (`OtpCoordinator.expire_overdue`) —
  this is what makes the Phase-10 OTP timeout actually fire while idle.
- `cvflow-discover` posts the daily digest (numbered; reply `/apply 1 2 4` / `/skip 3` / `/apply all`).
- **`cvflow-learn` (weekly) proposes preference edits** from apply/skip history — it only suggests;
  the user applies them by editing `profile/preferences.md` (never auto-applied, like the gate).

Manual run of any job: `cd /home/ubuntu/cvflow && .venv/bin/python -m cvflow.cron heartbeat`.

## Security posture
Unprivileged `ubuntu` user; `chmod 600` on `config.yaml`, the Fernet key, and encrypted blobs;
burner Google account for SSO; host firewall closed to inbound (Telegram is outbound long-polling).
Profile PII is intentionally committed to this PRIVATE repo; credentials never are.
