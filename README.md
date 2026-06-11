# cvflow

Autonomous, self-hosted job-application agent. Runs continuously on an Ubuntu server and is operated **entirely through a Telegram chat** — no dashboard, no SSH, no commands during normal use.

Each day it discovers and ranks job postings against your profile, shows you the top 5–10, and for the ones you pick it analyzes the JD, tailors your LaTeX resume, and — **only after you approve in chat** — drives the application via browser automation. Form-filling is **assisted, not magic**: the bot fills what it can, pauses to ask you (OTPs, clarifications, fields it can't resolve), and hands off gracefully where a portal is undriveable — it never submits without your approval. It tracks every application's status.

**Hard rule: zero per-call / SaaS cost.** Everything is open-source, free-tier, or self-hosted; the only ongoing cost is the server.

See **[CLAUDE.md](CLAUDE.md)** for the full goals, tech stack, architecture, and conventions, and the **[build plan](docs/superpowers/plans/2026-06-03-cvflow-build-plan.md)** for the phase roadmap and decision log.

## Architecture
**Hermes Agent** (Nous Research, self-hosted) is the always-on substrate — it provides the Telegram interface, scheduling, the LLM brain, and the browser runtime. cvflow supplies **deterministic domain skills** that Hermes calls over a local **stdio MCP server** (`src/cvflow/mcp/`): discovery, JD analysis, resume tailoring, storage, and the approval gate.

- **Brain:** **Mistral `mistral-small-2506`** (free tier; fast, native tool-calling), configured in `~/.hermes/config.yaml` (`provider: custom`, `base_url: https://api.mistral.ai/v1`, `MISTRAL_API_KEY` in `~/.hermes/.env`). Mistral also runs discovery distill/fit + `analyze_jd`/`learn`; **Cerebras `gpt-oss-120b`** does résumé tailoring, essays, and drop-summaries. (NVIDIA NIM and Gemini were removed — NIM's free-tier latency hit minutes/turn.)
- **The approval gate is deterministic code, not a convention.** The MCP surface exposes `request_review` and `submit` (which asserts `status == approved` and raises otherwise) but **no `approve` tool** — only the human Telegram approval callback can reach `statemachine.approve()`. The agent can *call* the gate but can never *satisfy* it.
- **Facts come only from `profile/`** — unanswerable fields trigger a clarification loop, never a guess. Nothing fails silently.

## Status
Implemented and operating on the EC2 host via Hermes; **Phases 0–11 + 13 + 14 complete** (282 tests, `ruff` + `mypy --strict` clean). 12 MCP tools live (no `approve` — the gate is human-only), plus deterministic slash commands off the agent loop: `/apply`·`/skip` (the gate) and `/discover` (manual discovery trigger). **Phase 12 is superseded by the [Phase-1 personal-hardening plan](docs/superpowers/plans/2026-06-10-phase-1-personal-hardening.md)** — shipped so far: **P1A** (config-derived, restart-proof cron via `systemd-run --user`), **P1B** (chat gets a friendly digest summary; full digest + drops retained to `logs/discover/`), the **Mistral brain switch** (NIM dropped for latency), and MCP/gate fixes (compact paginated `list_applications`/`get_application`, gate DB-path fix). Next up: 1C–1F.

| ✓ | Phase | Delivers |
|---|---|---|
| ✅ | 0 Foundations | config loading, logging, test/lint/type harness |
| ✅ | 1 Storage + state machine | SQLite tracking store + the un-bypassable approval gate |
| ✅ | 2 LLM layer | OpenAI-compatible client — Mistral (brain/distill/fit/analyze) + Cerebras (tailoring/essays/drops) |
| ✅ | 3 Knowledge base | `profile/**` + `form_fields.json` loader |
| ✅ | 4 Discovery | JobSpy search → dedup → LLM ranking → top-N (+ salary filter) |
| ✅ | 5 JD analysis | fetch JD → extract skills/quals/seniority/tone/applicant-instructions |
| ✅ | 6 Resume tailoring | reorder + select ≤2 projects, no-new-facts check, diff, Tectonic compile |
| ✅ | 7 Hermes integration | cvflow skills exposed as a live stdio MCP server |
| ✅ | 8 Approval-gate skill + chat flows | review message (PDF/diff), `/apply`/`/skip`, digest, OTP, clarification, essays |
| ✅ | 9 Browser automation | Playwright form-fill, uploads, pause/resume, proof capture (code done; untested vs real portals) |
| ✅ | 10 Auth | Fernet-encrypted token vault + email-OTP coordinator |
| ✅ | 11 Scheduling + deploy | Hermes cron (discover/sweep-otp/heartbeat) + systemd gateway unit (live) |
| ✅ | 13 Relevance + apply ergonomics + learning | preferences engine, `/apply 1 2 4` by number, weekly preference proposals |
| ✅ | 14 Discovery relevance v2 | distil → benchmark, 💰/📋 (pay/no-pay) cohorts, deterministic seniority/skill gates, drop-reason footer, Naukri adapter; manual `/discover` trigger |
| ☐ | 12 Assisted-apply hardening + dry run | recon real portals → harden top 1–2 ATSs → graceful manual handoff; gate-blocked dry run |

## Develop
```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"                  # runtime + pytest/ruff/mypy
playwright install chromium              # (for Phase 9 automation)
# resume compilation uses Tectonic (installed at /usr/local/bin/tectonic)
cp config.example.yaml config.yaml       # then fill in secrets (gitignored)

pytest                                    # all tests
ruff check . && mypy src                  # lint + type-check
```
`profile/` and `resume/*.tex` are real, committed data in this private repo (PII, no secrets), so a clone deploys without templates.

## Deploy / migrate to a new instance
The system is recoverable beyond a plain `git clone`: secret-free runtime artifacts
(the Hermes config, the systemd gateway unit, a values-stripped `.env` template) are
tracked under **[`artifacts/`](artifacts/README.md)**. Real secrets never enter git
(invariant 5) — they're re-supplied on the new box from the templates.

1. **Provision** an Ubuntu host (current: t3.small / 2 vCPU / 30 GB; SSH only — Telegram is outbound long-polling, no inbound HTTP needed).
2. **Clone + set up cvflow:**
   ```bash
   git clone git@github.com:satwikbasu/cvflow.git && cd cvflow
   python3 -m venv .venv && source .venv/bin/activate
   pip install -e .                      # runtime deps (incl. pycryptodome for Naukri) +
                                         # makes `cvflow` importable for the MCP subprocess
   # To run the test suite / the docs/HANDOFF.md verification prompt on the box, instead use:
   #   pip install -e ".[dev]"           # adds pytest + ruff + mypy
   playwright install chromium           # browser automation (Phase 9)
   cp config.example.yaml config.yaml    # fill in real secrets (gitignored)
   # add the Fernet key referenced by security.fernet_key_path
   ```
   `profile/`, `resume/*.tex`, and `form_fields.json` come with the clone (PII, no secrets).
3. **Install Hermes** (single-curl, per Nous docs) into `~/.hermes`.
4. **Restore the Hermes runtime** from the tracked artifacts:
   ```bash
   cp artifacts/hermes/hermes-config.yaml ~/.hermes/config.yaml
   cp artifacts/hermes/SOUL.md            ~/.hermes/SOUL.md     # brain persona + the URL-verbatim rule
   cp artifacts/hermes/hermes-env.template ~/.hermes/.env       # fill the secrets you use:
   #   MISTRAL_API_KEY (the BRAIN — same value as cvflow's config.yaml llm.distillation.api_key),
   #   TELEGRAM_BOT_TOKEN, TELEGRAM_ALLOWED_USERS, …
   chmod 600 ~/.hermes/.env
   # adjust absolute paths in hermes-config.yaml if the repo isn't at /home/ubuntu/cvflow
   ```
   The brain is **Mistral `mistral-small-2506`** (`model.provider: custom`, `base_url:
   https://api.mistral.ai/v1`); Hermes derives `MISTRAL_API_KEY` from the host. **Three gotchas
   that silently break a fresh box:**
   - **Never leave a numeric var present-but-empty** (e.g. `TERMINAL_TIMEOUT=`,
     `BROWSER_SESSION_TIMEOUT=`). Hermes does `int("")` on them and the gateway crashes at
     startup. Either give a value or **comment the line out** (the shipped template already does).
   - `agent.tool_use_enforcement: **force**` is required so the brain emits native tool-calls
     (with `auto`, weaker models leak raw `{"name":…}` JSON + double-call). Shipped as `force`;
     it works with Mistral on a **clean** session.
   - **After any brain-provider change, send `/new` in Telegram.** A session persisted under the
     old brain replays old-format tool-call IDs that Mistral rejects (`Tool call id … length 9`),
     so every turn 400s until you start a fresh session.
5. **Install the command hooks + plugins** (the deterministic slash commands that run *outside*
   the brain: the `/apply`/`/skip` approval gate and the manual `/discover` trigger):
   ```bash
   mkdir -p ~/.hermes/hooks ~/.hermes/plugins   # ⚠ plugins/ may not exist yet — cp into a
                                                # missing dir fails; create targets before copying
   cp -r artifacts/hermes/hooks/cvflow-gate       ~/.hermes/hooks/cvflow-gate
   cp -r artifacts/hermes/plugins/cvflow-gate     ~/.hermes/plugins/cvflow-gate
   cp -r artifacts/hermes/hooks/cvflow-discover   ~/.hermes/hooks/cvflow-discover
   cp -r artifacts/hermes/plugins/cvflow-discover ~/.hermes/plugins/cvflow-discover
   # ~/.hermes/config.yaml → plugins.enabled must list both cvflow-gate AND cvflow-discover
   # (the shipped hermes-config.yaml already does). Hooks import cvflow from $CVFLOW_ROOT
   # (default ~/cvflow) at runtime — set it in ~/.hermes/.env if the repo lives elsewhere.
   ```
   Detail + verification in [`docs/deploy.md`](docs/deploy.md) and [`artifacts/README.md`](artifacts/README.md).
6. **Install the gateway service:**
   ```bash
   sudo cp artifacts/hermes/hermes-gateway.service /etc/systemd/system/
   sudo systemctl daemon-reload && sudo systemctl enable --now hermes-gateway
   ```
7. **Verify the wiring.** Paste the prompt in **[`docs/HANDOFF.md`](docs/HANDOFF.md)** into a
   Claude Code session on the box — it checks the brain is actually routing through Mistral
   (the #1 fresh-box failure), the gateway/MCP registration (12 tools, no `approve`), and the
   suite. Quick manual check: `hermes mcp test cvflow` → 12 tools; from the authorized Telegram
   chat send `/new` then `give me the cvflow status report` → it calls `mcp_cvflow_status_report`
   and replies in seconds (not minutes). Then `/discover` → `🔎 Discovery started …` and
   `/apply <id>` on a pending-review job → `✅ Approved …`.
8. **Personalize to your own profile** (optional, when ready): follow
   **[`docs/onboarding-new-candidate.md`](docs/onboarding-new-candidate.md)** to regenerate the
   `profile/` knowledge base, `candidate_skills.yaml`, the résumé, and the `config.yaml`
   discovery/preferences blocks for a new candidate.

Full detail and the list of what's deliberately *not* tracked is in [`artifacts/README.md`](artifacts/README.md).

### Changing the schedule / reloading cron + config
- **Daily discovery time / timezone / heartbeat interval** → edit `config.yaml`
  `schedule.{daily_discovery_time,timezone,heartbeat_interval_minutes}`, then re-register
  (no gateway restart):
  ```bash
  .venv/bin/python -m cvflow.cron print-hermes-schedule   # dry-run; fails loudly on bad config
  scripts/install-hermes-cron.sh && hermes cron list      # idempotent: re-creates all 4 jobs
  ```
  (`sweep-otp` `every 5m` and `learn` `every 168h` cadences are hardcoded in
  `cron.py::format_hermes_schedule`, not config — edit there then re-run the installer.)
- **Discovery behavior** (`discovery.*`, `preferences.*`, `auth.otp_timeout_minutes` in
  `config.yaml`) → **no reload**; each cron fire reads `config.yaml` fresh, so the next run applies it.
- **A `cvflow-*.sh` script body** → `scripts/install-hermes-cron.sh` (copies into `~/.hermes/scripts/`,
  no restart).
- **A hook handler** (`artifacts/hermes/hooks/*/handler.py`) → copy to `~/.hermes/hooks/<name>/`
  **and** `sudo systemctl restart hermes-gateway` (hooks load at gateway start).

Full reload matrix + commands: [`docs/deploy.md`](docs/deploy.md) → "Changing the schedule / reloading cron + config".

## Layout
| Path | Purpose |
|---|---|
| `src/cvflow/` | application package (one subpackage per concern) |
| `profile/` | your knowledge base + form-field store (real files gitignored) |
| `resume/` | master LaTeX resume (real file gitignored) |
| `data/`, `logs/` | runtime DB, PDFs, tokens, cookies, logs (gitignored) |
| `config.example.yaml` | every config variable, documented |
| `artifacts/` | secret-free host runtime config for instance migration ([README](artifacts/README.md)) |
