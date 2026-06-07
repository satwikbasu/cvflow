"""Stage-2 of discovery: hybrid benchmark + ranking (Phase 14C).

LLM scores only `fit` (see fit_scores, Task C3). Code owns `comp` and the blend.
India-focused: no FX, no location score (deferred — see spec 14C §8).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from cvflow.discovery import JobPosting

__all__ = ["FitResult", "BenchmarkedJob", "comp_score", "benchmark_cohort"]


@dataclass(frozen=True)
class FitResult:
    fit_score: int
    fit_reason: str
    concerns: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class BenchmarkedJob:
    posting: JobPosting
    benchmark: int
    fit_score: int
    fit_reason: str
    concerns: list[str]
    cohort: str            # "M" | "N"
    ctc_lpa: float | None = None

    @property
    def job_id(self) -> str:
        return self.posting.job_id


def comp_score(ctc_lpa: float, min_lpa: int, top_lpa: int) -> float:
    """0 at the floor, 1 at the top anchor, clamped. INR only (no FX)."""
    span = max(top_lpa - min_lpa, 1)
    return max(0.0, min(1.0, (ctc_lpa - min_lpa) / span))


def _ctc_lpa(posting: JobPosting) -> float | None:
    amount = posting.max_amount if posting.max_amount is not None else posting.min_amount
    if amount is None or (posting.currency or "INR").upper() != "INR":
        return None
    return amount / 100_000


def benchmark_cohort(
    jobs: dict[str, JobPosting],
    fits: dict[str, FitResult],
    *,
    cohort: str,
    fit_weight: float,
    comp_weight: float,
    min_lpa: int,
    top_lpa: int,
) -> list[BenchmarkedJob]:
    """Score + sort one cohort. M blends fit+comp; N is fit only."""
    out: list[BenchmarkedJob] = []
    for job_id, posting in jobs.items():
        fit = fits.get(job_id, FitResult(0, "(no fit score)", ["RANKING_DEGRADED"]))
        lpa = _ctc_lpa(posting)
        if cohort == "M" and lpa is not None:
            c = comp_score(lpa, min_lpa, top_lpa)
            score = round(100 * (fit_weight * fit.fit_score / 100 + comp_weight * c))
        else:
            score = round(100 * (fit.fit_score / 100))
        out.append(
            BenchmarkedJob(
                posting=posting, benchmark=score, fit_score=fit.fit_score,
                fit_reason=fit.fit_reason, concerns=list(fit.concerns), cohort=cohort,
                ctc_lpa=lpa,
            )
        )
    out.sort(key=lambda j: j.benchmark, reverse=True)
    return out
