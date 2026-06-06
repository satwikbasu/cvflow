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
    for i, rj in enumerate(ranked, start=1):
        p = rj.posting
        company = p.company or "Unknown company"
        concerns = f"  ⚠️ {'; '.join(rj.concerns)}\n" if rj.concerns else ""
        parts.append(
            f"{i}. {p.title} @ {company}  (fit {rj.fit_score})\n"
            f"  {p.url}\n"
            f"  {rj.rationale}\n"
            f"{concerns}"
            f"  /apply {p.job_id} | /skip {p.job_id}\n"
        )
    parts.append("Reply: /apply 1 2 4  •  /skip 3  •  /apply all")
    return "\n".join(parts)


def run_job(job: str, *, store: Any, discovery: Any, otp: Any, notify: Any) -> None:
    """Dispatch one scheduled job. Pure of config/network — deps are injected."""
    if job == "discover":
        ranked = discovery.discover()
        store.set_digest_slots([rj.posting.job_id for rj in ranked])
        notify(format_digest(ranked))
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
    from cvflow.discovery import DiscoveryService, LLMRanker, format_preferences
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
        LLMRanker(
            brain, knowledge.full_context(), preferences=format_preferences(config.preferences)
        ),
        search_terms=config.discovery.search_terms,
        locations=config.discovery.locations,
        sites=config.discovery.sites,
        results_wanted_per_site=config.discovery.results_wanted_per_site,
        hours_old=config.discovery.hours_old,
        top_n=config.discovery.top_n_to_present,
        exclude_title_keywords=config.preferences.exclude_title_keywords,
        min_ctc_lpa=config.preferences.min_ctc_lpa,
        job_type=config.preferences.job_type,
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
    try:
        run_job(args[0], store=store, discovery=discovery, otp=otp, notify=notify)
    except Exception as exc:  # noqa: BLE001 — a cron crash must still reach the user
        notify(f"⚠️ cvflow cron job {args[0]!r} failed: {exc}")
        raise


if __name__ == "__main__":
    main()
