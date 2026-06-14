# Constrained bullet rephrasing — design

**Date:** 2026-06-14
**Status:** approved (brainstorm) → ready for implementation plan
**Scope:** private repo only.

## Goal

Make résumé tailoring align the **wording** of experience/project bullets to the target job,
not just reorder sections — without fabricating facts. The LLM may reword existing bullets to
mirror the JD's vocabulary; a deterministic guard rejects any reword that introduces a new fact
(number, tool, skill, claim), falling back to the original master wording for that bullet.

## The invariant tension (and how we resolve it)

Today tailoring is **reorder/select only, never rewrite**. That is what makes CLAUDE.md
invariant 2 ("never fabricate facts about the user") a *deterministic, exact* check:
`assert_no_new_facts` requires every content line of the tailored `.tex` to exist verbatim in the
master. Rephrasing produces LLM-generated text, so the exact check no longer applies.

**Resolution — a constrained-rephrase guard.** A reworded bullet is accepted only if it passes a
deterministic, no-network check against the candidate's own data; otherwise the original bullet is
kept verbatim. The human approval gate (review of the PDF + a word-level before/after diff before
`/apply`) remains the final backstop. Invariant 2 stays enforced — now by the guard + the gate
rather than an exact line match.

## Architecture / flow

All changes live in `src/cvflow/resume/__init__.py` (plus config + wiring). The existing
plan → render → compile → send path is preserved; a rephrase pass is inserted.

```
plan(jd, feedback):
  1. section ordering  (LLM call #1, unchanged: never-drop reorder + project floor)
  2. if config.resume.rephrase:
        bullets = extract \resumeItem{...} from experience.tex + each SELECTED project file
        rewrites = LLM call #2  (all bullets + JD required/preferred skills → reworded bullets)
        accepted = { original: reword for each bullet if _guard_ok(original, reword) else original }
     else: accepted = {}            # behaves exactly like today
  → TailoringPlan(section_order, selected_project_ids, diff_narration, rephrased=accepted)

tailored_document(plan):
  - heading: verbatim (already fixed)
  - experience + selected projects: emit the section/​project file text with each
    \resumeItem{orig} replaced by \resumeItem{accepted[orig]} (inline, not \input)
  - education, skills, certifications, titles, dates, companies: verbatim (\input as today)

compile_tailored / send-to-Telegram: unchanged.
```

Emphasis: the LLM prompt foregrounds **rewording for JD alignment**; section reordering stays but
is secondary. The diff leads with the wording changes.

## Components

### 1. The fact guard (`_guard_ok`, deterministic, no network)

The safety core. Built once per tailor run:

- `allowed_vocab`: the set of normalized word-tokens drawn from **`master.tex` + every
  `profile/*.md` + `profile/candidate_skills.yaml`**. `ResumeTailor` only knows the master today,
  so it gains a `fact_corpus: str` constructor input — the profile/skills text. `build_tools`
  builds it from the existing knowledge base (`KnowledgeBase.full_context()`, which already loads
  `profile/*.md`) plus the raw `candidate_skills.yaml` text. Vocab is computed once and cached.
- `STOPWORDS`: a curated module-level set of function words + common résumé verbs/adjectives
  (e.g. *a, the, for, with, using, and, of, to, built, designed, developed, led, improved,
  scalable, robust*). Stopwords never trigger rejection.

Tokenization (applied to both the corpus and each bullet): strip LaTeX markup (`\cmd{...}` → its
text, drop `$…$`, `\\`, `&`, `%`, braces), split on whitespace and `/`, lowercase, strip
surrounding punctuation, and light suffix-normalize (drop a trailing `s/es/ed/ing/d`) to avoid
plural/tense false-rejects. Numbers are the digit-runs (`\d[\d,.]*` → digits only, e.g.
`500K`→`500`, `10--20`→`10`,`20`).

`_guard_ok(original, reword)` returns **False (reject)** if either holds:
- **(a) new number:** any digit-run in `reword` is not present in `original`'s digit-runs.
- **(b) new content word:** any token in `reword` that is not a stopword and not a number is
  absent from `allowed_vocab`.

Otherwise **True (accept)**. On reject the caller keeps the original bullet.

`assert_no_new_facts` is **redefined** to this vocab+number basis over the full rendered document
(verbatim master text passes trivially; accepted rewords pass by construction). It stays as
defense-in-depth and the deterministic expression of invariant 2.

### 2. Bullet extraction / substitution

A brace-balanced helper finds each `\resumeItem{…}` occurrence in a section/project file's raw
text and exposes (inner_text, span). Substitution replaces the inner text with the accepted
string, leaving every other line — `\resumeSubheading{title}{dates}{company}{loc}`, list
start/end, `\resumeProjectHeading`, comments — untouched. Only `\resumeItem` bodies in
**experience.tex** and the **selected** project files are eligible.

### 3. Rephrase LLM call

One call to the existing tailoring provider (Cerebras `gpt-oss-120b`). Prompt: "Reword each of
these résumé bullets to align with the target job's language. You MUST NOT add, remove, or invent
any fact — no new tools, skills, numbers, employers, or claims; only rephrase what is already
stated. Return JSON: a list mapping each input bullet (by index) to its reworded text." Inputs:
the ordered list of eligible bullets + the JD `required_skills`/`preferred_quals`. Failure or
unparseable output → log + skip rephrasing (all bullets stay original); never blocks tailoring
(invariant 3).

### 4. Diff / review gate

`diff()` leads with the wording changes, then the structural summary:

```
Reworded bullets:
  - <original bullet>
  + <accepted reword>
  …(only bullets that actually changed)…

Section order: experience → … (kept all sections)
Projects shown: ipsec-dashboard, crudbot
<diff_narration>
```

This is what you read before `/apply` — the human backstop for any reword the guard let through.

### 5. Config toggle

`resume.rephrase: bool` — **default `true`**. Optional-with-default (loaded via `_opt_bool`,
consistent with `use_jd_analysis`). Documented in `config.example.yaml`. `false` → pure
reorder-only (today's behavior). Threaded: `config.resume.rephrase` → `build_tools` →
`ResumeTailor(..., rephrase=…)`.

### 6. Per-section enable/disable toggles

`resume.sections:` — an optional YAML mapping of section name → bool letting the user drop a whole
section from every tailored résumé. **Default: all sections shown** (an absent block, or an absent
key, means that section appears). Setting a section to `false` removes it entirely.

```yaml
resume:
  sections:
    projects: false      # never include the Projects section
    certifications: true # explicit true is the same as omitting it
```

Loaded into `ResumeConfig.disabled_sections: frozenset[str]` = the set of section names whose value
is `false`. Threaded `config.resume.disabled_sections` → `build_tools` → `ResumeTailor(...,
disabled_sections=…)`.

Enforcement is in `plan()`, **after** the never-drop reorder: `order` is filtered to drop any
disabled section, and if `"projects"` is disabled the project picks are emptied (so no projects are
selected, padded, or rephrased). This is the one sanctioned way a section leaves the résumé — it is
a deliberate user config, distinct from the LLM, which still may never drop an *enabled* section.
Unknown names in the mapping are ignored harmlessly (they match no real section). `diff()`'s
"Section order" line therefore lists only the enabled sections.

## Data flow

`/tailor n` → `run_tailor` → `request_review` → `ResumeTailor.plan` (order call + rephrase call +
guard) → `compile_tailored` (renders with accepted rewords, Tectonic) → PDF + `diff()` →
Telegram (PDF document + diff text). No new external calls beyond one extra Cerebras request per
`/tailor`, well within the 5 RPM / 2400 RPD free tier.

## Edge cases

- **rephrase=false** → no second LLM call; identical to current behavior.
- **rephrase LLM fails / bad JSON** → log, keep all originals, continue.
- **guard rejects a bullet** → keep original verbatim (safe default; surfaced as "unchanged").
- **no eligible bullets / empty section** → no-op.
- **bullet with nested braces** → brace-balanced extractor handles it; `assert_no_new_facts` still
  guards the rendered result.
- **a number the candidate truly has but phrased differently** (e.g. master "500K", reword "500K")
  → digit-run `500` matches, accepted; reword "half a million" → no new digits, words must be in
  vocab (likely rejected → keep original). Safe either way.
- **section disabled in config** (e.g. `projects: false`) → dropped from `order`; if it's
  `projects`, picks are emptied so nothing is selected/padded/rephrased. Enabled sections are still
  never dropped by the LLM.

## Testing (LLM mocked throughout)

- `_guard_ok`: accepts an in-vocab reword; rejects a new number ("...handling 1M req/day");
  rejects a fabricated tool/skill not in vocab; stopwords don't trigger rejection;
  plural/tense normalization accepts "containers" when master has "container".
- bullet extract/substitute: round-trips, leaves titles/dates/headings/list markers untouched.
- `plan` with a stub provider: a fabricated reword falls back to original; a clean reword is kept;
  `rephrased` map contains only accepted changes.
- `plan` with rephrase=false: no rephrase, `rephrased` empty.
- rephrase provider raises / returns junk → all originals kept, no exception.
- `tailored_document`: experience/selected-project bullets reflect accepted rewords; education /
  skills / certifications / job titles / dates remain byte-for-byte verbatim; heading present.
- `assert_no_new_facts` (vocab+number form): passes a guard-approved document; raises on an
  injected out-of-vocab token / new number.
- `diff()`: shows `- old` / `+ new` per changed bullet.
- config: `resume.rephrase` defaults true when absent; reads false override.
- config: `resume.sections` absent → `disabled_sections` empty; `{projects: false, skills: true}`
  → `disabled_sections == {"projects"}`.
- `plan`: a disabled section is dropped from `order`; `projects: false` → `selected_project_ids`
  empty and no project bullets rephrased; an enabled section the model omitted is still present.

## Scope

**In:** rephrasing of experience + selected-project `\resumeItem` bullets, the guard, the
`rephrase` toggle, per-section enable/disable toggles, the diff, wiring. **Out (verbatim,
untouched):** heading, job titles, company names, dates, education, certification names, the skills
list; the gate/approval invariant; discovery; the `/tailor` command and Telegram delivery
(already done).

## Files

| File | Change |
|---|---|
| `src/cvflow/resume/__init__.py` | `STOPWORDS`, tokenizer, `_guard_ok`, bullet extract/substitute, rephrase call in `plan`, `rephrased` on `TailoringPlan`, redefined `assert_no_new_facts`, `diff` before/after, `tailored_document` inline substitution, `rephrase` + `disabled_sections` ctor params, section filtering in `plan` |
| `src/cvflow/config.py`, `config.example.yaml` | `resume.rephrase` toggle (default true); `resume.sections` mapping → `disabled_sections` |
| `src/cvflow/mcp/tools.py` | `build_tools` passes `rephrase=` and `disabled_sections=` |
| `tests/test_resume.py`, `tests/test_config.py` | the tests above |
```
