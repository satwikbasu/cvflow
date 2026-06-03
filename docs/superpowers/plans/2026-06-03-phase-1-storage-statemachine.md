# Phase 1 — Storage + State Machine (safety-critical core) — TDD sub-plan

> Master roadmap: `2026-06-03-cvflow-build-plan.md` → Phase 1.
> **Exit:** tests prove illegal transitions raise; `approved` is reachable **only** via `approve()`;
> `guard_can_submit` blocks every non-approved status; dedup keyed by stable job ID is enforced.

## Invariants this phase enforces (CLAUDE.md)

- **The approval gate is deterministic.** Only `approve()` can produce `approved`, and only from
  `pending_review`. General `set_status` must *refuse* `approved` as a target. The submission guard
  asserts `status == approved` and raises otherwise. An agent may call these but can never satisfy the gate.
- **Never fabricate user facts.** `form_fields.json` empty value = known-but-unfilled → must be reported/asked,
  never guessed. The loader exposes populated vs missing explicitly.

## Work items

### 1.1 — `statemachine/` (TDD) — the gate core
- **States** (enum): `discovered`, `pending_review`, `approved`, `applied`, `otp_timeout`, `skipped`, `failed`.
- **Legal transition table:**
  - `discovered` → `pending_review`, `skipped`, `failed`
  - `pending_review` → `approved` (***only via `approve()`***), `skipped`, `failed`
  - `approved` → `applied`, `otp_timeout`, `failed`
  - `otp_timeout` → `applied`, `skipped`, `failed`
  - `applied`, `skipped`, `failed` → terminal (no outgoing)
- **API:**
  - `transition(current, target) -> Status`: validates against table; raises `IllegalTransition`.
    **Refuses `approved` as a target** (raises, pointing at `approve()`) so it can never be the path in.
  - `approve(current) -> Status`: the sole producer of `approved`; raises `IllegalTransition` unless `current == pending_review`.
  - `guard_can_submit(status) -> None`: raises `SubmissionBlocked` unless `status == approved`.
- **Tests:** every illegal transition raises; `transition(_, approved)` raises; `approve()` only from `pending_review`;
  `approve()` from any other state raises; `guard_can_submit` passes only for `approved` and blocks all 6 others; terminal states have no legal outgoing.

### 1.2 — `storage/` SQLite application store (TDD)
- **Schema** (`applications`): `job_id` TEXT PRIMARY KEY (stable dedup key), `company`, `role`, `jd_url`,
  `status`, `discovered_at`, `applied_at` (nullable), `tailored_pdf_path` (nullable), `confirmation_ref` (nullable).
- **`Application`** frozen dataclass mirroring a row.
- **`ApplicationStore(db_path)`** — creates schema on init (idempotent); usable on `:memory:` and tmp file:
  - `add(job_id, company, role, jd_url) -> Application` (status=`discovered`); raises `DuplicateJob` if job_id exists (**dedup**).
  - `get(job_id) -> Application | None`; `exists(job_id) -> bool`; `list_by_status(status) -> list[Application]`.
  - `set_status(job_id, target)` — routes through `statemachine.transition` (so it can never set `approved`);
    sets `applied_at` when target is `applied`; raises `UnknownJob` if absent.
  - `approve(job_id)` — the **only** store path to `approved`; routes through `statemachine.approve`.
  - `set_tailored_pdf(job_id, path)`, `set_confirmation(job_id, ref)`.
- **Tests:** add+get round-trip; duplicate job_id raises `DuplicateJob`; `set_status` rejects illegal transition;
  `set_status` cannot reach `approved`; `approve()` from `pending_review` works and persists; `approve()` elsewhere raises;
  `list_by_status` filters; unknown job raises.

### 1.3 — `form_fields.json` loader (TDD) — never-guess semantics
- **`FormFields.load(path) -> FormFields`** (ignores the `_comment` key).
  - `.values -> dict[str,str]`, `.populated -> dict[str,str]` (non-empty), `.missing() -> list[str]` (empty-valued keys).
  - `.is_filled(key) -> bool`; `.require(key) -> str` raises `MissingField` for empty **or** unknown key (never returns a guess).
- **Tests:** loads the example template; `missing()` returns the empty keys; `require` raises on an empty key and on an unknown key; `populated` excludes empties and `_comment`.

### 1.4 — harness green + commit
`pytest` green, `ruff`/`mypy(strict)` clean. Commit `feat(core): SQLite store + deterministic approval state machine + form-fields loader (Phase 1)`.
Tick Phase 1 box + progress log.

## Out of scope
Telegram wiring of the gate (Phase 8), browser submission that *calls* `guard_can_submit` (Phase 9).
