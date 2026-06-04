# artifacts/

Operational artifacts that live **outside** the repo on the running EC2 host but
are needed to reproduce the deployment on a fresh instance. Tracked here so the
system is recoverable beyond a plain `git clone`.

> **Invariant 5 (hard):** credentials never enter git. Everything in this
> directory has been verified **secret-free**. Real secrets (API keys, bot
> tokens, OAuth tokens, the Fernet key) stay only in gitignored locations on the
> host and must be re-supplied out-of-band on a new instance — never committed.

## Contents

### `hermes/` — the Hermes Agent runtime config

| File | What it is | Secrets? |
|---|---|---|
| `hermes-config.yaml` | Snapshot of `~/.hermes/config.yaml`. Holds the cvflow MCP-server wiring (`mcp_servers.cvflow`), the `disabled_toolsets` latency trim, the NIM brain config, and `telegram.allowed_chats`. | None. Every `api_key` field is empty (keys come from `.env`); `record_key: ctrl+b` is a voice keybind, not a credential. Contains the authorized Telegram chat ID (PII, intentionally fine in this private repo). |
| `hermes-env.template` | Every variable **name** from `~/.hermes/.env` with **values stripped**. The shape of the secret set to fill on a new box. | None — names only, all values blank. |
| `hermes-gateway.service` | The systemd unit running the gateway (`/etc/systemd/system/hermes-gateway.service`). Auto-restart, runs as `ubuntu`. | None. |
| `SOUL.md` | Hermes agent persona/identity file. | None. |
| `hooks/cvflow-gate/` | The **approval-gate hook** (`HOOK.yaml` + `handler.py`). Fires on `command:apply` / `command:skip`, routes to `cvflow.gate.handle_gate_command` (the sole `approve()` caller) OUTSIDE the agent loop, returns `decision:"handled"`. The verb is `/apply` (not the Hermes built-in `/approve`) so there is no collision. | None — imports cvflow + reads the host's gitignored `config.yaml` at runtime. |
| `plugins/cvflow-gate/` | Plugin (`plugin.yaml` + `__init__.py`) registering `/apply` and `/skip` so they become known commands (the hook then fires). Enabled via `plugins.enabled: [cvflow-gate]` in the config. | None. |

## Restoring on a new instance

1. **Provision** an Ubuntu box (the current host is a t3.small / 2 vCPU / 30 GB; SSH only, Telegram is outbound long-polling so no inbound HTTP needed).
2. **Clone cvflow** and set it up:
   ```bash
   git clone git@github.com:satwikbasu/cvflow.git && cd cvflow
   python3 -m venv .venv && source .venv/bin/activate
   pip install -e .                         # makes `cvflow` importable for the MCP subprocess
   cp config.example.yaml config.yaml       # then fill in the real secrets (gitignored)
   # populate the Fernet key referenced by security.fernet_key_path
   ```
   `profile/`, `resume/*.tex`, and `form_fields.json` come with the clone (PII, no secrets).
3. **Install Hermes** (single-curl per Nous docs) into `~/.hermes`.
4. **Restore the Hermes config:**
   ```bash
   cp artifacts/hermes/hermes-config.yaml ~/.hermes/config.yaml
   cp artifacts/hermes/hermes-env.template ~/.hermes/.env   # then fill EVERY real value:
   #   NVIDIA_API_KEY, TELEGRAM_BOT_TOKEN, GEMINI/Google key, etc.
   chmod 600 ~/.hermes/.env
   ```
   Adjust absolute paths in `hermes-config.yaml` (`mcp_servers.cvflow.command`/`cwd`)
   if the repo lives somewhere other than `/home/ubuntu/cvflow`.
5. **Install the approval-gate hook + plugin:**
   ```bash
   cp -r artifacts/hermes/hooks/cvflow-gate   ~/.hermes/hooks/cvflow-gate
   cp -r artifacts/hermes/plugins/cvflow-gate ~/.hermes/plugins/cvflow-gate
   # config already enables it (plugins.enabled: [cvflow-gate]). If the repo
   # is NOT at ~/cvflow, set CVFLOW_ROOT=/path/to/cvflow in ~/.hermes/.env
   # (the hook reads it; default is ~/cvflow).
   ```
6. **Install the gateway service:**
   ```bash
   sudo cp artifacts/hermes/hermes-gateway.service /etc/systemd/system/
   sudo systemctl daemon-reload && sudo systemctl enable --now hermes-gateway
   ```
7. **Verify:** `hermes mcp test cvflow` → 9 tools, no `approve`; gateway log shows
   `Loaded hook 'cvflow-gate' for events: ['command:apply', 'command:skip']`; send
   `mcp_cvflow_ping` from the authorized Telegram chat → `{"status":"ok","service":"cvflow"}`.
   Then `/apply <job_id>` on a pending-review job → `✅ Approved …` and status flips
   to `approved`.

## Deliberately NOT tracked (secrets / runtime state)

- `~/.hermes/.env` (real values) — the only secret store: NIM + Telegram + Gemini keys.
- cvflow `config.yaml` — gitignored; reproduce from `config.example.yaml` + secrets.
- `data/` (SQLite tracking DB, generated PDFs), `logs/` — runtime state, regenerated.
- Fernet key, OAuth tokens, browser `storage_state`, `~/.hermes/auth.json`, sessions.

Keep this snapshot in sync when the Hermes config or service unit changes
materially (e.g. re-enabling `browser`/`computer_use` for Phase 9, `cronjob` for
Phase 11).
