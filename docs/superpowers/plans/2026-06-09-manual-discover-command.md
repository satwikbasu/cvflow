# Manual `/discover` Command Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a deterministic `/discover` Telegram command that triggers a discovery run on demand, streams verbose per-stage progress, posts the usual digest, then posts a grouped report of every dropped job — mirroring the `/apply` gate wiring, running outside the brain, at zero per-call cost.

**Architecture:** A Hermes command hook + plugin (`cvflow-discover`) intercepts `/discover` in the gateway process (outside the agent loop) and spawns a detached `python -m cvflow.cron discover --progress`. A new `cvflow.runlock` (fcntl) guarantees the daily cron and a manual run never double-run. `DiscoveryService.discover()` gains a `progress` callback and collects `DropRecord`s; `cron.format_drop_report` groups + chunks them to ≤4000-char Telegram messages. All logic lives in testable cvflow code; the hook is a thin adapter. The approval-gate invariant is untouched — this is a *trigger*, never an `approve`.

**Tech Stack:** Python 3.11+, stdlib `fcntl`/`subprocess`, pytest, Hermes hooks/plugins, `HermesNotifier`.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/cvflow/runlock.py` | new — `discovery_lock` context manager, `is_locked` probe, `AlreadyRunning` |
| `src/cvflow/discovery/__init__.py` | `DropRecord` NamedTuple; `discover(progress=...)` + drop-record collection; `_drop_records` in result |
| `src/cvflow/cron.py` | `format_drop_report`; `run_job` gains `progress`/`report_drops`/`lock`; `main` parses `--progress` |
| `src/cvflow/discover_command.py` | new — `handle_discover_command`, `DiscoverResult` (gate's twin) |
| `artifacts/hermes/hooks/cvflow-discover/{HOOK.yaml,handler.py}` | new — `command:discover` hook adapter |
| `artifacts/hermes/plugins/cvflow-discover/{plugin.yaml,__init__.py}` | new — registers `/discover` as a known command |
| `artifacts/README.md` | document install + enable of the new hook/plugin |
| `tests/test_runlock.py` | new |
| `tests/test_discover_command.py` | new |
| `tests/test_drop_report.py` | new |
| `tests/test_discovery.py` | additions — progress + drop records |
| `tests/test_cron.py` | additions — `--progress` wiring, lock bail |
| `tests/test_hermes_discover_adapters.py` | new — hook adapter |

**Standing rules for every task:** TDD (write failing test → watch it fail → minimal impl → watch it pass). After each task's tests pass, run the full gate before committing: `pytest -q && ruff check . && mypy --strict src`. Conventional Commits, **no `Co-Authored-By` trailer**, straight to `main`. Never add an `approve` tool or weaken the gate.

---

## Task 1: `cvflow.runlock` — single-run guard

**Files:**
- Create: `src/cvflow/runlock.py`
- Test: `tests/test_runlock.py`

- [ ] **Step 1: Write the failing tests**

```python
"""runlock — fcntl single-run guard for discovery (daily cron + manual /discover)."""

import pytest

from cvflow.runlock import AlreadyRunning, discovery_lock, is_locked


def test_second_acquire_raises_while_held(tmp_path):
    path = str(tmp_path / "discover.lock")
    with discovery_lock(path):
        with pytest.raises(AlreadyRunning):
            with discovery_lock(path):
                pass


def test_lock_frees_on_context_exit(tmp_path):
    path = str(tmp_path / "discover.lock")
    with discovery_lock(path):
        pass
    # released — re-acquirable, no raise
    with discovery_lock(path):
        pass


def test_is_locked_true_while_held_false_after(tmp_path):
    path = str(tmp_path / "discover.lock")
    assert is_locked(path) is False
    with discovery_lock(path):
        assert is_locked(path) is True
    assert is_locked(path) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_runlock.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'cvflow.runlock'`

- [ ] **Step 3: Write the implementation**

```python
"""Single-run guard for discovery (daily cron + manual /discover must never double-run).

Uses ``fcntl.flock(LOCK_EX | LOCK_NB)`` on a lock file. The lock is tied to the open
file description, so it auto-releases when the fd closes — robust against crashes and
stale locks (CLAUDE.md invariant 3: never fail silently, but also never wedge). The
authoritative guard is the run that acquires the lock; ``is_locked`` is a best-effort
probe for fast command-time UX.
"""

from __future__ import annotations

import fcntl
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

__all__ = ["AlreadyRunning", "discovery_lock", "is_locked"]


class AlreadyRunning(Exception):
    """Raised when a discovery run is already holding the lock."""


@contextmanager
def discovery_lock(path: str = "data/discover.lock") -> Iterator[None]:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(p), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise AlreadyRunning(f"discovery lock held: {path}") from exc
        yield
    finally:
        os.close(fd)  # closing the fd releases the flock


def is_locked(path: str = "data/discover.lock") -> bool:
    """Best-effort probe: try to take the lock on a separate fd and release it."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(p), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    except OSError:
        return True
    finally:
        os.close(fd)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_runlock.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Full gate + commit**

```bash
pytest -q && ruff check . && mypy --strict src
git add src/cvflow/runlock.py tests/test_runlock.py
git commit -m "feat(runlock): fcntl single-run guard for discovery"
```

---

## Task 2: `DropRecord` + `discover()` progress callback

**Files:**
- Modify: `src/cvflow/discovery/__init__.py`
- Test: `tests/test_discovery.py`

This task adds the `progress` callback and the `DropRecord` NamedTuple, and threads a `records` list through every drop site. The existing `_dropped` counts behaviour is unchanged.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_discovery.py`)

These reuse the file's existing `_row` factory, `_two_stage_service` builder, and `_StubGemini`
stub (already defined near the top of `tests/test_discovery.py`). `_two_stage_service` defaults
to `exclude_title_keywords=["senior", "lead"]`, so a "Senior …" title is dropped at the prefilter
into bucket `other filter`.

```python
def test_discover_emits_progress_messages_in_order():
    store = ApplicationStore(":memory:")
    rows = [_row("1"), _row("2", site="indeed")]
    svc = _two_stage_service(store, lambda **k: rows)
    msgs: list[str] = []
    svc.discover(progress=msgs.append)
    joined = "\n".join(msgs)
    # one message per stage boundary, in pipeline order
    assert any(m.startswith("📡 Scraped") for m in msgs)
    assert any("candidates after prefilter" in m for m in msgs)
    assert any(m.startswith("🧪 Distilled") for m in msgs)
    assert any(m.startswith("📊 Cohorts") for m in msgs)
    assert joined.index("📡") < joined.index("🧹") < joined.index("🧪") < joined.index("📊")


def test_discover_collects_drop_records_with_bucket_and_detail():
    store = ApplicationStore(":memory:")
    rows = [_row("1"), _row("2", title="Senior Engineer")]  # row 2 dropped at prefilter
    svc = _two_stage_service(store, lambda **k: rows)
    result = svc.discover()
    records = result["_drop_records"]
    rec = next(r for r in records if r.bucket == "other filter")
    assert "@" in rec.label and rec.detail  # label "<title> @ <company>", detail = reason
    # per-bucket record counts match _dropped (excluding the capped summary)
    from collections import Counter
    by_bucket = Counter(r.bucket for r in records if r.bucket != "capped")
    for bucket, n in by_bucket.items():
        assert result["_dropped"][bucket] == n
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_discovery.py -q -k "progress or drop_records"`
Expected: FAIL — `TypeError: discover() got an unexpected keyword argument 'progress'` / `KeyError: '_drop_records'`

- [ ] **Step 3: Implement — `DropRecord`, drop helper, progress calls**

In `src/cvflow/discovery/__init__.py`:

(a) Add the import and NamedTuple near the top (after `from typing import Any`):

```python
from typing import Any, NamedTuple
```

```python
class DropRecord(NamedTuple):
    """One dropped job: ``bucket`` (a DROP_BUCKET_ORDER key), ``label`` ("<title> @ <company>"),
    ``detail`` (the human reason, matching the log line)."""

    bucket: str
    label: str
    detail: str
```

Add `"DropRecord"` to `__all__`.

(b) Add a helper next to `_bump`:

```python
def _label(p: JobPosting) -> str:
    return f"{p.title or 'Untitled'} @ {p.company or 'Unknown company'}"


def _drop(
    drops: dict[str, int], records: list[DropRecord], bucket: str, p: JobPosting, detail: str
) -> None:
    _bump(drops, bucket)
    records.append(DropRecord(bucket, _label(p), detail))
```

(c) Change `_prefilter` to accept `records` and use `_drop` at each drop site:

```python
    def _prefilter(
        self, postings: list[JobPosting], drops: dict[str, int], records: list[DropRecord]
    ) -> list[JobPosting]:
        kept: list[JobPosting] = []
        for p in postings:
            title = p.title.lower()
            if any(k in title for k in self._exclude_title_keywords):
                logger.info("prefilter drop (title) %s: %s", p.job_id, p.title)
                _drop(drops, records, "other filter", p, "excluded title keyword")
                continue
            if p.job_type and "intern" in p.job_type.lower():
                logger.info("prefilter drop (internship) %s: %s", p.job_id, p.job_type)
                _drop(drops, records, "other filter", p, "internship")
                continue
            cap = p.max_amount if p.max_amount is not None else p.min_amount
            if cap is not None and (p.currency or "INR").upper() == "INR":
                if cap / 100_000 < self._min_ctc_lpa:
                    logger.info("prefilter drop (salary) %s: %s", p.job_id, cap)
                    _drop(drops, records, "low pay",
                          p, f"{cap / 100_000:.1f} LPA < {self._min_ctc_lpa}")
                    continue
            if self._yoe_ceiling is not None and p.experience_range:
                min_years = _parse_min_years(p.experience_range)
                if min_years is not None and min_years > self._yoe_ceiling:
                    logger.info(
                        "prefilter drop (experience) %s: %s", p.job_id, p.experience_range
                    )
                    _drop(drops, records, "over-experience",
                          p, f"{min_years}y > ceiling {self._yoe_ceiling}")
                    continue
            kept.append(p)
        return kept
```

(d) Rewrite `discover` to accept `progress`, build `records`, and emit per-stage messages. Replace the whole method body:

```python
    def discover(
        self, progress: Callable[[str], None] = lambda _msg: None
    ) -> dict[str, list[BenchmarkedJob]]:
        t0 = time.monotonic()
        drops: dict[str, int] = {}
        records: list[DropRecord] = []
        rows = self._gather_rows()
        scrape_secs = time.monotonic() - t0
        logger.info("stage scrape: %d raw rows in %.1fs", len(rows), scrape_secs)
        progress(f"📡 Scraped {len(rows)} raw postings in {scrape_secs:.0f}s")
        postings = self._prefilter(normalize_rows(rows), drops, records)
        candidates: list[JobPosting] = []
        for p in postings:
            if self._already_seen(p.job_id):
                _drop(drops, records, "already seen", p, "already shown before")
            else:
                candidates.append(p)
        logger.info(
            "stage prefilter+dedup: %d candidates (from %d postings)",
            len(candidates), len(postings),
        )
        progress(
            f"🧹 {len(candidates)} candidates after prefilter + dedup (from {len(postings)})"
        )
        capped = candidates[: self._max_distill_per_cohort]
        if len(candidates) > self._max_distill_per_cohort:
            overflow = len(candidates) - self._max_distill_per_cohort
            _bump(drops, "capped", overflow)
            records.append(
                DropRecord("capped", "(summary)",
                           f"{overflow} jobs beyond the distill cap (not distilled this run)")
            )
            logger.info(
                "distill cap: %d of %d candidates (raise max_distill_per_cohort for more)",
                self._max_distill_per_cohort, len(candidates),
            )
            progress(f"✂️ Distill cap: {self._max_distill_per_cohort} of "
                     f"{len(candidates)} candidates ({overflow} deferred)")
        by_id = {p.job_id: p for p in capped}
        distiller = Distiller(self._distiller_provider, seed=self._distill_seed)
        t = time.monotonic()
        cruxes = distill_all(capped, self._store, distiller)
        logger.info(
            "stage distill: %d/%d jobs in %.1fs", len(cruxes), len(capped), time.monotonic() - t
        )
        progress(f"🧪 Distilled {len(cruxes)}/{len(capped)} JDs in {time.monotonic() - t:.0f}s")
        m_cruxes: list[Crux] = []
        n_cruxes: list[Crux] = []
        for c in cruxes:
            p = by_id[c.job_id]
            excluded, reason = crux_excluded(c.model_dump(), self._exclude_when)
            if excluded:
                field = reason.split()[0] if reason else ""
                _drop(drops, records, _EXCLUDE_BUCKET.get(field, "other filter"),
                      p, reason or "excluded by rule")
                logger.info("exclude_when drop %s: %s", c.job_id, reason)
                continue
            if self._candidate_skills:
                drop, missing = coverage_drop(
                    c.must_have_skills, self._candidate_skills, self._skill_synonyms,
                    max_missing_ratio=self._max_missing_skill_ratio,
                )
                if drop:
                    _drop(drops, records, "wrong stack", p, f"missing {', '.join(missing)}")
                    logger.info("must-have drop %s: missing %s", c.job_id, missing)
                    continue
            lpa = effective_lpa(by_id[c.job_id], c)
            if lpa is not None and lpa < self._min_ctc_lpa:
                _drop(drops, records, "low pay", p, f"{lpa:.1f} LPA < {self._min_ctc_lpa}")
                logger.info("salary-floor drop %s: %.1f LPA < %d", c.job_id, lpa, self._min_ctc_lpa)
                continue
            (m_cruxes if lpa is not None else n_cruxes).append(c)
        logger.info(
            "cohorts after gate+partition: M (stated INR pay)=%d, N (no stated pay)=%d",
            len(m_cruxes), len(n_cruxes),
        )
        progress(f"📊 Cohorts — 💰{len(m_cruxes)} stated-pay · 📋{len(n_cruxes)} no-pay")
        result = {
            "M": self._rank_cohort("M", m_cruxes, by_id),
            "N": self._rank_cohort("N", n_cruxes, by_id),
        }
        for cohort in (result["M"], result["N"]):
            for bj in cohort:
                p = bj.posting
                if not self._store.exists(p.job_id):
                    self._store.add(p.job_id, p.company, p.title, p.url)
        logger.info(
            "discover total: %.1fs — presenting M=%d, N=%d (filtered: %s)",
            time.monotonic() - t0, len(result["M"]), len(result["N"]), drops,
        )
        result["_dropped"] = drops  # type: ignore[assignment]  # footer-only metadata
        result["_drop_records"] = records  # type: ignore[assignment]  # grouped drop report
        return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_discovery.py -q`
Expected: PASS (existing + 2 new)

- [ ] **Step 5: Full gate + commit**

```bash
pytest -q && ruff check . && mypy --strict src
git add src/cvflow/discovery/__init__.py tests/test_discovery.py
git commit -m "feat(discovery): progress callback + DropRecord collection in discover()"
```

---

## Task 3: `format_drop_report` — grouped + chunked

**Files:**
- Modify: `src/cvflow/cron.py`
- Test: `tests/test_drop_report.py`

- [ ] **Step 1: Write the failing tests**

```python
"""format_drop_report — group all drops by bucket, chunk to <=4000-char Telegram messages."""

from cvflow.cron import format_drop_report
from cvflow.discovery import DropRecord


def test_empty_returns_no_messages():
    assert format_drop_report([]) == []


def test_groups_in_bucket_order_with_headers():
    records = [
        DropRecord("wrong stack", "React Dev @ A", "missing react"),
        DropRecord("too senior", "Lead Eng @ B", "seniority_signal senior"),
        DropRecord("wrong stack", "Vue Dev @ C", "missing vue"),
    ]
    msgs = format_drop_report(records)
    text = "\n".join(msgs)
    # too senior precedes wrong stack (DROP_BUCKET_ORDER)
    assert text.index("too senior") < text.index("wrong stack")
    assert "🚫 Dropped 1 — too senior" in text
    assert "🚫 Dropped 2 — wrong stack" in text
    assert "• Lead Eng @ B — seniority_signal senior" in text
    assert "• React Dev @ A — missing react" in text


def test_every_message_under_4000_chars_and_splits_keep_header():
    records = [DropRecord("wrong stack", f"Job {i} @ Co", "missing react node go") for i in range(400)]
    msgs = format_drop_report(records)
    assert len(msgs) > 1  # 400 rows can't fit one 4000-char message
    assert all(len(m) <= 4000 for m in msgs)
    # every chunk carries the bucket header context
    assert all("wrong stack" in m for m in msgs)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_drop_report.py -q`
Expected: FAIL — `ImportError: cannot import name 'format_drop_report'`

- [ ] **Step 3: Implement** — add to `src/cvflow/cron.py` (after `_filtered_footer`):

```python
_MAX_MSG = 4000  # Telegram's hard limit is 4096; leave headroom.


def format_drop_report(records: list[Any]) -> list[str]:
    """Group drop records by bucket (in DROP_BUCKET_ORDER) and chunk into <=4000-char
    messages. A bucket that overflows one message repeats its header (cont.) so each
    chunk keeps context. Empty input -> []."""
    if not records:
        return []
    from cvflow.discovery import DROP_BUCKET_ORDER

    grouped: dict[str, list[Any]] = {}
    for r in records:
        grouped.setdefault(r.bucket, []).append(r)

    messages: list[str] = []
    current: list[str] = []
    length = 0

    def flush() -> None:
        nonlocal current, length
        if current:
            messages.append("\n".join(current))
            current = []
            length = 0

    def add(line: str) -> None:
        nonlocal length
        if length + len(line) + 1 > _MAX_MSG:
            flush()
        current.append(line)
        length += len(line) + 1

    for bucket in DROP_BUCKET_ORDER:
        recs = grouped.get(bucket)
        if not recs:
            continue
        header = f"🚫 Dropped {len(recs)} — {bucket}"
        add(header)
        for r in recs:
            line = f"• {r.label} — {r.detail}"
            if length + len(line) + 1 > _MAX_MSG:
                flush()
                add(f"{header} (cont.)")
            add(line)
    flush()
    return messages
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_drop_report.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Full gate + commit**

```bash
pytest -q && ruff check . && mypy --strict src
git add src/cvflow/cron.py tests/test_drop_report.py
git commit -m "feat(cron): format_drop_report — grouped, chunked drop report"
```

---

## Task 4: `run_job` + `main` — lock, progress, drop report, `--progress`

**Files:**
- Modify: `src/cvflow/cron.py`
- Test: `tests/test_cron.py`

- [ ] **Step 1: Write the failing tests** (append to `tests/test_cron.py`)

```python
def test_run_job_discover_with_progress_streams_and_reports_drops():
    notes = []

    class _Disc:
        def discover(self, progress=lambda _m: None):
            progress("📡 Scraped 5 raw postings in 1s")
            r = _result()
            r["_drop_records"] = [_DropRecord("wrong stack", "X @ Y", "missing react")]
            return r

    from cvflow.cron import run_job
    from cvflow.discovery import DropRecord as _DropRecord  # noqa: F401 used above
    run_job("discover", store=ApplicationStore(":memory:"), discovery=_Disc(),
            otp=None, notify=notes.append, progress=notes.append, report_drops=True)
    assert any("📡 Scraped" in n for n in notes)
    assert any("https://jobs/indeed:7" in n for n in notes)   # digest
    assert any("wrong stack" in n for n in notes)             # drop report


def test_run_job_discover_plain_sends_neither_progress_nor_drops():
    notes = []

    class _Disc:
        def discover(self, progress=lambda _m: None):
            progress("📡 should-not-be-sent")
            r = _result()
            r["_drop_records"] = [_DR("wrong stack", "X @ Y", "missing react")]
            return r

    from cvflow.cron import run_job
    from cvflow.discovery import DropRecord as _DR  # noqa: F401
    run_job("discover", store=ApplicationStore(":memory:"), discovery=_Disc(),
            otp=None, notify=notes.append)
    assert not any("should-not-be-sent" in n for n in notes)
    assert not any("wrong stack" in n for n in notes)
    assert any("https://jobs/indeed:7" in n for n in notes)   # digest still sent


def test_run_job_discover_bails_when_lock_held():
    from contextlib import contextmanager

    from cvflow.cron import run_job
    from cvflow.runlock import AlreadyRunning

    @contextmanager
    def _held_lock():
        raise AlreadyRunning("held")
        yield  # pragma: no cover

    notes = []

    class _Disc:
        def discover(self, progress=lambda _m: None):
            raise AssertionError("discover must not run when lock is held")

    run_job("discover", store=ApplicationStore(":memory:"), discovery=_Disc(),
            otp=None, notify=notes.append, lock=_held_lock())
    assert any("already in progress" in n for n in notes)


def test_main_discover_progress_flag_wires_progress_and_drop_report():
    notes = []

    class _Disc:
        def discover(self, progress=lambda _m: None):
            progress("📡 Scraped 3 raw postings in 1s")
            r = {"M": [], "N": []}
            r["_drop_records"] = [_DR2("wrong stack", "X @ Y", "missing react")]
            return r

    from cvflow.cron import main
    from cvflow.discovery import DropRecord as _DR2  # noqa: F401
    main(["discover", "--progress"],
         services=(ApplicationStore(":memory:"), _Disc(), None, notes.append, None))
    assert any("📡 Scraped" in n for n in notes)
    assert any("wrong stack" in n for n in notes)
```

Also update the existing fake `_Disc` classes in `tests/test_cron.py`
(`test_run_job_discover_sends_digest`, `test_run_job_discover_sets_slots_in_combined_order`,
`test_main_notifies_on_job_failure`) so their `discover` accepts the new kwarg:
change each `def discover(self):` to `def discover(self, progress=lambda _m: None):`.
(`test_main_notifies_on_job_failure`'s `_Disc.discover` keeps raising — just add the param.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cron.py -q`
Expected: FAIL — `TypeError: run_job() got an unexpected keyword argument 'progress'` and the new tests error.

- [ ] **Step 3: Implement** — change the `discover` branch of `run_job` and its signature, and `main`'s arg parsing.

Replace the `run_job` signature and the `if job == "discover":` branch:

```python
def run_job(
    job: str, *, store: Any, discovery: Any, otp: Any, notify: Any,
    learn_provider: Any = None, min_decisions: int = 5,
    learning_dir: str = "data/learning",
    progress: Callable[[str], None] | None = None,
    report_drops: bool = False,
    lock: Any = None,
) -> None:
    """Dispatch one scheduled job. Pure of config/network — deps are injected.

    For ``discover``: a single-run lock (default real ``discovery_lock``) prevents the
    daily cron and a manual ``/discover`` double-running. ``progress`` streams per-stage
    messages (manual only); ``report_drops`` posts the grouped drop report after the digest.
    """
    if job == "discover":
        from cvflow.runlock import AlreadyRunning, discovery_lock

        cm = lock if lock is not None else discovery_lock()
        try:
            with cm:
                result = discovery.discover(progress=progress or (lambda _m: None))
                ordered = [bj.posting.job_id for bj in result.get("M", []) + result.get("N", [])]
                store.set_digest_slots(ordered)
                notify(format_digest(result))
                if report_drops:
                    for chunk in format_drop_report(result.get("_drop_records", [])):
                        notify(chunk)
        except AlreadyRunning:
            notify("⏳ A discovery run is already in progress — the digest is on its way")
        return
    elif job == "sweep-otp":
```

Add the import at the top of `cron.py` (after `from typing import Any`):

```python
from collections.abc import Callable
```

Replace `main`'s argument handling (the `if len(args) != 1:` block and the `run_job(...)` call):

```python
    args = list(argv) if argv is not None else sys.argv[1:]
    if not args or len(args) > 2:
        raise SystemExit(
            "usage: python -m cvflow.cron <discover [--progress]|sweep-otp|heartbeat|learn>"
        )
    job = args[0]
    manual = job == "discover" and "--progress" in args[1:]
```

...keep the `services is None` block unchanged, then:

```python
    store, discovery, otp, notify, brain = services
    try:
        run_job(
            job, store=store, discovery=discovery, otp=otp, notify=notify,
            learn_provider=brain,
            progress=notify if manual else None,
            report_drops=manual,
        )
    except Exception as exc:  # noqa: BLE001 — a cron crash must still reach the user
        notify(f"⚠️ cvflow cron job {job!r} failed: {exc}")
        raise
```

(Note: replace the prior `args[0]` references with `job`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cron.py -q`
Expected: PASS (existing + 4 new)

- [ ] **Step 5: Full gate + commit**

```bash
pytest -q && ruff check . && mypy --strict src
git add src/cvflow/cron.py tests/test_cron.py
git commit -m "feat(cron): --progress flag, run lock, drop-report send for discover"
```

---

## Task 5: `cvflow.discover_command` — the gate's twin

**Files:**
- Create: `src/cvflow/discover_command.py`
- Test: `tests/test_discover_command.py`

- [ ] **Step 1: Write the failing tests**

```python
"""handle_discover_command — deterministic /discover trigger (off the agent loop)."""

from cvflow.discover_command import DiscoverResult, handle_discover_command


def _spy():
    calls = {"n": 0}

    def spawn():
        calls["n"] += 1

    return spawn, calls


def test_authorized_and_free_spawns_and_acks():
    spawn, calls = _spy()
    res = handle_discover_command(
        user_id=42, authorized_user_id=42, spawn=spawn, is_locked=lambda: False
    )
    assert res.handled is True
    assert calls["n"] == 1
    assert "Discovery started" in res.message


def test_authorized_but_locked_does_not_spawn():
    spawn, calls = _spy()
    res = handle_discover_command(
        user_id=42, authorized_user_id=42, spawn=spawn, is_locked=lambda: True
    )
    assert res.handled is True
    assert calls["n"] == 0
    assert "already in progress" in res.message


def test_unauthorized_ignored_no_spawn():
    spawn, calls = _spy()
    res = handle_discover_command(
        user_id=999, authorized_user_id=42, spawn=spawn, is_locked=lambda: False
    )
    assert res.handled is False
    assert calls["n"] == 0
    assert res.message == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_discover_command.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'cvflow.discover_command'`

- [ ] **Step 3: Implement**

```python
"""Deterministic ``/discover`` trigger — the gate's twin, off the agent loop.

A Hermes ``command:discover`` hook invokes :func:`handle_discover_command` in the gateway
process (outside the brain). It is a *trigger*, never an approval: it spawns a detached
discovery run and acks. ``spawn`` and ``is_locked`` are injected so tests never fork a
process or touch a real lock. The approval-gate invariant (CLAUDE.md #1) is untouched —
there is no ``approve`` anywhere here.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

__all__ = ["DiscoverResult", "handle_discover_command"]


@dataclass(frozen=True)
class DiscoverResult:
    """Outcome of a /discover command. ``handled`` short-circuits Hermes dispatch."""

    handled: bool
    message: str


def _same_user(a: object, b: object) -> bool:
    return str(a).strip() == str(b).strip()


def handle_discover_command(
    *,
    user_id: object,
    authorized_user_id: object,
    spawn: Callable[[], None],
    is_locked: Callable[[], bool],
) -> DiscoverResult:
    if not _same_user(user_id, authorized_user_id):
        return DiscoverResult(handled=False, message="")
    if is_locked():
        return DiscoverResult(
            handled=True,
            message="⏳ A discovery run is already in progress — the digest is on its way.",
        )
    spawn()
    return DiscoverResult(
        handled=True,
        message="🔎 Discovery started — I'll post progress, the digest, "
        "and the dropped-jobs report here.",
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_discover_command.py -q`
Expected: PASS (3 passed)

- [ ] **Step 5: Full gate + commit**

```bash
pytest -q && ruff check . && mypy --strict src
git add src/cvflow/discover_command.py tests/test_discover_command.py
git commit -m "feat(discover-command): handle_discover_command trigger (gate's twin)"
```

---

## Task 6: Hermes hook adapter (`cvflow-discover`)

**Files:**
- Create: `artifacts/hermes/hooks/cvflow-discover/HOOK.yaml`
- Create: `artifacts/hermes/hooks/cvflow-discover/handler.py`
- Test: `tests/test_hermes_discover_adapters.py`

- [ ] **Step 1: Write the failing tests**

```python
"""cvflow-discover hook adapter — routes /discover to handle_discover_command."""

import asyncio
import importlib.util
from pathlib import Path

HOOK = Path("artifacts/hermes/hooks/cvflow-discover/handler.py")


def _load(path):
    spec = importlib.util.spec_from_file_location("cvflow_discover_hook", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_hook_authorized_spawns_and_acks(monkeypatch):
    mod = _load(HOOK)
    calls = {"n": 0}
    monkeypatch.setattr(mod, "_authorized_user_id", lambda: 42)
    monkeypatch.setattr(mod, "_spawn", lambda: calls.__setitem__("n", calls["n"] + 1))
    monkeypatch.setattr(mod, "_is_locked", lambda: False)
    result = asyncio.run(mod.handle("command:discover", {"user_id": 42}))
    assert result["decision"] == "handled"
    assert "Discovery started" in result["message"]
    assert calls["n"] == 1


def test_hook_ignores_unauthorized(monkeypatch):
    mod = _load(HOOK)
    calls = {"n": 0}
    monkeypatch.setattr(mod, "_authorized_user_id", lambda: 42)
    monkeypatch.setattr(mod, "_spawn", lambda: calls.__setitem__("n", calls["n"] + 1))
    monkeypatch.setattr(mod, "_is_locked", lambda: False)
    result = asyncio.run(mod.handle("command:discover", {"user_id": 999}))
    assert result == {}
    assert calls["n"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_hermes_discover_adapters.py -q`
Expected: FAIL — handler file does not exist (`spec_from_file_location` → exec fails / FileNotFoundError).

- [ ] **Step 3: Create `HOOK.yaml`**

```yaml
name: cvflow-discover
description: Deterministic manual discovery trigger for cvflow (/discover).
events:
  - command:discover
```

- [ ] **Step 4: Create `handler.py`**

```python
"""Hermes command hook: routes /discover to cvflow's deterministic discovery trigger.

Runs in the gateway process, OUTSIDE the agent loop. A run takes ~15-20 min, so this
spawns a detached ``python -m cvflow.cron discover --progress`` (start_new_session=True)
and acks instantly; the detached run streams progress/digest/drop-report via HermesNotifier
(no gateway, no LLM). Unauthorized users -> {} (fall through). This is a *trigger*, never an
approval — the approval-gate invariant is untouched.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from typing import Any


def _repo_root() -> str:
    return os.environ.get("CVFLOW_ROOT", os.path.expanduser("~/cvflow"))


def _ensure_import() -> None:
    root_src = os.path.join(_repo_root(), "src")
    if root_src not in sys.path:
        sys.path.insert(0, root_src)


def _authorized_user_id():
    _ensure_import()
    from cvflow.config import load_config

    cfg = load_config(os.path.join(_repo_root(), "config.yaml"))
    return cfg.telegram.authorized_user_id


def _lock_path() -> str:
    return os.path.join(_repo_root(), "data", "discover.lock")


def _is_locked() -> bool:
    _ensure_import()
    from cvflow.runlock import is_locked

    return is_locked(_lock_path())


def _spawn() -> None:
    root = _repo_root()
    python = os.path.join(root, ".venv", "bin", "python")
    log_path = os.path.join(root, "data", f"discover-manual-{int(time.time())}.log")
    os.makedirs(os.path.join(root, "data"), exist_ok=True)
    log = open(log_path, "w")  # noqa: SIM115 — detached child owns this fd for its lifetime
    subprocess.Popen(  # noqa: S603 — fixed argv, no shell
        [python, "-m", "cvflow.cron", "discover", "--progress"],
        cwd=root,
        start_new_session=True,
        stdout=log,
        stderr=subprocess.STDOUT,
    )


async def handle(event_type: str, context: dict[str, Any]) -> dict[str, Any]:
    try:
        _ensure_import()
        from cvflow.discover_command import handle_discover_command

        res = handle_discover_command(
            user_id=context.get("user_id"),
            authorized_user_id=_authorized_user_id(),
            spawn=_spawn,
            is_locked=_is_locked,
        )
        if not res.handled:
            return {}
        return {"decision": "handled", "message": res.message}
    except Exception:  # noqa: BLE001 — never leak a stack trace to the user; fall through
        return {}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_hermes_discover_adapters.py -q`
Expected: PASS (2 passed)

- [ ] **Step 6: Full gate + commit**

```bash
pytest -q && ruff check . && mypy --strict src
git add artifacts/hermes/hooks/cvflow-discover tests/test_hermes_discover_adapters.py
git commit -m "feat(hermes): cvflow-discover command hook adapter"
```

---

## Task 7: Hermes plugin (`cvflow-discover`) + README

**Files:**
- Create: `artifacts/hermes/plugins/cvflow-discover/plugin.yaml`
- Create: `artifacts/hermes/plugins/cvflow-discover/__init__.py`
- Modify: `artifacts/README.md`

No new unit tests (the plugin is a thin command-registration shim mirroring `cvflow-gate`,
whose behaviour is covered by the hook test above). Verify import-cleanliness via the gate.

- [ ] **Step 1: Create `plugin.yaml`**

```yaml
name: cvflow-discover
version: 1.0.0
description: "Registers cvflow's /discover slash command so the gateway treats it as a known command. The cvflow-discover HOOK intercepts command:discover and spawns a detached discovery run — a trigger, never an approval."
author: "cvflow"
kind: standalone
```

- [ ] **Step 2: Create `__init__.py`**

```python
"""cvflow-discover plugin: makes the /discover slash command gateway-known.

The real work lives in the ``cvflow-discover`` HOOK (event ``command:discover``), which
the gateway fires only for *known* slash commands. ``/discover`` is not a Hermes built-in,
so this plugin registers it to make it known — which is what lets the hook fire. The
registered handler is a fallback only: in practice the hook intercepts first. If the hook
is missing, the fallback tells the user to install it rather than silently doing nothing.
"""

from __future__ import annotations

_FALLBACK = (
    "⚠️ cvflow /discover not active: the cvflow-discover hook is not installed. "
    "Install artifacts/hermes/hooks/cvflow-discover into ~/.hermes/hooks/ and restart "
    "the gateway."
)


def _fallback(raw_args: str) -> str | None:
    return _FALLBACK


def register(ctx) -> None:
    ctx.register_command(
        "discover",
        handler=_fallback,
        description="Trigger a cvflow discovery run now (handled by the cvflow-discover hook).",
        args_hint="",
    )
```

- [ ] **Step 3: Update `artifacts/README.md`**

In the install step that copies the gate hook/plugin (step 5), append the discover hook/plugin copy so both are installed together. After the existing `cp -r ... cvflow-gate ...` lines, add:

```bash
   cp -r artifacts/hermes/hooks/cvflow-discover   ~/.hermes/hooks/cvflow-discover
   cp -r artifacts/hermes/plugins/cvflow-discover ~/.hermes/plugins/cvflow-discover
   # add cvflow-discover to plugins.enabled in ~/.hermes/config.yaml (alongside cvflow-gate)
```

And in the Verify step (step 7), add a line:

```
   `/discover` from the authorized chat → `🔎 Discovery started …`, then progress
   messages, the digest, and the grouped drop report arrive over the next ~15-20 min.
```

Also update `hermes-config.yaml`'s `plugins.enabled` if it lists plugins explicitly —
add `cvflow-discover` next to `cvflow-gate`. (Check the file; if `plugins.enabled` is not
present or is a wildcard, leave it and rely on the README note.)

- [ ] **Step 4: Verify plugin imports cleanly + full gate**

```bash
python -c "import importlib.util, pathlib; \
spec=importlib.util.spec_from_file_location('p','artifacts/hermes/plugins/cvflow-discover/__init__.py'); \
m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); print('register' in dir(m))"
pytest -q && ruff check . && mypy --strict src
```
Expected: prints `True`; all green.

- [ ] **Step 5: Commit**

```bash
git add artifacts/hermes/plugins/cvflow-discover artifacts/README.md artifacts/hermes/hermes-config.yaml
git commit -m "feat(hermes): cvflow-discover plugin + install/verify docs"
```

---

## Task 8: Update build-plan progress log + memory

**Files:**
- Modify: `docs/superpowers/plans/2026-06-03-cvflow-build-plan.md` (progress log)
- Modify: `~/.claude/projects/-home-ubuntu-cvflow/memory/manual-discover-command.md`

- [ ] **Step 1:** Append a dated entry to the build-plan progress log recording the `/discover` command shipped (hook+plugin `cvflow-discover`, `runlock`, drop report, `--progress`), per CLAUDE.md ("keep the phase checkboxes and the plan's progress log in sync").

- [ ] **Step 2:** Update the `manual-discover-command` memory: flip status from "PLANNED / not yet implemented" to "BUILT 2026-06-09" and point at this plan + the shipped files. Update the `MEMORY.md` index line hook accordingly.

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/plans/2026-06-03-cvflow-build-plan.md
git commit -m "docs: record manual /discover command shipped in build plan"
```
(Memory files live outside the repo — no commit needed for those.)

---

## Final verification

- [ ] Run the whole suite + lint + types one last time:

```bash
pytest -q && ruff check . && mypy --strict src
```
Expected: all green, no warnings.

- [ ] Confirm the gate invariant is intact — no new `approve` caller:

```bash
grep -rn "\.approve(" src/cvflow | grep -v "src/cvflow/gate/"
```
Expected: no output (the gate remains the sole `approve` caller).
