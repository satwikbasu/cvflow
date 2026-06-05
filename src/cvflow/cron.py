"""Deterministic scheduled-job entrypoint (Phase 11): `python -m cvflow.cron <job>`.

Runs each daily/recurring job directly (no agent loop) and pushes results to
Telegram via HermesNotifier. The brain is only involved later, when the user
replies to select/approve. Jobs: discover (digest), sweep-otp, heartbeat.
"""

from __future__ import annotations

from typing import Any

from cvflow.statemachine import Status


def format_digest(ranked: list[Any]) -> str:
    """Render the daily digest. Always includes each job's URL + /apply,/skip."""
    if not ranked:
        return "No new jobs today."
    parts = ["🗞️ cvflow — new jobs today:\n"]
    for rj in ranked:
        p = rj.posting
        parts.append(
            f"• {p.title} @ {p.company}\n"
            f"  {p.url}\n"
            f"  {rj.rationale}\n"
            f"  /apply {p.job_id} | /skip {p.job_id}\n"
        )
    return "\n".join(parts)


def run_job(job: str, *, store: Any, discovery: Any, otp: Any, notify: Any) -> None:
    """Dispatch one scheduled job. Pure of config/network — deps are injected."""
    if job == "discover":
        notify(format_digest(discovery.discover()))
    elif job == "sweep-otp":
        otp.expire_overdue()
    elif job == "heartbeat":
        counts = ", ".join(f"{s.value}={len(store.list_by_status(s))}" for s in Status)
        notify(f"💓 cvflow alive — {counts}")
    else:
        raise ValueError(f"unknown cron job: {job}")
