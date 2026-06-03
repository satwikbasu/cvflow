# Phase 2 — LLM layer (Gemini tailoring client) — TDD sub-plan

> Master roadmap: `2026-06-03-cvflow-build-plan.md` → Phase 2.
> **Exit:** tests (mocked, no live key) prove the call path, RPD accounting, and cache hits.

## Scope

Only the **Gemini** client used by the resume-tailoring escalation. The agent *brain*
(NIM `meta/llama-3.3-70b-instruct`) is configured inside Hermes in Phase 7 — **not** here.

## Standing rules in play
- **Respect free-tier limits by design** (rule 5): Gemini RPD is tracked and enforced; the provider
  refuses to exceed `max_requests_per_day` rather than letting the API bill/deny silently.
- **Never fail silently** (rule 4): exceeding RPD raises a clear `RpdExceeded`, not a swallowed error.
- **Cache and batch LLM calls** (rule 5): identical prompts are served from cache and do **not** consume RPD.

## Work items

### 2.1 — `src/cvflow/llm/` — `GeminiProvider` (TDD)
- **Constructor:** `GeminiProvider(api_key, model, max_requests_per_day, *, client=None, now=...)`.
  - `client` is injectable for tests (a fake with `.models.generate_content(...)`); when `None`,
    lazily builds `google.genai.Client(api_key=...)` so import/tests need no live key.
  - `now` is an injectable clock (`Callable[[], date]`) for deterministic day-rollover tests.
- **`generate(prompt: str) -> str`:**
  1. Cache hit (keyed by `(model, prompt)`) → return cached text, **no** RPD consumed.
  2. Else enforce RPD: if today's count `>= max_requests_per_day` → raise `RpdExceeded`.
  3. Call `client.models.generate_content(model=..., contents=prompt)`; take `.text`;
     increment today's count; store in cache; return text.
- **Day rollover:** the per-day counter resets when the injected clock's date changes.
- **Errors:** `LLMError` base; `RpdExceeded(LLMError)`.

- **Tests (mocked client, no network):**
  1. `generate` calls the client once and returns `.text`.
  2. RPD accounting: N calls with distinct prompts increment the counter; the (N+1)th over budget raises `RpdExceeded`.
  3. Cache hit: same prompt twice → client called once; second is cache-served and does not consume RPD.
  4. Day rollover resets the counter (advance injected clock) → calls allowed again.
  5. Budget of 0 → first call raises `RpdExceeded` before touching the client.

### 2.2 — harness green + commit
`pytest` green, `ruff`/`mypy(strict)` clean. Add `google-genai` already pinned in pyproject.
Commit `feat(llm): Gemini tailoring client with RPD tracking + response cache (Phase 2)`.
Tick Phase 2 box + progress log.

## Out of scope
The NIM brain (Hermes config, Phase 7); prompt construction for tailoring (Phase 6 uses this client).
