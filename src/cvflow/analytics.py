"""Analytics rollups + decision recording (Phase 14D).

Pure read/write over the SQLite store — no model calls. This is the dashboard
payload and the input to the weekly learning summary.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
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


def _median_or_none(values: Sequence[float]) -> float | None:
    return median(values) if values else None


def _rollup(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate one set of decision rows into apply-rates, medians, and top-skipped."""
    totals: dict[str, int] = defaultdict(int)
    by_role: dict[str, list[int]] = defaultdict(list)
    by_company_type: dict[str, list[int]] = defaultdict(list)
    by_cohort: dict[str, list[int]] = defaultdict(list)
    applied_fit: list[int] = []
    skipped_fit: list[int] = []
    applied_bench: list[int] = []
    skipped_bench: list[int] = []
    applied_ctc: list[float] = []
    skipped_ctc: list[float] = []
    skipped_company: dict[str, int] = defaultdict(int)
    skipped_role: dict[str, int] = defaultdict(int)
    for r in rows:
        is_apply = r["decision"] == "apply"
        flag = 1 if is_apply else 0
        totals[r["decision"]] += 1
        if r.get("role_family"):
            by_role[r["role_family"]].append(flag)
        if r.get("company_type"):
            by_company_type[r["company_type"]].append(flag)
        if r.get("cohort"):
            by_cohort[r["cohort"]].append(flag)
        if r.get("fit_score") is not None:
            (applied_fit if is_apply else skipped_fit).append(r["fit_score"])
        if r.get("benchmark") is not None:
            (applied_bench if is_apply else skipped_bench).append(r["benchmark"])
        if r.get("ctc_lpa") is not None:
            (applied_ctc if is_apply else skipped_ctc).append(r["ctc_lpa"])
        if not is_apply and r.get("company"):
            skipped_company[r["company"]] += 1
        if not is_apply and r.get("role_family"):
            skipped_role[r["role_family"]] += 1

    def _rate(d: dict[str, list[int]]) -> dict[str, float]:
        return {k: sum(v) / len(v) for k, v in d.items()}

    return {
        "totals": dict(totals),
        "apply_rate_by_role": _rate(by_role),
        "apply_rate_by_company_type": _rate(by_company_type),
        "apply_rate_by_cohort": _rate(by_cohort),
        "median_fit_applied": _median_or_none(applied_fit),
        "median_fit_skipped": _median_or_none(skipped_fit),
        "median_benchmark_applied": _median_or_none(applied_bench),
        "median_benchmark_skipped": _median_or_none(skipped_bench),
        "median_ctc_applied": _median_or_none(applied_ctc),
        "median_ctc_skipped": _median_or_none(skipped_ctc),
        "top_skipped_companies": sorted(
            skipped_company.items(), key=lambda kv: kv[1], reverse=True
        )[:10],
        "top_skipped_roles": sorted(
            skipped_role.items(), key=lambda kv: kv[1], reverse=True
        )[:10],
    }


def _status_counts(store: Any) -> dict[str, int]:
    try:
        from cvflow.statemachine import Status

        return {s.value: len(store.list_by_status(s)) for s in Status}
    except Exception:  # noqa: BLE001 — analytics never fails the caller
        return {}


def summarize(store: Any) -> dict[str, Any]:
    """Aggregate the decisions log into a dashboard-ready dict.

    All-time rollup fields sit at the top level (stable keys); a ``last_7d``
    sub-rollup and a ``status_counts`` snapshot are added alongside.
    """
    rows = store.recent_decisions(10_000)
    cutoff = (datetime.now(UTC) - timedelta(days=7)).isoformat()
    recent = [r for r in rows if str(r.get("decided_at", "")) >= cutoff]
    summary = _rollup(rows)
    summary["last_7d"] = _rollup(recent)
    summary["status_counts"] = _status_counts(store)
    return summary
