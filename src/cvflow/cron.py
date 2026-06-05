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


def _build(config: Any) -> tuple[Any, Any, Any, Any]:
    """Construct the minimal services for the cron jobs (NO browser/Automator)."""
    from cvflow.auth import OtpCoordinator
    from cvflow.discovery import DiscoveryService, LLMRanker
    from cvflow.knowledge import KnowledgeBase
    from cvflow.llm import NimProvider
    from cvflow.notify import HermesNotifier
    from cvflow.storage import ApplicationStore

    store = ApplicationStore(config.storage.db_path)
    knowledge = KnowledgeBase.load(
        config.profile.knowledge_base_dir, config.storage.form_fields_path
    )
    brain = NimProvider(
        base_url=config.llm.brain.base_url,
        api_key=config.llm.brain.api_key,
        model=config.llm.brain.model,
        max_requests_per_minute=config.llm.brain.max_requests_per_minute,
    )
    discovery = DiscoveryService(
        store,
        LLMRanker(brain, knowledge.full_context()),
        search_terms=config.discovery.search_terms,
        locations=config.discovery.locations,
        sites=config.discovery.sites,
        results_wanted_per_site=config.discovery.results_wanted_per_site,
        hours_old=config.discovery.hours_old,
        top_n=config.discovery.top_n_to_present,
    )
    notify = HermesNotifier()
    otp = OtpCoordinator(
        store=store, notify=notify, timeout_minutes=config.auth.otp_timeout_minutes
    )
    return store, discovery, otp, notify


def main(
    argv: list[str] | None = None,
    *,
    services: tuple[Any, Any, Any, Any] | None = None,
) -> None:
    import sys

    args = list(argv) if argv is not None else sys.argv[1:]
    if len(args) != 1:
        raise SystemExit("usage: python -m cvflow.cron <discover|sweep-otp|heartbeat>")
    if services is None:
        from cvflow.config import load_config

        services = _build(load_config("config.yaml"))
    store, discovery, otp, notify = services
    run_job(args[0], store=store, discovery=discovery, otp=otp, notify=notify)


if __name__ == "__main__":
    main()
