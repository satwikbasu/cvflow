"""Deterministic scheduled-job entrypoint (Phase 11): `python -m cvflow.cron <job>`.

Runs each daily/recurring job directly (no agent loop) and pushes results to
Telegram via HermesNotifier. The brain is only involved later, when the user
replies to select/approve. Jobs: discover (digest), sweep-otp, heartbeat.
"""

from __future__ import annotations

from collections.abc import Callable
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
    footer = _filtered_footer(result.get("_dropped"))
    if footer:
        parts.append(footer)
    parts.append("Reply: /apply 1 2 4  •  /skip 3  •  /apply all")
    return "\n".join(parts)


def _filtered_footer(dropped: dict[str, int] | None) -> str:
    """One line summarising what the gates removed today, so the digest isn't a black box."""
    if not dropped:
        return ""
    from cvflow.discovery import DROP_BUCKET_ORDER

    items = [f"{dropped[k]} {k}" for k in DROP_BUCKET_ORDER if dropped.get(k)]
    return "🔍 Filtered today: " + " · ".join(items) if items else ""


_MAX_MSG = 4000  # Telegram's hard limit is 4096; leave headroom.


def format_drop_report(records: list[Any]) -> list[str]:
    """Group drop records by bucket (in DROP_BUCKET_ORDER) and chunk into <=4000-char
    messages. A bucket that overflows one message repeats its header (cont.) so each
    chunk keeps context. Empty input -> []."""
    if not records:
        return []
    from cvflow.discovery import DROP_BUCKET_ORDER

    grouped: dict[str, list[Any]] = {}
    for r in records:
        grouped.setdefault(r.bucket, []).append(r)

    messages: list[str] = []
    current: list[str] = []
    length = 0

    def flush() -> None:
        nonlocal current, length
        if current:
            messages.append("\n".join(current))
            current = []
            length = 0

    def add(line: str) -> None:
        nonlocal length
        if length + len(line) + 1 > _MAX_MSG:
            flush()
        current.append(line)
        length += len(line) + 1

    for bucket in DROP_BUCKET_ORDER:
        recs = grouped.get(bucket)
        if not recs:
            continue
        header = f"🚫 Dropped {len(recs)} — {bucket}"
        add(header)
        for r in recs:
            line = f"• {r.label} — {r.detail}"
            if length + len(line) + 1 > _MAX_MSG:
                flush()
                add(f"{header} (cont.)")
            add(line)
    flush()
    return messages


def run_job(
    job: str, *, store: Any, discovery: Any, otp: Any, notify: Any,
    learn_provider: Any = None, min_decisions: int = 5,
    learning_dir: str = "data/learning",
    progress: Callable[[str], None] | None = None,
    report_drops: bool = False,
    lock: Any = None,
    summary_provider: Any = None,
    log_dir: str | None = None,
    summary_max_chars: int = 700,
    summary_samples: int = 3,
) -> None:
    """Dispatch one scheduled job. Pure of config/network — deps are injected.

    For ``discover``: a single-run lock (default real ``discovery_lock``) prevents the
    daily cron and a manual ``/discover`` double-running. ``progress`` streams per-stage
    messages (manual only); ``report_drops`` posts the grouped drop report after the digest.
    """
    if job == "discover":
        from cvflow.runlock import AlreadyRunning, discovery_lock

        cm = lock if lock is not None else discovery_lock()
        acquired = False
        try:
            with cm:
                acquired = True  # past lock acquisition — any AlreadyRunning now is a real bug
                from cvflow.digest_summary import summarize_drops, write_discover_log

                result = discovery.discover(progress=progress or (lambda _m: None))
                ordered = [bj.posting.job_id for bj in result.get("M", []) + result.get("N", [])]
                store.set_digest_slots(ordered)
                digest = format_digest(result)
                log_path = None
                if log_dir:
                    chunks = format_drop_report(result.get("_drop_records", []))
                    log_path = write_discover_log(digest, chunks, log_dir)
                notify(digest)
                if report_drops:
                    summary = (
                        summarize_drops(
                            result.get("_drop_records", []),
                            provider=summary_provider,
                            max_chars=summary_max_chars,
                            samples_per_bucket=summary_samples,
                        )
                        if summary_provider
                        else None
                    )
                    msg = (
                        summary
                        or _filtered_footer(result.get("_dropped"))
                        or "🚫 No jobs dropped."
                    )
                    if log_path is not None:
                        msg += f"\n📄 Full breakdown: {log_path}"
                    notify(msg)
        except AlreadyRunning:
            if acquired:
                raise  # came from inside the run, not lock contention — never swallow it
            notify("⏳ A discovery run is already in progress — the digest is on its way")
        return
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


def _build_provider(cfg: Any) -> Any:
    """Construct a NimProvider from a config LLM block."""
    from cvflow.llm import NimProvider

    return NimProvider(
        base_url=cfg.base_url, api_key=cfg.api_key, model=cfg.model,
        max_requests_per_minute=cfg.max_requests_per_minute,
        seed_field="random_seed" if cfg.provider == "mistral" else "seed",
    )


def _build(config: Any) -> tuple[Any, Any, Any, Any, Any]:
    """Construct the minimal services for the cron jobs (NO browser/Automator)."""
    from pathlib import Path

    from cvflow.auth import OtpCoordinator
    from cvflow.discovery import DiscoveryService
    from cvflow.discovery.benchmark import build_fingerprint
    from cvflow.discovery.skills import load_skill_profile
    from cvflow.knowledge import KnowledgeBase
    from cvflow.notify import HermesNotifier
    from cvflow.storage import ApplicationStore

    store = ApplicationStore(config.storage.db_path)
    knowledge = KnowledgeBase.load(
        config.profile.knowledge_base_dir, config.storage.form_fields_path
    )

    brain = _build_provider(config.llm.brain)
    distiller = _build_provider(config.llm.distillation)  # Mistral mistral-small
    fingerprint = build_fingerprint(
        prefs_text=knowledge.full_context(), prefer_roles=config.preferences.prefer_roles
    )
    candidate_skills, skill_synonyms = load_skill_profile(
        Path(config.profile.knowledge_base_dir) / "candidate_skills.yaml"
    )
    discovery = DiscoveryService(
        store,
        search_terms=config.discovery.search_terms,
        locations=config.discovery.locations,
        sites=config.discovery.sites,
        results_wanted_per_site=config.discovery.results_wanted_per_site,
        hours_old=config.discovery.hours_old,
        exclude_title_keywords=config.preferences.exclude_title_keywords,
        min_ctc_lpa=config.preferences.min_ctc_lpa,
        country_indeed=config.discovery.country_indeed,
        linkedin_fetch_description=config.discovery.linkedin_fetch_description,
        distiller=distiller,
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
        reconsider_discovered=config.discovery.reconsider_discovered,
        candidate_skills=candidate_skills,
        skill_synonyms=skill_synonyms,
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
    if not args or len(args) > 2:
        raise SystemExit(
            "usage: python -m cvflow.cron <discover [--progress]|sweep-otp|heartbeat|learn>"
        )
    job = args[0]
    manual = job == "discover" and "--progress" in args[1:]
    summary_provider: Any = None
    log_dir: str | None = None
    summary_max_chars = 700
    summary_samples = 3
    report_drops = manual
    if services is None:
        import logging

        from cvflow.config import load_config

        # Real invocation: stream INFO progress (stage timings, per-job distill) to stdout
        # so a live run is observable. Tests inject `services` and skip this.
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )
        cfg = load_config("config.yaml")
        services = _build(cfg)
        block = getattr(cfg.llm, cfg.discovery.drop_summary_provider)
        summary_provider = _build_provider(block)
        log_dir = cfg.discovery.log_dir
        summary_max_chars = cfg.discovery.drop_summary_max_chars
        summary_samples = cfg.discovery.drop_summary_samples_per_bucket
        report_drops = cfg.discovery.summarize_drops if manual else cfg.discovery.cron_sends_drops
    store, discovery, otp, notify, brain = services
    try:
        run_job(
            job, store=store, discovery=discovery, otp=otp, notify=notify,
            learn_provider=brain,
            progress=notify if manual else None,
            report_drops=report_drops,
            summary_provider=summary_provider,
            log_dir=log_dir,
            summary_max_chars=summary_max_chars,
            summary_samples=summary_samples,
        )
    except Exception as exc:  # noqa: BLE001 — a cron crash must still reach the user
        notify(f"⚠️ cvflow cron job {job!r} failed: {exc}")
        raise


if __name__ == "__main__":
    main()
