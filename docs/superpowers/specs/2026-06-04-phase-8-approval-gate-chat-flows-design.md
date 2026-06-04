# Phase 8 — Approval-gate skill + chat flows — Design

**Date:** 2026-06-04
**Phase:** 8 (build plan) · **Status:** design approved, pre-implementation
**Invariants honored:** gate is deterministic code (CLAUDE.md inv. 1); never fabricate (inv. 2 + essay-auto-answer policy); never fail silently (inv. 3); zero per-call cost (inv. 4).

## Problem

Hermes (the agent substrate) must let a **human** approve an application from Telegram, and that approval must reach `statemachine.approve()` through **deterministic code that is outside the agent's probabilistic loop**. The agent may *prepare* a review (tailor the resume, send the PDF) and *attempt* submission, but it must never be able to satisfy the gate itself. Phase 8 also delivers the surrounding chat flows (review message, status reports, essay/free-text auto-answer) and wires real Gemini tailoring so the review carries a real PDF + diff.

## Mechanism investigation (Hermes source, verified)

Four candidate human-callback surfaces were traced in `~/.hermes/hermes-agent`:

| Surface | Where | Routes to | Verdict |
|---|---|---|---|
| Slash command → `command:<name>` hook | `gateway/run.py:8003`, `gateway/hooks.py` | deterministic Python in the gateway process; hook ctx has `user_id`+`args`; `decision:"handled"` returns a reply and **short-circuits before the brain** (`run.py:8035`) | **gate path** |
| `clarify` primitive | `tools/clarify_gateway.py` | resolves back **into the agent loop** | OTP/clarification only, **not** the gate |
| Inline buttons (custom `callback_data`) | `telegram.py:_handle_callback_query` | only built-in prefixes handled | rejected — needs a Hermes fork |
| MCP `request_review` tool | cvflow MCP server | returns to brain | used to *send* the review, not as the gate |

Verified facts: plugin-registered slash commands become "known commands" (`commands.py:326 is_gateway_known_command`) and receive the full `command:<name>` hook lifecycle; a hook returning `{"decision":"handled","message":...}` replies and bypasses both plugin-dispatch and the brain; the hook ctx carries `user_id` (re-check authorized user) and `args` (the job_id); the single authorized user is already enforced at the transport (`telegram.allowed_chats`). Plugin command handlers are invoked as `handler(args)` only (no `user_id`), which is why the **hook** (not a plain plugin handler) carries the gate logic — the plugin registration exists only to make the command "known".

## Architecture

```
Telegram (authorized user)
  │  /approve <job_id>           /skip <job_id>
  ▼
Hermes gateway  ── command:approve / command:skip hook ──►  artifacts/hermes/hooks/cvflow-gate/handler.py
  │  (decision:"handled", returns reply, brain never sees it)        │ imports
  │                                                                   ▼
  │                                                        cvflow.gate.handle_gate_command(...)
  │                                                          • re-checks authorized user_id
  │                                                          • approve → store.approve()  ◄── SOLE caller
  │                                                          • skip    → store.set_status(SKIPPED)
  │                                                          • never silent: every outcome → message
  ▼
Brain (probabilistic loop) ── MCP cvflow skills ──►  request_review / compose_essay / status_report / discover / list / get / submit
   request_review  → tailor (Gemini) → compile tailored PDF → diff + analysis → payload → brain sends PDF via send_document
   submit          → guard_can_submit (raises unless approved)   ◄── agent can call, can never satisfy
```

The brain has **no** approve tool (`TOOL_NAMES` excludes it, by construction since Phase 7). The only `approve()` caller in the running system is the gate hook, fired by a real inbound human slash command. The brain emits outbound text/tool-calls only; it cannot inject an inbound Telegram update, so it cannot trigger the hook.

## Components

### 1. `src/cvflow/gate/` — deterministic gate command handler (pure, the safety core)
- `GateResult(handled: bool, message: str | None)`.
- `handle_gate_command(*, command: str, args: str, user_id, authorized_user_id, store) -> GateResult`:
  - Unauthorized `user_id` → `GateResult(handled=False, message=None)` (silently ignored; transport already blocks strangers, this is defense-in-depth — an unknown stranger is not a user-facing "skip").
  - `approve <job_id>` → `store.approve(job_id)`; success → `"✅ Approved {job_id} — submitting."`; `IllegalTransition` (wrong state) → `"⚠️ Can't approve {job_id}: it is {status}, not pending review."`; `UnknownJob` → `"⚠️ No application {job_id}."` — **never silent**.
  - `skip <job_id>` → `store.set_status(job_id, SKIPPED)`; analogous messages.
  - This module is the **sole** caller of `store.approve()` in production code; a test asserts no approve path exists on the MCP surface (already true) and that this handler routes only via real human commands.

### 2. Hermes adapters (committed under `artifacts/hermes/`, deploy = clone)
- `artifacts/hermes/plugins/cvflow-gate/plugin.yaml` + `handler.py` — registers `/approve` and `/skip` (with `args_hint "<job_id>"`) so they are known commands with a Telegram menu entry. Handlers are trivial fallbacks (the hook intercepts first).
- `artifacts/hermes/hooks/cvflow-gate/HOOK.yaml` (events `command:approve`, `command:skip`) + `handler.py` — builds the store from cvflow config, calls `cvflow.gate.handle_gate_command`, returns `{"decision":"handled","message":...}`.
- Both are thin import shims over the pure module; documented install (symlink/copy into `~/.hermes/{plugins,hooks}/`) in `docs/deploy.md` notes. Pytest covers the pure module; a smoke test asserts the shims import and dispatch.

### 3. `request_review` MCP skill (enhanced) + `build_tools` wiring
- Signature `request_review(job_id, *, feedback: str | None = None)`.
- Transition `discovered → pending_review` (idempotent if already pending_review).
- Load persisted `JDAnalysis`; `ResumeTailor.plan(jd[, feedback])` → `render` → `assert_no_new_facts` → **`compile_tailored(plan, outdir)`** (new resume method) → persist `tailored_pdf_path`.
- Returns `{job_id, status, pdf_path, diff, analysis_summary, instructions: "Reply /approve <id> to approve & apply, or /skip <id> to skip."}`. The brain relays this and pushes the PDF via Hermes' `send_document`.
- **`/edit` UX:** editing is not gate-critical and the PDF must be re-sent (only the brain has `send_document`, and plugin/hook replies are text-only + 30 s-bounded). So edits re-enter through `request_review(job_id, feedback=...)`: the user asks for changes in chat (or a thin `/edit <id> <feedback>` command that rewrites to a brain instruction), the brain re-tailors and re-sends. `feedback` is threaded into the tailoring prompt. *(Deviation from a literal deterministic `/edit` hook — flagged for review; rationale: PDF delivery and Gemini+Tectonic runtime cannot live safely inside the 30 s text-only hook.)*
- `build_tools` wires the previously-`None` collaborators: `tailor = ResumeTailor(GeminiProvider(...from config.llm.tailoring...), parse_master(config.resume.master_tex_path))`; `analyzer` and `discovery` from their Phase 4/5 constructors.

### 4. `src/cvflow/resume` — `compile_tailored(plan, outdir) -> Path`
- Build a full tailored `master.tex` from the real preamble with `\input` lines reordered per `plan.section_order` and the `projects` slot replaced by only the selected project inputs; write it into the resume root (so relative `\input` paths resolve) and Tectonic-compile to `outdir`. `assert_no_new_facts` runs first. Reuses existing `CompileError`.

### 5. `src/cvflow/essays/` — personality-fingerprint composer (pure)
- `EssayAnswer(text: str | None, grounded: bool, citations: list[str], needs_clarification: bool, clarification: str | None)`.
- `compose_answer(question, knowledge, *, provider) -> EssayAnswer`: strict prompt — answer **only** from the supplied KB context, name the profile docs used, and if a required fact is genuinely absent return a structured "NEEDS_CLARIFICATION: <what>" sentinel. Deterministic post-check validates citations reference real KB doc keys; an ungroundable/empty answer → `needs_clarification=True` (never a guess), satisfying the essay-auto-answer policy.
- Exposed as MCP skill `compose_essay(question)`; when `needs_clarification`, the brain asks the user via the native `clarify` loop.

### 6. Chat-flow read skills
- `status_report()` MCP skill — counts/lists applications by status for a status digest. Daily digest reuses `discover`; OTP + clarification reuse Hermes' native `clarify` (cvflow OTP wiring is Phase 10).

## Testing (all mocked transport / mocked LLM, no live keys)

1. **Gate is the only route to `approved`:** `handle_gate_command(approve)` from `pending_review` → `approved`; from any other status → `IllegalTransition` message, status unchanged; MCP surface still has no approve tool.
2. **Unauthorized ignored:** mismatched `user_id` → `handled=False`, no state change, no message.
3. **Review carries PDF/diff/analysis:** `request_review` returns a payload with a real `pdf_path` (compiled), `diff`, and `analysis_summary` (Tectonic-dependent compile test skips when `tectonic` absent, mirroring Phase 6).
4. **Essay grounded + cited:** `compose_answer` for an answerable question returns text whose citations are real KB doc keys.
5. **Ungroundable → clarification:** a question needing an absent fact returns `needs_clarification=True`, `text=None` — no guess.
6. **No-new-facts on tailored compile:** `assert_no_new_facts` holds for the tailored document.
7. **Adapter smoke:** the plugin/hook shims import and dispatch to the pure handler.

## Out of scope (later phases)
Browser form-filling + actually using essay answers in forms (Phase 9); cvflow OTP wait/timeout (Phase 10); scheduler-driven daily digest trigger (Phase 11).
