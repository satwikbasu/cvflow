# P1B — Digest log retention + LLM drop-summary (TDD sub-plan)

> Executes workstream **1B** of `2026-06-10-phase-1-personal-hardening.md`, as refined by the
> founder on 2026-06-10. Standing rules from CLAUDE.md + the master plan apply (TDD, Conventional
> Commits straight to main, no `Co-Authored-By`, never fail silently, new config keys are
> **optional-with-defaults** so the live config keeps loading).

**Goal:** Stop flooding chat with the raw multi-hundred-line drop report. Keep the digest exactly
as it is today. Always retain the full digest + drop report to a log file on disk. On a **manual
`/discover`** only, send ONE LLM-written summary of the dropped jobs (compressed first, then
Cerebras). The daily cron sends the digest only. All knobs live in `config.yaml`.

**Key facts (verified 2026-06-10):**
- `cron.run_job("discover")`: today the daily cron (`main(["discover"])`) already sends digest
  only (`report_drops=False`); the manual `/discover` (`main(["discover","--progress"])`) sets
  `report_drops=True` and currently sends `format_drop_report(...)` chunks raw to chat — THAT is
  the 1092-line flood in `data/telegram-discovery.txt`.
- `format_digest` and `format_drop_report` stay **untouched**. `digest_slots` set from the full
  result (unchanged). The drop summary is the only new LLM call.
- Provider: Cerebras (the `tailoring` block) — manual `/discover` is infrequent, so the 5 RPM tier
  is fine, and reasoning quality is better than NIM.

---

## New config keys (all under `discovery:`, optional with defaults)

| key | default | meaning |
|---|---|---|
| `log_dir` | `"logs/discover"` | full digest + drop report written here every run |
| `summarize_drops` | `true` | manual `/discover` only: LLM-summarize the drop log to chat |
| `drop_summary_provider` | `"tailoring"` | which `llm.*` block to use (`tailoring`=Cerebras / `brain` / `distillation`) |
| `drop_summary_max_chars` | `700` | cap on the summary message |
| `drop_summary_samples_per_bucket` | `3` | compression: example titles per bucket fed to the LLM |
| `cron_sends_drops` | `false` | daily cron stays digest-only (kept for completeness; default off) |

---

### Task 1 — config: optional discovery-summary keys

**Files:** `src/cvflow/config.py`, `config.example.yaml`; tests `tests/test_config.py`.

- Add optional getters to `config.py` (they default instead of raising when the key is absent):
  `_opt_str`, `_opt_int`, `_opt_bool` (mirror the existing `_get_*` type-guards but return a
  supplied default when the key is missing).
- Extend `DiscoveryConfig` with the six fields above (defaults baked into the dataclass too).
  Parse them in `load_config` via the optional getters so an existing `config.yaml` with none of
  them still loads unchanged.
- `config.example.yaml`: add the six keys under `discovery:` with the comments from the table.

**Tests:** (a) a config dict with none of the new keys loads and yields the documented defaults;
(b) overriding each key is reflected; (c) a bad type still raises `ConfigError`.

**Commit:** `feat(config): optional discovery drop-summary keys`

---

### Task 2 — `cvflow/digest_summary.py`: log writer + compressor + summarizer

**Files:** create `src/cvflow/digest_summary.py`; tests `tests/test_digest_summary.py`.

```python
def write_discover_log(digest_text: str, drop_chunks: list[str], log_dir: str,
                       *, now: Callable[[], datetime] = ...) -> Path:
    """Write the full digest + drop report to <log_dir>/<UTC-ISO>.md; return the path.
    Created before any LLM call so retention never depends on the model."""

def compress_drops(records: list[DropRecord], samples_per_bucket: int) -> str:
    """Deterministic compact block: group records by bucket in DROP_BUCKET_ORDER, one line per
    non-empty bucket = '- <bucket>: <count> — e.g. "<label>", "<label>", …' (≤ samples_per_bucket
    examples). Stable regardless of total drop count. '' when no records."""

def summarize_drops(records, *, provider, max_chars, samples_per_bucket) -> str | None:
    """One provider.generate() call over compress_drops(...). Returns a friendly ≤max_chars
    paragraph, or None when there are no records or the provider raises (caller falls back)."""
```

- `summarize_drops` prompt: warm, ≤max_chars, group the dropped jobs by reason in plain language,
  name the biggest buckets with a couple of example titles, note if nothing salaried was dropped
  on pay; **do not invent jobs or counts — use only the supplied block**. `provider.generate`
  may raise `LLMError`/`RpmExceeded` → return `None`.
- Catch the provider exception types from `cvflow.llm` (`LLMError` covers `RpmExceeded`).

**Tests:** compressor groups in `DROP_BUCKET_ORDER`, caps samples, handles the `capped` summary
record, returns `""` on empty; `write_discover_log` writes a file containing both the digest and
the drop chunks and returns an existing path; `summarize_drops` returns the provider text on
success and `None` when the provider raises (fake providers).

**Commit:** `feat(digest-summary): drop-log writer + compressor + LLM summarizer`

---

### Task 3 — wire into `cron.run_job` / `main`

**Files:** `src/cvflow/cron.py`; tests `tests/test_cron.py`.

- Lift the provider factory in `_build` to a module-level `_build_provider(cfg_block) -> NimProvider`
  so `main` can build the summary provider too (no behavior change to `_build`).
- `run_job` gains kwargs (all defaulted, injected by `main`):
  `summary_provider: Any = None`, `log_dir: str = "logs/discover"`,
  `summary_max_chars: int = 700`, `summary_samples: int = 3`.
- New discover-branch behavior (replacing the raw-chunk loop):
  ```python
  digest = format_digest(result)
  chunks = format_drop_report(result.get("_drop_records", []))
  log_path = write_discover_log(digest, chunks, log_dir)   # always retain
  store.set_digest_slots(ordered)
  notify(digest)                                            # digest unchanged
  if report_drops:                                          # manual /discover only
      summary = (summarize_drops(result.get("_drop_records", []), provider=summary_provider,
                                 max_chars=summary_max_chars, samples_per_bucket=summary_samples)
                 if summary_provider else None)
      tail = f"\n📄 Full breakdown: {log_path}"
      notify((summary or _filtered_footer(result.get("_dropped")) or "🚫 No jobs dropped.") + tail)
  ```
  The raw `for chunk in format_drop_report(...)` chat loop is removed. Fallback on LLM failure =
  the deterministic `_filtered_footer` line + log pointer (never silent, never the raw dump).
- `main`: in the real (`services is None`) path, read `cfg.discovery.log_dir`/`summarize_drops`/
  `drop_summary_*`/`cron_sends_drops`; build the summary provider from
  `cfg.llm.<drop_summary_provider>` via `_build_provider`; pass into `run_job`. Manual run sets
  `report_drops = manual and cfg.discovery.summarize_drops`; the daily cron passes
  `report_drops = cfg.discovery.cron_sends_drops` (default False). Injected-service test runs keep
  `summary_provider=None` (no log/summamry) so existing `test_cron.py` cases are unaffected.

**Tests (extend `tests/test_cron.py`):** (a) manual run with a fake summary provider → chat gets
the summary + "Full breakdown" pointer, NOT raw chunks; log file written; (b) summary provider
raises → chat gets the `_filtered_footer` fallback + pointer, never the raw chunks; (c) daily run
(`report_drops` False) → digest only, no drop message, but the log file is still written; (d)
digest text byte-identical to `format_digest(result)` in both modes.

**Commit:** `feat(cron): retain full discover log, LLM-summarize drops on manual /discover`

---

### Task 4 — docs

**Files:** `docs/deploy.md` (note the new `discovery.*` summary keys + that `/discover` now sends
a summary + a log path under `logs/discover/`), `docs/architecture-and-workflow.md` (§6/§13 update:
drops summarized not dumped; log retained), CLAUDE.md if it references the drop report.

**Commit:** `docs: document discover log retention + drop summary`

---

## Definition of done
- Manual `/discover` chat output: progress + the unchanged digest + ONE ≤700-char drop summary +
  a `logs/discover/<ts>.md` pointer. No 100+-line dumps.
- Daily cron: digest only; full log still on disk.
- LLM failure → deterministic footer fallback; full detail always retained on disk first.
- Every new config key optional; live `config.yaml` loads unchanged. `pytest`/`ruff`/`mypy` green.

## Progress log
- 2026-06-10 — sub-plan written.
- 2026-06-10 — **SHIPPED** (subagent-driven, all 4 tasks reviewed). Commits: `a8ea8a5` (config keys),
  `fd1c407` (digest_summary module), `899a51c` (cron wiring), `f1b381d` (docs). 275 tests green,
  ruff + mypy --strict clean. Manual `/discover` now sends progress + the unchanged digest + ONE
  Cerebras-summarized drop note + a `logs/discover/<ts>.md` pointer; daily cron is digest-only;
  full digest+drops always retained on disk; LLM failure falls back to the deterministic footer.
  All knobs in `discovery.*` config (optional-with-defaults; live config loads unchanged).
