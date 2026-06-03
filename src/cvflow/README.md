# src/cvflow/ — application package

One subpackage per concern. See **[../../CLAUDE.md](../../CLAUDE.md)** for goals, stack, and architecture.

| Subpackage | Responsibility | Goals |
|---|---|---|
| `config.py` | load & validate `config.yaml` | — |
| `statemachine/` | application status state machine — **the un-bypassable review gate**. Only path to `approved` is the Telegram approval callback; submission asserts `status == approved`. | 4 |
| `bot/` | Telegram interface: daily digests, approval prompts, OTP requests, clarification Q&A, status reports, heartbeat | 1,4,7,8,9,10 |
| `discovery/` | JobSpy multi-board search + cross-day dedup + LLM ranking → top 5–10 | 1 |
| `analysis/` | fetch & parse full JD (skills, quals, seniority, tone, applicant instructions) | 2 |
| `resume/` | tailor modular LaTeX per job, compile PDF, generate plain-language diff vs master | 3,4 |
| `automation/` | Playwright form filling (multi-page, uploads, dropdowns), proof capture | 5,8 |
| `auth/` | Google OAuth flow + email-OTP fallback | 6,7 |
| `storage/` | SQLite models + form_fields loader (populated vs empty-key handling) | 9 |
| `llm/` | Gemini client wrapper with rate limiting + caching (respect free-tier RPD) | 1,2,3,8 |
| `scheduler/` | APScheduler daily-discovery trigger + heartbeat | 10 |

**Invariants:** never fabricate user facts (read only from `profile/`); never bypass the review gate; never fail silently (log + Telegram). See CLAUDE.md.
