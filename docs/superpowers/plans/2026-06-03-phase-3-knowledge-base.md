# Phase 3 — Knowledge-base loader — TDD sub-plan

> Master roadmap: `2026-06-03-cvflow-build-plan.md` → Phase 3.
> **Exit:** tests load the `*.example.md` templates and expose experience/skills/essays/form-fields;
> missing-field detection returns a pending list (no guessing).

## Invariant in play
- **Never fabricate user facts** (CLAUDE.md invariant 2): the KB is the single source of truth, loaded
  in full as LLM context. Missing form fields surface as a pending list — never a guess.

## Work item — `src/cvflow/knowledge/` — `KnowledgeBase` (TDD)

Reads the markdown KB + `form_fields.json` into one structured object for downstream prompts.
Reuses `FormFields` from `cvflow.storage`.

- **`KnowledgeBase.load(profile_dir, form_fields_path=None) -> KnowledgeBase`:**
  - Recursively reads `*.md` under `profile_dir` **including** `projects/*.md`.
  - **Excludes** `*.example.md` templates and `README.md` (repo guidance, not user facts).
  - Keys each doc by its path stem relative to `profile_dir` (e.g. `skills`, `experience`, `projects/crudbot`).
  - Loads `form_fields.json` (defaults to `<profile_dir>/form_fields.json`) via `FormFields`.
- **Accessors:**
  - `documents: dict[str, str]` (all loaded docs).
  - convenience properties: `skills`, `experience`, `essays` (essay_answers), `education`, `personality`.
  - `project_docs() -> dict[str, str]` (the `projects/*` entries).
  - `form_fields: FormFields`; `missing_form_fields() -> list[str]` (delegates to `FormFields.missing()`).
  - `full_context() -> str`: every doc concatenated with a `# <key>`-style header, for full-context prompting.
- A missing required doc (e.g. no `skills.md`) → accessor raises `KeyError`; absence is explicit, not silent.

## Tests (use a tmp profile dir built from the committed `*.example.*` templates → real-name copies)
1. Load a tmp dir with `skills.md`, `experience.md`, `essay_answers.md`, `projects/foo.md`, `form_fields.json`
   → `documents` has all four keys (incl. `projects/foo`); example/README files are excluded.
2. Convenience accessors return the right doc text (`skills`, `experience`, `essays`).
3. `project_docs()` returns only the `projects/*` entries.
4. `missing_form_fields()` returns the empty-valued keys (no guessing).
5. `full_context()` contains content from every loaded doc.
6. Loading the committed `*.example.md` templates (copied to real names in tmp) succeeds and exposes skills/experience/essays.

## harness green + commit
`pytest` green, `ruff`/`mypy(strict)` clean. Commit
`feat(knowledge): full-context profile KB loader with missing-field detection (Phase 3)`.
Tick Phase 3 box + progress log.

## Out of scope
Ranking/prompt assembly that *consumes* the KB (Phases 4–6).
