"""Stage-2 of discovery: hybrid benchmark + ranking (Phase 14C).

LLM scores only `fit` (see fit_scores, Task C3). Code owns `comp` and the blend.
India-focused: no FX, no location score (deferred — see spec 14C §8).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

from cvflow.discovery import JobPosting

logger = logging.getLogger("cvflow.discovery.benchmark")

__all__ = [
    "FitResult",
    "BenchmarkedJob",
    "comp_score",
    "benchmark_cohort",
    "fit_scores",
    "build_fingerprint",
    "FIT_SEED",
]

FIT_SEED = 1409


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


class _Provider(Protocol):
    def generate(self, prompt: str, **kwargs: Any) -> str: ...


def build_fingerprint(*, prefs_text: str, prefer_roles: dict[str, float]) -> str:
    roles = ", ".join(f"{k}={v}" for k, v in sorted(prefer_roles.items()))
    return f"{prefs_text}\nPreferred role families (weight): {roles}\n"


_FIT_PREAMBLE = (
    "You score job FIT (0-100) for a candidate from compact job 'cruxes'.\n"
    "Rubric: 90-100 role weight>=0.8 AND stack overlaps core tools AND seniority "
    "fresher/junior/mid; 70-89 weight>=0.5 or partial stack; 40-69 weight 0.2-0.5 or "
    "little overlap; 0-39 weight<0.2 or unrelated. Modifiers: -10 service/staffing "
    "company; +5 modern infra stack (docker/k8s/ci-cd/cloud). Clamp 0-100.\n"
    'Return ONLY a JSON array of {"job_id","fit_score","fit_reason"(<=120 chars),'
    '"concern_codes"(subset of STACK_MISMATCH,SERVICE_COMPANY,SENIORITY_BORDERLINE,'
    "ROLE_ADJACENT)}. Use only the given job_ids.\n"
)


def _fit_view(crux: Any) -> dict[str, Any]:
    return {
        "job_id": crux.job_id, "role_family": crux.role_family,
        "seniority_signal": crux.seniority_signal, "tech_stack": crux.tech_stack,
        "work_mode": crux.work_mode, "country": crux.country,
        "company_type": crux.company_type, "one_line": crux.one_line,
    }


def _unwrap_array(raw: str) -> str:
    """json_object mode may return {"jobs":[...]} or {"results":[...]}; find the array."""
    raw = raw.strip()
    if raw.startswith("["):
        return raw
    obj = json.loads(raw)
    if isinstance(obj, list):
        return raw
    for v in obj.values():
        if isinstance(v, list):
            return json.dumps(v)
    return "[]"


def fit_scores(
    cruxes: list[Any],
    *,
    fingerprint: str,
    prefer_roles: dict[str, float],
    provider: _Provider,
) -> dict[str, FitResult]:
    """One NIM call over all cruxes. On failure, every job degrades to fit 0 + flag."""
    if not cruxes:
        return {}
    ids = {c.job_id for c in cruxes}
    jobs_json = json.dumps(
        [_fit_view(c) for c in sorted(cruxes, key=lambda c: c.job_id)], indent=2
    )
    roles = ", ".join(f"{k}={v}" for k, v in sorted(prefer_roles.items()))
    prompt = (
        f"{_FIT_PREAMBLE}\nPreferred role families (weight): {roles}\n\n"
        f"## Candidate\n{fingerprint}\n\n## Jobs\n{jobs_json}\n"
    )
    try:
        raw = provider.generate(
            prompt, temperature=0, seed=FIT_SEED, top_p=0.1,
            max_tokens=min(4096, 60 * len(cruxes) + 200), json_object=True,
        )
        # response_format=json_object may wrap the array in an object; accept either.
        parsed = json.loads(_unwrap_array(raw))
    except Exception as exc:  # noqa: BLE001 — degrade, never lose the cohort
        logger.warning("fit scoring failed (%s); degrading cohort to fit 0", exc)
        return {jid: FitResult(0, "(ranking unavailable)", ["RANKING_DEGRADED"]) for jid in ids}
    out: dict[str, FitResult] = {}
    for entry in parsed:
        jid = str(entry.get("job_id"))
        if jid not in ids:
            continue
        out[jid] = FitResult(
            fit_score=int(entry.get("fit_score", 0) or 0),
            fit_reason=str(entry.get("fit_reason", "")),
            concerns=[str(c) for c in entry.get("concern_codes", [])],
        )
    for jid in ids:  # any job the model omitted still gets a row
        out.setdefault(jid, FitResult(0, "(omitted by ranker)", ["RANKING_DEGRADED"]))
    return out
