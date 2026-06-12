# cvflow bootstrap installer + config wizard — design spec

> **Status:** approved design (brainstorm 2026-06-12). Next: implementation plan
> (`docs/superpowers/plans/2026-06-12-bootstrap-installer.md`).
> **Authority:** new deploy tooling; makes `docs/deploy.md`'s manual steps the "under the hood"
> reference and the installer the canonical fresh-box path.

## Goal

On a **blank Ubuntu box**, one command —

```bash
curl -fsSL https://raw.githubusercontent.com/<owner>/cvflow/main/scripts/bootstrap.sh | bash
```

— brings cvflow to a **fully running, verified** state: system deps, repo, venv, secrets,
`config.yaml`, Hermes + its runtime config, hooks/plugins, cron, the systemd gateway unit, and a
post-install **config-tuning wizard**. The experience is **interactive (pure bash, reads
`/dev/tty`), idempotent, graceful on wrong/missing input, and never fails silently.**

This codifies every setup problem hit while standing up the second EC2 box (see "Issues prevented").

## Decisions (locked in brainstorm 2026-06-12)

- **Entry point:** the repo will be made **public** (a fresh public repo with all real config as
  `.example` placeholders, no PII). So `curl … | bash` works directly — bootstrap clones, no auth.
- **Language:** **pure bash**, prompts read from `/dev/tty` (stdin is the piped script under
  `curl|bash`), with a non-interactive fallback. No `whiptail`/`dialog` (zero deps, testable).
- **Key validation:** **live-validate** each secret as entered (cheap test call), `✓`/`✗`,
  re-enter, skippable-with-warning.
- **Privileges:** run as the **normal unprivileged user**; `sudo` only per-step (apt, systemd).
- **Profile scope:** **runtime only** — get the agent running with placeholder profile, then point
  to `docs/onboarding-new-candidate.md`. Profile authoring stays a separate flow.
- **Config wizard:** a **successor script** (`configure.sh`) the installer calls at the end; tunes
  `config.yaml` one knob at a time with skippable defaults; reloads only what changed.
- **Output style:** plain glyphs `✓` (ok) `✗` (fail) `⚠` (warn) `→` (info), like `hermes doctor`.
  **No emojis.**
- **CI:** GitHub Actions runs `shellcheck` + `bats` + a dry-run idempotency check on push/PR.

## Components

| File | Responsibility | Tested by |
|---|---|---|
| `scripts/bootstrap.sh` | Tiny, public, `curl\|bash`'d. Ensure `git`+`curl`; prompt repo URL (default) + target dir; clone/update; `exec scripts/install.sh "$@"`. | shellcheck; bats (arg parsing, idempotent clone) |
| `scripts/install.sh` | Orchestrator — runs the phases in order, each idempotent + logged. | shellcheck; bats (phase dispatch, `--dry-run`, idempotency) |
| `scripts/configure.sh` | Post-install **config wizard**: walk `config.yaml` tunables one at a time, skippable defaults, reload-what-changed. Also standalone. | shellcheck; bats (prompt loop, reload routing) |
| `scripts/install-lib.sh` | Sourced helpers (the unit-tested core): `ask`/`ask_secret`/`confirm`, `validate_*`, `step`/`ok`/`warn`/`die`/`log`, `need_cmd`, `with_sudo`, idempotency guards, `env_upsert`, `yaml_set`. | bats (every helper) |
| `scripts/install-hermes-cron.sh` | **Existing** — reused as the cron phase. | (already shipped) |
| `.github/workflows/installer-ci.yml` | shellcheck + bats + dry-run idempotency on push/PR. | itself |

`install-lib.sh` holds all logic worth testing; the orchestrators stay thin so the bats suite
covers the real behavior without a live box.

## `install.sh` phases

Each phase: detects done-state (idempotent), prints `→ <phase>`, does the work, ends `✓`/`⚠`/`✗`.

0. **Preflight** — Ubuntu (`/etc/os-release`), bash ≥4, `/dev/tty` present (else `--non-interactive`),
   `sudo` works (warn if not passwordless), disk space, network reachability to github/api hosts.
1. **System deps** (`sudo apt-get`, skip-if-present) — `git python3.11 python3.11-venv
   build-essential curl tectonic xvfb` + playwright OS deps.
2. **Python** — create `.venv`; **`pip install -e .`** (editable — so future `git pull`s take
   effect, the staleness bug); `playwright install chromium`.
3. **Collect + validate secrets** (`/dev/tty`, `✓`/`✗`, re-enter, skippable-with-warning):
   - Telegram `bot_token` → `GET https://api.telegram.org/bot<token>/getMe`.
   - Telegram `authorized_user_id` → numeric validator.
   - Mistral key (brain + distillation) → `GET https://api.mistral.ai/v1/models`.
   - Cerebras key (tailoring) → `GET https://api.cerebras.ai/v1/models` (browser UA).
   Skipped keys are recorded and surfaced in the final "degraded" summary.
4. **`config.yaml`** — `cp config.example.yaml config.yaml` if absent; inject keys (`llm.brain` +
   `llm.distillation` = Mistral, `llm.tailoring` = Cerebras), telegram; **assert key-superset
   parity with `config.example.yaml`**; load-test with `python -m cvflow.cron print-hermes-schedule`;
   `chmod 600`. Ensure the Fernet key path.
5. **Install Hermes** — Nous single-curl installer; skip if `~/.hermes/hermes-agent` exists.
6. **`~/.hermes` runtime config** —
   - `~/.hermes/config.yaml`: copy `artifacts/hermes/hermes-config.yaml` (fresh) **or** set the
     keys on an existing file — `model.default: mistral-small-2506`, **`model.provider: custom`**,
     `model.base_url: https://api.mistral.ai/v1`, `agent.tool_use_enforcement: force`,
     `plugins.enabled: [cvflow-gate, cvflow-discover]`, the **12-tool** `mcp_servers.cvflow.tools.include`
     (never `approve`), and `CVFLOW_ROOT` if the repo isn't at `~/cvflow`.
   - `~/.hermes/SOUL.md` ← `artifacts/hermes/SOUL.md` (URL-verbatim rule).
   - `~/.hermes/.env` from `artifacts/hermes/hermes-env.template` via `env_upsert`: set
     `MISTRAL_API_KEY` (the brain), `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_USERS`; **keep numeric
     vars commented** (empty `int("")` crashes the gateway). `chmod 600`.
7. **Hooks + plugins** — **`mkdir -p ~/.hermes/hooks ~/.hermes/plugins` FIRST**, then `cp -r` the
   four dirs (`cvflow-gate`/`cvflow-discover` × `hooks`/`plugins`).
8. **Linger** — `loginctl enable-linger "$USER"` (detached discovery survives gateway restarts).
9. **Cron** — run `scripts/install-hermes-cron.sh` (registers all four jobs incl. `cvflow-learn`,
   config-derived UTC schedule).
10. **systemd** — `sudo cp artifacts/hermes/hermes-gateway.service /etc/systemd/system/`;
    `sudo systemctl daemon-reload`; `sudo systemctl enable --now hermes-gateway`.
11. **Verify** — gateway `active`; journal shows **2** `Loaded hook 'cvflow…'` lines; `hermes mcp
    test cvflow` → 12 tools, no `approve`. Print a summary: what was configured, the **degraded
    list** (skipped keys → which features won't work), the live Telegram test (`/new` → "give me the
    cvflow status report" → fast tool call), and the **profile-personalization pointer**
    (`docs/onboarding-new-candidate.md`).
12. **Hand off to `configure.sh`** — offer to run the config wizard now (default yes).

## `configure.sh` — the config wizard (successor)

Standalone-runnable; the installer calls it at the end. Walks the **tunable** `config.yaml`
settings (NOT secrets — those are done) one at a time. For each: show the label, current value
(from `config.yaml`, falling back to `config.example.yaml`'s default), accept **Enter to keep**, or
a new value (validated). Grouped, ordered:

- **schedule**: `daily_discovery_time` (HH:MM), `timezone` (IANA, validated via `zoneinfo`),
  `heartbeat_interval_minutes`.
- **preferences**: `min_ctc_lpa`, `yoe_have`, `exclude_title_keywords`, `prefer_roles`,
  `top_ctc_lpa`, `fit_weight`/`comp_weight` (must sum to 1.0 — validated).
- **discovery**: `search_terms`, `locations`, `sites`, `results_wanted_per_site`, `hours_old`,
  `max_distill_per_cohort`, `top_n_per_cohort`, and the drop-summary knobs
  (`summarize_drops`, `cron_sends_drops`, `drop_summary_*`).
- **auth**: `otp_timeout_minutes`.

After edits, **reload only what changed**, per `docs/deploy.md`'s reload matrix:
- `schedule.*` changed → run `install-hermes-cron.sh` (no gateway restart).
- `discovery.*` / `preferences.*` / `auth.*` changed → nothing (next run re-reads `config.yaml`).
- If a change touches the brain/MCP wiring (not expected here, but guarded) → offer a
  `sudo systemctl restart hermes-gateway` (and remind `/new`).
The wizard writes back with `yaml_set` (preserves the rest of the file), runs the
`print-hermes-schedule` load-test, and prints a one-line summary of what changed + what it reloaded.

## UX conventions

- Glyphs: `→` step/info, `✓` success, `⚠` warning (non-fatal), `✗` failure (fatal). **No emojis.**
- Prompts show the default in brackets and accept Enter to keep: `Daily discovery time [12:00]:`.
- Invalid input re-prompts with a `✗ <reason>` line; an empty answer to a skippable item keeps the
  default; a special `skip` keyword (where offered) skips with a `⚠ <feature> will not work` note.
- Colors via `tput` when the terminal supports it; plain text otherwise.

## Idempotency model

Every phase converges to desired state on re-run: `mkdir -p`, `env_upsert` (grep-before-append /
replace-in-place), `yaml_set` (set-or-update), `install-hermes-cron.sh` (already idempotent),
`systemctl enable` (idempotent), Hermes-install skip-if-present, `pip install -e .` (no-op if
unchanged). Re-running the whole installer is safe and only fills gaps. `--reconfigure` forces
re-collection of secrets.

## Error handling — never silent

- `set -Eeuo pipefail`; an `ERR` trap prints the **failed command, line number, current phase**,
  and a pointer to the full transcript at **`~/cvflow-install.log`** (all output `tee`'d there).
- **Fatal** (apt / clone / venv / Hermes install fail) → `die` with a one-line remediation hint and
  non-zero exit.
- **Non-fatal** (a skipped optional key, a validation the user chose to bypass) → `warn`, continue,
  and list it in the end-of-run **degraded summary** so nothing is lost.
- No step swallows output; every step ends in exactly one of `✓`/`⚠`/`✗`.

## Flags / modes

- `--dry-run` — print every action, change nothing (used by CI + idempotency test).
- `--non-interactive` — read answers from env vars (`CVFLOW_TELEGRAM_BOT_TOKEN`, `CVFLOW_MISTRAL_API_KEY`,
  …); for CI / unattended re-runs. Missing required env in this mode → `die` (never hang).
- `--reconfigure` — re-collect secrets even if `config.yaml` exists.
- `--skip-system-deps` — for boxes where apt is managed externally.

## Issues prevented (from the 2026-06-11/12 second-box bring-up)

| Issue hit | Phase that prevents it |
|---|---|
| `~/.hermes/plugins/` missing → `cp` failed | 7 (`mkdir -p` first) |
| Cerebras key absent → 401 crash on drop-summary | 3 (live key validation) |
| Hermes brain still NIM (slow chat) | 6 (sets Mistral `model.default`) |
| `provider: mistral` → "Unknown provider" | 6 (writes `provider: custom`) |
| `SOUL.md` not copied → URL mangling | 6 (copies it) |
| `MISTRAL_API_KEY` absent in `.env` | 6 (`env_upsert`) |
| empty numeric env var → `int("")` gateway crash | 6 (keeps them commented) |
| non-editable install → `git pull` didn't take effect | 2 (`pip install -e .`) |
| plugins not in `plugins.enabled` → `/discover` silent | 6 (sets `plugins.enabled`) |
| linger off → detached run died on gateway restart | 8 (`enable-linger`) |
| `cvflow-learn` unregistered | 9 (`install-hermes-cron.sh`) |
| `config.yaml` missing keys vs example | 4 (key-superset parity check) |

## Testing & CI

- **`shellcheck`** on all `scripts/*.sh` (warnings = failure).
- **`bats-core`** unit tests on `install-lib.sh` helpers: validators (telegram id numeric, HH:MM,
  IANA tz, fit/comp sum=1.0), `env_upsert` (insert vs replace vs commented), `yaml_set`
  (set-or-update, preserves siblings), idempotency guards, the degraded-list accumulator.
- **Idempotency test:** `install.sh --dry-run` run twice → the second run reports every phase
  already-done (no pending changes).
- **`.github/workflows/installer-ci.yml`** runs the three above on `ubuntu-latest` for push/PR.
- **e2e** stays a documented manual checklist (the verification script we already use, generalized)
  — a real Hermes/Telegram box can't run in CI.

## Docs wiring

- This spec → `docs/superpowers/specs/2026-06-12-bootstrap-installer-design.md`.
- Plan → `docs/superpowers/plans/2026-06-12-bootstrap-installer.md` (writing-plans).
- `docs/superpowers/plans/2026-06-03-cvflow-build-plan.md` — add a deploy-tooling entry linking here.
- `docs/deploy.md` — the installer becomes the canonical fresh-box path; the existing manual steps
  stay as the "what it does under the hood" reference.
- `README.md` — the one-line `curl … | bash` install.

## Out of scope (YAGNI)

- Non-Ubuntu OSes, ARM specifics, container/k8s packaging.
- Profile/candidate authoring (stays in `onboarding-new-candidate.md`).
- Multi-tenant / hosted provisioning (Phase 2).
- A `whiptail` UI (documented upgrade path only).

## Risks

| Risk | Mitigation |
|---|---|
| Public repo leaks PII | Separate fresh public repo, all real config as `.example` placeholders (user-owned step, pre-flip). |
| Hermes' own installer changes its flow | Pin/skip-if-present; the Hermes step is isolated and re-runnable; failure is fatal with a hint. |
| `yaml_set` on the large `~/.hermes/config.yaml` corrupts it | Prefer copying the tracked `artifacts/hermes/hermes-config.yaml` on a fresh box; `yaml_set` only for in-place key updates, backed up first; load-test after. |
| CI can't exercise the live path | bats + shellcheck + dry-run idempotency cover logic; e2e is a manual checklist. |
