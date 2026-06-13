"""ATS (applicant-tracking system) recon — classify job-posting URLs by portal.

P1E-1 instrumentation: every presented/analyzed job is tagged with the ATS that
hosts its application form, so the automation go/no-go decision (Greenhouse +
Lever share of *approved* jobs) is made on real data, not a guess. Pure URL
patterns by default; an optional one-hop redirect resolve handles shortlink/
aggregator URLs that only reveal the real ATS after a redirect.
"""

from __future__ import annotations

import logging
import urllib.error
import urllib.request
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from cvflow.storage import ApplicationStore

logger = logging.getLogger(__name__)

# The fixed vocabulary of classifications. "other" is the catch-all.
ATS_LABELS: tuple[str, ...] = (
    "greenhouse",
    "lever",
    "ashby",
    "workday",
    "taleo",
    "successfactors",
    "icims",
    "naukri",
    "linkedin",
    "indeed",
    "other",
)

# host-substring -> label. Matched against the URL netloc (host) only, so a
# vendor name appearing as a path segment or a careers-site subdomain prefix
# (e.g. greenhouse.acme.com) does NOT match — only the real ATS domains do.
_HOST_PATTERNS: tuple[tuple[str, str], ...] = (
    ("greenhouse.io", "greenhouse"),
    ("lever.co", "lever"),
    ("ashbyhq.com", "ashby"),
    ("myworkdayjobs.com", "workday"),
    ("taleo.net", "taleo"),
    ("successfactors.com", "successfactors"),
    ("successfactors.eu", "successfactors"),
    ("icims.com", "icims"),
    ("naukri.com", "naukri"),
    ("linkedin.com", "linkedin"),
    ("indeed.com", "indeed"),
)


def _classify_host(host: str) -> str:
    host = host.lower()
    for needle, label in _HOST_PATTERNS:
        # match the registrable domain or a subdomain of it, not a prefix label:
        # "acme.greenhouse.io" and "greenhouse.io" match; "greenhouse.acme.com" does not.
        if host == needle or host.endswith("." + needle):
            return label
    return "other"


def _resolve_one_hop(url: str) -> str | None:
    """Return the final URL after following redirects (10 s), or None on failure.

    Uses a HEAD request; urllib transparently follows redirects, so the response
    URL is the resolved target. Never raises — recon must never break discovery.
    """
    try:
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 - http(s) only
            return str(resp.url)
    except (urllib.error.URLError, ValueError, OSError) as exc:
        logger.warning("recon redirect resolve failed for %s: %s", url, exc)
        return None


def classify_ats(url: str, fetch_redirect: bool = False) -> str:
    """Return the ATS label for ``url`` (one of :data:`ATS_LABELS`).

    With ``fetch_redirect=True``, follow one hop of HTTP redirects (10 s
    timeout) and classify the resolved URL; any network failure falls back to
    classifying the original URL.
    """
    label = _classify_host(urlsplit(url).netloc)
    if label != "other" or not fetch_redirect:
        return label
    resolved = _resolve_one_hop(url)
    return _classify_host(urlsplit(resolved).netloc) if resolved else "other"


def build_report(store: ApplicationStore) -> dict[str, object]:
    """Per-ATS counts of presented jobs and approved (decision='apply') jobs.

    Untagged applications (``ats IS NULL``) and the literal ``other`` label both
    bucket under ``other`` so percentages cover the full presented set.
    """
    conn = store._conn  # same connection; read-only aggregate
    presented: dict[str, int] = {}
    for row in conn.execute(
        "SELECT COALESCE(ats, 'other') AS k, COUNT(*) AS n FROM applications GROUP BY k"
    ):
        presented[row["k"]] = presented.get(row["k"], 0) + row["n"]
    approved: dict[str, int] = {}
    for row in conn.execute(
        "SELECT COALESCE(a.ats, 'other') AS k, COUNT(*) AS n "
        "FROM decisions d JOIN applications a ON a.job_id = d.job_id "
        "WHERE d.decision = 'apply' GROUP BY k"
    ):
        approved[row["k"]] = approved.get(row["k"], 0) + row["n"]
    return {
        "presented": presented,
        "approved": approved,
        "totals": {
            "presented": sum(presented.values()),
            "approved": sum(approved.values()),
        },
    }


def format_report(report: dict[str, object]) -> str:
    presented: dict[str, int] = report["presented"]  # type: ignore[assignment]
    approved: dict[str, int] = report["approved"]  # type: ignore[assignment]
    totals: dict[str, int] = report["totals"]  # type: ignore[assignment]
    p_total = max(totals["presented"], 1)
    a_total = max(totals["approved"], 1)
    lines = ["ATS recon report", ""]
    lines.append(f"{'ats':<16}{'presented':>12}{'approved':>12}")
    for label in ATS_LABELS:
        p = presented.get(label, 0)
        a = approved.get(label, 0)
        if p == 0 and a == 0:
            continue
        lines.append(
            f"{label:<16}{p:>6} ({100 * p // p_total:>3}%){a:>6} ({100 * a // a_total:>3}%)"
        )
    lines += ["", f"totals: presented={totals['presented']} approved={totals['approved']}"]
    gh_lever = approved.get("greenhouse", 0) + approved.get("lever", 0)
    share = 100 * gh_lever // a_total
    lines.append(f"Greenhouse+Lever share of approved: {share}% (go/no-go >= ~20%)")
    return "\n".join(lines)


def build_healthcheck(store: ApplicationStore, since: str) -> dict[str, object]:
    """Is the recon instrument actually classifying *newly* discovered jobs?

    Looks only at jobs discovered on/after ``since`` (an ISO timestamp/date) —
    older rows pre-date tagging and are all ``other`` by definition, so they'd
    mask the signal. Returns the per-ATS spread of recent jobs plus a verdict:
    WORKING (some recent job got a real ATS), SUSPICIOUS (every recent job is
    ``other`` — the URL classifier is likely missing ATSes hidden behind a
    redirect or JS apply-handoff), or NO DATA (nothing discovered yet).
    """
    conn = store._conn  # same connection; read-only aggregate
    recent: dict[str, int] = {}
    for row in conn.execute(
        "SELECT COALESCE(ats, 'other') AS k, COUNT(*) AS n FROM applications "
        "WHERE discovered_at >= ? GROUP BY k",
        (since,),
    ):
        recent[row["k"]] = row["n"]
    total = sum(recent.values())
    non_other = total - recent.get("other", 0)
    if total == 0:
        verdict = "NO DATA — no jobs discovered since the cutoff yet"
    elif non_other == 0:
        verdict = (
            "SUSPICIOUS — every new job tagged 'other'. The URL classifier is "
            "probably missing ATSes hidden behind a redirect or a JS apply-handoff; "
            "consider upgrading to DOM-level classification (open the apply page)."
        )
    else:
        verdict = "WORKING — new jobs are being classified across real ATSes"
    return {
        "since": since,
        "recent": recent,
        "recent_total": total,
        "recent_non_other": non_other,
        "verdict": verdict,
    }


def format_healthcheck(report: dict[str, object]) -> str:
    recent: dict[str, int] = report["recent"]  # type: ignore[assignment]
    total: int = report["recent_total"]  # type: ignore[assignment]
    non_other: int = report["recent_non_other"]  # type: ignore[assignment]
    lines = [
        "ATS recon health-check",
        f"(jobs discovered since {report['since']})",
        "",
    ]
    for label in ATS_LABELS:
        n = recent.get(label, 0)
        if n:
            lines.append(f"{label:<16}{n:>6}")
    lines += [
        "",
        f"recent jobs: {total} (classified to a real ATS: {non_other})",
        f"Verdict: {report['verdict']}",
    ]
    return "\n".join(lines)


def _main(argv: list[str] | None = None) -> int:
    import argparse
    from datetime import UTC, datetime, timedelta

    from cvflow.config import load_config
    from cvflow.storage import ApplicationStore

    parser = argparse.ArgumentParser(prog="cvflow.recon")
    parser.add_argument("command", choices=["report", "healthcheck"])
    args = parser.parse_args(argv)
    config = load_config("config.yaml")
    store = ApplicationStore(config.storage.db_path)
    if args.command == "healthcheck":
        since = (datetime.now(UTC) - timedelta(days=7)).isoformat()
        print(format_healthcheck(build_healthcheck(store, since)))
    else:
        print(format_report(build_report(store)))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
