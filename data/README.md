# data/ (gitignored)

Runtime state. **Nothing here is committed** (except this README). Contains sensitive data — protect with filesystem perms (`chmod 600`) and run under a dedicated unprivileged user.

| Item | What |
|---|---|
| `cvflow.db` | SQLite application-tracking store (Goal 9) |
| `resumes/` | compiled tailored resume PDFs |
| `auth_state/` | Playwright persistent-context cookies/session (Goals 5–7) |
| `*token*.json` | OAuth tokens — **Fernet-encrypted at rest** |
| `.fernet_key` | encryption key — never commit, `chmod 600` |
| `discover-manual-<ts>.log` | **live** raw log of a `/discover` run (one file per run, tail while it runs) |
| `discover-cron-<ts>.log` | **live** raw log of the daily cron discovery run (one file per run) |
| `discover.lock` | `fcntl` run-lock so the daily cron and a manual `/discover` never double-run |

### Discovery logs — the convention

Every discovery run, manual or scheduled, streams its **live** raw log to a per-run file in **this
directory** (`discover-manual-<ts>.log` for `/discover`, `discover-cron-<ts>.log` for the daily
cron; `<ts>` is a unix timestamp). When a run **finishes**, its formatted digest + drop report is
archived to `logs/discover/<ts>.md` (see `logs/README.md`). So: **`data/` = live per-run logs,
`logs/discover/` = the finished digest archive.**
