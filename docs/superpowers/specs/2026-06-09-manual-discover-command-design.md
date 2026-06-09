# Manual `/discover` command — design spec

**Status:** approved (brainstormed 2026-06-09). Next step: `superpowers:writing-plans` → TDD build.

## Summary

Add a deterministic `/discover` Telegram command that triggers a discovery run on demand,
streams per-stage progress to the chat, posts the usual two-section digest, and then posts
a consolidated, grouped report of every dropped job + reason. It mirrors the existing
`/apply` wiring exactly: a Hermes command hook + plugin running **outside the brain**, with
all logic in testable cvflow code. No LLM is involved in the trigger; zero per-call cost;
the approval-gate invariant is untouched (this adds a *trigger*, never an `approve`).

## Goals

- A human-only `/discover` command, runnable any time from the authorized Telegram chat.
- It runs the **same** pipeline as the daily cron and produces the **same** digest.
- It survives the ~15–20 min runtime without blocking the gateway (detached background run).
- It streams **verbose per-stage progress** and a **full grouped drop report** so the user
  sees, in chat, what the daily log would show — without flooding Telegram.
- A run lock guarantees the daily cron and a manual `/discover` can never double-run.

## Non-goals

- No change to the daily scheduled run's output — it stays **digest-only** (no progress, no
  drop report) to avoid 15+ automatic messages at noon.
- No `min_fit_score` floor, no ranking changes — this is purely a trigger + reporting feature.
- No new MCP tool; the existing brain-mediated `discover` MCP tool is left as-is.

## Decisions (from brainstorming)

| Decision | Choice |
|---|---|
| Placement | Hermes **command hook** (deterministic, outside the brain), mirroring `/apply` |
| Concurrency | **Refuse** with a notice if a run is already in progress (file lock) |
| Progress detail | **Verbose per-stage** (a message at each pipeline stage boundary) |
| Drop report scope | **All drops, grouped** (prefilter + exclude_when + must-have + salary-floor + already-seen), chunked |
| Manual vs daily | Progress + drop report fire **only on manual `/discover`**; daily run stays digest-only |

## Architecture

```
Telegram: /discover
  → Hermes plugin (cvflow-discover) registers the command
  → Hermes hook on command:discover (gateway process, OUTSIDE the brain)
      → cvflow.discover_command.handle_discover_command(user_id, authorized_user_id, spawn)
          · unauthorized → handled=False (ignored, like /apply)
          · lock already held → "⏳ already in progress" (handled)
          · else → spawn detached run, "🔎 Discovery started…" (handled)
      → returns {"decision":"handled","message":...}; brain never sees it
  ── detached background process ──
  <repo>/.venv/bin/python -m cvflow.cron discover --progress   (start_new_session=True)
      → run_job("discover", progress=notify, report_drops=True)
          · acquire data/discover.lock (non-blocking); if held → notify + exit
          · DiscoveryService.discover(progress=notify) — emits per-stage messages
          · notify(format_digest(result))                — the digest + footer
          · for chunk in format_drop_report(result drop records): notify(chunk)
          · release lock
```

`hermes send` (the `HermesNotifier`) needs no gateway and no LLM, so the detached process
notifies independently.

## Components & interfaces

### 1. `cvflow.runlock` (new) — single-run guard
- `discovery_lock(path="data/discover.lock")` — context manager using `fcntl.flock(LOCK_EX|LOCK_NB)`.
  Raises `AlreadyRunning` if held. Auto-releases when the process exits (fd closed) — robust
  against crashes/stale locks.
- `is_locked(path)` — best-effort non-blocking probe (try-acquire-release) for the command
  pre-check. (Authoritative guard is the run acquiring the lock; the probe is for fast UX.)

### 2. `DiscoveryService.discover()` — progress + drop records
- New param `progress: Callable[[str], None] = lambda _msg: None`. Called at each existing
  stage boundary with a formatted message:
  - `📡 Scraped {N} raw postings in {t:.0f}s`
  - `🧹 {N} candidates after prefilter + dedup (from {M})` (+ a `distill cap` line if it bites)
  - `🧪 Distilled {N}/{M} JDs in {t:.0f}s`
  - `📊 Cohorts — 💰{m} stated-pay · 📋{n} no-pay`
- New collection: at every drop site, append a `DropRecord(bucket, label, detail)` where
  `label = "<title> @ <company>"` (from the `JobPosting` already in hand) and `detail` is the
  reason, matching the log:
  - prefilter title/internship → `excluded title` / `internship`
  - prefilter/salary-floor → `{lpa} LPA < {floor}`
  - prefilter experience / exclude_when → the rule string (`min_years_required > 2`, `seniority_signal ∈ [senior,lead]`, `country == other`, red-flag)
  - must-have → `missing [react, node, …]`
  - already-seen → `already shown before`
  - `capped` is NOT per-job (no semantic reason) — recorded as a single summary record.
- `discover()` returns the existing `result["_dropped"]` (counts) **and** adds
  `result["_drop_records"] = list[DropRecord]`. (Same `# type: ignore` pattern as `_dropped`.)

`DropRecord` is a small `NamedTuple(bucket: str, label: str, detail: str)` defined in
`discovery/__init__.py`.

### 3. `cvflow.cron` — wiring
- `format_drop_report(records: list[DropRecord]) -> list[str]` (alongside `format_digest`):
  groups records by `bucket` in `DROP_BUCKET_ORDER`, emits a `🚫 Dropped {n} — {bucket}`
  header then `• {label} — {detail}` lines, and **chunks output into ≤4000-char messages**
  (Telegram's hard limit is 4096). Returns the list of message strings. The `capped` summary
  record renders as a single line.
- `run_job("discover", …)` gains a `progress` callback and a `report_drops: bool`:
  - acquire `discovery_lock()`; on `AlreadyRunning` → `notify("⏳ A discovery run is already in
    progress — the digest is on its way")` and return.
  - run `discovery.discover(progress=progress)`, `set_digest_slots`, `notify(format_digest)`.
  - if `report_drops`: `for chunk in format_drop_report(records): notify(chunk)`.
- `main()` parses an optional `--progress` flag: `discover` (daily: quiet) vs
  `discover --progress` (manual: progress callback = notify, `report_drops=True`).

### 4. `cvflow.discover_command` (new) — the gate's twin
- `handle_discover_command(*, user_id, authorized_user_id, spawn, is_locked) -> DiscoverResult`
  (`DiscoverResult(handled: bool, message: str)`):
  - `user_id != authorized_user_id` → `DiscoverResult(False, "")` (ignored).
  - `is_locked()` → `DiscoverResult(True, "⏳ A discovery run is already in progress — the
    digest is on its way.")`.
  - else → `spawn()`; `DiscoverResult(True, "🔎 Discovery started — I'll post progress, the
    digest, and the dropped-jobs report here.")`.
  - `spawn` and `is_locked` are injected so tests never fork a process or touch a real lock.

### 5. Hermes artifacts (new) — `cvflow-discover`
- `artifacts/hermes/hooks/cvflow-discover/{HOOK.yaml,handler.py}` — `HOOK.yaml` events:
  `[command:discover]`. `handler.py` mirrors the gate handler: reads `CVFLOW_ROOT`/config,
  builds the real `spawn` (`Popen([<root>/.venv/bin/python, "-m","cvflow.cron","discover",
  "--progress"], cwd=root, start_new_session=True, stdout=<root>/data/discover-manual-<ts>.log,
  stderr=STDOUT)`) and the real `is_locked`, calls `handle_discover_command`, returns
  `{"decision":"handled","message":...}` or `{}`.
- `artifacts/hermes/plugins/cvflow-discover/{plugin.yaml,__init__.py}` — registers `/discover`
  as a known command (mirrors `cvflow-gate`).
- Deploy: install into `~/.hermes/{hooks,plugins}/`, add `cvflow-discover` to
  `plugins.enabled` in `~/.hermes/config.yaml`. Documented in `artifacts/README.md`.

## Message sequence (manual `/discover`)

1. `🔎 Discovery started — …` (hook ack)
2. `📡 Scraped N raw postings in …s`
3. `🧹 N candidates after prefilter + dedup (from M)` (+ cap line if any)
4. `🧪 Distilled N/M JDs in …s`
5. `📊 Cohorts — 💰m stated-pay · 📋n no-pay`
6. the **digest** (💰/📋 sections + `🔍 Filtered today` footer)
7. the **grouped drop report** (~10 chunked `🚫 Dropped …` messages)

Digest before the drop report — the actionable part arrives first.

## Error handling

- `HermesNotifier` already never raises (best-effort `hermes send`), so a failed progress/
  report message never crashes the run (invariant 3).
- A crash mid-run still releases the lock (context manager / fd close on exit).
- The hook returns `{}` for unauthorized or any handler exception (fall through; never a
  stack trace to the user). The detached run logs to `data/discover-manual-<ts>.log`.
- If `discover()` itself raises, `cron.main` already catches at the top, notifies the user,
  and re-raises — unchanged.

## Testing (TDD)

- **runlock:** acquiring twice → second raises `AlreadyRunning`; `is_locked` true while held,
  false after release; lock frees on context exit.
- **discover() progress:** a fake `progress` collector receives one message per stage in order.
- **discover() drop records:** one `DropRecord` per dropped job with the correct bucket +
  detail; `capped` yields a single summary record; counts in `_dropped` still match record
  counts per bucket (excluding the capped summary).
- **format_drop_report:** groups in `DROP_BUCKET_ORDER`; every message ≤4000 chars; a group
  larger than one message splits across messages keeping its header context; empty → `[]`.
- **handle_discover_command:** authorized+free → spawns once + "started"; authorized+locked →
  no spawn + "in progress"; unauthorized → `handled=False`, no spawn (mocked `spawn`/`is_locked`).
- **cron.main / run_job:** `discover --progress` wires progress→notify and sends the drop
  report; plain `discover` sends neither; `run_job` bails with the "in progress" notice when
  the lock is held (injected lock).

## Files touched

| File | Change |
|---|---|
| `src/cvflow/runlock.py` | new — `discovery_lock`, `is_locked`, `AlreadyRunning` |
| `src/cvflow/discovery/__init__.py` | `progress` param + `DropRecord` collection + `_drop_records` in result |
| `src/cvflow/cron.py` | `format_drop_report`, `--progress` flag, lock acquire, drop-report send |
| `src/cvflow/discover_command.py` | new — `handle_discover_command`, `DiscoverResult` |
| `artifacts/hermes/hooks/cvflow-discover/` | new — `HOOK.yaml` + `handler.py` |
| `artifacts/hermes/plugins/cvflow-discover/` | new — `plugin.yaml` + `__init__.py` |
| `artifacts/README.md` | document the new hook/plugin + enable step |
| `tests/` | `test_runlock.py`, `test_discover_command.py`, `test_drop_report.py`, additions to `test_discovery.py` + `test_cron.py` |

## Invariants preserved

- **No `approve` tool/method** anywhere — `/discover` is a trigger, not an approval.
- **Zero per-call/SaaS cost** — no LLM in the trigger or reporting; stdlib + `hermes send`.
- **Never fail silently** — every stage, drop, and refusal is surfaced to Telegram.
- **Deterministic gates unchanged** — drop records are read off the existing gate decisions.
