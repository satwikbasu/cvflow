# logs/ (gitignored)

Application logs. Every skip, OTP timeout, clarification, and failure must be logged here (and reported to the user via Telegram — nothing fails silently). Not committed.

| Item | What |
|---|---|
| `discover/<ts>.md` | **finished** discovery archive — the full formatted digest + per-job drop report, written once a run completes (`write_discover_log`). `<ts>` is a UTC ISO timestamp. |

**Discovery logs split by lifecycle:** the **live** per-run raw log streams to `data/` while a run
is in flight (`data/discover-manual-<ts>.log` for `/discover`, `data/discover-cron-<ts>.log` for the
daily cron — see `data/README.md`); the **finished** digest is archived here under `discover/`. A
run that is killed before completing leaves only its `data/` live log, never a `logs/discover/`
file.
