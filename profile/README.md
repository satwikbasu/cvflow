# profile/ — your knowledge base (single source of truth)

These markdown files are cvflow's **only** source of facts about you. They are loaded **in full** as LLM context before any ranking, resume tailoring, form-filling, or clarification answer.

**cvflow must never invent, infer, or embellish facts beyond what is written here.** If a required answer isn't present, it triggers the mid-form clarification loop (asks you in Telegram) instead of guessing.

Copy each `*.example.md` to its real name (drop `.example`) and fill it in with your real information. Real files are **gitignored** — only the `*.example.*` templates are committed.

| File | Holds |
|---|---|
| `experience.md` | employment history: roles, dates, responsibilities, measurable impact |
| `skills.md` | technical skills and proficiency levels |
| `projects.md` | projects and their outcomes |
| `education.md` | degrees, institutions, dates |
| `personality.md` | work style, values, culture-fit signals |
| `essay_answers.md` | pre-written answers to common application essays, in your voice |
| `form_fields.json` | structured recurring form values (see `form_fields.example.json`) |
