# cvflow bootstrap installer + config wizard — design spec

> **Status:** approved design (brainstorm 2026-06-12); **revised 2026-06-12 after the Fable
> review** — public-cut scope (no automation/auth/OTP), profile scaffolding phase, tectonic
> install fix, release-tag pinning, `--no-services` mode, CI idempotency fix, AGPL wiring.
> Execution: master plan `docs/superpowers/plans/2026-06-12-public-release.md`; installer TDD
> sub-plan `docs/superpowers/plans/2026-06-12-bootstrap-installer.md`.
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
- **CI:** GitHub Actions runs `shellcheck` + `bats` + a dry-run smoke + a **containerized real-run
  idempotency job** on push/PR (see Testing & CI — a dry-run-twice check cannot test idempotency).

### Revised decisions (2026-06-12, post-Fable-review — public-cut alignment)

- **Public scope = discovery + tailoring only.** The public repo ships **without**
  `src/cvflow/automation/`, `src/cvflow/auth/`, and everything OTP. Consequences for the
  installer: **no xvfb, no Playwright** in system deps; **no `cvflow-sweep-otp`** cron job
  (three jobs, not four); the MCP allowlist is the automation-free set (below); no `auth`
  group in the config wizard.
- **License & release model:** AGPL-3.0. Releases are annotated tags (`vX.Y.Z`);
  `bootstrap.sh` clones the **latest release tag by default** (`--ref <tag|branch>` overrides) so
  `curl|bash` users never get an untested HEAD.
- **Tectonic is NOT an Ubuntu apt package** (verified on 24.04 — the live box runs a
  `/usr/local/bin/tectonic` from the official installer). Phase 1 installs it via the official
  install script, skip-if-`command -v tectonic`.
- **Profile scaffolding is an installer phase.** The public repo has no committed `profile/`
  (that was private-repo PII); without a scaffold the tool crashes on first run
  (`KnowledgeBase.load`, `candidate_skills.yaml`, `parse_master`). Phase 4b copies the committed
  `profile.example/` + placeholder `resume/` into place. Candidate *authoring* stays in
  `docs/onboarding-new-candidate.md`.
- **Hermes phases are a separable group** (5–7, 10), exposed as `--no-services`: installs the
  Python tool only (run `python -m cvflow.cron discover` by hand; no Telegram/agent). This is
  the cheap seam toward a future CLI-only mode, the CI vehicle, and the migration hedge for the
  planned Hermes retirement at P2a. The README must disclose that full install runs Hermes'
  own third-party installer (a nested `curl|bash`).
- **`python -m cvflow.checkup`** (new, small): the onboarding §6 checklist as one deterministic
  command — config loads, knowledge base loads (+ lists empty form fields), `candidate_skills.yaml`
  drift test, `master.tex` compiles. Run by the verify phase and documented for post-onboarding use.

## Components

| File | Responsibility | Tested by |
|---|---|---|
| `scripts/bootstrap.sh` | Tiny, public, `curl\|bash`'d. Ensure `git`+`curl`; resolve the **latest release tag** (or `--ref`); prompt target dir; clone/update at that ref; `exec scripts/install.sh "$@"`. | shellcheck; bats (arg parsing, ref resolution, idempotent clone) |
| `scripts/install.sh` | Orchestrator — runs the phases in order, each idempotent + logged. | shellcheck; bats (phase dispatch, `--dry-run`, idempotency) |
| `scripts/configure.sh` | Post-install **config wizard**: walk `config.yaml` tunables one at a time, skippable defaults, reload-what-changed. Also standalone. | shellcheck; bats (prompt loop, reload routing) |
| `scripts/install-lib.sh` | Sourced helpers (the unit-tested core): `ask`/`ask_secret`/`confirm`, `validate_*`, `step`/`ok`/`warn`/`die`/`log`, `need_cmd`, `with_sudo`, idempotency guards, `env_upsert`, `yaml_set`. | bats (every helper) |
| `scripts/install-hermes-cron.sh` | **Existing** — reused as the cron phase (public variant registers **3** jobs: discover, heartbeat, learn — no sweep-otp). | (already shipped; public variant adjusted) |
| `src/cvflow/checkup.py` (`python -m cvflow.checkup`) | The onboarding §6 verification as one command: config load, KB load + empty-field list, skills drift test, master.tex compile. | pytest (not bats — it's Python) |
| `.github/workflows/installer-ci.yml` | shellcheck + bats + dry-run idempotency on push/PR. | itself |

`install-lib.sh` holds all logic worth testing; the orchestrators stay thin so the bats suite
covers the real behavior without a live box.

## `install.sh` phases

Each phase: detects done-state (idempotent), prints `→ <phase>`, does the work, ends `✓`/`⚠`/`✗`.

0. **Preflight** — Ubuntu (`/etc/os-release`), bash ≥4, `/dev/tty` present (else `--non-interactive`),
   `sudo` works (warn if not passwordless), disk space, network reachability to github/api hosts.
1. **System deps** (`sudo apt-get`, skip-if-present) — `git python3.11 python3.11-venv
   build-essential curl`. **No xvfb, no Playwright OS deps** (no automation in the public tool).
   Then **Tectonic via its official install script** (skip if `command -v tectonic`) — it is
   **not** an Ubuntu apt package; install to `/usr/local/bin` with sudo, verify `tectonic --version`.
2. **Python** — create `.venv`; **`pip install -e .`** (editable — so future `git pull`s take
   effect, the staleness bug). No `playwright install`.
3. **Collect + validate secrets** (`/dev/tty`, `✓`/`✗`, re-enter, skippable-with-warning):
   - Telegram `bot_token` → `GET https://api.telegram.org/bot<token>/getMe`.
   - Telegram `authorized_user_id` → numeric validator.
   - Mistral key (brain + distillation) → `GET https://api.mistral.ai/v1/models`.
   - Cerebras key (tailoring) → `GET https://api.cerebras.ai/v1/models` (browser UA).
   Skipped keys are recorded and surfaced in the final "degraded" summary.
4. **`config.yaml`** — `cp config.example.yaml config.yaml` if absent; inject keys (`llm.brain` +
   `llm.distillation` = Mistral, `llm.tailoring` = Cerebras), telegram; **assert key-superset
   parity with `config.example.yaml`**; load-test with `python -m cvflow.cron print-hermes-schedule`;
   `chmod 600`.

4b. **Profile scaffold** — if `profile/` is absent, `cp -r profile.example/ profile/` (placeholder
   knowledge base, empty-string `form_fields.json`, a minimal valid `candidate_skills.yaml`);
   verify the placeholder `resume/master.tex` compiles (one tectonic smoke run); print the
   personalization pointer (`docs/onboarding-new-candidate.md`) and add **"placeholder profile —
   digests will be generic until you onboard"** to the degraded summary. Never overwrites an
   existing `profile/`.

   *(Phases 5–7 and 10 are the **services group** — skipped entirely under `--no-services`.)*

5. **Install Hermes** — Nous single-curl installer; skip if `~/.hermes/hermes-agent` exists.
6. **`~/.hermes` runtime config** —
   - `~/.hermes/config.yaml`: copy `artifacts/hermes/hermes-config.yaml` (fresh) **or** set the
     keys on an existing file — `model.default: mistral-small-2506`, **`model.provider: custom`**,
     `model.base_url: https://api.mistral.ai/v1`, `agent.tool_use_enforcement: force`,
     `plugins.enabled: [cvflow-gate, cvflow-discover]`, the **automation-free**
     `mcp_servers.cvflow.tools.include` allowlist — `ping, discover, analyze_jd,
     list_applications, get_application, request_review, compose_essay, status_report`
     (grows if apply-kit tools land before release; **never** `approve`, never any
     fill/submit/OTP tool) — and `CVFLOW_ROOT` if the repo isn't at `~/cvflow`.
   - `~/.hermes/SOUL.md` ← `artifacts/hermes/SOUL.md` (URL-verbatim rule).
   - `~/.hermes/.env` from `artifacts/hermes/hermes-env.template` via `env_upsert`: set
     `MISTRAL_API_KEY` (the brain), `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_USERS`; **keep numeric
     vars commented** (empty `int("")` crashes the gateway). `chmod 600`.
7. **Hooks + plugins** — **`mkdir -p ~/.hermes/hooks ~/.hermes/plugins` FIRST**, then `cp -r` the
   four dirs (`cvflow-gate`/`cvflow-discover` × `hooks`/`plugins`).
8. **Linger** — `loginctl enable-linger "$USER"` (detached discovery survives gateway restarts).
9. **Cron** — run `scripts/install-hermes-cron.sh` (public variant registers the **three** jobs:
   `cvflow-discover`, `cvflow-heartbeat`, `cvflow-learn`; config-derived UTC schedule; there is
   no `cvflow-sweep-otp` in the public tool).
10. **systemd** — `sudo cp artifacts/hermes/hermes-gateway.service /etc/systemd/system/`;
    `sudo systemctl daemon-reload`; `sudo systemctl enable --now hermes-gateway`.
11. **Verify** — `python -m cvflow.checkup` (config loads, KB loads + empty-field list, skills
    drift test, master.tex compiles); then, unless `--no-services`: gateway `active`; journal
    shows **2** `Loaded hook 'cvflow…'` lines; `hermes mcp test cvflow` → the allowlist count
    exactly, no `approve`, no fill/submit/OTP tools. Print a summary: what was configured, the
    **degraded list** (skipped keys → which features won't work; placeholder profile note), the
    live Telegram test (`/new` → "give me the cvflow status report" → fast tool call), and the
    **profile-personalization pointer** (`docs/onboarding-new-candidate.md`).
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

After edits, **reload only what changed**, per `docs/deploy.md`'s reload matrix:
- `schedule.*` changed → run `install-hermes-cron.sh` (no gateway restart).
- `discovery.*` / `preferences.*` changed → nothing (next run re-reads `config.yaml`).
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
- `--no-services` — install the Python tool only (phases 0–4b + verify-lite; no Hermes, no
  systemd, no cron). For CLI-only users (`python -m cvflow.cron discover` by hand), CI, and as
  the migration seam for the planned Hermes retirement.
- `bootstrap.sh --ref <tag|branch>` — override the default latest-release-tag checkout.

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
- **Dry-run smoke:** `install.sh --dry-run --non-interactive` dispatches every phase, exits 0,
  and changes nothing (assert no filesystem diff). *(Note: a dry-run-twice check cannot test
  idempotency — a dry run never changes state, so the second run sees the same world.)*
- **Container idempotency job (the real test):** in a `ubuntu:24.04` container, run
  `install.sh --non-interactive --no-services` with fake keys and key-validation stubbed
  (`CVFLOW_SKIP_VALIDATION=1`); run it **twice**; the second run must report every phase
  already-done and produce no filesystem changes (diff a `find`-manifest before/after).
- **`.github/workflows/installer-ci.yml`** runs all of the above on `ubuntu-latest` for push/PR.
- **e2e** stays a documented manual checklist (the verification script we already use, generalized)
  — a real Hermes/Telegram box can't run in CI.

## Public-repo wiring (AGPL release)

- **LICENSE:** AGPL-3.0 at the repo root (full text). No per-file headers (not required; YAGNI).
  Copyright held by the founder — preserves the right to offer a differently-licensed hosted
  version later.
- **README must contain, honestly:** what the tool does (discovery + résumé tailoring; it does
  **not** auto-apply); the one-line install; which API keys are needed and that all are free-tier;
  a plain-language risk disclaimer — scraping runs on **your** machine, **your** IP, **your**
  keys, and may violate job boards' terms of service, at your own risk; disclosure that the full
  install runs Hermes' own third-party installer (nested `curl|bash`) with `--no-services` as the
  opt-out; supported platform (Ubuntu 24.04).
- **SECURITY.md:** private vulnerability reporting via GitHub security advisories; what is and
  isn't in scope (users' own keys/config are their responsibility).
- **Naukri `nkparam` adapter ships** (named decision): the token-minting technique is already
  public knowledge (NopeRi); usage risk sits with the user and is covered by the README
  disclaimer. Revisit only if the repo draws a complaint.
- **Releases:** annotated `vX.Y.Z` tags; `bootstrap.sh` defaults to the latest tag, so a release
  is the QA gate — never point users at HEAD.

## Docs wiring

- This spec → `docs/superpowers/specs/2026-06-12-bootstrap-installer-design.md`.
- Master release plan → `docs/superpowers/plans/2026-06-12-public-release.md`; installer TDD
  sub-plan → `docs/superpowers/plans/2026-06-12-bootstrap-installer.md` (writing-plans).
- `docs/superpowers/plans/2026-06-03-cvflow-build-plan.md` — add a deploy-tooling entry linking here.
- `docs/deploy.md` — the installer becomes the canonical fresh-box path; the existing manual steps
  stay as the "what it does under the hood" reference.
- `README.md` — the one-line `curl … | bash` install.

## Out of scope (YAGNI)

- Non-Ubuntu OSes, ARM specifics, container/k8s packaging.
- Profile/candidate **authoring** (the installer only scaffolds placeholders; authoring stays in
  `onboarding-new-candidate.md`).
- Automation / auth / OTP — excluded from the public tool entirely (revised decisions above).
- Multi-tenant / hosted provisioning (Phase 2 — private).
- A `whiptail` UI (documented upgrade path only). PyPI packaging, Docker images.

## Risks

| Risk | Mitigation |
|---|---|
| Public repo leaks PII | Separate fresh public repo, all real config as `.example` placeholders (user-owned step, pre-flip). |
| Hermes' own installer changes its flow | Pin/skip-if-present; the Hermes step is isolated and re-runnable; failure is fatal with a hint. |
| `yaml_set` on the large `~/.hermes/config.yaml` corrupts it | Prefer copying the tracked `artifacts/hermes/hermes-config.yaml` on a fresh box; `yaml_set` only for in-place key updates, backed up first; load-test after. |
| CI can't exercise the live Hermes/Telegram path | bats + shellcheck + the container `--no-services` idempotency job cover everything else; e2e is a manual checklist on a real box. |
| Nested third-party `curl\|bash` (Hermes installer) is a supply-chain exposure for public users | Disclosed in the README; `--no-services` opts out; the Hermes phase is isolated so a pinned/vendored install can replace it later. |
| The planned P2a Hermes retirement breaks public users onboarded onto Hermes | Releases are tagged (users stay on a working tag); `--no-services` already works Hermes-free; ship migration notes with the release that swaps substrates. |
