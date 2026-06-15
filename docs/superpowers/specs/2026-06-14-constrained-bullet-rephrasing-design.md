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
plan → render → compile → send path is preserved; rewording is folded into the **single** existing
ordering call — gpt-oss-120b is a reasoning model and returns ordering + selection + rewrites in
one structured response, so we never spend a second request against the free-tier rate limit
(5 req/min on Cerebras).

```
plan(jd, feedback):
  1. candidates = (if config.resume.rephrase) extract \resumeItem{...} from experience.tex +
        EVERY project file (fixed before the call; respects disabled_sections) else []
  2. ONE LLM call → JSON {section_order, selected_project_ids, rewrites, diff_narration}
        - with the rewrites ask + numbered candidates when rephrase is on; ordering-only prompt
          when off. The prompt foregrounds REWORDING as the primary task (ordering/selection are
          secondary light touches) — per the locked decision "rephrasing > reordering".
  3. order = never-drop reorder, then drop config-disabled sections; picks = floor logic
  4. accepted = { original: reword for candidate i if _guard_ok(original, reword) else dropped }
        (best-effort: a missing/malformed rewrites field → {} = reorder-only, never raises;
         a hard failure of the single call raises, same as pre-feature — but it's one call now)
  → TailoringPlan(section_order, selected_project_ids, diff_narration, rephrased=accepted)

tailored_document(plan):
  - heading: verbatim (already fixed)
  - experience + selected projects: emit the section/​project file text with each
    \resumeItem{orig} replaced by \resumeItem{accepted[orig]} (inline, not \input).
    Rewrites for unselected projects are simply never applied (content-keyed map).
  - education, skills, certifications, titles, dates, companies: verbatim (\input as today)

compile_tailored / send-to-Telegram: unchanged.
```

Emphasis: the LLM prompt foregrounds **rewording for JD alignment** as the primary task; section
reordering/selection stay but are secondary. The diff leads with the wording changes.

> **Design note (2026-06-14, post-implementation):** the first cut used a *separate* second LLM
> call for rewording. That doubled the per-`/tailor` Cerebras footprint against a 5-req/min key and
> caused 429s. Folded into the single ordering call (above). All other decisions are unchanged.

## Components

### 1. The fact guard (`_guard_ok`, deterministic, no network) — numbers-only

The deterministic block is on **fabricated numbers** only. `_numbers(text)` = the set of digit-runs
(commas stripped, e.g. `500K`→`500`, `10,000`→`10000`).

`_guard_ok(original, reword)` returns **False (reject)** iff `reword` contains a digit-run not in
`original` — i.e. it would invent a metric (a count, %, scale). Otherwise **True**: ordinary synonym
rewording (verbs/adjectives/phrasing) is allowed. On reject the caller keeps the original bullet.

`assert_no_new_facts` is the document-level backstop: it raises only if the rendered tex contains a
number not present anywhere in the master + profile (`_master_numbers`, built once in `__init__`
from the master sections + project blocks + a `fact_corpus` constructor input = the knowledge base
+ `candidate_skills.yaml`). Wording is **not** checked.

> **Design decision (2026-06-14, post-live-test):** the first cut used a vocabulary guard — every
> non-stopword in a reword had to already appear in the candidate's résumé/profile. Live testing
> showed this rejected **13 of 14** rewrites on ordinary synonyms (`architected`, `utilized`,
> `optimized`, `delivered`…), gutting the feature: rephrasing *is* "use different words," so a
> "must already appear" check rejects nearly all of it, and the LLM's `diff_narration` (written
> before the guard culls anything) then overstated what survived. The real fabrication risk is new
> **numbers/metrics** and invented **skills/tools** — not ordinary English. We block numbers
> deterministically and rely on the **human review of the before/after diff** (read before
> `/apply`) to catch an invented skill/tool. Invariant 2 is thus enforced by the number guard +
> the review gate, not a vocabulary whitelist. (The vocabulary machinery — `_tokens`/`STOPWORDS`/
> `_norm` — was removed.)

### 2. Bullet extraction / substitution

A brace-balanced helper finds each `\resumeItem{…}` occurrence in a section/project file's raw
text and exposes (inner_text, span). Substitution replaces the inner text with the accepted
string, leaving every other line — `\resumeSubheading{title}{dates}{company}{loc}`, list
start/end, `\resumeProjectHeading`, comments — untouched. Only `\resumeItem` bodies in
**experience.tex** and the **selected** project files are eligible.

### 3. The combined tailoring call

The **single** existing call to the tailoring provider (Cerebras `gpt-oss-120b`) now also returns
the rewrites. Prompt (`_PLAN_WITH_REWRITES_PROMPT`) foregrounds rewording as the primary task: "Your
PRIMARY job is to REWORD the listed bullets … Reordering/selection are secondary. HARD RULE: do NOT
add, remove, or invent any fact … only rephrase what each bullet states. Return JSON: rewrites (one
per numbered bullet, same order — the main output), section_order, selected_project_ids,
diff_narration." Inputs: the numbered candidate bullets (experience + every project, fixed before
the call) + sections + projects + JD skills + optional feedback. When `rephrase` is off, the
ordering-only `_PLAN_PROMPT` is used instead (still one call). A missing/malformed `rewrites` field
→ reorder-only (best-effort, never raises); only a hard failure of the single call raises — and
it's the same one call the tailorer always made, so the free-tier footprint is unchanged from
pre-feature. The accepted rewrites are kept only if they pass `_guard_ok`.

### 4. Diff / review gate

`diff()` leads with the wording changes, then the structural summary, then a **truthful,
code-derived** one-liner — NOT the model's `diff_narration` prose (which describes its intent
*before* the guard culls rewrites and tends to overstate; it is no longer shown):

```
Reworded bullets:
  - <original bullet>
  + <accepted reword>
  …(only bullets that actually changed)…

Section order: experience → … (kept all sections)
Projects shown: ipsec-dashboard, crudbot

N bullet(s) reworded (the ± lines above); everything else is verbatim from your master résumé.
```

This is what you read before `/apply` — the human backstop for any invented skill/tool, now that
the guard only blocks numbers. (`diff_narration` is still returned by the model and stored on the
plan, but it is not displayed.)

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

`/tailor n` → `run_tailor` → `request_review` → `ResumeTailor.plan` (ONE combined call: ordering +
selection + rewrites, then the fact guard) → `compile_tailored` (renders with accepted rewords,
Tectonic) → PDF + `diff()` → Telegram (PDF document + diff text). **No extra LLM calls** — the
tailorer makes the same single Cerebras request it always did, so the 5 RPM / 2400 RPD free-tier
footprint is unchanged.

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
