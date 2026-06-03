# resume/

Your **master LaTeX resume** — the complete, unabridged record of experience, projects, and skills. cvflow tailors a per-job copy from it (reorder sections, adjust bullet emphasis, align keywords) **without fabricating facts**, then compiles to PDF.

Copy `master.example.tex` → `master.tex` (gitignored) and build it out. **Keep it modular:** put each section in its own file and pull it in with `\input{}`. Tailoring then reorders/swaps whole section files deterministically, and the diff for the review gate (Goal 4) stays meaningful — far more robust than regex-editing one giant file.

```
resume/
├── master.tex          # preamble + \input of section files, in default order
└── sections/
    ├── experience.tex
    ├── skills.tex
    ├── projects.tex
    └── education.tex
```

Compiled tailored PDFs are written to `data/resumes/` (gitignored), not here.
