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
mkdir -p ~/.hermes/scripts
cp artifacts/hermes/scripts/cvflow-*.sh ~/.hermes/scripts/ && chmod +x ~/.hermes/scripts/cvflow-*.sh

hermes cron create '0 8 * * *'  --no-agent --script cvflow-discover.sh  --name cvflow-discover
hermes cron create 'every 5m'   --no-agent --script cvflow-sweep-otp.sh --name cvflow-sweep-otp
hermes cron create 'every 30m'  --no-agent --script cvflow-heartbeat.sh --name cvflow-heartbeat
hermes cron list
```
- `0 8 * * *` = daily 08:00 (`schedule.daily_discovery_time`); `every 30m` ↔
  `schedule.heartbeat_interval_minutes`.
- **`cvflow-sweep-otp` (every 5 min) enforces OTP timeouts** (`OtpCoordinator.expire_overdue`) —
  this is what makes the Phase-10 OTP timeout actually fire while idle.
- `cvflow-discover` posts the daily digest (each job with its URL + `/apply <id>` / `/skip <id>`).

Manual run of any job: `cd /home/ubuntu/cvflow && .venv/bin/python -m cvflow.cron heartbeat`.

## Security posture
Unprivileged `ubuntu` user; `chmod 600` on `config.yaml`, the Fernet key, and encrypted blobs;
burner Google account for SSO; host firewall closed to inbound (Telegram is outbound long-polling).
Profile PII is intentionally committed to this PRIVATE repo; credentials never are.
