# profile/ — your knowledge base (single source of truth)

These markdown files are cvflow's **only** source of facts about you. They are loaded **in full** as LLM context before any ranking, resume tailoring, form-filling, or clarification answer.

**cvflow must never invent, infer, or embellish facts beyond what is written here.** If a required answer isn't present, it triggers the mid-form clarification loop (asks you in Telegram) instead of guessing.

This is a **private, single-user repo**, so these files hold real data and are **committed directly** (PII, but no secrets — secrets live only in the gitignored `config.yaml`). Edit them in place; there are no `*.example` templates.

| File | Holds |
|---|---|
| `experience.md` | employment history: roles, dates, responsibilities, measurable impact |
| `skills.md` | technical skills and proficiency levels |
| `projects.md` | projects and their outcomes |
| `education.md` | degrees, institutions, dates |
| `personality.md` | work style, values, culture-fit signals |
| `essay_answers.md` | pre-written answers to common application essays, in your voice |
| `form_fields.json` | structured recurring form values (empty value = known field, ask the user — never guess) |
