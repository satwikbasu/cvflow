# Phase 13 — Relevance Engine + Apply Ergonomics + Preference Learning — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make discovery surface only jobs the user wants and qualifies for (A), let the user approve by digest number (B), and propose preference updates from apply/skip history (C).

**Architecture:** A = preferences (structured config + `profile/preferences.md`) drive a deterministic pre-filter (job_type/title/salary on JobSpy's structured fields) then a smarter LLM ranker (fit score + concerns, excludes fuzzy mismatches). B = persist the digest's ordinal→job_id map; the existing `/apply`/`/skip` hook resolves ordinals/ranges/`all`/raw-ids and approves many at once. C = a weekly cron job summarizes apply/skip decisions and proposes (never applies) edits to `preferences.md`. The approval gate stays deterministic and off the agent loop throughout.

**Tech Stack:** Python 3.11+, JobSpy, SQLite, the NIM/Gemini `generate(prompt)->str` seam, pytest. No new dependency.

---

## File structure
- `src/cvflow/config.py` — **modify**: `PreferencesConfig` + `preferences:` parsing.
- `profile/preferences.md` — **create**: soft-preference prose (committed PII).
- `src/cvflow/discovery/__init__.py` — **modify**: NaN clean + salary fields; deterministic pre-filter; ranker prompt + `RankedJob` fit_score/concerns; `job_type` search param.
- `src/cvflow/storage/__init__.py` — **modify**: `digest_slots` table + setters/getters (additive + migration).
- `src/cvflow/gate/__init__.py` — **modify**: `resolve_targets` + multi-target `handle_gate_command`.
- `src/cvflow/cron.py` — **modify**: discover persists slots + numbered digest; `learn` job.
- `src/cvflow/learning.py` — **create**: `summarize_decisions`.
- `src/cvflow/mcp/tools.py` — **modify**: wire preferences into discovery/ranker.
- `artifacts/hermes/scripts/cvflow-learn.sh`, `docs/deploy.md` — **modify/create**: weekly learn job.
- Tests: `tests/test_config.py`, `tests/test_discovery.py`, `tests/test_storage.py`, `tests/test_gate.py`, `tests/test_cron.py`, `tests/test_learning.py`.

---

# PART A — Relevance engine

### Task A1: `PreferencesConfig` + `preferences:` parsing

**Files:** Modify `src/cvflow/config.py`; Test `tests/test_config.py`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_config.py
def test_preferences_block_parsed(tmp_path):
    from cvflow.config import load_config
    cfg_text = (tmp_path / "config.yaml")
    base = (
        "telegram:\n  bot_token: x\n  authorized_user_id: 1\n"
        "schedule:\n  daily_discovery_time: '08:00'\n  timezone: UTC\n  heartbeat_interval_minutes: 30\n"
        "discovery:\n  search_terms: [a]\n  locations: [Remote]\n  sites: [indeed]\n"
        "  results_wanted_per_site: 5\n  hours_old: 72\n  top_n_to_present: 5\n"
        "preferences:\n  yoe_have: 1\n  min_ctc_lpa: 7\n  job_type: fulltime\n"
        "  exclude_title_keywords: [senior, lead]\n  prefer_product_companies: true\n"
        "  exclude_app_maintenance: true\n  exclude_night_shift_only: true\n"
        "llm:\n  brain:\n    provider: nvidia\n    api_key: k\n    base_url: u\n    model: m\n    max_requests_per_minute: 40\n"
        "  tailoring:\n    provider: google\n    api_key: k\n    model: m\n    max_requests_per_day: 50\n"
        "resume:\n  master_tex_path: resume/master.tex\n  output_dir: data/tailored\n  latex_compiler: tectonic\n"
        "automation:\n  headless: true\n  use_stealth: true\n  storage_state_dir: data/bw\n  form_timeout_minutes: 30\n"
        "auth:\n  google_account_email: a@b.c\n  application_email: a@b.c\n  otp_timeout_minutes: 15\n"
        "storage:\n  db_path: data/db.sqlite\n  form_fields_path: profile/form_fields.json\n"
        "security:\n  fernet_key_path: data/.k\n"
        "profile:\n  knowledge_base_dir: profile\n"
    )
    cfg_text.write_text(base)
    cfg = load_config(cfg_text)
    assert cfg.preferences.yoe_have == 1
    assert cfg.preferences.min_ctc_lpa == 7
    assert cfg.preferences.job_type == "fulltime"
    assert cfg.preferences.exclude_title_keywords == ["senior", "lead"]
    assert cfg.preferences.prefer_product_companies is True
    assert cfg.preferences.exclude_app_maintenance is True
    assert cfg.preferences.exclude_night_shift_only is True
```

- [ ] **Step 2: Run** `pytest tests/test_config.py::test_preferences_block_parsed -v` → FAIL (`preferences` missing).

- [ ] **Step 3: Implement.** Add the dataclass after `DiscoveryConfig`:

```python
@dataclass(frozen=True)
class PreferencesConfig:
    yoe_have: int
    min_ctc_lpa: int
    job_type: str
    exclude_title_keywords: list[str]
    prefer_product_companies: bool
    exclude_app_maintenance: bool
    exclude_night_shift_only: bool
```

Add `preferences: PreferencesConfig` to the `Config` dataclass (after `discovery`). In `load_config`, add `pref = _section(data, "preferences", "")` and build it:

```python
        preferences=PreferencesConfig(
            yoe_have=_get_int(pref, "yoe_have", "preferences."),
            min_ctc_lpa=_get_int(pref, "min_ctc_lpa", "preferences."),
            job_type=_get_str(pref, "job_type", "preferences."),
            exclude_title_keywords=_get_str_list(pref, "exclude_title_keywords", "preferences."),
            prefer_product_companies=_get_bool(pref, "prefer_product_companies", "preferences."),
            exclude_app_maintenance=_get_bool(pref, "exclude_app_maintenance", "preferences."),
            exclude_night_shift_only=_get_bool(pref, "exclude_night_shift_only", "preferences."),
        ),
```

Add the block to the real `config.yaml` and `config.example.yaml` too:

```yaml
preferences:
  yoe_have: 1
  min_ctc_lpa: 7
  job_type: "fulltime"
  exclude_title_keywords: ["senior", "sr.", "lead", "principal", "staff", "manager", "architect", "head", "director", "vp"]
  prefer_product_companies: true
  exclude_app_maintenance: true
  exclude_night_shift_only: true
```

- [ ] **Step 4: Run** `pytest tests/test_config.py -v` → PASS.
- [ ] **Step 5: Commit** `git commit -am "feat(config): preferences block (yoe/ctc/job_type/deal-breakers)"`

---

### Task A2: `profile/preferences.md` (soft prose)

**Files:** Create `profile/preferences.md`.

- [ ] **Step 1: Create the file** (committed PII; no test — it's data the ranker reads via the KB):

```markdown
# Job Preferences (soft signals for ranking)

> Hard filters live in config.yaml `preferences:`. This file is nuance the ranker should weigh.

- **Target company type:** strongly prefer **product-based** companies over service/consultancy/staffing.
- **Company reputation:** prefer well-regarded companies; down-rank unknown body-shops.
- **Role leaning:** prefer DevOps / SRE / platform / infrastructure / backend over pure front-end
  or long-term application-codebase maintenance.
- **Experience level:** ~1 year; entry-level / junior / fresher-friendly roles only.
- **Location:** based in Kolkata; open to Remote and any Indian metro. No overseas relocation.
- **Tie-breakers:** modern infra stack (Docker/K8s/CI-CD/cloud), clear growth, healthy culture.
```

- [ ] **Step 2: Commit** `git add profile/preferences.md && git commit -m "feat(profile): soft job-preference prose for ranking"`

---

### Task A3: `normalize_rows` — NaN cleanup + carry salary fields

**Files:** Modify `src/cvflow/discovery/__init__.py`; Test `tests/test_discovery.py`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_discovery.py
def test_normalize_cleans_nan_and_carries_salary() -> None:
    rows = [{
        "id": "1", "site": "indeed", "title": "Backend", "company": float("nan"),
        "location": "Remote", "description": "d", "job_url": "https://x/1",
        "date_posted": "2026-06-02", "min_amount": 800000.0, "max_amount": 1200000.0,
        "currency": "INR",
    }]
    p = normalize_rows(rows)[0]
    assert p.company == ""          # NaN -> empty (digest shows "Unknown company")
    assert p.min_amount == 800000.0
    assert p.max_amount == 1200000.0
    assert p.currency == "INR"


def test_normalize_missing_salary_is_none() -> None:
    p = normalize_rows([_row("1")])[0]
    assert p.min_amount is None
    assert p.currency is None
```

- [ ] **Step 2: Run** `pytest tests/test_discovery.py::test_normalize_cleans_nan_and_carries_salary -v` → FAIL.

- [ ] **Step 3: Implement.** Add salary fields to `JobPosting` (after `date_posted`):

```python
    min_amount: float | None = None
    max_amount: float | None = None
    currency: str | None = None
```

Add a cleaner near `_stable_job_id`:

```python
def _clean(value: Any) -> str:
    """Stringify a JobSpy cell, mapping NaN/None/'nan' to ''."""
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() == "nan" else text


def _num(value: Any) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if f != f else f  # NaN check
```

In `normalize_rows`, replace each `str(row.get(...) or "")` with `_clean(row.get(...))`, and add the salary fields to the `JobPosting(...)`:

```python
                min_amount=_num(row.get("min_amount")),
                max_amount=_num(row.get("max_amount")),
                currency=_clean(row.get("currency")) or None,
```

- [ ] **Step 4: Run** `pytest tests/test_discovery.py -v` → PASS.
- [ ] **Step 5: Commit** `git commit -am "feat(discovery): clean NaN cells; carry structured salary fields"`

---

### Task A4: Deterministic pre-filter (title + salary + job_type)

**Files:** Modify `src/cvflow/discovery/__init__.py`; Test `tests/test_discovery.py`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_discovery.py
def test_prefilter_drops_excluded_titles_and_below_floor_salary() -> None:
    store = ApplicationStore(":memory:")
    ranker = _RecordingRanker()
    rows = [
        _row("1", title="Senior DevOps Engineer"),                      # title excluded
        _row("2", title="DevOps Engineer", min_amount=400000.0,
             max_amount=500000.0, currency="INR"),                      # 5 LPA < 7 -> drop
        _row("3", title="DevOps Engineer", min_amount=800000.0,
             max_amount=1200000.0, currency="INR"),                     # 8-12 LPA -> keep
        _row("4", title="Platform Engineer"),                           # no salary -> keep (flagged later)
    ]
    svc = DiscoveryService(
        store=store, ranker=ranker, search_fn=lambda **k: rows,
        search_terms=["x"], locations=["Remote"], sites=["indeed"],
        results_wanted_per_site=10, hours_old=72, top_n=10,
        throttle_seconds=0.0, sleep=lambda s: None,
        exclude_title_keywords=["senior", "lead"], min_ctc_lpa=7,
    )
    svc.discover()
    kept = {p.job_id for p in ranker.seen_candidates}
    assert kept == {"indeed:3", "indeed:4"}


def test_prefilter_passes_job_type_to_search_fn() -> None:
    store = ApplicationStore(":memory:")
    captured = {}

    def search_fn(**kwargs):
        captured.update(kwargs)
        return []

    DiscoveryService(
        store=store, ranker=_RecordingRanker(), search_fn=search_fn,
        search_terms=["x"], locations=["Remote"], sites=["indeed"],
        results_wanted_per_site=10, hours_old=72, top_n=5,
        throttle_seconds=0.0, sleep=lambda s: None, job_type="fulltime",
    ).discover()
    assert captured.get("job_type") == "fulltime"
```

(Extend `_row` to accept the salary keys — they already flow through `**over`.)

- [ ] **Step 2: Run** `pytest tests/test_discovery.py::test_prefilter_drops_excluded_titles_and_below_floor_salary -v` → FAIL.

- [ ] **Step 3: Implement.** Add params to `DiscoveryService.__init__` (keyword, defaulted so existing tests pass):

```python
        exclude_title_keywords: list[str] | None = None,
        min_ctc_lpa: int = 0,
        job_type: str | None = None,
```

In `__init__` body:

```python
        self._exclude_title_keywords = [k.lower() for k in (exclude_title_keywords or [])]
        self._min_ctc_lpa = min_ctc_lpa
        self._job_type = job_type
```

Pass `job_type` in `_gather_rows`'s `self._search_fn(...)` call:

```python
                        hours_old=self._hours_old,
                        job_type=self._job_type,
```

Add the pre-filter method and call it in `discover()` before dedup:

```python
    def _prefilter(self, postings: list[JobPosting]) -> list[JobPosting]:
        kept: list[JobPosting] = []
        for p in postings:
            title = p.title.lower()
            if any(k in title for k in self._exclude_title_keywords):
                logger.info("prefilter drop (title) %s: %s", p.job_id, p.title)
                continue
            cap = p.max_amount if p.max_amount is not None else p.min_amount
            # only filter on salary when stated AND in INR (else keep + let ranker flag)
            if cap is not None and (p.currency or "INR").upper() == "INR":
                if cap / 100_000 < self._min_ctc_lpa:
                    logger.info("prefilter drop (salary) %s: %s", p.job_id, cap)
                    continue
            kept.append(p)
        return kept
```

In `discover()`, insert the pre-filter:

```python
    def discover(self) -> list[RankedJob]:
        postings = self._prefilter(normalize_rows(self._gather_rows()))
        candidates = [p for p in postings if not self._store.exists(p.job_id)]
```

Update the default `_jobspy_search` to accept and use `job_type` + annual salary:

```python
def _jobspy_search(
    *,
    site_name: list[str],
    search_term: str,
    location: str,
    results_wanted: int,
    hours_old: int,
    job_type: str | None = None,
) -> list[dict[str, Any]]:
    from jobspy import scrape_jobs

    df = scrape_jobs(
        site_name=site_name,
        search_term=search_term,
        location=location,
        results_wanted=results_wanted,
        hours_old=hours_old,
        job_type=job_type,
        enforce_annual_salary=True,
    )
```

- [ ] **Step 4: Run** `pytest tests/test_discovery.py -v` → PASS.
- [ ] **Step 5: Commit** `git commit -am "feat(discovery): deterministic pre-filter (title/salary/job_type)"`

---

### Task A5: Smarter ranker (prompt + fit_score + concerns)

**Files:** Modify `src/cvflow/discovery/__init__.py`; Test `tests/test_discovery.py`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_discovery.py
def test_ranker_prompt_includes_preferences_and_parses_score_concerns() -> None:
    captured = {}

    class _Prov:
        def generate(self, prompt: str) -> str:
            captured["prompt"] = prompt
            return (
                '[{"job_id": "linkedin:1", "fit_score": 88, '
                '"rationale": "infra fit", "concerns": ["salary not stated"]}]'
            )

    ranker = LLMRanker(_Prov(), "PROFILE", preferences="HARD: yoe<=1; product cos")
    out = ranker.rank(normalize_rows([_row("1")]), top_n=5)
    assert "product cos" in captured["prompt"]
    assert out[0].fit_score == 88
    assert out[0].concerns == ["salary not stated"]


def test_ranker_excluded_jobs_simply_absent() -> None:
    class _Prov:
        def generate(self, prompt: str) -> str:
            return '[{"job_id": "linkedin:2", "fit_score": 70, "rationale": "ok", "concerns": []}]'

    ranker = LLMRanker(_Prov(), "P", preferences="prefs")
    out = ranker.rank(normalize_rows([_row("1"), _row("2")]), top_n=5)
    assert [r.posting.job_id for r in out] == ["linkedin:2"]  # job 1 excluded by LLM
```

- [ ] **Step 2: Run** `pytest tests/test_discovery.py::test_ranker_prompt_includes_preferences_and_parses_score_concerns -v` → FAIL.

- [ ] **Step 3: Implement.** Add fields to `RankedJob` (need `from dataclasses import dataclass, field`):

```python
    fit_score: int = 0
    concerns: list[str] = field(default_factory=list)
```

Update `LLMRanker.__init__` to take preferences:

```python
    def __init__(self, provider: _Provider, profile_context: str, preferences: str = "") -> None:
        self._provider = provider
        self._profile_context = profile_context
        self._preferences = preferences
```

Replace `_build_prompt` and `rank` body:

```python
    def _build_prompt(self, postings: list[JobPosting]) -> str:
        jobs = [
            {"job_id": p.job_id, "title": p.title, "company": p.company,
             "location": p.location, "description": p.description[:1500]}
            for p in postings
        ]
        return (
            "You rank job postings for a candidate and EXCLUDE ones they should not apply to.\n"
            "Hard rules — OMIT a job entirely if its description implies any of:\n"
            "  • required minimum experience greater than the candidate's (a stated range must "
            "include the candidate's years, or be fresher/entry-level);\n"
            "  • night-shift or rotational-on-call ONLY;\n"
            "  • the role is primarily long-term maintenance of a large application codebase.\n"
            "If a fact is NOT stated, do NOT exclude on it — keep the job and add a concern.\n"
            "Prefer reputable, product-based companies. Use only the given job_ids.\n"
            "Return ONLY a JSON array, best-first, of "
            '{"job_id", "fit_score" (0-100), "rationale" (specific, cite the profile/preferences), '
            '"concerns" (array of short strings, e.g. "salary not stated", "YOE not stated")}.\n\n'
            f"## Candidate preferences (hard + soft)\n{self._preferences}\n\n"
            f"## Candidate profile\n{self._profile_context}\n\n"
            f"## Job postings\n{json.dumps(jobs, indent=2)}\n"
        )

    def rank(self, postings: list[JobPosting], top_n: int) -> list[RankedJob]:
        by_id = {p.job_id: p for p in postings}
        raw = self._provider.generate(self._build_prompt(postings))
        entries = json.loads(_strip_code_fence(raw))
        ranked: list[RankedJob] = []
        for entry in entries:
            posting = by_id.get(str(entry.get("job_id")))
            if posting is None:
                continue
            ranked.append(
                RankedJob(
                    posting=posting,
                    summary=str(entry.get("summary", "")),
                    rationale=str(entry.get("rationale", "")),
                    fit_score=int(entry.get("fit_score", 0) or 0),
                    concerns=[str(c) for c in entry.get("concerns", [])],
                )
            )
        return ranked[:top_n]
```

- [ ] **Step 4: Run** `pytest tests/test_discovery.py -v` → PASS.
- [ ] **Step 5: Commit** `git commit -am "feat(discovery): preference-aware ranker with fit_score + concerns"`

---

### Task A6: Wire preferences into `build_tools` + `cron._build`; drop dead sites

**Files:** Modify `src/cvflow/mcp/tools.py`, `src/cvflow/cron.py`, `config.yaml`. (Verified by suite + smoke; no new unit test — these constructors aren't unit-harnessed.)

- [ ] **Step 1: Add a shared preferences formatter.** In `src/cvflow/discovery/__init__.py` add:

```python
def format_preferences(prefs: Any) -> str:
    """Render the structured PreferencesConfig into prompt text for the ranker."""
    return (
        f"- Candidate has {prefs.yoe_have} year(s) experience; only roles whose required "
        f"experience includes {prefs.yoe_have} or are fresher/entry-level.\n"
        f"- Minimum acceptable CTC: {prefs.min_ctc_lpa} LPA (jobs without stated pay are kept "
        f"but flagged).\n"
        f"- Exclude titles containing: {', '.join(prefs.exclude_title_keywords)}.\n"
        f"- Exclude internships/contract (full-time only).\n"
        f"- {'Exclude' if prefs.exclude_night_shift_only else 'Allow'} night-shift/on-call-only roles.\n"
        f"- {'Avoid' if prefs.exclude_app_maintenance else 'Allow'} pure long-term app-maintenance roles.\n"
        f"- {'Prefer product-based companies.' if prefs.prefer_product_companies else ''}"
    )
```

Add `"format_preferences"` to `__all__`.

- [ ] **Step 2: Wire `build_tools`** (`src/cvflow/mcp/tools.py`). Replace the discovery/ranker construction:

```python
    from cvflow.discovery import DiscoveryService, LLMRanker, format_preferences

    ranker = LLMRanker(
        brain, knowledge.full_context(), preferences=format_preferences(config.preferences)
    )
    discovery = DiscoveryService(
        store, ranker,
        search_terms=config.discovery.search_terms,
        locations=config.discovery.locations,
        sites=config.discovery.sites,
        results_wanted_per_site=config.discovery.results_wanted_per_site,
        hours_old=config.discovery.hours_old,
        top_n=config.discovery.top_n_to_present,
        exclude_title_keywords=config.preferences.exclude_title_keywords,
        min_ctc_lpa=config.preferences.min_ctc_lpa,
        job_type=config.preferences.job_type,
    )
```

- [ ] **Step 3: Wire `cron._build`** (`src/cvflow/cron.py`) identically — add `format_preferences` import, build the `LLMRanker(..., preferences=format_preferences(config.preferences))`, and add the three pre-filter kwargs to `DiscoveryService(...)`.

- [ ] **Step 4: Drop dead sites** in `config.yaml`: change `discovery.sites` to `["linkedin", "indeed", "google"]` (remove `glassdoor`, `zip_recruiter`).

- [ ] **Step 5: Verify + commit**

Run: `pytest -q && ruff check . && mypy --strict src` → green. Then:
```bash
python -c "from cvflow.config import load_config; from cvflow.mcp.tools import build_tools; build_tools(load_config('config.yaml')); print('ok')"
git commit -am "feat(discovery): wire preferences into build_tools + cron; drop 403 sites"
```

---

### Task A7: Digest shows fit score + concerns

**Files:** Modify `src/cvflow/cron.py`; Test `tests/test_cron.py`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_cron.py
def test_format_digest_shows_fit_score_and_concerns():
    from cvflow.cron import format_digest
    from cvflow.discovery import JobPosting, RankedJob
    p = JobPosting(job_id="indeed:7", title="DevOps", company="", location="Remote",
                   description="d", url="https://jobs/7", site="indeed", date_posted="x")
    rj = RankedJob(posting=p, summary="s", rationale="infra fit", fit_score=88,
                   concerns=["salary not stated"])
    text = format_digest([rj])
    assert "88" in text
    assert "salary not stated" in text
    assert "Unknown company" in text  # empty company rendered, not blank/nan
```

- [ ] **Step 2: Run** `pytest tests/test_cron.py::test_format_digest_shows_fit_score_and_concerns -v` → FAIL.

- [ ] **Step 3: Implement.** Replace `format_digest`'s per-job block:

```python
    for i, rj in enumerate(ranked, start=1):
        p = rj.posting
        company = p.company or "Unknown company"
        concerns = f"  ⚠️ {'; '.join(rj.concerns)}\n" if rj.concerns else ""
        parts.append(
            f"{i}. {p.title} @ {company}  (fit {rj.fit_score})\n"
            f"  {p.url}\n"
            f"  {rj.rationale}\n"
            f"{concerns}"
            f"  /apply {p.job_id} | /skip {p.job_id}\n"
        )
```

- [ ] **Step 4: Run** `pytest tests/test_cron.py -v` → PASS.
- [ ] **Step 5: Commit** `git commit -am "feat(cron): digest shows fit score, concerns, Unknown-company fallback"`

---

# PART B — Apply ergonomics (select by number)

### Task B1: `digest_slots` storage (additive + migration)

**Files:** Modify `src/cvflow/storage/__init__.py`; Test `tests/test_storage.py`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_storage.py
def test_digest_slots_roundtrip_and_replace():
    from cvflow.storage import ApplicationStore
    s = ApplicationStore(":memory:")
    s.set_digest_slots(["indeed:a", "indeed:b", "indeed:c"])
    assert s.get_digest_slot(1) == "indeed:a"
    assert s.get_digest_slot(3) == "indeed:c"
    assert s.get_digest_slot(9) is None
    assert s.digest_slots() == ["indeed:a", "indeed:b", "indeed:c"]
    s.set_digest_slots(["indeed:x"])  # replaces
    assert s.get_digest_slot(1) == "indeed:x"
    assert s.get_digest_slot(2) is None
```

- [ ] **Step 2: Run** `pytest tests/test_storage.py::test_digest_slots_roundtrip_and_replace -v` → FAIL.

- [ ] **Step 3: Implement.** Add to `_SCHEMA` (inside the executescript string):

```python
CREATE TABLE IF NOT EXISTS digest_slots (
    slot         INTEGER PRIMARY KEY,
    job_id       TEXT NOT NULL,
    presented_at TEXT NOT NULL
);
```

Add methods to `ApplicationStore`:

```python
    def set_digest_slots(self, job_ids: list[str]) -> None:
        """Replace the presented-digest ordinal→job_id map (slot 1..N)."""
        with self._conn:
            self._conn.execute("DELETE FROM digest_slots")
            self._conn.executemany(
                "INSERT INTO digest_slots (slot, job_id, presented_at) VALUES (?, ?, ?)",
                [(i, jid, _now()) for i, jid in enumerate(job_ids, start=1)],
            )

    def get_digest_slot(self, slot: int) -> str | None:
        row = self._conn.execute(
            "SELECT job_id FROM digest_slots WHERE slot = ?", (slot,)
        ).fetchone()
        return row["job_id"] if row is not None else None

    def digest_slots(self) -> list[str]:
        rows = self._conn.execute(
            "SELECT job_id FROM digest_slots ORDER BY slot"
        ).fetchall()
        return [r["job_id"] for r in rows]
```

- [ ] **Step 4: Run** `pytest tests/test_storage.py -v` → PASS.
- [ ] **Step 5: Commit** `git commit -am "feat(storage): digest_slots ordinal→job_id map"`

---

### Task B2: `gate.resolve_targets`

**Files:** Modify `src/cvflow/gate/__init__.py`; Test `tests/test_gate.py`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_gate.py
def test_resolve_targets_ordinals_ranges_all_and_raw():
    from cvflow.gate import resolve_targets
    from cvflow.storage import ApplicationStore
    s = ApplicationStore(":memory:")
    s.set_digest_slots(["indeed:a", "indeed:b", "indeed:c"])

    assert resolve_targets("1 2", s) == (["indeed:a", "indeed:b"], [])
    assert resolve_targets("1,3", s) == (["indeed:a", "indeed:c"], [])
    assert resolve_targets("1-3", s) == (["indeed:a", "indeed:b", "indeed:c"], [])
    assert resolve_targets("all", s) == (["indeed:a", "indeed:b", "indeed:c"], [])
    assert resolve_targets("indeed:z", s) == (["indeed:z"], [])      # raw id passthrough
    assert resolve_targets("2 9", s) == (["indeed:b"], ["9"])        # 9 out of range
    assert resolve_targets("1 1", s) == (["indeed:a"], [])           # dedupe
```

- [ ] **Step 2: Run** `pytest tests/test_gate.py::test_resolve_targets_ordinals_ranges_all_and_raw -v` → FAIL.

- [ ] **Step 3: Implement.** Add to `src/cvflow/gate/__init__.py` (add `"resolve_targets"` to `__all__`):

```python
def resolve_targets(args: str, store: ApplicationStore) -> tuple[list[str], list[str]]:
    """Resolve an apply/skip argument string into job_ids.

    Accepts ordinals (1 2), comma lists (1,2), ranges (1-3), 'all', and raw
    job_ids (containing ':'). Returns (resolved_job_ids_in_order, unrecognized_tokens),
    de-duplicated, order preserved.
    """
    text = args.strip().lower()
    resolved: list[str] = []
    unknown: list[str] = []
    seen: set[str] = set()

    def _add(job_id: str) -> None:
        if job_id and job_id not in seen:
            seen.add(job_id)
            resolved.append(job_id)

    if text == "all":
        for jid in store.digest_slots():
            _add(jid)
        return resolved, unknown

    for token in text.replace(",", " ").split():
        if ":" in token:  # raw job_id
            _add(token)
        elif "-" in token and all(part.isdigit() for part in token.split("-", 1)):
            lo, hi = (int(x) for x in token.split("-", 1))
            for n in range(lo, hi + 1):
                jid = store.get_digest_slot(n)
                if jid:
                    _add(jid)
                else:
                    unknown.append(str(n))
        elif token.isdigit():
            jid = store.get_digest_slot(int(token))
            if jid:
                _add(jid)
            else:
                unknown.append(token)
        else:
            unknown.append(token)
    return resolved, unknown
```

- [ ] **Step 4: Run** `pytest tests/test_gate.py -v` → PASS.
- [ ] **Step 5: Commit** `git commit -am "feat(gate): resolve_targets (ordinals/ranges/all/raw ids)"`

---

### Task B3: multi-target `handle_gate_command`

**Files:** Modify `src/cvflow/gate/__init__.py`; Test `tests/test_gate.py`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_gate.py
def _pending_store():
    from cvflow.storage import ApplicationStore
    from cvflow.statemachine import Status
    s = ApplicationStore(":memory:")
    for jid in ("indeed:a", "indeed:b"):
        s.add(jid, "Co", "Role", "https://x")
        s.set_status(jid, Status.PENDING_REVIEW)
    s.set_digest_slots(["indeed:a", "indeed:b"])
    return s


def test_apply_multiple_by_ordinal():
    from cvflow.gate import handle_gate_command
    from cvflow.statemachine import Status
    s = _pending_store()
    res = handle_gate_command(command="apply", args="1 2", user_id=1,
                              authorized_user_id=1, store=s)
    assert res.handled is True
    assert s.get("indeed:a").status == Status.APPROVED
    assert s.get("indeed:b").status == Status.APPROVED
    assert "indeed:a" in res.message and "indeed:b" in res.message


def test_apply_reports_unknown_ordinal():
    from cvflow.gate import handle_gate_command
    s = _pending_store()
    res = handle_gate_command(command="apply", args="1 9", user_id=1,
                              authorized_user_id=1, store=s)
    assert "9" in res.message  # unknown reported, job 1 still approved
```

- [ ] **Step 2: Run** `pytest tests/test_gate.py::test_apply_multiple_by_ordinal -v` → FAIL.

- [ ] **Step 3: Implement.** Replace the body of `handle_gate_command` after the auth check with a multi-target version:

```python
    if command not in ("apply", "skip"):
        return GateResult(handled=False, message=None)
    if not args.strip():
        return GateResult(handled=True, message=f"⚠️ Usage: /{command} <number(s)|all>")

    targets, unknown = resolve_targets(args, store)
    if not targets and not unknown:
        return GateResult(handled=True, message=f"⚠️ Usage: /{command} <number(s)|all>")

    ok: list[str] = []
    problems: list[str] = []
    for job_id in targets:
        try:
            if command == "apply":
                store.approve(job_id)
            else:
                store.set_status(job_id, Status.SKIPPED)
            ok.append(job_id)
        except UnknownJob:
            problems.append(f"{job_id} (no such job)")
        except IllegalTransition:
            app = store.get(job_id)
            status = app.status.value if app else "unknown"
            problems.append(f"{job_id} ({status})")

    verb = "✅ Approved" if command == "apply" else "⏭️ Skipped"
    lines = []
    if ok:
        lines.append(f"{verb}: {', '.join(ok)}" + (" — submitting." if command == "apply" else "."))
    if problems:
        lines.append("⚠️ couldn't " + command + ": " + "; ".join(problems))
    if unknown:
        lines.append("⚠️ unknown: " + ", ".join(unknown))
    return GateResult(handled=True, message="\n".join(lines))
```

(Remove the old single-`job_id` logic this replaces. Keep the imports.)

- [ ] **Step 4: Run** `pytest tests/test_gate.py -v`. NOTE: the reply WORDING changed (now `✅ Approved: <id> — submitting.` / `⏭️ Skipped: <id>.`). Existing tests that assert the old exact strings (e.g. `"✅ Approved {id}"`) must be updated to match the new aggregated format — update them so the suite passes. Behavior (which jobs get approved/skipped, unauthorized ignored) is unchanged; only the message string differs.
- [ ] **Step 5: Commit** `git commit -am "feat(gate): batch apply/skip by ordinal with aggregated reply"`

---

### Task B4: discover persists slots + numbered digest

**Files:** Modify `src/cvflow/cron.py`; Test `tests/test_cron.py`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_cron.py
def test_run_job_discover_persists_digest_slots():
    from cvflow.cron import run_job
    from cvflow.discovery import JobPosting, RankedJob
    from cvflow.storage import ApplicationStore
    store = ApplicationStore(":memory:")

    def _rj(jid):
        p = JobPosting(job_id=jid, title="T", company="C", location="L",
                       description="d", url=f"https://{jid}", site="indeed", date_posted="x")
        return RankedJob(posting=p, summary="s", rationale="r")

    class _Disc:
        def discover(self):
            return [_rj("indeed:a"), _rj("indeed:b")]

    run_job("discover", store=store, discovery=_Disc(), otp=None, notify=lambda m: None)
    assert store.digest_slots() == ["indeed:a", "indeed:b"]
```

- [ ] **Step 2: Run** `pytest tests/test_cron.py::test_run_job_discover_persists_digest_slots -v` → FAIL.

- [ ] **Step 3: Implement.** In `run_job`, change the `discover` branch:

```python
    if job == "discover":
        ranked = discovery.discover()
        store.set_digest_slots([rj.posting.job_id for rj in ranked])
        notify(format_digest(ranked))
```

(The numbered format + `/apply 1 2 4` hint already landed in A7's `format_digest`; ensure the closing hint line reads `Reply: /apply 1 2 4  •  /skip 3  •  /apply all` — add it once after the loop.)

- [ ] **Step 4: Run** `pytest tests/test_cron.py -v` → PASS.
- [ ] **Step 5: Commit** `git commit -am "feat(cron): persist digest ordinals; numbered digest with /apply N hint"`

---

# PART C — Preference learning

### Task C1: `summarize_decisions`

**Files:** Create `src/cvflow/learning.py`; Test `tests/test_learning.py`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_learning.py
"""Preference-learning summary — proposes, never applies. No network."""

from cvflow.learning import summarize_decisions


class _Prov:
    def __init__(self, reply): self._reply = reply; self.prompt = None
    def generate(self, prompt): self.prompt = prompt; return self._reply


def test_summarize_builds_prompt_and_returns_suggestion():
    prov = _Prov("Consider avoiding service companies.")
    out = summarize_decisions(
        applied=[{"role": "DevOps", "company": "ProdCo"}],
        skipped=[{"role": "Support", "company": "ServiceCo"}],
        provider=prov, min_decisions=1,
    )
    assert "ServiceCo" in prov.prompt
    assert out == "Consider avoiding service companies."


def test_summarize_too_little_data_returns_sentinel():
    prov = _Prov("should not be called")
    out = summarize_decisions(applied=[], skipped=[], provider=prov, min_decisions=5)
    assert out is None
    assert prov.prompt is None  # LLM not called
```

- [ ] **Step 2: Run** `pytest tests/test_learning.py -v` → FAIL.

- [ ] **Step 3: Implement.**

```python
# src/cvflow/learning.py
"""Preference learning (Phase 13C): propose preference edits from apply/skip history.

PROPOSES ONLY — never edits preferences.md or filters. The durable source of truth
stays the version-controlled preferences.md + config (auditable, like the gate).
"""

from __future__ import annotations

import json
from typing import Any, Protocol


class _Provider(Protocol):
    def generate(self, prompt: str) -> str: ...


_PROMPT = """You analyze a user's job-application decisions and propose preference updates.
APPLIED (wanted) and SKIPPED (not wanted) jobs are below. Identify concrete patterns and
suggest specific, optional edits to their preferences file. Do not invent facts about the user.
Reply with a short plain-language message (no JSON).

APPLIED:
{applied}

SKIPPED:
{skipped}
"""


def summarize_decisions(
    applied: list[dict[str, Any]],
    skipped: list[dict[str, Any]],
    *,
    provider: _Provider,
    min_decisions: int = 5,
) -> str | None:
    """Return a suggestion string, or None when there's too little signal."""
    if len(applied) + len(skipped) < min_decisions:
        return None
    return provider.generate(
        _PROMPT.format(applied=json.dumps(applied, indent=2), skipped=json.dumps(skipped, indent=2))
    ).strip()
```

- [ ] **Step 4: Run** `pytest tests/test_learning.py -v` → PASS.
- [ ] **Step 5: Commit** `git commit -am "feat(learning): summarize_decisions (proposes preference edits, never applies)"`

---

### Task C2: `learn` cron job

**Files:** Modify `src/cvflow/cron.py`; Test `tests/test_cron.py`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_cron.py
def test_run_job_learn_notifies_suggestion():
    from cvflow.cron import run_job
    from cvflow.statemachine import Status
    from cvflow.storage import ApplicationStore
    store = ApplicationStore(":memory:")
    for jid, st in [("indeed:a", Status.APPLIED), ("indeed:b", Status.SKIPPED),
                    ("indeed:c", Status.SKIPPED)]:
        store.add(jid, f"Co-{jid}", "Role", "https://x")
        if st is Status.APPLIED:
            store.set_status(jid, Status.PENDING_REVIEW); store.approve(jid)
            store.set_status(jid, Status.APPLIED)
        else:
            store.set_status(jid, Status.SKIPPED)
    notes = []

    class _Prov:
        def generate(self, prompt): return "Avoid service cos."

    run_job("learn", store=store, discovery=None, otp=None, notify=notes.append,
            learn_provider=_Prov(), min_decisions=1)
    assert any("Avoid service cos." in n for n in notes)
```

- [ ] **Step 2: Run** `pytest tests/test_cron.py::test_run_job_learn_notifies_suggestion -v` → FAIL.

- [ ] **Step 3: Implement.** Extend `run_job`'s signature and add the branch:

```python
def run_job(
    job: str, *, store: Any, discovery: Any, otp: Any, notify: Any,
    learn_provider: Any = None, min_decisions: int = 5,
) -> None:
```

Add before the `else`:

```python
    elif job == "learn":
        from cvflow.learning import summarize_decisions
        from cvflow.statemachine import Status

        def _jobs(*statuses: Status) -> list[dict[str, Any]]:
            out: list[dict[str, Any]] = []
            for s in statuses:
                out += [{"role": a.role, "company": a.company} for a in store.list_by_status(s)]
            return out

        applied = _jobs(Status.APPROVED, Status.APPLIED)
        skipped = _jobs(Status.SKIPPED)
        suggestion = summarize_decisions(
            applied, skipped, provider=learn_provider, min_decisions=min_decisions
        )
        if suggestion:
            notify("💡 Preference suggestions (reply by editing profile/preferences.md):\n" + suggestion)
```

Wire `learn` in `_build` + `main`: `_build` returns a 5-tuple now `(store, discovery, otp, notify, brain)` where `brain` is the `NimProvider` (reused as `learn_provider`). Update `main` to pass `learn_provider=services[4]` when `job == "learn"`. Simplest: `main` unpacks `store, discovery, otp, notify, brain = services` and calls `run_job(args[0], store=store, discovery=discovery, otp=otp, notify=notify, learn_provider=brain)`. Update `_build` to also return `brain` (construct the `NimProvider` once and reuse for the ranker and learning), and update the Phase-11 `test_main_*` services tuples to 5-tuples (append `None`).

- [ ] **Step 4: Run** `pytest tests/test_cron.py -v` → PASS (update the two existing `services=(...)` tuples in `test_main_*` to include a 5th element `None`).
- [ ] **Step 5: Commit** `git commit -am "feat(cron): weekly learn job proposes preference updates"`

---

### Task C3: learn wrapper + deploy doc

**Files:** Create `artifacts/hermes/scripts/cvflow-learn.sh`; Modify `docs/deploy.md`.

- [ ] **Step 1: Create the wrapper**

```bash
#!/usr/bin/env bash
# cvflow weekly preference-learning suggestions — invoked by `hermes cron --no-agent`.
set -euo pipefail
cd /home/ubuntu/cvflow
exec /home/ubuntu/cvflow/.venv/bin/python -m cvflow.cron learn
```
`chmod +x artifacts/hermes/scripts/cvflow-learn.sh`

- [ ] **Step 2: Document the cron job** in `docs/deploy.md` (under Scheduled jobs):

```bash
cp artifacts/hermes/scripts/cvflow-learn.sh ~/.hermes/scripts/ && chmod +x ~/.hermes/scripts/cvflow-learn.sh
hermes cron create 'every 168h' --no-agent --script cvflow-learn.sh --name cvflow-learn
```
Note: proposes preference edits only; the user applies them by editing `profile/preferences.md`.

- [ ] **Step 3: Commit** `git add artifacts/hermes/scripts/cvflow-learn.sh docs/deploy.md && git commit -m "feat(deploy): weekly cvflow-learn cron wrapper + docs"`

---

### Task D: Full verification + build-plan sync

**Files:** Modify `docs/superpowers/plans/2026-06-03-cvflow-build-plan.md`.

- [ ] **Step 1: Full verification** `pytest -q && ruff check . && mypy --strict src` → all green.
- [ ] **Step 2: Live smoke**
```bash
python -c "from cvflow.config import load_config; from cvflow.mcp.tools import build_tools, TOOL_NAMES; t=build_tools(load_config('config.yaml')); print('approve absent:', 'approve' not in TOOL_NAMES)"
~/.hermes/scripts/cvflow-heartbeat.sh   # still works
```
- [ ] **Step 3: Add a `### [x] Phase 13` entry** to the build plan (after Phase 12) describing A/B/C, with a dated progress-log entry; install + register the `cvflow-learn` cron job per C3.
- [ ] **Step 4: Commit + push**
```bash
git commit -am "docs(phase-13): mark Phase 13 (relevance/apply/learning) complete; sync log"
git push
```

---

## Notes for the executor
- **Gate invariant:** no `approve` tool ever; `handle_gate_command` (hook, off the agent loop) stays the sole `store.approve` caller — batching/ordinals don't change that.
- **Never silent:** deterministic drops are logged; missing salary/YOE are kept + flagged; learning proposes only.
- **Additive storage:** `digest_slots` is a new table; don't touch the gate/state-machine core.
- **Type consistency:** `RankedJob(fit_score:int, concerns:list[str])`, `JobPosting(min_amount/max_amount:float|None, currency:str|None)`, `LLMRanker(provider, profile_context, preferences="")`, `DiscoveryService(..., exclude_title_keywords, min_ctc_lpa, job_type)`, `_build` returns a 5-tuple `(store, discovery, otp, notify, brain)`.
- **Zero cost:** no new dependency.
