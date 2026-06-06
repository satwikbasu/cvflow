# Phase 13C — Preference Learning (propose updates from apply/skip history) — Design

Date: 2026-06-06
Status: approved (brainstorm), pre-implementation
Related: builds on 13A (`preferences.md`) + 13B (apply/skip signals); `[[hermes-runtime-ops]]`.
Part of Phase 13.

## Problem / intent
Every `/apply` and `/skip` is a labeled signal about what the user actually wants. We want the system
to *learn* from that — but **without** putting opaque, drifting agent memory in the decision path
(same principle as the approval gate). Learning must be **human-approved and auditable**.

## Decision (locked in brainstorm)
Learning is a **feedback loop that only PROPOSES** preference changes; it never edits
`profile/preferences.md` or filters automatically. The durable source of truth stays the
version-controlled `preferences.md` + config (13A). Hermes "memory/insights" is not trusted to
silently steer applications.

## Components

### 1. Decision signals (already in storage)
The gate already records outcomes as statuses: `approved`/`applied` = wanted, `skipped` = not wanted.
13B persisted the presented jobs, and `Application` carries company/role/jd_url. No new capture is
required; learning reads these records.

### 2. `cvflow.learning.summarize_decisions(applied, skipped, *, provider) -> str`
- Pure function (LLM via the existing `generate(prompt)->str` seam). Inputs: lists of recently
  applied vs skipped jobs (title/company + any stored `concerns`/JD analysis). Output: a short
  plain-language pattern summary **plus concrete, optional suggestions** phrased as proposed edits to
  `preferences.md` (e.g. "You've skipped 6 service-company roles — consider adding
  'avoid service/consultancy companies' to preferences.md").
- Never invents user facts; if there's too little signal, it says so (no fabricated pattern).

### 3. `cvflow.cron learn` job
- New subcommand in `cvflow.cron`: gather applied (`approved`/`applied`) + skipped Applications,
  call `summarize_decisions`, and send the suggestions to Telegram via the notifier, prefixed
  `💡 Preference suggestions (reply by editing profile/preferences.md):`.
- If there are too few decisions (configurable threshold, default 5 total) → send nothing or a brief
  "not enough data yet" (never crash).
- Scheduled **weekly** via `hermes cron` (`artifacts/hermes/scripts/cvflow-learn.sh`), documented in
  `docs/deploy.md`.

### 4. Application (human-in-loop, manual)
The user reads the suggestions and edits `profile/preferences.md` (and/or `config.yaml`) themselves —
a normal committed change they can diff and revert. No auto-apply, no `/pref` command (kept out of
scope to preserve auditability and avoid scope creep).

## Data flow
weekly cron → gather applied/skipped from store → `summarize_decisions` (LLM) → Telegram suggestion
message → user manually edits `preferences.md` → 13A picks it up next discovery.

## Error handling
Too little data → graceful "not enough data" (or silent per config), never a crash. LLM failure →
the Phase-11 cron wrapper notifies "learn job failed" (invariant 3). Suggestions are advisory only;
nothing changes filters until the user edits the file.

## Testing (mocked LLM, no network)
- `summarize_decisions`: builds a prompt from applied/skipped; returns the model's suggestion text;
  with empty/sparse input returns a "not enough data" sentinel (no fabricated pattern).
- `run_job("learn")`: gathers the right status buckets and notifies with the suggestion; below the
  threshold → no suggestion sent.

## Exit criteria
A weekly job reviews the user's apply/skip history and sends human-readable preference suggestions to
Telegram; it never edits preferences or filters on its own.

## Accepted trade-offs
- **Propose-only, manual apply** — deliberately not automated (auditability > convenience); a future
  `/pref accept` could automate application once trusted.
- Learning quality depends on accumulated decisions; early on it will mostly say "not enough data".
- Uses the same NIM/Gemini seam — subject to free-tier latency, but it's a weekly batch so that's fine.

## Out of scope
Auto-editing preferences; a `/pref` command; using Hermes's native memory store as the source of
truth (explicitly rejected — durable prefs stay in the repo).
