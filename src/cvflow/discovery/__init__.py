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
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

__all__ = [
    "JobPosting",
    "RankedJob",
    "LLMRanker",
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


@dataclass(frozen=True)
class RankedJob:
    posting: JobPosting
    summary: str
    rationale: str


def _stable_job_id(site: str, raw_id: str, url: str) -> str:
    if raw_id:
        return f"{site}:{raw_id}"
    digest = hashlib.sha1(url.encode()).hexdigest()[:12]
    return f"{site}:{digest}"


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
                title=str(row.get("title") or ""),
                company=str(row.get("company") or ""),
                location=str(row.get("location") or ""),
                description=str(row.get("description") or ""),
                url=url,
                site=site,
                date_posted=str(row.get("date_posted") or ""),
            )
        )
    return out


def _strip_code_fence(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        if t.endswith("```"):
            t = t.rsplit("```", 1)[0]
    return t.strip()


class _Provider(Protocol):
    def generate(self, prompt: str) -> str: ...


class _Ranker(Protocol):
    def rank(self, postings: list[JobPosting], top_n: int) -> list[RankedJob]: ...


class LLMRanker:
    """Ranks candidate postings against the profile via the (Gemini) LLM client."""

    def __init__(self, provider: _Provider, profile_context: str) -> None:
        self._provider = provider
        self._profile_context = profile_context

    def _build_prompt(self, postings: list[JobPosting]) -> str:
        jobs = [
            {
                "job_id": p.job_id,
                "title": p.title,
                "company": p.company,
                "location": p.location,
                "description": p.description[:1500],
            }
            for p in postings
        ]
        return (
            "You rank job postings by fit against a candidate's profile.\n"
            "Return ONLY a JSON array, best-first, of objects "
            '{"job_id", "summary", "rationale"}. Use only job_ids from the list.\n\n'
            f"## Candidate profile\n{self._profile_context}\n\n"
            f"## Job postings\n{json.dumps(jobs, indent=2)}\n"
        )

    def rank(self, postings: list[JobPosting], top_n: int) -> list[RankedJob]:
        by_id = {p.job_id: p for p in postings}
        raw = self._provider.generate(self._build_prompt(postings))
        entries = json.loads(_strip_code_fence(raw))
        ranked: list[RankedJob] = []
        for entry in entries:
            posting = by_id.get(str(entry.get("job_id")))
            if posting is None:
                continue  # LLM-fabricated / non-candidate id — drop it
            ranked.append(
                RankedJob(
                    posting=posting,
                    summary=str(entry.get("summary", "")),
                    rationale=str(entry.get("rationale", "")),
                )
            )
        return ranked[:top_n]


SearchFn = Callable[..., list[dict[str, Any]]]


def _jobspy_search(
    *,
    site_name: list[str],
    search_term: str,
    location: str,
    results_wanted: int,
    hours_old: int,
) -> list[dict[str, Any]]:
    from jobspy import scrape_jobs

    df = scrape_jobs(
        site_name=site_name,
        search_term=search_term,
        location=location,
        results_wanted=results_wanted,
        hours_old=hours_old,
    )
    if df is None or df.empty:
        return []
    return list(df.to_dict("records"))


class DiscoveryService:
    """Orchestrates search → cross-day dedup → ranking → persist → top-N."""

    def __init__(
        self,
        store: Any,
        ranker: _Ranker,
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
    ) -> None:
        self._store = store
        self._ranker = ranker
        self._search_fn = search_fn
        self._search_terms = search_terms
        self._locations = locations
        self._sites = sites
        self._results_wanted_per_site = results_wanted_per_site
        self._hours_old = hours_old
        self._top_n = top_n
        self._throttle_seconds = throttle_seconds
        self._sleep = sleep

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

    def discover(self) -> list[RankedJob]:
        postings = normalize_rows(self._gather_rows())
        candidates = [p for p in postings if not self._store.exists(p.job_id)]
        try:
            ranked = self._ranker.rank(candidates, self._top_n)
        except Exception as exc:  # noqa: BLE001
            # The ranking LLM (NIM free tier) can time out / error. Don't lose the
            # whole digest — degrade to unranked candidates so the user still sees
            # today's jobs (invariant 3); they're flagged as unranked.
            logger.warning("ranking failed (%s); presenting unranked candidates", exc)
            ranked = [
                RankedJob(posting=p, summary="", rationale="(ranking unavailable)")
                for p in candidates[: self._top_n]
            ]
        for rj in ranked:
            p = rj.posting
            self._store.add(p.job_id, p.company, p.title, p.url)
        return ranked
