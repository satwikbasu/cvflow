# Phase 0 — Foundations (TDD sub-plan)

> Master roadmap: `2026-06-03-cvflow-build-plan.md` → Phase 0.
> **Exit:** `pytest` runs (config tests green); `ruff`/`mypy` clean; config loads from a real `config.yaml`.

## Goal

A runnable package skeleton: pinned deps + tooling config, a typed/validated config loader,
rotating file logging into `logs/`, and a green test harness.

## Standing rules in play

- TDD: failing test → watch fail → minimal impl → watch pass → commit (small Conventional Commits).
- Never fail silently: a malformed/missing config or unwritable log dir must raise a clear error, not pass quietly.
- Secrets never in git: tests use a synthetic fixture config, **never** the real `config.yaml`.

## Work items

### 0.1 — `pyproject.toml` + tooling
- Move to `pyproject.toml` as the single source: project metadata, `src/` layout, pinned runtime deps
  (mirror `requirements.txt`: python-jobspy, playwright, google-genai, cryptography, pyyaml),
  dev deps (pytest, ruff, mypy, types-PyYAML).
- Configure `[tool.pytest.ini_options]` (testpaths=tests, src on path), `[tool.ruff]`, `[tool.mypy]` (strict-ish, `src`).
- Keep `requirements.txt` as the deploy pin list; pyproject is dev/build truth.
- **Verify:** `pip install -e ".[dev]"` succeeds; `pytest` collects 0/➀ tests; `ruff check .` & `mypy src` clean on empty package.

### 0.2 — `src/cvflow/config.py` (TDD) — the deliverable
Typed, validated loader for `config.yaml`.
- **Test first** (`tests/test_config.py`), using a tmp_path fixture YAML (synthetic, no real secrets):
  1. valid config → returns a typed `Config` with nested sections (telegram, schedule, discovery, llm.brain/tailoring, resume, automation, auth, storage, security, profile).
  2. missing required key → raises `ConfigError` naming the key/path.
  3. wrong type (e.g. `authorized_user_id` not int, `daily_discovery_time` malformed) → raises `ConfigError`.
  4. api keys are `.strip()`-ed (real config has a leading space in the NIM key).
  5. `load_config(path)` raises `ConfigError` (not bare FileNotFoundError) when the file is missing.
- **Impl:** frozen `@dataclass` per section + top-level `Config`; `load_config(path) -> Config`;
  one `ConfigError(Exception)`; validate presence + types + the `HH:MM` format. No env-var magic, no defaults that mask missing secrets.
- **Verify:** the 5 tests pass; `mypy`/`ruff` clean.

### 0.3 — logging setup (`src/cvflow/logging_setup.py`) (TDD)
- **Test first:** `setup_logging(log_dir)` creates a rotating file handler writing into the dir, returns a configured logger, is idempotent (no duplicate handlers on repeat calls).
- **Impl:** `RotatingFileHandler` → `logs/cvflow.log`, console handler, level from arg/default INFO. Telegram surfacing comes later (Phase 8) — out of scope here.
- **Verify:** test asserts a log file is written and handler count stays stable across calls.

### 0.4 — harness green + commit
- `pytest` green, `ruff check .` clean, `mypy src` clean.
- Commit: `feat(config): typed config loader + logging + test harness (Phase 0)`.
- Tick Phase 0 box in the master plan; append to progress log.

## Out of scope (later phases)
SQLite/state machine (Phase 1), Gemini client (Phase 2), KB loader (Phase 3), Telegram surfacing of logs (Phase 8).
