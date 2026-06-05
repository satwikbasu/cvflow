# Phase 11 — Scheduling + daemon wiring + systemd deploy — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run cvflow unattended — a daily discovery digest, a recurring OTP-timeout sweep, and a heartbeat, all pushed to Telegram via `hermes send` and scheduled by `hermes cron` — and document the deployment in `docs/deploy.md`.

**Architecture:** A deterministic `python -m cvflow.cron <job>` entrypoint runs each scheduled job directly (no agent loop). A `HermesNotifier` (shells `hermes send`) becomes the single outbound-notice path, replacing the logging-only stub used by the `Automator` and `OtpCoordinator`. `hermes cron --no-agent --script` wrappers trigger the entrypoint on schedule; the running gateway fires them automatically.

**Tech Stack:** Python 3.11+ (stdlib `subprocess`), Hermes CLI (`hermes send` / `hermes cron`), systemd, xvfb, pytest. No new Python dependency.

---

## File structure

- `src/cvflow/notify.py` — **create**: `HermesNotifier`.
- `src/cvflow/cron.py` — **create**: `format_digest`, `run_job`, `main` (`python -m cvflow.cron`).
- `src/cvflow/mcp/tools.py` — **modify**: use `HermesNotifier` in `build_tools`; remove `_telegram_notify`.
- `artifacts/hermes/scripts/` — **create**: 3 wrapper scripts.
- `artifacts/hermes/hermes-config.yaml` + live `~/.hermes/config.yaml` — **modify**: fix tool allowlist.
- `docs/deploy.md` — **create**.
- `tests/test_notify.py`, `tests/test_cron.py` — **create**.

---

### Task 1: `HermesNotifier` (outbound Telegram via `hermes send`)

**Files:**
- Create: `src/cvflow/notify.py`
- Test: `tests/test_notify.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_notify.py
"""HermesNotifier — builds the hermes send argv; never raises. No real subprocess."""

import subprocess

from cvflow.notify import HermesNotifier


def test_calls_hermes_send_with_expected_argv():
    calls = []

    def fake_runner(argv, **kwargs):
        calls.append((argv, kwargs))
        return None

    HermesNotifier(runner=fake_runner)("hello there")
    argv, kwargs = calls[0]
    assert argv == ["hermes", "send", "--to", "telegram", "--quiet", "hello there"]
    assert kwargs.get("check") is True


def test_custom_target():
    calls = []
    HermesNotifier(target="telegram:123", runner=lambda argv, **k: calls.append(argv))("hi")
    assert calls[0][3] == "telegram:123"


def test_never_raises_on_runner_failure(caplog):
    def boom(argv, **kwargs):
        raise subprocess.CalledProcessError(1, argv)

    # must NOT raise — a notification failure can't be allowed to crash the caller
    HermesNotifier(runner=boom)("important notice")
    assert "important notice" in caplog.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_notify.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cvflow.notify'`.

- [ ] **Step 3: Write the implementation**

```python
# src/cvflow/notify.py
"""Outbound user notices via `hermes send` (Phase 11).

`hermes send` reuses the gateway's Telegram credentials, needs no running gateway
and no LLM. This is the single outbound-notice path for cvflow (mid-flow crash/OTP
notices + scheduled digest/heartbeat/sweep). It NEVER raises: a notification
failure must not crash the automation that emitted it (invariant 3 stays a log +
best-effort send, never a silent crash).
"""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("cvflow.notify")


class HermesNotifier:
    """Callable that pushes a message to Telegram via `hermes send`."""

    def __init__(
        self, target: str = "telegram", runner: Callable[..., Any] = subprocess.run
    ) -> None:
        self._target = target
        self._runner = runner

    def __call__(self, message: str) -> None:
        try:
            self._runner(
                ["hermes", "send", "--to", self._target, "--quiet", message],
                check=True,
            )
        except Exception as exc:  # noqa: BLE001 — notice must not crash the caller
            logger.warning("hermes send failed (%s); message was: %s", exc, message)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_notify.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/cvflow/notify.py tests/test_notify.py
git commit -m "feat(notify): HermesNotifier outbound Telegram via hermes send"
```

---

### Task 2: `format_digest` + `run_job` (cron core, pure)

**Files:**
- Create: `src/cvflow/cron.py`
- Test: `tests/test_cron.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cron.py
"""cron core — format_digest + run_job dispatch with fakes. No network/browser."""

from cvflow.cron import format_digest, run_job
from cvflow.discovery import JobPosting, RankedJob
from cvflow.statemachine import Status
from cvflow.storage import ApplicationStore


def _ranked():
    p = JobPosting(
        job_id="indeed:7", title="Backend Dev", company="Acme", location="Remote",
        description="d", url="https://jobs/7", site="indeed", date_posted="2026-06-05",
    )
    return [RankedJob(posting=p, summary="s", rationale="great fit")]


def test_format_digest_includes_url_and_commands():
    text = format_digest(_ranked())
    assert "https://jobs/7" in text
    assert "/apply indeed:7" in text
    assert "/skip indeed:7" in text
    assert "Backend Dev" in text


def test_format_digest_empty():
    assert format_digest([]) == "No new jobs today."


def test_run_job_discover_sends_digest():
    notes = []

    class _Disc:
        def discover(self):
            return _ranked()

    run_job("discover", store=None, discovery=_Disc(), otp=None, notify=notes.append)
    assert notes and "https://jobs/7" in notes[0]


def test_run_job_sweep_otp_calls_expire_overdue():
    called = {"n": 0}

    class _Otp:
        def expire_overdue(self):
            called["n"] += 1
            return []

    run_job("sweep-otp", store=None, discovery=None, otp=_Otp(), notify=lambda m: None)
    assert called["n"] == 1


def test_run_job_heartbeat_reports_status_counts():
    store = ApplicationStore(":memory:")
    store.add("indeed:1", "Acme", "Backend", "https://jobs/1")
    notes = []
    run_job("heartbeat", store=store, discovery=None, otp=None, notify=notes.append)
    assert notes and "alive" in notes[0]
    assert f"{Status.DISCOVERED.value}=1" in notes[0]


def test_run_job_unknown_raises():
    import pytest
    with pytest.raises(ValueError):
        run_job("bogus", store=None, discovery=None, otp=None, notify=lambda m: None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cron.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cvflow.cron'`.

- [ ] **Step 3: Write the implementation**

```python
# src/cvflow/cron.py
"""Deterministic scheduled-job entrypoint (Phase 11): `python -m cvflow.cron <job>`.

Runs each daily/recurring job directly (no agent loop) and pushes results to
Telegram via HermesNotifier. The brain is only involved later, when the user
replies to select/approve. Jobs: discover (digest), sweep-otp, heartbeat.
"""

from __future__ import annotations

from typing import Any

from cvflow.statemachine import Status


def format_digest(ranked: list[Any]) -> str:
    """Render the daily digest. Always includes each job's URL + /apply,/skip."""
    if not ranked:
        return "No new jobs today."
    parts = ["🗞️ cvflow — new jobs today:\n"]
    for rj in ranked:
        p = rj.posting
        parts.append(
            f"• {p.title} @ {p.company}\n"
            f"  {p.url}\n"
            f"  {rj.rationale}\n"
            f"  /apply {p.job_id} | /skip {p.job_id}\n"
        )
    return "\n".join(parts)


def run_job(
    job: str, *, store: Any, discovery: Any, otp: Any, notify: Any
) -> None:
    """Dispatch one scheduled job. Pure of config/network — deps are injected."""
    if job == "discover":
        notify(format_digest(discovery.discover()))
    elif job == "sweep-otp":
        otp.expire_overdue()
    elif job == "heartbeat":
        counts = ", ".join(f"{s.value}={len(store.list_by_status(s))}" for s in Status)
        notify(f"💓 cvflow alive — {counts}")
    else:
        raise ValueError(f"unknown cron job: {job}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cron.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add src/cvflow/cron.py tests/test_cron.py
git commit -m "feat(cron): format_digest + run_job dispatch (deterministic pipeline)"
```

---

### Task 3: `cron.main()` + `python -m cvflow.cron` wiring

**Files:**
- Modify: `src/cvflow/cron.py`
- Test: `tests/test_cron.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_cron.py
def test_main_dispatches_with_injected_services():
    notes = []
    from cvflow.storage import ApplicationStore
    store = ApplicationStore(":memory:")
    services = (store, None, None, notes.append)

    from cvflow.cron import main
    main(["heartbeat"], services=services)
    assert notes and "alive" in notes[0]


def test_main_bad_args_exits():
    import pytest
    from cvflow.cron import main
    with pytest.raises(SystemExit):
        main([], services=(None, None, None, lambda m: None))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cron.py::test_main_dispatches_with_injected_services -v`
Expected: FAIL — `ImportError: cannot import name 'main'`.

- [ ] **Step 3: Implement `_build`, `main`, and the module entrypoint**

Append to `src/cvflow/cron.py`:

```python
def _build(config: Any) -> tuple[Any, Any, Any, Any]:
    """Construct the minimal services for the cron jobs (NO browser/Automator)."""
    from cvflow.auth import OtpCoordinator
    from cvflow.discovery import DiscoveryService, LLMRanker
    from cvflow.knowledge import KnowledgeBase
    from cvflow.llm import NimProvider
    from cvflow.notify import HermesNotifier
    from cvflow.storage import ApplicationStore

    store = ApplicationStore(config.storage.db_path)
    knowledge = KnowledgeBase.load(
        config.profile.knowledge_base_dir, config.storage.form_fields_path
    )
    brain = NimProvider(
        base_url=config.llm.brain.base_url,
        api_key=config.llm.brain.api_key,
        model=config.llm.brain.model,
        max_requests_per_minute=config.llm.brain.max_requests_per_minute,
    )
    discovery = DiscoveryService(
        store,
        LLMRanker(brain, knowledge.full_context()),
        search_terms=config.discovery.search_terms,
        locations=config.discovery.locations,
        sites=config.discovery.sites,
        results_wanted_per_site=config.discovery.results_wanted_per_site,
        hours_old=config.discovery.hours_old,
        top_n=config.discovery.top_n_to_present,
    )
    notify = HermesNotifier()
    otp = OtpCoordinator(
        store=store, notify=notify, timeout_minutes=config.auth.otp_timeout_minutes
    )
    return store, discovery, otp, notify


def main(argv: list[str] | None = None, *, services: tuple[Any, Any, Any, Any] | None = None) -> None:
    import sys

    args = list(argv) if argv is not None else sys.argv[1:]
    if len(args) != 1:
        raise SystemExit("usage: python -m cvflow.cron <discover|sweep-otp|heartbeat>")
    if services is None:
        from cvflow.config import load_config

        services = _build(load_config("config.yaml"))
    store, discovery, otp, notify = services
    run_job(args[0], store=store, discovery=discovery, otp=otp, notify=notify)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cron.py -v`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add src/cvflow/cron.py tests/test_cron.py
git commit -m "feat(cron): main() + python -m cvflow.cron entrypoint"
```

---

### Task 4: Wire `HermesNotifier` into `build_tools`

**Files:**
- Modify: `src/cvflow/mcp/tools.py` (the `Automator` + `OtpCoordinator` construction in `build_tools`, and remove the `_telegram_notify` helper)

No new unit test: `build_tools` launches a real `SessionManager` (Chromium), so it is not exercised by the browserless test suite; this change is verified by the full suite still passing + the Task 6 live smoke. (`_telegram_notify` had no test either.)

- [ ] **Step 1: Replace the notifier**

In `src/cvflow/mcp/tools.py`, add the import inside `build_tools` near the other local imports (e.g. with the `from cvflow.auth import ...` line):

```python
    from cvflow.notify import HermesNotifier
```

Add one shared notifier and use it in BOTH places. Change the `Automator(...)` call's `notify=_telegram_notify` to `notify=notifier`, and the `OtpCoordinator(...)` call's `notify=_telegram_notify` to `notify=notifier`. Define `notifier = HermesNotifier()` once, before the `Automator(...)` construction:

```python
    notifier = HermesNotifier()
    automator = Automator(
        store=store,
        knowledge=knowledge,
        provider=tailoring,
        sessions=sessions,
        filler_factory=FormFiller,
        notify=notifier,
        screenshot_dir=config.automation.storage_state_dir,
    )
```

and

```python
    otp_coordinator = OtpCoordinator(
        store=store,
        notify=notifier,
        timeout_minutes=config.auth.otp_timeout_minutes,
    )
```

- [ ] **Step 2: Remove the orphaned stub**

Delete the `_telegram_notify` function definition (the `def _telegram_notify(message: str) -> None: ...` block added in Phase 9) — it now has no callers.

- [ ] **Step 3: Verify the suite + types**

Run: `pytest -q && ruff check . && mypy --strict src`
Expected: all green (no test referenced `_telegram_notify`).

- [ ] **Step 4: Commit**

```bash
git add src/cvflow/mcp/tools.py
git commit -m "feat(mcp): route Automator + OtpCoordinator notices through HermesNotifier"
```

---

### Task 5: Cron wrapper scripts + fix the tool allowlist

**Files:**
- Create: `artifacts/hermes/scripts/cvflow-discover.sh`, `cvflow-sweep-otp.sh`, `cvflow-heartbeat.sh`
- Modify: `artifacts/hermes/hermes-config.yaml` (tracked) and `~/.hermes/config.yaml` (live, gitignored)

- [ ] **Step 1: Create the three wrapper scripts**

`artifacts/hermes/scripts/cvflow-discover.sh`:

```bash
#!/usr/bin/env bash
# cvflow daily discovery digest — invoked by `hermes cron --no-agent`.
set -euo pipefail
cd /home/ubuntu/cvflow
exec /home/ubuntu/cvflow/.venv/bin/python -m cvflow.cron discover
```

`artifacts/hermes/scripts/cvflow-sweep-otp.sh`:

```bash
#!/usr/bin/env bash
# cvflow OTP-timeout sweep — invoked by `hermes cron --no-agent`.
set -euo pipefail
cd /home/ubuntu/cvflow
exec /home/ubuntu/cvflow/.venv/bin/python -m cvflow.cron sweep-otp
```

`artifacts/hermes/scripts/cvflow-heartbeat.sh`:

```bash
#!/usr/bin/env bash
# cvflow heartbeat — invoked by `hermes cron --no-agent`.
set -euo pipefail
cd /home/ubuntu/cvflow
exec /home/ubuntu/cvflow/.venv/bin/python -m cvflow.cron heartbeat
```

Make them executable: `chmod +x artifacts/hermes/scripts/*.sh`.

- [ ] **Step 2: Fix the tracked allowlist**

In `artifacts/hermes/hermes-config.yaml`, replace the `mcp_servers.cvflow.tools.include` list with the full current set:

```yaml
    tools:
      include: [ping, discover, analyze_jd, list_applications, get_application,
            request_review, submit, compose_essay, status_report,
            fill_application, resume_application, submit_otp]
```

- [ ] **Step 3: Fix the LIVE allowlist (back up first)**

Run:
```bash
cp ~/.hermes/config.yaml ~/.hermes/config.yaml.bak.$(date +%s)
```
Then edit `~/.hermes/config.yaml` so `mcp_servers.cvflow.tools.include` matches the full set above. Reload so the gateway picks up the new tools:
```bash
hermes gateway run --replace >/dev/null 2>&1 || systemctl restart hermes-gateway
hermes mcp test cvflow 2>&1 | tail -5   # expect the 12 tools listed, NO 'approve'
```
Expected: `cvflow` connects and lists the 12 tools; `approve` is absent.

- [ ] **Step 4: Commit (tracked files only)**

```bash
git add artifacts/hermes/scripts artifacts/hermes/hermes-config.yaml
git commit -m "feat(deploy): cvflow cron wrapper scripts + full MCP tool allowlist"
```

---

### Task 6: `docs/deploy.md` + create cron jobs + verify + build-plan sync

**Files:**
- Create: `docs/deploy.md`
- Modify: `docs/superpowers/plans/2026-06-03-cvflow-build-plan.md`

- [ ] **Step 1: Write `docs/deploy.md`**

Create `docs/deploy.md` with these sections (fill with the real commands below):

````markdown
# cvflow Deployment (Ubuntu / EC2)

## Runtime
Hermes runs under systemd as `hermes-gateway.service` (User=ubuntu, Restart=always); the unit
source is tracked at `artifacts/hermes/hermes-gateway.service`. While the gateway runs,
`hermes cron` jobs fire automatically (`hermes cron status` → "Gateway is running").

## Secrets / config (never committed)
`config.yaml` (repo root) + `~/.hermes/.env` hold the tokens. Required: `telegram.bot_token`,
`telegram.authorized_user_id`, `llm.brain.api_key` (NIM), `llm.tailoring.api_key` (Gemini).
`chmod 600 config.yaml`. The Fernet key (`data/.fernet_key`) is auto-created `chmod 600`.

## Headed browser under xvfb (stealth, prod)
Set `automation.headless: false` in `config.yaml`, then run the gateway with a virtual display:
```bash
sudo apt-get install -y xvfb
# add to the gateway unit's environment, or a drop-in:
Environment="DISPLAY=:99"
# and start Xvfb (e.g. a simple unit or `Xvfb :99 -screen 0 1920x1080x24 &`)
```
Tests run headless; this is prod-only.

## MCP tool allowlist
`~/.hermes/config.yaml` → `mcp_servers.cvflow.tools.include` must list all 12 cvflow tools
(`ping, discover, analyze_jd, list_applications, get_application, request_review, submit,
compose_essay, status_report, fill_application, resume_application, submit_otp`) — and NEVER
`approve` (the gate is the `/apply` hook + plugin under `artifacts/hermes/`).

## Scheduled jobs
Install the wrappers and register the cron jobs:
```bash
mkdir -p ~/.hermes/scripts
cp artifacts/hermes/scripts/cvflow-*.sh ~/.hermes/scripts/ && chmod +x ~/.hermes/scripts/cvflow-*.sh

hermes cron create '0 8 * * *'  --no-agent --script cvflow-discover.sh  --name cvflow-discover
hermes cron create 'every 5m'   --no-agent --script cvflow-sweep-otp.sh --name cvflow-sweep-otp
hermes cron create 'every 30m'  --no-agent --script cvflow-heartbeat.sh --name cvflow-heartbeat
hermes cron list
```
(`0 8 * * *` = daily 08:00, from `schedule.daily_discovery_time`; heartbeat interval from
`schedule.heartbeat_interval_minutes`.) `sweep-otp` (every 5 min) enforces OTP timeouts —
`OtpCoordinator.expire_overdue`.

## Security posture
Unprivileged user; `chmod 600` on `config.yaml`, the Fernet key, and encrypted blobs; burner Google
account; host firewall (Telegram is outbound long-polling — no inbound ports).
````

- [ ] **Step 2: Verify the live pipeline**

Run:
```bash
cd /home/ubuntu/cvflow && source .venv/bin/activate
python -m cvflow.cron heartbeat      # should send a Telegram heartbeat
mkdir -p ~/.hermes/scripts && cp artifacts/hermes/scripts/cvflow-*.sh ~/.hermes/scripts/ && chmod +x ~/.hermes/scripts/cvflow-*.sh
hermes cron create '0 8 * * *' --no-agent --script cvflow-discover.sh --name cvflow-discover
hermes cron create 'every 5m' --no-agent --script cvflow-sweep-otp.sh --name cvflow-sweep-otp
hermes cron create 'every 30m' --no-agent --script cvflow-heartbeat.sh --name cvflow-heartbeat
hermes cron list
```
Expected: a heartbeat message arrives in Telegram; `hermes cron list` shows the three jobs.

- [ ] **Step 3: Full verification**

Run: `pytest -q && ruff check . && mypy --strict src`
Expected: all green.

- [ ] **Step 4: Sync the build plan**

Tick `### [x] Phase 11 — Scheduling + deploy (via Hermes)` and add a 2026-06-05 Progress-log entry
summarizing: `HermesNotifier` (single outbound path via `hermes send`, never raises) wired into
`Automator`/`OtpCoordinator`; `cvflow.cron` deterministic entrypoint (`discover` digest with
URLs+/apply,/skip, `sweep-otp` closing the Phase-10 carry-forward, `heartbeat`); `hermes cron`
`--no-agent` wrapper scripts; fixed the stale 12-tool allowlist (still no `approve`);
`docs/deploy.md` (systemd + xvfb + cron + posture); test count.

- [ ] **Step 5: Commit + push**

```bash
git add docs/deploy.md docs/superpowers/plans/2026-06-03-cvflow-build-plan.md
git commit -m "docs(phase-11): deploy guide; mark Phase 11 complete; sync progress log"
git push
```

---

## Notes for the executor
- **Gate invariant:** the allowlist must never include `approve`; verify with `hermes mcp test cvflow`.
- **Never silent (invariant 3):** `HermesNotifier` logs on failure but never raises; every cron job
  emits a message (digest / "No new jobs today." / heartbeat / expiry notice).
- **No browser in cron:** `_build` constructs only store/discovery/otp/notify — never a
  `SessionManager` (a sweep must not launch Chromium).
- **Zero cost:** no new Python dependency; `hermes send`/`hermes cron` are part of the runtime.
- **Live edits:** `~/.hermes/config.yaml` is gitignored — back it up before editing; only the
  `artifacts/hermes/*` templates are committed.
