# cvflow

Autonomous, self-hosted job-application agent. Runs continuously on an Ubuntu server and is operated **entirely through a Telegram chat** — no dashboard, no SSH, no commands during normal use.

Each day it discovers and ranks job postings against your profile, shows you the top 5–10, and for the ones you pick it analyzes the JD, tailors your LaTeX resume, and — **only after you approve in chat** — fills and submits the application via browser automation. It asks you for OTPs and clarifications mid-flow, and tracks every application's status.

**Hard rule: zero per-call / SaaS cost.** Everything is open-source, free-tier, or self-hosted; the only ongoing cost is the server.

See **[CLAUDE.md](CLAUDE.md)** for the full goals, tech stack, architecture, and conventions. This repo is currently scaffolding — implementation lives under `src/cvflow/`.

## Quick start (once implemented)
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
# install TeX Live (sudo apt install texlive-full) for resume compilation
cp config.example.yaml config.yaml      # then fill in secrets
# populate profile/ from the *.example.md templates and add resume/master.tex
```

## Layout
| Path | Purpose |
|---|---|
| `src/cvflow/` | application package (one subpackage per concern) |
| `profile/` | your knowledge base + form-field store (real files gitignored) |
| `resume/` | master LaTeX resume (real file gitignored) |
| `data/`, `logs/` | runtime DB, PDFs, tokens, cookies, logs (gitignored) |
| `config.example.yaml` | every config variable, documented |
