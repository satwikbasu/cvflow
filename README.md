# cvflow

Autonomous, self-hosted job-application agent. Runs continuously on an Ubuntu server and is operated **entirely through a Telegram chat** — no dashboard, no SSH, no commands during normal use.

Each day it discovers and ranks job postings against your profile, shows you the top 5–10, and for the ones you pick it analyzes the JD, tailors your LaTeX resume, and — **only after you approve in chat** — fills and submits the application via browser automation. It asks you for OTPs and clarifications mid-flow, and tracks every application's status.

**Hard rule: zero per-call / SaaS cost.** Everything is open-source, free-tier, or self-hosted; the only ongoing cost is the server.

See **[CLAUDE.md](CLAUDE.md)** for the full goals, tech stack, architecture, and conventions, and the **[build plan](docs/superpowers/plans/2026-06-03-cvflow-build-plan.md)** for the phase roadmap and decision log.

## Architecture
**Hermes Agent** (Nous Research, self-hosted) is the always-on substrate — it provides the Telegram interface, scheduling, the LLM brain, and the browser runtime. cvflow supplies **deterministic domain skills** that Hermes calls over a local **stdio MCP server** (`src/cvflow/mcp/`): discovery, JD analysis, resume tailoring, storage, and the approval gate.

- **Brain:** `meta/llama-3.3-70b-instruct` on the NVIDIA NIM free tier; resume tailoring escalates to Gemini 2.5 Flash.
- **The approval gate is deterministic code, not a convention.** The MCP surface exposes `request_review` and `submit` (which asserts `status == approved` and raises otherwise) but **no `approve` tool** — only the human Telegram approval callback can reach `statemachine.approve()`. The agent can *call* the gate but can never *satisfy* it.
- **Facts come only from `profile/`** — unanswerable fields trigger a clarification loop, never a guess. Nothing fails silently.

## Status
Implemented and operating on the EC2 host via Hermes; **Phases 0–7 complete** (82 tests, `ruff` + `mypy --strict` clean).

| ✓ | Phase | Delivers |
|---|---|---|
| ✅ | 0 Foundations | config loading, logging, test/lint/type harness |
| ✅ | 1 Storage + state machine | SQLite tracking store + the un-bypassable approval gate |
| ✅ | 2 LLM layer | Gemini tailoring client (RPD tracking + cache) |
| ✅ | 3 Knowledge base | `profile/**` + `form_fields.json` loader |
| ✅ | 4 Discovery | JobSpy search → dedup → LLM ranking → top-N (+ salary-hike filter) |
| ✅ | 5 JD analysis | fetch JD → extract skills/quals/seniority/tone/applicant-instructions |
| ✅ | 6 Resume tailoring | reorder + select ≤2 projects, no-new-facts check, diff, Tectonic compile |
| ✅ | 7 Hermes integration | cvflow skills exposed as a live stdio MCP server; `ping` round-trip proven from Telegram |
| ☐ | 8 Approval-gate skill + chat flows | review message (PDF/diff), approve/edit/skip, digest, OTP, clarification, essays |
| ☐ | 9 Browser automation | Playwright form-fill, uploads, pause/resume, proof capture |
| ☐ | 10 Auth | Google OAuth + email-OTP fallback (Fernet-encrypted tokens) |
| ☐ | 11 Scheduling + deploy | Hermes daily schedule + heartbeat; systemd (gateway unit already live) |
| ☐ | 12 End-to-end dry run | one full discover→approve→submit cycle on a throwaway account |

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
   pip install -e .                      # makes `cvflow` importable for the MCP subprocess
   cp config.example.yaml config.yaml    # fill in real secrets (gitignored)
   # add the Fernet key referenced by security.fernet_key_path
   ```
   `profile/`, `resume/*.tex`, and `form_fields.json` come with the clone (PII, no secrets).
3. **Install Hermes** (single-curl, per Nous docs) into `~/.hermes`.
4. **Restore the Hermes runtime** from the tracked artifacts:
   ```bash
   cp artifacts/hermes/hermes-config.yaml ~/.hermes/config.yaml
   cp artifacts/hermes/hermes-env.template ~/.hermes/.env   # fill EVERY value:
   #   NVIDIA_API_KEY, TELEGRAM_BOT_TOKEN, Gemini/Google key, …
   chmod 600 ~/.hermes/.env
   # adjust absolute paths in hermes-config.yaml if the repo isn't at /home/ubuntu/cvflow
   ```
5. **Install the gateway service:**
   ```bash
   sudo cp artifacts/hermes/hermes-gateway.service /etc/systemd/system/
   sudo systemctl daemon-reload && sudo systemctl enable --now hermes-gateway
   ```
6. **Verify:** `hermes mcp test cvflow` → 7 tools; from the authorized Telegram chat send `mcp_cvflow_ping` → `{"status":"ok","service":"cvflow"}`.

Full detail and the list of what's deliberately *not* tracked is in [`artifacts/README.md`](artifacts/README.md).

## Layout
| Path | Purpose |
|---|---|
| `src/cvflow/` | application package (one subpackage per concern) |
| `profile/` | your knowledge base + form-field store (real files gitignored) |
| `resume/` | master LaTeX resume (real file gitignored) |
| `data/`, `logs/` | runtime DB, PDFs, tokens, cookies, logs (gitignored) |
| `config.example.yaml` | every config variable, documented |
| `artifacts/` | secret-free host runtime config for instance migration ([README](artifacts/README.md)) |
