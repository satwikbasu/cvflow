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
from urllib.parse import urlsplit

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
