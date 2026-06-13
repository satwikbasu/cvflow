# cvflow public open-source release (AGPL) — Master Plan

> **For agentic workers (Opus):** This is a **master roadmap** — source of truth for order, scope,
> and exit criteria of the public release. Before executing workstream W2 (and any other
> workstream you judge needs it), write a bite-sized TDD sub-plan with `superpowers:writing-plans`
> and execute with `superpowers:subagent-driven-development` or `superpowers:executing-plans`.
> Keep checkboxes + the progress log in sync.
>
> **Read first:** memory `bootstrap-installer-decisions` (locked installer prefs — do NOT re-ask),
> memory `build-progress` (what's shipped), the revised spec
> `docs/superpowers/specs/2026-06-12-bootstrap-installer-design.md`, and
> `docs/architecture-review-fable.md` §3bis (why automation is out) + the open-source decision
> (AGPL, public engine / private business split).

**Goal:** Ship cvflow as a public, AGPL-3.0, self-hosted open-source tool — discovery + résumé
tailoring only — in a **fresh public repo** (URL provided by the founder), installable on a blank
Ubuntu 24.04 box with one `curl | bash`, end-to-end verified, with zero PII and **zero trace of
AI-assisted development** anywhere public.

**Architecture:** The public repo is a curated export of the private repo minus automation/auth/
OTP/PII/internal docs, plus the bootstrap installer (per the revised spec), `profile.example/`
scaffolding, a placeholder résumé template, fresh public docs, and CI. History starts at one
initial commit — the private repo's history (which contains PII) never travels.

**Tech stack:** unchanged core (Python 3.11, Hermes, Mistral + Cerebras, SQLite, JobSpy +
nkparam, Tectonic) + bash/bats/shellcheck for the installer + GitHub Actions.

---

## Inputs required from the founder (ask once, at W0 — block until provided)

1. **Public repo URL** (he will create the empty repo and provide the link).
2. **GitHub PAT** with repo + workflow read scope — used only to verify CI runs (per memory).
3. **Confirm the public name/tagline** (default: keep "cvflow"; one-line description for the
   README and the GitHub repo description field).
4. **Confirm the Naukri adapter ships** (spec's named decision; default yes).

## Hard rules (apply to every workstream — non-negotiable)

1. **No trace of AI assistance in anything public.** This means, concretely:
   - **Commit messages:** Conventional Commits, terse, written like a human maintainer.
     **NEVER append `Co-Authored-By: Claude …` or any "Generated with …" trailer** — this
     explicitly overrides the default commit-trailer behavior for ALL commits in the public repo.
   - **No agent artifacts in the public tree:** no `CLAUDE.md`, no `.claude/`, no
     `docs/superpowers/`, no `AGENTS.md`, no references to plans/specs/memory, no
     "phase N" / "invariant 2" style internal vocabulary in public docs or comments.
   - **Humanizer pass is mandatory:** run the `writing:humanize-text` skill on every public
     prose artifact (README, SECURITY.md, release notes, onboarding doc, installer prompt/warning
     strings, GitHub repo description) and `writing:humanize-code` on comments/docstrings/log
     strings in any code you **write or edit** for the public tree (don't churn untouched files).
     The tool *being* an AI agent is public and fine; the *development process* is not mentioned.
   - **Git identity:** the founder's global git config (never set repo-local user/email).
2. **PII zero-tolerance.** Nothing from the private `profile/`, `resume/*.tex`, real
   `form_fields.json` values, Telegram IDs, salary preferences, employer/college names, or the
   founder's name/email/phone may appear in the public tree, in any commit, or in any test
   fixture. The public repo starts from a **single initial commit** of the curated tree — never
   from the private history.
3. **Surgical cut.** The public tree contains only what the tailoring-scope tool needs. Removal
   edits touch exactly the wiring seams listed in W1; do not refactor, rename, or "improve"
   shipped code beyond what removal requires (the existing code is already reviewed and tested).
4. **The gate invariant survives the cut:** no `approve` tool, `/apply` hook stays the sole
   `store.approve()` caller. Never-fabricate and never-fail-silently stay intact.
5. **AGPL-3.0**, copyright the founder. The private repo and the future hosted service are
   unaffected (he holds the copyright).
6. **The private repo is the upstream.** All work happens in a staging directory built FROM the
   private repo; the installer + checkup code is developed there and the generic parts
   (installer scripts, checkup, CI) are also committed back to the private repo so the two trees
   share one implementation.

## Workstream order

```
W0 inputs + freeze          (founder inputs; pick the source commit)
W1 the public cut           (staging tree: prune + seam edits + scaffolds + green suite)
W2 installer + checkup      (the revised spec, TDD sub-plan, bats/shellcheck)
W3 public docs + polish     (README/SECURITY/LICENSE/onboarding; humanizer; PII sweep)
W4 repo creation + CI       (single initial commit, push, Actions green via PAT)
W5 end-to-end verification  (fresh box, real curl|bash, synthetic candidate)
W6 release v0.1.0           (tag, release notes, final smoke, private-repo sync-back)
```

---

### [ ] W0 — Inputs + freeze

- Collect the four founder inputs above.
- Pick the source commit on the private `main` (latest green: full pytest + ruff + mypy
  --strict). Record its SHA in the progress log — the cut is reproducible from it.
- Create the staging directory **outside** the private repo: `~/cvflow-public-staging/`
  (export via `git archive <sha> | tar -x -C ~/cvflow-public-staging`, so no `.git`, no
  gitignored files, no history travels).

**Exit:** inputs recorded; staging tree exists and matches the recorded SHA.

### [ ] W1 — The public cut (in staging)

#### W1-1 Delete (whole paths, no replacement)

- `src/cvflow/automation/`, `src/cvflow/auth/` and all their tests.
- `src/cvflow/recon.py` + its tests **if** unwired beyond the `ats` column; if discovery already
  persists `ats` by the source SHA, keep the column write and drop only the report/CLI surface
  (checkpoint: grep wiring first, decide, record).
- `profile/` (real PII), `resume/` real content (template is rebuilt in W1-4), `CLAUDE.md`,
  `.claude/` if exported, `docs/superpowers/`, `docs/HANDOFF.md`, `docs/hosting-aws-ec2.md`,
  `docs/architecture-review-fable.md`.
- `docs/architecture-and-workflow.md` + `docs/discovery-engine.md`: **exclude from v0.1**
  (smallest PII review surface; revisit as public docs in a later release).

#### W1-2 Seam edits (the only code changes; grep each symbol to catch stragglers)

| File | Edit |
|---|---|
| `src/cvflow/mcp/tools.py` | remove `fill_application`, `resume_application`, `submit_otp`, `submit` from `TOOL_NAMES` + their methods; drop `automator`/`otp_coordinator` ctor params; `build_tools` loses the Automator/SessionManager/TokenVault/OtpCoordinator wiring |
| `src/cvflow/mcp/server.py` | registration follows `TOOL_NAMES` (verify no hardcoded list) |
| `src/cvflow/cron.py` | remove the `sweep-otp` job + `OtpCoordinator` from `_build` (returns one fewer service); usage string updated |
| `src/cvflow/config.py` | remove `AutomationConfig`, `AuthConfig`, `SecurityConfig` dataclasses + loading/validation (grep `fernet`, `otp`, `automation.` first — `security` exists only for `TokenVault`, which leaves with `auth/`) |
| `config.example.yaml` | drop the `automation:`, `auth:`, `security:` blocks |
| `src/cvflow/statemachine/` | **untouched** (the `otp_timeout`/`failed` statuses are harmless; gate tests stay intact) |
| `src/cvflow/storage/` | keep schema as-is; remove `set_otp_deadline`/`list_awaiting_otp` only if nothing references them after the cut (grep; prefer keeping over churn) |
| `artifacts/hermes/hermes-config.yaml` | allowlist → the 8 automation-free tools (spec phase 6); plus apply-kit tools if they shipped before the source SHA |
| `artifacts/hermes/scripts/` | delete `cvflow-sweep-otp.sh`; `scripts/install-hermes-cron.sh` registers 3 jobs |
| `artifacts/hermes/SOUL.md` | **review line by line** — genericize anything personal to the founder's voice/data |
| `requirements.txt` / `pyproject.toml` | drop `playwright`, `cryptography` (Fernet) if now unused (grep imports); keep `pycryptodome` (nkparam) |
| `.gitignore` | rewrite for public reality: `config.yaml`, `data/`, `logs/` stay ignored; **`profile/` becomes ignored** (users' PII never lands in their forks by accident); `profile.example/` tracked; `resume/` tracked as template with a comment warning users their edits contain PII if they fork-and-push |

#### W1-3 Tests

- Delete tests for removed modules; adapt tests that load the real committed `profile/`
  (Phase-3-era) to load `profile.example/` instead.
- Add: a test asserting `TOOL_NAMES` contains no `approve`/`fill_application`/
  `resume_application`/`submit_otp`/`submit` (the public allowlist guard).
- Full suite + `ruff check .` + `mypy src` green **in staging**.

#### W1-4 Scaffolds (new content — humanize-code/text applies)

- `profile.example/`: one placeholder file per entry in `docs/onboarding-new-candidate.md` §1 —
  obviously-fake content (`Jane Candidate`, `jane@example.com` style), `form_fields.json` with
  the exact key set and all-empty values + the `_comment`, a minimal `candidate_skills.yaml`
  (3–4 generic skills + 2 synonyms) that **passes the drift test**.
- `resume/`: keep the existing LaTeX macros/preamble verbatim; replace all content with
  placeholder heading + one sample entry per section, clearly marked. Must compile with
  tectonic (CI-checked by `checkup` in W2).
- `src/cvflow/checkup.py` + `__main__` hook (`python -m cvflow.checkup`): runs config load,
  `KnowledgeBase.load` (+ prints empty form fields), the skills drift check, and a tectonic
  compile of `master.tex` (skippable with `--no-latex`); exit non-zero on any failure, plain
  `✓/✗/⚠` output matching the installer's glyphs. With tests.

**Exit:** staging suite green; `python -m cvflow.checkup` passes on the scaffolded tree;
`build_tools` smoke-imports (no Playwright/Fernet imports anywhere — `grep -r "playwright\|fernet\|Fernet"` clean).

### [ ] W2 — Installer + config wizard (the revised spec)

- Write the TDD sub-plan `docs/superpowers/plans/2026-06-12-bootstrap-installer.md` (in the
  **private** repo, via `superpowers:writing-plans`) covering exactly the revised spec:
  `bootstrap.sh` (latest-tag default, `--ref`), `install.sh` phases 0–12 incl. **4b profile
  scaffold** and the **services group / `--no-services`**, `configure.sh` (no auth group),
  `install-lib.sh` helpers with bats coverage, `installer-ci.yml` (shellcheck + bats + dry-run
  smoke + the **container idempotency job** — not dry-run-twice), tectonic via official
  installer, `CVFLOW_SKIP_VALIDATION=1` stub for CI.
- Implement **in staging**; copy the generic pieces (scripts, checkup, CI workflow) back into
  the private repo in the same workstream so the implementations never diverge.
- All user-facing prompt/error/summary strings get the humanizer treatment (plain, no AI tells,
  the spec's glyph conventions, no emojis).
- Local verification before W4: `shellcheck` clean, `bats` green, the container idempotency job
  runs green in a local `docker run ubuntu:24.04`.

**Exit:** spec's components all exist; local shellcheck/bats/container-idempotency green;
`install.sh --no-services --non-interactive` (fake keys, stub validation) converges twice in a
clean container.

### [ ] W3 — Public docs + polish (humanizer everywhere)

- **`README.md`** (fresh, via `writing:humanize-text`), containing: what it does in two plain
  sentences (finds well-fitting jobs daily, tailors your LaTeX résumé per job — **does not
  auto-apply**); the one-line install; requirements (Ubuntu 24.04, free Telegram bot, free
  Mistral + Cerebras keys — with signup links); a quickstart (install → onboard via the
  onboarding doc → first digest); configuration pointers (`configure.sh`, `config.example.yaml`);
  the honest risk paragraph (your machine, your IP, your keys; scraping may violate boards' ToS;
  use at your own risk); the nested Hermes-installer disclosure + `--no-services`; license line.
- **`LICENSE`** — AGPL-3.0 full text, founder's copyright line.
- **`SECURITY.md`** — GitHub private advisories; scope note.
- **`docs/onboarding-new-candidate.md`** — adapt: "committed in the repo" framing → "scaffolded
  by the installer; replace the placeholders"; paths updated for `profile.example/`; §6 checklist
  now points at `python -m cvflow.checkup`. Humanize.
- **`docs/deploy.md`** — adapt as "what the installer does under the hood" (3 cron jobs, no
  xvfb/automation/OTP sections, no private-repo PII framing). Humanize.
- **PII sweep (blocking):** build the grep pattern list **locally from the private repo's real
  values** (names, emails, phone, employers, college, Telegram IDs, salary figures from the
  private config/profile — the patterns themselves never get written into staging or this plan);
  run it over every file in staging; then **manually read** every doc/markdown file shipped.
  Also `grep -ri "claude\|anthropic\|copilot\|gpt-\|llm-generated\|ai-generated"` over docs,
  comments, and scripts — hits must be about the tool's own LLM providers only.
- **AI-tell sweep:** run `writing:humanize-text` review over README/SECURITY/onboarding/deploy
  as a final pass; fix em-dash overuse, bolded-header bullet walls, promotional language.

**Exit:** both sweeps clean and recorded in the progress log; docs read like a maintainer wrote
them.

### [ ] W4 — Repo creation, push, CI green

1. In staging: `git init -b main`; verify `git config user.name/email` resolve to the founder's
   **global** identity; single initial commit — message like `cvflow 0.1: job discovery and
   resume tailoring, self-hosted` (humanized, conventional, **no trailers of any kind**).
2. `git remote add origin <PUBLIC_REPO_URL>`; push `main`.
3. Repo settings (via `gh` with the PAT): description + topics (`job-search`, `resume`,
   `self-hosted`, `telegram-bot`, …), issues on, Actions on, default branch `main`.
4. Watch `installer-ci.yml` (`gh run watch`); fix-forward with small humanized commits until
   green. **Every commit message in this repo follows hard rule 1.**
5. Audit the pushed result **from the outside**: fresh `git clone` of the public URL into a tmp
   dir; re-run the W3 PII + AI-tell greps on the clone; `git log` shows exactly the expected
   commits with the founder's identity and no trailers.

**Exit:** public repo live; CI green; outside-clone audit clean.

### [ ] W5 — End-to-end verification (fresh box)

On a **fresh Ubuntu 24.04 VM/instance** (not the dev box; founder provides or approves spend —
a throwaway EC2 of the existing type is fine):

1. Run the real public one-liner with `--ref main` (tag doesn't exist yet).
2. Complete the interactive install with **real keys** (founder supplies test keys; a spare
   Telegram bot token, NOT the private prod bot).
3. Verify the degraded path once: re-run with the Cerebras key skipped → warning + degraded
   summary lists it.
4. Onboard a **synthetic candidate** (invented persona; never the founder's data) via the
   onboarding doc + any capable LLM; `python -m cvflow.checkup` passes.
5. Run `/discover` from Telegram; digest arrives; `request_review` on one job produces a compiled
   PDF + diff in chat; `/apply` + `/skip` behave; heartbeat fires.
6. Re-run the installer → all phases report already-done. Run `configure.sh`, change
   `daily_discovery_time`, confirm only the cron reinstall happens.
7. Record every rough edge found; fix-forward in the public repo (humanized commits); re-run the
   affected step.

**Exit:** the checklist above passes start-to-finish on a box that had never seen cvflow;
the synthetic candidate's digest is plausibly ranked (gates visibly firing in the footer).

### [ ] W6 — Release + sync-back

1. Tag `v0.1.0` (annotated); confirm `bootstrap.sh` with no `--ref` resolves and installs the
   tag (final smoke: one more clean-box or container `--no-services` run via the default path).
2. GitHub Release with humanized notes (what it is, what it needs, known limitations — no
   auto-apply, Ubuntu-only, free-tier rate limits).
3. Private-repo sync-back: commit the shared installer/checkup/CI pieces (if not already, per
   W2); update `docs/deploy.md` + the build plan + memory (`build-progress`,
   `bootstrap-installer-decisions`) with the release state + public repo URL; note in
   `docs/architecture-review-fable.md` §5 that the public release shipped.
4. Hand the founder a short ops note: how to cut the next release (tag = release), how to
   review issues/PRs, and the standing rule that **anything merged to the public repo follows
   hard rules 1–2 forever**.

**Exit:** `v0.1.0` live and installable via the default one-liner; private repo and memory
updated; founder briefed.

---

## Definition of done

1. A stranger with an Ubuntu 24.04 box, a Telegram account, and two free API keys gets from the
   README's one-liner to a ranked daily digest and a tailored PDF without contacting the founder.
2. The public tree contains no PII, no automation/auth/OTP code, no agent-development artifacts,
   and no AI-attribution anywhere (`git log` included); both sweeps are recorded as clean.
3. AGPL-3.0 in place; CI green on `main`; `v0.1.0` tagged; bootstrap installs the tag by default.
4. The private repo remains fully functional and is the upstream for shared pieces.

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| PII slips through in a doc or fixture | W3 dual sweep (grep from real values + manual read) AND the W4 outside-clone re-audit; fresh history means one miss is fixable by rewriting a 1-commit-deep repo before anyone forks |
| A removal seam breaks something subtle (config strictness, MCP registration) | every seam edit is grep-verified + the staging suite/ruff/mypy must be green; `build_tools` smoke + `hermes mcp test` in W5 |
| Default commit trailer leaks AI attribution | hard rule 1 restated per workstream; W4 step 5 audits `git log` explicitly |
| Installer works on the dev box but not a clean one | W5 runs on a genuinely fresh VM with the real public URL; CI's container job covers the `--no-services` subset continuously |
| Private/public installer drift | W2 commits shared pieces to both trees in the same workstream; sync-back is a W6 exit criterion |
| Hermes upstream changes break the install | spec risk table: isolated re-runnable phase, fatal-with-hint; releases pin the tested tag |

## Explicit non-goals

PyPI/Docker packaging · non-Ubuntu support · automation/auth/OTP in any form · multi-tenant or
hosted features (private Phase 2) · rewriting/refactoring shipped core code · publishing the
internal architecture docs (revisit post-v0.1) · marketing/announcement content beyond the
release notes.

## Progress log

- 2026-06-12 — plan written (Fable session), spec revised the same day. Awaiting W0 founder
  inputs: public repo URL, PAT, name/tagline confirm, Naukri-ship confirm.
