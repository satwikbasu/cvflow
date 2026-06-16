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
import re
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
    "display_last_digest",
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

        Render each job on ONE line as ``N. <role> @ <company> — <link>`` (don't show the
        internal job_id; the user references jobs by their number ``N``). After listing, the
        user can say "details for N" / "/tailor N" and it refers to the same job. Default 10
        per page; ``total`` + ``next_offset`` let you page ("show the next 10" → call again
        with offset=next_offset).
        """
        apps = self._store.list_by_status(Status(status))
        total = len(apps)
        # Re-map the ordinal→job_id slots to THIS listing (numbered by discovered_at), so a
        # later "details/tailor/apply n" refers to the numbers just shown. The last numbered
        # list the user saw wins — overwriting any earlier /discover digest map.
        self._store.set_digest_slots([a.job_id for a in apps])
        window = apps[offset : offset + limit]
        end = offset + len(window)
        return {
            "status": status,
            "total": total,
            "offset": offset,
            "count": len(window),
            "next_offset": end if end < total else None,
            "applications": [
                {"n": offset + i, "role": a.role, "company": a.company, "link": a.jd_url}
                for i, a in enumerate(window, start=1)
            ],
        }

    def get_application(self, ref: str) -> dict[str, Any] | None:
        """Get / show ONE job's details — by job_id (e.g. "naukri:080626501910") OR by the
        ordinal "n" from the most recent digest or listing (e.g. "4" -> "details for job 4").
        USE THIS when asked for the details,
        info, link, status, or scores of a particular job (e.g. "details for job 4", "show me
        the Django backend job", "what's the link for that application?").

        Returns status, JD URL/link, company, role, dates, the fit/benchmark scores, and a crux
        summary (role family, must-have skills, one-line) so you can decide whether to /tailor
        it. If you only know the company or role NAME, first call ``list_applications`` to find
        the matching ``job_id``. Drops always-null proof/OTP internals; None if not found.
        """
        job_id = ref
        if ":" not in str(ref):
            try:
                slot = int(ref)
            except (TypeError, ValueError):
                return None
            resolved = self._store.get_digest_slot(slot)
            if resolved is None:
                return None
            job_id = resolved
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
        for k in ("tailored_pdf_path", "applied_at", "confirmation_ref",
                  "benchmark", "fit_score", "fit_reason", "cohort", "ctc_lpa"):
            v = getattr(app, k, None)
            if v is not None:
                out[k] = v
        if app.concerns:
            try:
                parsed = json.loads(app.concerns)
            except (ValueError, TypeError):
                parsed = None
            if parsed:  # set_discovery_meta stores "[]" for no concerns — drop that noise
                out["concerns"] = parsed
        crux_json = self._store.get_crux(job_id)
        if crux_json:
            c = json.loads(crux_json)
            out["crux"] = {
                k: c.get(k) for k in
                ("role_family", "seniority_signal", "must_have_skills",
                 "company_type", "one_line")
            }
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
        # Per-job filename so tailored resumes don't overwrite each other on disk.
        stem = "tailored-" + re.sub(r"[^A-Za-z0-9._-]", "-", job_id)
        pdf = self._tailor.compile_tailored(plan, self._output_dir, stem=stem)
        if app.status is Status.DISCOVERED:
            self._store.set_status(job_id, Status.PENDING_REVIEW)
        self._store.set_tailored_pdf(job_id, str(pdf))
        return {
            "job_id": job_id,
            "company": app.company,
            "role": app.role,
            "status": Status.PENDING_REVIEW.value,
            "pdf_path": str(pdf),
            "diff": self._tailor.diff(plan),
            "analysis_summary": json.loads(analysis.to_json()),
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

    def display_last_digest(self) -> dict[str, Any]:
        """Re-show the most recent job digest, exactly as it was first sent to the chat.

        USE THIS when asked to see today's jobs again ("show me the digest", "what
        were today's jobs?", "list them again"). Returns the verbatim digest text —
        same numbering and /apply | /skip lines — so the ordinals still match. The
        job numbers stay valid until the next discovery run. If no digest exists yet,
        returns ``digest: None`` with a message telling the user to run /discover.
        """
        row = self._store.get_last_digest()
        if row is None:
            return {
                "digest": None,
                "message": "No digest yet — run /discover to generate today's jobs.",
            }
        digest, generated_at = row
        return {"digest": digest, "generated_at": generated_at}

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
    # The fact guard's allowed vocabulary = the profile knowledge base + the curated skill list.
    skills_path = Path(config.profile.knowledge_base_dir) / "candidate_skills.yaml"
    fact_corpus = knowledge.full_context()
    if skills_path.exists():
        fact_corpus += "\n" + skills_path.read_text()
    tailor = ResumeTailor(
        tailoring,
        parse_master(master_root),
        max_projects=config.resume.max_projects,
        fact_corpus=fact_corpus,
        rephrase=config.resume.rephrase,
        disabled_sections=config.resume.disabled_sections,
    )

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
