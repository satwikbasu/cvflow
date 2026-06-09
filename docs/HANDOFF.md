# cvflow — setup verification prompt

Paste the prompt below into a fresh **Claude Code** session running **on the new EC2 host**
inside the cvflow repo. It makes Claude verify, read-only, that a freshly-provisioned
instance is wired correctly — with special attention to the most common failure: the
**Telegram bot replying from Hermes's own default brain instead of the NVIDIA NIM API**.

Assumes everything up to (but **not** including) `docs/onboarding-new-candidate.md` is
done: repo cloned, venv + deps installed, Playwright + TeX present, secrets filled into
`config.yaml` (NVIDIA NIM key, Telegram bot token + user id), and the Hermes gateway
installed per the README "Deploy / migrate" steps. The candidate-tuning step
(`onboarding-new-candidate.md`) is **not** assumed done — the committed `profile/` is still
the original author's data, which is fine for wiring verification.

---

```text
You are verifying a freshly set-up instance of "cvflow" on this EC2 host. cvflow is an
autonomous, self-hosted job-application agent operated entirely through Telegram. Its
always-on substrate is the **Hermes Agent** (Nous Research), which provides the Telegram
interface, scheduling, the browser runtime, and the **LLM brain**. cvflow supplies
deterministic domain skills to Hermes over a local stdio **MCP server** (`-m cvflow.mcp`).

This is a READ-ONLY verification task. Do NOT modify code, do NOT edit config, do NOT
commit, and NEVER print a full secret value (show only presence / a short prefix). At the
end, give me a PASS/FAIL table and a clear verdict, plus the exact fix for any FAIL.

## Critical background — where the brain is actually configured
The brain the Telegram bot uses is configured in **Hermes**, NOT in cvflow:
- `~/.hermes/config.yaml` → `model.default` must be `meta/llama-3.3-70b-instruct`,
  `model.provider: nvidia`, `model.base_url: https://integrate.api.nvidia.com/v1`.
- `~/.hermes/.env` → must contain a real **`NVIDIA_API_KEY`** (this is the key the brain
  actually uses) and **`TELEGRAM_BOT_TOKEN`**.
cvflow's own `config.yaml` `llm.brain` block is only a MIRROR for reference — the bot does
NOT read its API key from there. The #1 reason a cvflow bot "replies on its own / not via
NIM" is that the NVIDIA key was put into cvflow's `config.yaml` but is MISSING, blank, a
placeholder, or INVALID in `~/.hermes/.env` (or `model.default` isn't the NIM model), so
Hermes errors out or falls back to a different brain. Find out which.

## Run these checks (adapt paths if the repo isn't at ~/cvflow or Hermes isn't at ~/.hermes)

1. Repo + test suite health
   - `cd ~/cvflow && source .venv/bin/activate`
   - `git log --oneline -3` and `git status` (expect a clean, recent checkout)
   - `pytest -q`  →  all pass
   - `ruff check .`  and  `mypy --strict src`  →  clean
   - `python -c "from cvflow.config import load_config; c=load_config('config.yaml'); print('brain_model', c.llm.brain.model); print('tg_id_set', c.telegram.authorized_user_id!=0); print('tg_token_set', bool(c.telegram.bot_token)); print('nim_key_prefix', (c.llm.brain.api_key or '')[:6])"`
     (cvflow config loads; brain model is the NIM model; telegram id/token set; NIM key
     starts with `nvapi-`. Note: this only proves cvflow's MIRROR, not the live brain.)

2. Hermes brain wiring — the decisive part
   - Inspect `~/.hermes/config.yaml`: confirm `model.default: meta/llama-3.3-70b-instruct`,
     `provider: nvidia`, the NIM `base_url`, and that `mcp_servers.cvflow` exists (command =
     the repo venv python, args `["-m","cvflow.mcp"]`).
   - Inspect `~/.hermes/.env` WITHOUT printing secrets, e.g.:
     `grep -E '^(NVIDIA_API_KEY|TELEGRAM_BOT_TOKEN)=' ~/.hermes/.env | sed -E 's/=(.{0,6}).*/=\1…/'`
     Confirm `NVIDIA_API_KEY` is present, non-empty, not a placeholder, and starts `nvapi-`;
     `TELEGRAM_BOT_TOKEN` is present. If `NVIDIA_API_KEY` is absent/blank here, that is the bug.

3. Prove the NIM key actually works (smoking-gun test)
   - Source the key from Hermes's env and call NIM directly:
     `set -a; . ~/.hermes/.env; set +a; curl -s -o /dev/null -w "%{http_code}\n" https://integrate.api.nvidia.com/v1/chat/completions -H "Authorization: Bearer $NVIDIA_API_KEY" -H "content-type: application/json" -d '{"model":"meta/llama-3.3-70b-instruct","messages":[{"role":"user","content":"ping"}],"max_tokens":5}'`
   - `200` = key valid and NIM reachable (the brain CAN use NIM). `401`/`403` = invalid/expired
     key = the bot cannot use NIM and will fail or fall back. Report the exact code.

4. Gateway + MCP registration
   - `systemctl is-active hermes-gateway` → `active`
   - Discover the Hermes CLI (`hermes --help`, `hermes mcp --help`) and run its MCP test for
     the cvflow server (e.g. `hermes mcp test cvflow`). Expect **12 tools** — ping, discover,
     analyze_jd, list_applications, get_application, request_review, submit, compose_essay,
     status_report, fill_application, resume_application, submit_otp — and **NO `approve`
     tool** (the approval gate is human-only; an `approve` tool would be a critical defect).
   - Confirm `agent.disabled_toolsets` keeps `browser`/`computer_use` etc. as configured.

5. Live brain-path proof (the user's actual concern)
   - `journalctl -u hermes-gateway -n 40 --no-pager` to see current state.
   - Ask me to send one message to the Telegram bot from the authorized account; then
     `journalctl -u hermes-gateway --since "2 min ago" --no-pager` and look for an outbound
     call to `integrate.api.nvidia.com` / the NIM model name, and the absence of auth errors,
     provider-fallback messages, or "no model configured" warnings. Also have me confirm the
     bot's reply arrived. (As a built-in liveness check, from the authorized chat the message
     `mcp_cvflow_ping` should return `{"status":"ok","service":"cvflow"}` — proving the MCP
     path, distinct from the brain path.)

6. Candidate-tuning status (expected NOT done yet)
   - The committed `profile/` is still the original author's data. This is EXPECTED at this
     stage — wiring verification does not require the new candidate's profile. Do NOT flag the
     author's PII as an error. Just note that `docs/onboarding-new-candidate.md` is the next
     step to personalize the profile/skills/config before relying on the digest.

## Report
Produce a PASS/FAIL table for checks 1–5, then a one-line verdict answering: **"Is the
Telegram bot genuinely routing through the NVIDIA NIM API?"** If NO, name the precise cause
(most likely: `NVIDIA_API_KEY` missing/invalid in `~/.hermes/.env`, or `model.default` not
the NIM model) and the exact fix — typically: put the real key into `~/.hermes/.env`
(`chmod 600`), ensure `model.default`/`provider`/`base_url` are the NIM values, then
`sudo systemctl restart hermes-gateway` and re-run check 3 and check 5.
```
