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
from typing import Any

from cvflow.statemachine import Status
from cvflow.storage import UnknownJob

TOOL_NAMES: tuple[str, ...] = (
    "ping",
    "discover",
    "analyze_jd",
    "list_applications",
    "get_application",
    "request_review",
    "compose_essay",
    "status_report",
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
        use_jd_analysis: bool = False,
        notify: Any = None,
    ) -> None:
        self._store = store
        self._knowledge = knowledge
        self._discovery = discovery
        self._analyzer = analyzer
        self._tailor = tailor
        self._output_dir = output_dir
        self._essay_provider = essay_provider
        self._use_jd_analysis = use_jd_analysis
        self._notify = notify

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    def ping(self) -> dict[str, str]:
        """Return a simple health-check dict."""
        return {"status": "ok", "service": "cvflow"}

    # ------------------------------------------------------------------
    # Read skills
    # ------------------------------------------------------------------

    def list_applications(
        self, status: str, limit: int = 10, offset: int = 0
    ) -> dict[str, Any]:
        """List / show the user's job applications in a given status. USE THIS whenever
        asked to list, show, or see jobs or applications — e.g. "list the discovered jobs",
        "show my applications", "what jobs are pending review", "any approved jobs?".

        ``status`` must be one of: discovered, pending_review, approved, applied,
        otp_timeout, skipped, failed (the digest's new jobs are ``discovered``).

        Returns a compact, paginated view — minimal per-job fields (job_id/company/role) so a
        60+ job list doesn't blow the reply budget. Default 10 per page; ``total`` +
        ``next_offset`` let you page ("show the next 10" → call again with offset=next_offset).
        """
        apps = self._store.list_by_status(Status(status))
        total = len(apps)
        window = apps[offset : offset + limit]
        end = offset + len(window)
        return {
            "status": status,
            "total": total,
            "offset": offset,
            "count": len(window),
            "next_offset": end if end < total else None,
            "applications": [
                {"job_id": a.job_id, "company": a.company, "role": a.role}
                for a in window
            ],
        }

    def get_application(self, job_id: str) -> dict[str, Any] | None:
        """Get / show the full details of ONE specific job application — its status, the JD
        URL/link, company, role, and dates. USE THIS when asked for the details, info, link,
        or status of a particular job (e.g. "details for the Rarr Technologies role", "show me
        the Django backend job", "what's the link for that application?").

        Identified by ``job_id`` (e.g. "naukri:080626501910"). If you only know the company or
        role NAME, first call ``list_applications`` to find the matching ``job_id``, then pass
        it here. Returns a compact record (drops always-null proof/OTP internals); None if not
        found.
        """
        app = self._store.get(job_id)
        if app is None:
            return None
        out: dict[str, Any] = {
            "job_id": app.job_id,
            "company": app.company,
            "role": app.role,
            "status": app.status.value,
            "jd_url": app.jd_url,
            "discovered_at": app.discovered_at,
        }
        for k in ("tailored_pdf_path", "applied_at", "confirmation_ref"):
            v = getattr(app, k, None)
            if v:
                out[k] = v
        return out

    # ------------------------------------------------------------------
    # Workflow skills
    # ------------------------------------------------------------------

    def request_review(self, job_id: str, *, feedback: str | None = None) -> dict[str, Any]:
        """Tailor + compile the PDF, then move to PENDING_REVIEW. Never approves.

        Builds the JDAnalysis from the cached crux (default) or the stored JD text, per
        config.resume.use_jd_analysis. Status flips only AFTER a successful compile, so a
        tailoring failure never orphans the job in pending_review. Approval is the human
        /apply command handled out-of-band by cvflow.gate.
        """
        from cvflow.analysis import resolve_tailoring_analysis

        app = self._store.get(job_id)
        if app is None:
            raise UnknownJob(job_id)
        analysis = resolve_tailoring_analysis(
            job_id, store=self._store, analyzer=self._analyzer,
            use_jd_analysis=self._use_jd_analysis, notify=self._notify,
        )
        plan = self._tailor.plan(analysis, feedback=feedback)
        pdf = self._tailor.compile_tailored(plan, self._output_dir)
        if app.status is Status.DISCOVERED:
            self._store.set_status(job_id, Status.PENDING_REVIEW)
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
    from cvflow.notify import HermesNotifier
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
    from cvflow.discovery.skills import load_skill_profile

    brain = _provider(config.llm.brain)
    fingerprint = build_fingerprint(
        prefs_text=knowledge.full_context(), prefer_roles=config.preferences.prefer_roles
    )
    candidate_skills, skill_synonyms = load_skill_profile(
        Path(config.profile.knowledge_base_dir) / "candidate_skills.yaml"
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
        candidate_skills=candidate_skills,
        skill_synonyms=skill_synonyms,
    )
    analyzer = JDAnalyzer(brain)

    return CvflowTools(
        store=store,
        knowledge=knowledge,
        discovery=discovery,
        analyzer=analyzer,
        tailor=tailor,
        output_dir=config.resume.output_dir,
        essay_provider=tailoring,
        use_jd_analysis=config.resume.use_jd_analysis,
        notify=HermesNotifier(),
    )
