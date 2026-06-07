"""Analytics rollups + decision recording (Phase 14D).

Pure read/write over the SQLite store — no model calls. This is the dashboard
payload and the input to the weekly learning summary.
"""

from __future__ import annotations

from collections import defaultdict
from statistics import median
from typing import Any


def record_decision(
    store: Any, decision: str, bj: Any, *,
    role_family: str | None = None, company_type: str | None = None,
) -> None:
    """Persist an apply/skip with the benchmarked-job context (best-effort)."""
    store.add_decision(
        job_id=bj.posting.job_id, decision=decision, cohort=bj.cohort,
        benchmark=bj.benchmark, fit_score=bj.fit_score, company=bj.posting.company,
        ctc_lpa=bj.ctc_lpa, concerns=list(bj.concerns),
        role_family=role_family, company_type=company_type,
    )


def summarize(store: Any) -> dict[str, Any]:
    """Aggregate the decisions log into a dashboard-ready dict."""
    rows = store.recent_decisions(10_000)
    totals: dict[str, int] = defaultdict(int)
    by_role: dict[str, list[int]] = defaultdict(list)
    applied_fit: list[int] = []
    skipped_fit: list[int] = []
    skipped_company: dict[str, int] = defaultdict(int)
    for r in rows:
        is_apply = r["decision"] == "apply"
        totals[r["decision"]] += 1
        if r.get("role_family"):
            by_role[r["role_family"]].append(1 if is_apply else 0)
        if r.get("fit_score") is not None:
            (applied_fit if is_apply else skipped_fit).append(r["fit_score"])
        if not is_apply and r.get("company"):
            skipped_company[r["company"]] += 1
    return {
        "totals": dict(totals),
        "apply_rate_by_role": {k: sum(v) / len(v) for k, v in by_role.items()},
        "median_fit_applied": median(applied_fit) if applied_fit else None,
        "median_fit_skipped": median(skipped_fit) if skipped_fit else None,
        "top_skipped_companies": sorted(
            skipped_company.items(), key=lambda kv: kv[1], reverse=True
        )[:10],
    }
