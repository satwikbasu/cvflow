"""Discovery (Goal 1): multi-board search → cross-day dedup → LLM ranking → top-N.

Scraping is abstracted behind an injectable ``search_fn`` (default: JobSpy). This
is the source-adapter seam: a different provider (e.g. a hosted jobs API) can slot
in here without touching dedup, ranking, or storage. JobSpy is the only zero-cost
self-hosted default (CLAUDE.md invariant 4 — no per-call/SaaS cost).

Ranking reorders only real postings; it never invents jobs (invariant 2 — any
job_id the LLM returns that is not a candidate is dropped).

PRIVACY (open risk, see build plan): ranking sends profile context (PII) to the
free-tier LLM. Tests are fully mocked; resolve consent/redaction before live runs.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

__all__ = [
    "JobPosting",
    "BenchmarkedJob",
    "DiscoveryService",
    "normalize_rows",
]

logger = logging.getLogger("cvflow.discovery")


@dataclass(frozen=True)
class JobPosting:
    job_id: str
    title: str
    company: str
    location: str
    description: str
    url: str
    site: str
    date_posted: str
    min_amount: float | None = None
    max_amount: float | None = None
    currency: str | None = None
    experience_range: str | None = None
    job_type: str | None = None


def _stable_job_id(site: str, raw_id: str, url: str) -> str:
    if raw_id:
        return f"{site}:{raw_id}"
    digest = hashlib.sha1(url.encode()).hexdigest()[:12]
    return f"{site}:{digest}"


def _clean(value: Any) -> str:
    """Stringify a JobSpy cell, mapping NaN/None/'nan' to ''."""
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() == "nan" else text


def _num(value: Any) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if f != f else f  # NaN check


_YEARS_RE = re.compile(r"(\d+)")


def _parse_min_years(text: str | None) -> int | None:
    """Parse the minimum years from a Naukri ``experience_range`` (e.g. '2-4 Yrs').

    Returns 0 for fresher/entry-level, the leading integer for a range/'5+', and
    None when no number is stated (never fabricate — invariant 2).
    """
    if not text:
        return None
    t = text.strip().lower()
    if "fresher" in t or "entry" in t:
        return 0
    m = _YEARS_RE.search(t)
    return int(m.group(1)) if m else None


def normalize_rows(rows: list[dict[str, Any]]) -> list[JobPosting]:
    """Convert raw search rows to deduplicated :class:`JobPosting` objects."""
    out: list[JobPosting] = []
    seen: set[str] = set()
    for row in rows:
        url = str(row.get("job_url") or row.get("url") or "")
        site = str(row.get("site") or "")
        if not url and not row.get("id"):
            continue  # unusable row (no stable handle)
        job_id = _stable_job_id(site, str(row.get("id") or ""), url)
        if job_id in seen:
            continue  # within-batch dedup, first wins
        seen.add(job_id)
        out.append(
            JobPosting(
                job_id=job_id,
                title=_clean(row.get("title")),
                company=_clean(row.get("company")),
                location=_clean(row.get("location")),
                description=_clean(row.get("description")),
                url=url,
                site=site,
                date_posted=_clean(row.get("date_posted")),
                min_amount=_num(row.get("min_amount")),
                max_amount=_num(row.get("max_amount")),
                currency=_clean(row.get("currency")) or None,
                experience_range=_clean(row.get("experience_range")) or None,
                job_type=_clean(row.get("job_type")) or None,
            )
        )
    return out



SearchFn = Callable[..., list[dict[str, Any]]]

# Imported here (not at module top) because these modules import JobPosting from
# this package — JobPosting must be defined first to avoid a circular import.
from cvflow.discovery.benchmark import (  # noqa: E402
    BenchmarkedJob,
    benchmark_cohort,
    effective_lpa,
    fit_scores,
)
from cvflow.discovery.distill import Crux, Distiller, distill_all  # noqa: E402
from cvflow.discovery.rules import crux_excluded  # noqa: E402


def _jobspy_search(
    *,
    site_name: list[str],
    search_term: str,
    location: str,
    results_wanted: int,
    hours_old: int,
    country_indeed: str = "usa",
    linkedin_fetch_description: bool = False,
) -> list[dict[str, Any]]:
    from jobspy import scrape_jobs

    df = scrape_jobs(
        site_name=site_name,
        search_term=search_term,
        location=location,
        results_wanted=results_wanted,
        hours_old=hours_old,
        country_indeed=country_indeed,
        linkedin_fetch_description=linkedin_fetch_description,
        enforce_annual_salary=True,
    )
    if df is None or df.empty:
        return []
    return list(df.to_dict("records"))


class DiscoveryService:
    """Orchestrates search → cross-day dedup → ranking → persist → top-N."""

    def __init__(
        self,
        store: Any,
        search_fn: SearchFn = _jobspy_search,
        *,
        search_terms: list[str],
        locations: list[str],
        sites: list[str],
        results_wanted_per_site: int,
        hours_old: int,
        top_n: int,
        throttle_seconds: float = 5.0,
        sleep: Callable[[float], None] = time.sleep,
        exclude_title_keywords: list[str] | None = None,
        min_ctc_lpa: int = 0,
        country_indeed: str = "usa",
        linkedin_fetch_description: bool = False,
        max_rank_candidates: int = 40,
        distiller: Any = None,
        brain: Any = None,
        fingerprint: str = "",
        prefer_roles: dict[str, float] | None = None,
        exclude_when: list[dict[str, Any]] | None = None,
        fit_weight: float = 0.70,
        comp_weight: float = 0.30,
        top_ctc_lpa: int = 40,
        max_distill_per_cohort: int = 60,
        top_n_per_cohort: int = 5,
        distill_seed: int = 73,
        yoe_ceiling: int | None = None,
    ) -> None:
        self._store = store
        self._search_fn = search_fn
        self._yoe_ceiling = yoe_ceiling
        self._search_terms = search_terms
        self._locations = locations
        self._sites = sites
        self._results_wanted_per_site = results_wanted_per_site
        self._hours_old = hours_old
        self._top_n = top_n
        self._throttle_seconds = throttle_seconds
        self._sleep = sleep
        self._exclude_title_keywords = [k.lower() for k in (exclude_title_keywords or [])]
        self._min_ctc_lpa = min_ctc_lpa
        self._country_indeed = country_indeed
        self._linkedin_fetch_description = linkedin_fetch_description
        self._max_rank_candidates = max_rank_candidates
        self._distiller_provider = distiller
        self._brain = brain
        self._fingerprint = fingerprint
        self._prefer_roles = prefer_roles or {}
        self._exclude_when = exclude_when or []
        self._fit_weight = fit_weight
        self._comp_weight = comp_weight
        self._top_ctc_lpa = top_ctc_lpa
        self._max_distill_per_cohort = max_distill_per_cohort
        self._top_n_per_cohort = top_n_per_cohort
        self._distill_seed = distill_seed

    def _gather_rows(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for term in self._search_terms:
            for location in self._locations:
                try:
                    rows.extend(
                        self._search_fn(
                            site_name=self._sites,
                            search_term=term,
                            location=location,
                            results_wanted=self._results_wanted_per_site,
                            hours_old=self._hours_old,
                            country_indeed=self._country_indeed,
                            linkedin_fetch_description=self._linkedin_fetch_description,
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    # JobSpy/board scraping is brittle (anti-bot 403s, endpoint drift,
                    # a single unparseable posting). One failing batch must never abort
                    # the whole run or silence the digest (invariant 3) — log + continue.
                    logger.warning(
                        "discovery search failed for term=%r location=%r: %s",
                        term, location, exc,
                    )
                self._sleep(self._throttle_seconds)
        return rows

    def _prefilter(self, postings: list[JobPosting]) -> list[JobPosting]:
        kept: list[JobPosting] = []
        for p in postings:
            title = p.title.lower()
            if any(k in title for k in self._exclude_title_keywords):
                logger.info("prefilter drop (title) %s: %s", p.job_id, p.title)
                continue
            # Internships are a hard deal-breaker (full-time only). JobSpy's structured
            # job_type is reliable when present; an absent job_type is never dropped.
            if p.job_type and "intern" in p.job_type.lower():
                logger.info("prefilter drop (internship) %s: %s", p.job_id, p.job_type)
                continue
            cap = p.max_amount if p.max_amount is not None else p.min_amount
            # only filter on salary when stated AND in INR (else keep + let ranker flag)
            if cap is not None and (p.currency or "INR").upper() == "INR":
                if cap / 100_000 < self._min_ctc_lpa:
                    logger.info("prefilter drop (salary) %s: %s", p.job_id, cap)
                    continue
            # Naukri structured YOE gate (when experience_range is stated): drop only on a
            # parsed minimum above the ceiling — an unparseable/absent range is kept.
            if self._yoe_ceiling is not None and p.experience_range:
                min_years = _parse_min_years(p.experience_range)
                if min_years is not None and min_years > self._yoe_ceiling:
                    logger.info(
                        "prefilter drop (experience) %s: %s", p.job_id, p.experience_range
                    )
                    continue
            kept.append(p)
        return kept

    def _rank_cohort(
        self, cohort: str, cruxes: list[Crux], by_id: dict[str, JobPosting]
    ) -> list[BenchmarkedJob]:
        """Fit-score + benchmark one already-distilled, already-partitioned cohort."""
        if not cruxes:
            return []
        jobs = {c.job_id: by_id[c.job_id] for c in cruxes}
        t = time.monotonic()
        # Fit runs on the distillation provider (Mistral) — it enforces the json_schema
        # array shape that NIM can't (NIM ignores schemas + emits a single object).
        fits = fit_scores(
            cruxes, fingerprint=self._fingerprint,
            prefer_roles=self._prefer_roles, provider=self._distiller_provider,
        )
        logger.info(
            "cohort %s: fit-scored %d cruxes in %.1fs (1 call)",
            cohort, len(cruxes), time.monotonic() - t,
        )
        ranked = benchmark_cohort(
            jobs, fits, cohort=cohort, fit_weight=self._fit_weight,
            comp_weight=self._comp_weight, min_lpa=self._min_ctc_lpa,
            top_lpa=self._top_ctc_lpa,
            cruxes={c.job_id: c for c in cruxes},
        )
        return ranked[: self._top_n_per_cohort]

    def discover(self) -> dict[str, list[BenchmarkedJob]]:
        t0 = time.monotonic()
        rows = self._gather_rows()
        logger.info("stage scrape: %d raw rows in %.1fs", len(rows), time.monotonic() - t0)
        postings = self._prefilter(normalize_rows(rows))
        candidates = [p for p in postings if not self._store.exists(p.job_id)]
        logger.info(
            "stage prefilter+dedup: %d candidates (from %d postings)",
            len(candidates), len(postings),
        )
        # Distill ONCE over the newest candidates, then partition — salary is usually only
        # in the JD text (JobSpy structured fields are empty), so the M/N split must use the
        # crux-extracted salary, not the pre-distill structured field.
        capped = sorted(candidates, key=lambda p: p.date_posted, reverse=True)[
            : self._max_distill_per_cohort
        ]
        by_id = {p.job_id: p for p in capped}
        distiller = Distiller(self._distiller_provider, seed=self._distill_seed)
        t = time.monotonic()
        cruxes = distill_all(capped, self._store, distiller)
        logger.info(
            "stage distill: %d/%d jobs in %.1fs", len(cruxes), len(capped), time.monotonic() - t
        )
        m_cruxes: list[Crux] = []
        n_cruxes: list[Crux] = []
        for c in cruxes:
            excluded, reason = crux_excluded(c.model_dump(), self._exclude_when)
            if excluded:
                logger.info("exclude_when drop %s: %s", c.job_id, reason)
                continue
            lpa = effective_lpa(by_id[c.job_id], c)
            if lpa is not None and lpa < self._min_ctc_lpa:
                logger.info("salary-floor drop %s: %.1f LPA < %d", c.job_id, lpa, self._min_ctc_lpa)
                continue
            (m_cruxes if lpa is not None else n_cruxes).append(c)
        logger.info(
            "cohorts after gate+partition: M (stated INR pay)=%d, N (no stated pay)=%d",
            len(m_cruxes), len(n_cruxes),
        )
        result = {
            "M": self._rank_cohort("M", m_cruxes, by_id),
            "N": self._rank_cohort("N", n_cruxes, by_id),
        }
        for cohort in result.values():
            for bj in cohort:
                p = bj.posting
                if not self._store.exists(p.job_id):
                    self._store.add(p.job_id, p.company, p.title, p.url)
        logger.info(
            "discover total: %.1fs — presenting M=%d, N=%d",
            time.monotonic() - t0, len(result["M"]), len(result["N"]),
        )
        return result
