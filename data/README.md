# data/ (gitignored)

Runtime state. **Nothing here is committed** (except this README). Contains sensitive data — protect with filesystem perms (`chmod 600`) and run under a dedicated unprivileged user.

| Item | What |
|---|---|
| `cvflow.db` | SQLite application-tracking store (Goal 9) |
| `resumes/` | compiled tailored resume PDFs |
| `auth_state/` | Playwright persistent-context cookies/session (Goals 5–7) |
| `*token*.json` | OAuth tokens — **Fernet-encrypted at rest** |
| `.fernet_key` | encryption key — never commit, `chmod 600` |
