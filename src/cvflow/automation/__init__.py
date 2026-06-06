"""Deterministic browser automation skill (Phase 9, Goals 5 & 8).

Field values are resolved deterministic-first (form_fields lookup -> grounded
essay composition -> clarify/skip); an LLM never guesses *what fact* fills a box
(invariant 2). guard_can_submit gates every entrypoint (Goal 4). Failures are
never silent: a crash marks the app failed and notifies the user with the job URL
(invariant 3).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol

from cvflow.essays import compose_answer
from cvflow.statemachine import Status, guard_can_submit
from cvflow.storage import UnknownJob

__all__ = [
    "FieldSpec",
    "FieldFill",
    "FieldResolution",
    "NeedsClarification",
    "resolve_field",
    "SessionManager",
    "FormFiller",
    "Proof",
    "Automator",
]


class _Provider(Protocol):
    def generate(self, prompt: str) -> str: ...


@dataclass(frozen=True)
class FieldSpec:
    label: str
    name: str
    field_type: str  # text | textarea | select | checkbox | file
    options: list[str]
    required: bool


@dataclass(frozen=True)
class FieldFill:
    label: str
    value: str
    source: str  # form_fields | composed | clarified | skipped


@dataclass(frozen=True)
class FieldResolution:
    fill: FieldFill | None
    clarify: bool
    question: str | None


@dataclass(frozen=True)
class NeedsClarification:
    job_id: str
    question: str


@dataclass(frozen=True)
class Proof:
    url: str
    page_title: str
    screenshot_path: str
    confirmation_ref: str | None


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.strip().lower()).strip("_")


def resolve_field(
    spec: FieldSpec, form_fields: Any, knowledge: Any, *, provider: _Provider
) -> FieldResolution:
    """Resolve one field deterministic-first; never guess a fact."""
    # 1) literal lookup against form_fields (exact name, then normalized label)
    for key in (spec.name, _normalize(spec.name), _normalize(spec.label)):
        if form_fields.is_filled(key):
            return FieldResolution(
                FieldFill(spec.label, form_fields.require(key), "form_fields"),
                False, None,
            )
    # 2) free-text / essay -> grounded composition
    if spec.field_type == "textarea":
        ans = compose_answer(spec.label, knowledge, provider=provider)
        if ans.grounded and ans.text:
            return FieldResolution(
                FieldFill(spec.label, ans.text, "composed"), False, None
            )
    # 3) escalate (required) or skip (optional) -- never guess
    if spec.required:
        return FieldResolution(None, True, spec.label)
    return FieldResolution(FieldFill(spec.label, "", "skipped"), False, None)


class SessionManager:
    """Holds live persistent browser contexts keyed by job_id (Option-C hybrid).

    The on-disk user_data_dir persists login/cookies across restarts; the live
    context/page is held in-process so pause->resume continues on the same page.
    """

    def __init__(self, *, user_data_root: str, headless: bool = False,
                 use_stealth: bool = True) -> None:
        from pathlib import Path

        self._root = Path(user_data_root)
        self._root.mkdir(parents=True, exist_ok=True)
        self._headless = headless
        self._args = (
            ["--disable-blink-features=AutomationControlled"] if use_stealth else []
        )
        # Playwright is started LAZILY on first open(): sync_playwright().start()
        # sets up an asyncio loop, which must NOT happen at MCP-server import/startup
        # (FastMCP runs its own stdio asyncio loop). Constructing this manager is cheap.
        self._pw: Any = None
        self._contexts: dict[str, Any] = {}
        self._pages: dict[str, Any] = {}

    def _ensure_started(self) -> Any:
        if self._pw is None:
            from playwright.sync_api import sync_playwright

            self._pw = sync_playwright().start()
        return self._pw

    def open(self, job_id: str, url: str) -> Any:
        if job_id not in self._contexts:
            ctx = self._ensure_started().chromium.launch_persistent_context(
                user_data_dir=str(self._root / job_id),
                headless=self._headless,
                args=self._args,
            )
            self._contexts[job_id] = ctx
            self._pages[job_id] = ctx.pages[0] if ctx.pages else ctx.new_page()
            self._pages[job_id].goto(url)
        return self._pages[job_id]

    def page(self, job_id: str) -> Any | None:
        return self._pages.get(job_id)

    def close(self, job_id: str) -> None:
        ctx = self._contexts.pop(job_id, None)
        self._pages.pop(job_id, None)
        if ctx is not None:
            ctx.close()

    def close_all(self) -> None:
        for job_id in list(self._contexts):
            self.close(job_id)
        if self._pw is not None:
            self._pw.stop()
            self._pw = None


class FormFiller:
    """Reads & fills fields on a single Playwright Page; captures proof."""

    def __init__(self, page: Any) -> None:
        self._page = page

    def discover_fields(self) -> list[FieldSpec]:
        specs: list[FieldSpec] = []
        for el in self._page.query_selector_all("input, textarea, select"):
            name = el.get_attribute("name") or el.get_attribute("id") or ""
            if not name:
                continue
            tag = el.evaluate("e => e.tagName.toLowerCase()")
            if tag == "textarea":
                ftype = "textarea"
            elif tag == "select":
                ftype = "select"
            else:
                ftype = el.get_attribute("type") or "text"
            options = (
                [o.get_attribute("value") or "" for o in el.query_selector_all("option")]
                if ftype == "select" else []
            )
            label = self._label_for(name) or name
            required = el.get_attribute("required") is not None
            specs.append(FieldSpec(label, name, ftype, options, required))
        return specs

    def _label_for(self, name: str) -> str:
        el = self._page.query_selector(f"label[for='{name}']")
        return el.inner_text().strip() if el else ""

    def apply(self, name: str, field_type: str, value: str) -> None:
        target = (
            f"[name='{name}']"
            if self._page.query_selector(f"[name='{name}']")
            else f"#{name}"
        )
        if field_type == "select":
            self._page.select_option(target, value)
        elif field_type == "checkbox":
            if value.lower() in ("true", "yes", "1"):
                self._page.check(target)
        elif field_type == "file":
            self._page.set_input_files(target, value)
        else:
            self._page.fill(target, value)

    def submit(self) -> None:
        self._page.click("#submit, button[type='submit'], input[type='submit']")
        self._page.wait_for_load_state("networkidle")

    def capture_proof(self, screenshot_path: str) -> Proof:
        self._page.screenshot(path=screenshot_path)
        body = self._page.inner_text("body")
        m = re.search(r"#\s*([A-Za-z0-9-]+)", body)
        return Proof(
            url=self._page.url,
            page_title=self._page.title(),
            screenshot_path=screenshot_path,
            confirmation_ref=m.group(0) if m else None,
        )


class Automator:
    """Gated orchestration of fill -> (pause/resume) -> disclose -> submit -> proof."""

    def __init__(
        self, *, store: Any, knowledge: Any, provider: _Provider, sessions: Any,
        filler_factory: Any, notify: Any, screenshot_dir: str,
    ) -> None:
        self._store = store
        self._kb = knowledge
        self._provider = provider
        self._sessions = sessions
        self._make_filler = filler_factory
        self._notify = notify
        self._shot_dir = screenshot_dir
        self._pending: dict[str, FieldSpec] = {}   # job_id -> awaiting-clarification spec
        self._composed: dict[str, list[FieldFill]] = {}

    def _app(self, job_id: str) -> Any:
        app = self._store.get(job_id)
        if app is None:
            raise UnknownJob(job_id)
        return app

    def fill(self, job_id: str) -> dict[str, Any]:
        app = self._app(job_id)
        guard_can_submit(app.status)  # raises SubmissionBlocked unless APPROVED
        page = self._sessions.open(job_id, app.jd_url)
        self._composed[job_id] = []
        filler = self._make_filler(page)
        return self._run(job_id, app, filler, filler.discover_fields())

    def resume(self, job_id: str, answer: str) -> dict[str, Any]:
        app = self._app(job_id)
        guard_can_submit(app.status)
        spec = self._pending.pop(job_id, None)
        page = self._sessions.page(job_id)
        filler = self._make_filler(page)
        if spec is not None:
            filler.apply(spec.name, spec.field_type, answer)
        return self._run(job_id, app, filler, filler.discover_fields(),
                         already=spec.name if spec else None)

    def _run(
        self, job_id: str, app: Any, filler: Any, specs: list[FieldSpec],
        already: str | None = None,
    ) -> dict[str, Any]:
        ff = self._kb.form_fields
        try:
            for spec in specs:
                if already is not None and spec.name == already:
                    continue
                r = resolve_field(spec, ff, self._kb, provider=self._provider)
                if r.clarify:
                    self._pending[job_id] = spec
                    self._notify(f"[{job_id}] Need an answer: {r.question}")
                    return {"needs_clarification": True, "job_id": job_id,
                            "question": r.question}
                assert r.fill is not None  # non-clarify always carries a fill
                if r.fill.source == "skipped":
                    continue
                filler.apply(spec.name, spec.field_type, r.fill.value)
                if r.fill.source == "composed":
                    self._composed[job_id].append(r.fill)
            self._disclose_composed(job_id)
            filler.submit()
            proof = filler.capture_proof(f"{self._shot_dir}/{job_id}.png")
            self._store.set_proof(job_id, url=proof.url,
                                  screenshot_path=proof.screenshot_path,
                                  page_title=proof.page_title)
            if proof.confirmation_ref:
                self._store.set_confirmation(job_id, proof.confirmation_ref)
            self._store.set_status(job_id, Status.APPLIED)
            self._sessions.close(job_id)
            return {"status": "applied", "job_id": job_id, "proof_url": proof.url}
        except Exception as exc:  # never silent (invariant 3)
            self._store.set_status(job_id, Status.FAILED)
            self._sessions.close(job_id)
            self._notify(
                f"❌ Application failed for {app.company} — {app.role} "
                f"({app.jd_url}): {exc}"
            )
            return {"failed": True, "job_id": job_id, "error": str(exc)}

    def _disclose_composed(self, job_id: str) -> None:
        for fill in self._composed.get(job_id, []):
            self._notify(
                f"[{job_id}] Composed answer for '{fill.label}': {fill.value}"
            )
