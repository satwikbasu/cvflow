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
    "compose_essay",
    "status_report",
    "fill_application",
    "resume_application",
    "submit_otp",
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
        output_dir: str = "data/tailored",
        essay_provider: Any = None,
        automator: Any = None,
        otp_coordinator: Any = None,
    ) -> None:
        self._store = store
        self._knowledge = knowledge
        self._discovery = discovery
        self._analyzer = analyzer
        self._tailor = tailor
        self._output_dir = output_dir
        self._essay_provider = essay_provider
        self._automator = automator
        self._otp = otp_coordinator

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

    def request_review(self, job_id: str, *, feedback: str | None = None) -> dict[str, Any]:
        """Move to PENDING_REVIEW, tailor + compile the PDF, return the review payload.

        Prepares the review; it never approves. Approval is the human /approve
        command handled out-of-band by cvflow.gate.
        """
        app = self._store.get(job_id)
        if app is None:
            raise UnknownJob(job_id)
        if app.status is Status.DISCOVERED:
            self._store.set_status(job_id, Status.PENDING_REVIEW)
        analysis = self._store.get_analysis(job_id)
        if analysis is None:
            raise UnknownJob(f"no JD analysis for {job_id}; run analyze_jd first")
        plan = self._tailor.plan(analysis, feedback=feedback)
        pdf = self._tailor.compile_tailored(plan, self._output_dir)
        self._store.set_tailored_pdf(job_id, str(pdf))
        return {
            "job_id": job_id,
            "status": Status.PENDING_REVIEW.value,
            "pdf_path": str(pdf),
            "diff": self._tailor.diff(plan),
            "analysis_summary": json.loads(analysis.to_json()),
            "instructions": (
                f"Reply /apply {job_id} to approve & apply, or /skip {job_id} to skip."
            ),
        }

    def compose_essay(self, question: str) -> dict[str, Any]:
        """Compose a grounded answer; flag clarification instead of guessing."""
        from cvflow.essays import compose_answer

        ans = compose_answer(question, self._knowledge, provider=self._essay_provider)
        return {
            "text": ans.text,
            "grounded": ans.grounded,
            "citations": ans.citations,
            "needs_clarification": ans.needs_clarification,
            "clarification": ans.clarification,
        }

    def status_report(self) -> dict[str, int]:
        """Return a count of applications per status."""
        return {s.value: len(self._store.list_by_status(s)) for s in Status}

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

    def fill_application(self, job_id: str) -> dict[str, Any]:
        """Fill the approved application; returns proof or a clarification request.

        Gated: the underlying Automator asserts guard_can_submit, so only an
        APPROVED job (set out-of-band by the human /apply) reaches the browser.
        """
        result: dict[str, Any] = self._automator.fill(job_id)
        return result

    def resume_application(self, job_id: str, answer: str) -> dict[str, Any]:
        """Continue a paused application with the user's clarification answer."""
        result: dict[str, Any] = self._automator.resume(job_id, answer)
        return result

    def submit_otp(self, job_id: str, otp: str) -> dict[str, Any]:
        """Resolve a user-supplied OTP; on time, resume the application.

        Gated: an on-time OTP routes into Automator.resume, which asserts
        guard_can_submit. An expired OTP returns otp_timeout and does NOT resume.
        """
        code = self._otp.provide(job_id, otp)
        if code is None:
            return {"otp_timeout": True, "job_id": job_id}
        result: dict[str, Any] = self._automator.resume(job_id, code)
        return result

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def discover(self) -> list[dict[str, Any]]:
        """Run discovery and return ranked-job dicts (M cohort first, then N)."""
        result = self._discovery.discover()
        out: list[dict[str, Any]] = []
        for bj in result.get("M", []) + result.get("N", []):
            out.append({
                "job_id": bj.posting.job_id, "title": bj.posting.title,
                "company": bj.posting.company, "location": bj.posting.location,
                "url": bj.posting.url, "cohort": bj.cohort, "benchmark": bj.benchmark,
                "fit_score": bj.fit_score, "fit_reason": bj.fit_reason,
                "concerns": bj.concerns, "ctc_lpa": bj.ctc_lpa,
            })
        return out

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
    from pathlib import Path

    from cvflow.knowledge import KnowledgeBase
    from cvflow.llm import NimProvider
    from cvflow.resume import ResumeTailor, parse_master
    from cvflow.storage import ApplicationStore

    store = ApplicationStore(config.storage.db_path)
    knowledge = KnowledgeBase.load(
        config.profile.knowledge_base_dir,
        config.storage.form_fields_path,
    )

    def _provider(cfg: Any) -> NimProvider:
        return NimProvider(
            base_url=cfg.base_url, api_key=cfg.api_key, model=cfg.model,
            max_requests_per_minute=cfg.max_requests_per_minute,
            seed_field="random_seed" if cfg.provider == "mistral" else "seed",
        )

    tailoring = _provider(config.llm.tailoring)        # Cerebras gpt-oss-120b
    distiller = _provider(config.llm.distillation)     # Mistral mistral-small
    # master_tex_path points at the master.tex FILE; parse_master wants its dir root.
    master_root = Path(config.resume.master_tex_path).parent
    tailor = ResumeTailor(tailoring, parse_master(master_root))

    from cvflow.analysis import JDAnalyzer
    from cvflow.discovery import DiscoveryService
    from cvflow.discovery.benchmark import build_fingerprint

    brain = _provider(config.llm.brain)
    fingerprint = build_fingerprint(
        prefs_text=knowledge.full_context(), prefer_roles=config.preferences.prefer_roles
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
    )
    analyzer = JDAnalyzer(brain)

    from cvflow.automation import Automator, FormFiller, SessionManager
    from cvflow.notify import HermesNotifier

    notifier = HermesNotifier()
    sessions = SessionManager(
        user_data_root=config.automation.storage_state_dir,
        headless=config.automation.headless,
        use_stealth=config.automation.use_stealth,
    )
    automator = Automator(
        store=store,
        knowledge=knowledge,
        provider=tailoring,
        sessions=sessions,
        filler_factory=FormFiller,
        notify=notifier,
        screenshot_dir=config.automation.storage_state_dir,
    )

    from cvflow.auth import OtpCoordinator, TokenVault

    TokenVault.create_or_load(config.security.fernet_key_path)  # ensure key exists, chmod 600
    otp_coordinator = OtpCoordinator(
        store=store,
        notify=notifier,
        timeout_minutes=config.auth.otp_timeout_minutes,
    )
    return CvflowTools(
        store=store,
        knowledge=knowledge,
        discovery=discovery,
        analyzer=analyzer,
        tailor=tailor,
        output_dir=config.resume.output_dir,
        essay_provider=tailoring,
        automator=automator,
        otp_coordinator=otp_coordinator,
    )
