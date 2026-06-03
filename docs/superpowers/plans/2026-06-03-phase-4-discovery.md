# Phase 4 — Discovery (Goal 1) — TDD sub-plan

> Master roadmap: `2026-06-03-cvflow-build-plan.md` → Phase 4.
> **Exit:** tests (mocked JobSpy output) prove dedup across runs, ranking ordering, and top-N selection.

## Standing rules in play
- **Respect free-tier limits / throttle** (rule 5): a configurable delay between scrape calls (injectable sleep).
- **Never fabricate** (rule 3): ranking only reorders real postings; it never invents jobs. Unknown ids from the LLM are dropped.
- **Privacy risk (open):** ranking sends profile PII to the free-tier LLM. Flagged in the plan's open risks;
  tests are fully mocked (no live API). Note in the commit; resolve before live runs.
- **Cost (rule 6):** no new paid surface — reuses the Phase-2 Gemini free tier + JobSpy (free).

## Design (injection for testability)
- `search_fn(*, site_name, search_term, location, results_wanted, hours_old) -> list[dict]` —
  default wraps `jobspy.scrape_jobs(...).to_dict("records")`; tests inject a fake returning row dicts (no pandas/network).
- Ranking via the Phase-2 `GeminiProvider` wrapped in an `LLMRanker`; tests inject a fake provider returning JSON.
- Cross-day dedup via the Phase-1 `ApplicationStore` (`exists()` / `add()`).

## Work items — `src/cvflow/discovery/`

### 4.1 — normalization (TDD)
- `JobPosting` frozen dataclass: `job_id, title, company, location, description, url, site, date_posted`.
- `normalize_rows(rows) -> list[JobPosting]`: stable `job_id = f"{site}:{id}"` (fallback: sha1 of url);
  skips rows with no usable url/id. Within-batch dedup by `job_id` (first wins).

### 4.2 — `LLMRanker` (TDD)
- `LLMRanker(provider, profile_context)`; `rank(postings, top_n) -> list[RankedJob]`.
- Builds a prompt embedding `profile_context` + numbered postings; calls `provider.generate`;
  parses a JSON array of `{job_id, summary, rationale}` (best-first); strips ```` ```json ```` fences.
- Maps back to the real `JobPosting`; **drops** any job_id not in the candidate set (never fabricate);
  truncates to `top_n`. `RankedJob` frozen dataclass: `posting, summary, rationale`.

### 4.3 — `DiscoveryService.discover()` (TDD)
- `DiscoveryService(store, ranker, search_fn=..., *, search_terms, locations, sites, results_wanted_per_site, hours_old, top_n, throttle_seconds=..., sleep=time.sleep)`.
- Flow: iterate `search_terms × locations` (one scrape per pair across all `sites`), `sleep(throttle_seconds)` between calls →
  `normalize_rows` → **drop** any `job_id` already in `store` (cross-day dedup) and within-batch dups →
  `ranker.rank(candidates, top_n)` → persist each presented job to the store as `discovered` (`store.add`) →
  return the `list[RankedJob]`.

## Tests (mocked search_fn + ranker/provider)
1. `normalize_rows`: rows → postings with stable `site:id` job_id; within-batch dedup; url-fallback id.
2. cross-day dedup: a job already in the store is excluded from candidates handed to the ranker.
3. ranking ordering + top-N: service returns the ranker's order, truncated to `top_n`.
4. presented jobs are persisted as `discovered` (and thus dedup'd on the next run).
5. throttle: `sleep` is called once per scrape pair.
6. `LLMRanker`: parses provider JSON (with code fence) into ordered `RankedJob`; drops unknown job_ids.

## harness green + commit
`pytest` green, `ruff`/`mypy(strict)` clean. Commit
`feat(discovery): JobSpy search + cross-day dedup + LLM ranking (Phase 4)`.
Tick Phase 4 box + progress log; reiterate the LLM-PII privacy risk.

## Out of scope
Full JD fetch/parse (Phase 5); the Telegram digest UI (Phase 8).
