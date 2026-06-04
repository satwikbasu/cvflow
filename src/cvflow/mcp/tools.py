"""Pure-Python dispatcher that maps MCP skill calls to cvflow domain modules.

This module intentionally contains NO MCP transport code (no FastMCP import).
The server adapter (a later task) wraps these methods; keeping transport out
of here means every method is unit-testable without a running MCP server.

Critical invariant: there is NO ``approve`` method here and ``TOOL_NAMES``
does NOT contain "approve". The only path to ``Status.APPROVED`` is the
out-of-band Telegram approval callback calling
:meth:`cvflow.storage.ApplicationStore.approve` directly.  This class cannot
satisfy the approval gate — by construction.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from cvflow.statemachine import Status, guard_can_submit
from cvflow.storage import UnknownJob

TOOL_NAMES: tuple[str, ...] = (
    "ping",
    "discover",
    "analyze_jd",
    "list_applications",
    "get_application",
    "request_review",
    "submit",
)


class CvflowTools:
    """Dispatcher binding MCP skill names to cvflow domain operations."""

    def __init__(
        self,
        *,
        store: Any,
        knowledge: Any,
        discovery: Any,
        analyzer: Any,
        tailor: Any,
    ) -> None:
        self._store = store
        self._knowledge = knowledge
        self._discovery = discovery
        self._analyzer = analyzer
        self._tailor = tailor

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    def ping(self) -> dict[str, str]:
        """Return a simple health-check dict."""
        return {"status": "ok", "service": "cvflow"}

    # ------------------------------------------------------------------
    # Read skills
    # ------------------------------------------------------------------

    @staticmethod
    def _app_to_dict(app: Any) -> dict[str, Any]:
        d: dict[str, Any] = asdict(app)
        d["status"] = app.status.value
        return d

    def list_applications(self, status: str) -> list[dict[str, Any]]:
        """Return all applications with the given status string."""
        apps = self._store.list_by_status(Status(status))
        return [self._app_to_dict(a) for a in apps]

    def get_application(self, job_id: str) -> dict[str, Any] | None:
        """Return a single application dict or None if not found."""
        app = self._store.get(job_id)
        if app is None:
            return None
        return self._app_to_dict(app)

    # ------------------------------------------------------------------
    # Workflow skills
    # ------------------------------------------------------------------

    def request_review(self, job_id: str) -> dict[str, Any]:
        """Transition a job from DISCOVERED → PENDING_REVIEW."""
        self._store.set_status(job_id, Status.PENDING_REVIEW)
        return self._app_to_dict(self._store.get(job_id))

    def submit(self, job_id: str) -> dict[str, Any]:
        """Attempt to submit.  Raises SubmissionBlocked unless status is APPROVED.

        The APPROVED state can only be reached via the out-of-band Telegram
        approval callback — never from within this class.
        """
        app = self._store.get(job_id)
        if app is None:
            raise UnknownJob(job_id)
        guard_can_submit(app.status)  # raises SubmissionBlocked if not approved
        return self._app_to_dict(app)

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def discover(self) -> list[dict[str, Any]]:
        """Run discovery and return a list of ranked-job dicts."""
        ranked = self._discovery.discover()
        return [
            {
                "job_id": rj.posting.job_id,
                "title": rj.posting.title,
                "company": rj.posting.company,
                "location": rj.posting.location,
                "url": rj.posting.url,
                "summary": rj.summary,
                "rationale": rj.rationale,
            }
            for rj in ranked
        ]

    # ------------------------------------------------------------------
    # Analysis
    # ------------------------------------------------------------------

    def analyze_jd(self, job_id: str, *, jd_text: str | None = None) -> dict[str, Any]:
        """Fetch (or accept), analyze, persist, and return a JD analysis dict."""
        from cvflow.analysis import fetch_jd

        app = self._store.get(job_id)
        if app is None:
            raise UnknownJob(job_id)
        text = jd_text if jd_text is not None else fetch_jd(app.jd_url)
        analysis = self._analyzer.analyze(text)
        self._store.save_analysis(job_id, analysis)
        result: dict[str, Any] = json.loads(analysis.to_json())
        return result


def build_tools(config: Any) -> CvflowTools:
    """Wire up a :class:`CvflowTools` from a loaded :class:`cvflow.config.Config`."""
    from cvflow.knowledge import KnowledgeBase
    from cvflow.storage import ApplicationStore

    store = ApplicationStore(config.storage.db_path)
    knowledge = KnowledgeBase.load(
        config.profile.knowledge_base_dir,
        config.storage.form_fields_path,
    )
    return CvflowTools(
        store=store,
        knowledge=knowledge,
        discovery=None,
        analyzer=None,
        tailor=None,
    )
