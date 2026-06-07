"""Deterministic scheduled-job entrypoint (Phase 11): `python -m cvflow.cron <job>`.

Runs each daily/recurring job directly (no agent loop) and pushes results to
Telegram via HermesNotifier. The brain is only involved later, when the user
replies to select/approve. Jobs: discover (digest), sweep-otp, heartbeat.
"""

from __future__ import annotations

from typing import Any

from cvflow.statemachine import Status


def format_digest(result: dict[str, Any]) -> str:
    """Render the two-section digest (M = stated pay, N = no pay). Continuously numbered."""
    m, n = result.get("M", []), result.get("N", [])
    if not m and not n:
        return "No new jobs today."
    parts = ["🗞️ cvflow — new jobs today:\n"]
    idx = 1

    def _block(bj: Any, i: int) -> str:
        p = bj.posting
        company = p.company or "Unknown company"
        pay = f" · {round(bj.ctc_lpa)} LPA" if getattr(bj, "ctc_lpa", None) else ""
        loc = f" · {p.location}" if p.location else ""
        concerns = f"  ⚠️ {'; '.join(bj.concerns)}\n" if bj.concerns else ""
        return (
            f"{i}. {p.title} @ {company}  (bench {bj.benchmark} · fit {bj.fit_score}{pay}{loc})\n"
            f"  {p.url}\n  {bj.fit_reason}\n{concerns}"
            f"  /apply {p.job_id} | /skip {p.job_id}\n"
        )

    parts.append("💰 With stated pay (ranked by value)\n")
    if m:
        for bj in m:
            parts.append(_block(bj, idx))
            idx += 1
    else:
        parts.append("No stated-pay jobs today.\n")
    parts.append("📋 Pay not stated (ranked by fit)\n")
    if n:
        for bj in n:
            parts.append(_block(bj, idx))
            idx += 1
    else:
        parts.append("No pay-not-stated jobs today.\n")
    parts.append("Reply: /apply 1 2 4  •  /skip 3  •  /apply all")
    return "\n".join(parts)


def run_job(
    job: str, *, store: Any, discovery: Any, otp: Any, notify: Any,
    learn_provider: Any = None, min_decisions: int = 5,
    learning_dir: str = "data/learning",
) -> None:
    """Dispatch one scheduled job. Pure of config/network — deps are injected."""
    if job == "discover":
        result = discovery.discover()
        ordered = [bj.posting.job_id for bj in result.get("M", []) + result.get("N", [])]
        store.set_digest_slots(ordered)
        notify(format_digest(result))
    elif job == "sweep-otp":
        otp.expire_overdue()
    elif job == "heartbeat":
        counts = ", ".join(f"{s.value}={len(store.list_by_status(s))}" for s in Status)
        notify(f"💓 cvflow alive — {counts}")
    elif job == "learn":
        import json
        from datetime import UTC, datetime
        from pathlib import Path

        from cvflow.analytics import summarize
        from cvflow.learning import summarize_decisions

        stats = summarize(store)
        decisions = store.recent_decisions(200)
        applied = [d for d in decisions if d["decision"] == "apply"]
        skipped = [d for d in decisions if d["decision"] == "skip"]
        suggestion = summarize_decisions(
            applied, skipped, provider=learn_provider, min_decisions=min_decisions
        )
        if suggestion:
            day = datetime.now(UTC).date().isoformat()
            Path(learning_dir).mkdir(parents=True, exist_ok=True)
            path = Path(learning_dir) / f"{day}.md"
            entry = (
                f"\n## {datetime.now(UTC).isoformat()}\n\n"
                f"**Stats:** {json.dumps(stats, default=str)}\n\n"
                f"**Suggestion:**\n{suggestion}\n"
            )
            with path.open("a") as fh:
                fh.write(entry)
            notify("💡 Preference suggestions (logged to data/learning/):\n" + suggestion)
    else:
        raise ValueError(f"unknown cron job: {job}")


def _build(config: Any) -> tuple[Any, Any, Any, Any, Any]:
    """Construct the minimal services for the cron jobs (NO browser/Automator)."""
    from cvflow.auth import OtpCoordinator
    from cvflow.discovery import DiscoveryService
    from cvflow.discovery.benchmark import build_fingerprint
    from cvflow.knowledge import KnowledgeBase
    from cvflow.llm import GeminiProvider, NimProvider
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
    gemini = GeminiProvider(
        api_key=config.llm.tailoring.api_key,
        model=config.llm.tailoring.model,
        max_requests_per_day=config.llm.tailoring.max_requests_per_day,
    )
    fingerprint = build_fingerprint(
        prefs_text=knowledge.full_context(), prefer_roles=config.preferences.prefer_roles
    )
    discovery = DiscoveryService(
        store,
        search_terms=config.discovery.search_terms,
        locations=config.discovery.locations,
        sites=config.discovery.sites,
        results_wanted_per_site=config.discovery.results_wanted_per_site,
        hours_old=config.discovery.hours_old,
        top_n=config.discovery.top_n_to_present,
        exclude_title_keywords=config.preferences.exclude_title_keywords,
        min_ctc_lpa=config.preferences.min_ctc_lpa,
        country_indeed=config.discovery.country_indeed,
        linkedin_fetch_description=config.discovery.linkedin_fetch_description,
        gemini=gemini,
        brain=brain,
        fingerprint=fingerprint,
        prefer_roles=config.preferences.prefer_roles,
        exclude_when=config.preferences.exclude_when,
        fit_weight=config.preferences.fit_weight,
        comp_weight=config.preferences.comp_weight,
        top_ctc_lpa=config.preferences.top_ctc_lpa,
        max_distill_per_cohort=config.discovery.max_distill_per_cohort,
        top_n_per_cohort=config.discovery.top_n_per_cohort,
        yoe_ceiling=config.preferences.yoe_have + config.preferences.yoe_buffer,
    )
    notify = HermesNotifier()
    otp = OtpCoordinator(
        store=store, notify=notify, timeout_minutes=config.auth.otp_timeout_minutes
    )
    return store, discovery, otp, notify, brain


def main(
    argv: list[str] | None = None,
    *,
    services: tuple[Any, Any, Any, Any, Any] | None = None,
) -> None:
    import sys

    args = list(argv) if argv is not None else sys.argv[1:]
    if len(args) != 1:
        raise SystemExit("usage: python -m cvflow.cron <discover|sweep-otp|heartbeat|learn>")
    if services is None:
        from cvflow.config import load_config

        services = _build(load_config("config.yaml"))
    store, discovery, otp, notify, brain = services
    try:
        run_job(
            args[0], store=store, discovery=discovery, otp=otp, notify=notify,
            learn_provider=brain,
        )
    except Exception as exc:  # noqa: BLE001 — a cron crash must still reach the user
        notify(f"⚠️ cvflow cron job {args[0]!r} failed: {exc}")
        raise


if __name__ == "__main__":
    main()
