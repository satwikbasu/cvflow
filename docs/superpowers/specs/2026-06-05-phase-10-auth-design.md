# Phase 10 — Auth (Google SSO vault + email-OTP fallback) — Design

Date: 2026-06-05
Status: approved (brainstorm), pre-implementation
Related: build plan Phase 10 (Goals 6, 7); `[[approval-gate-wiring]]`, `[[hermes-runtime-ops]]`;
builds on Phase 9 automation (pause/resume, persistent browser context).

## Goal
Deliver login support for the browser automation: (1) secure secrets at rest (Fernet), and (2) an
email-OTP fallback that messages the user, waits up to a timeout, and marks `otp_timeout` + notifies
on expiry — never silently. **Decision (locked): encrypted vault + browser SSO** — sites are logged
into via the Phase-9 persistent browser context ("Sign in with Google" done once, headed, by the
operator). No Google API / Gmail SDK, no auto-reading of inboxes; OTPs are relayed by the user.

## Components (`src/cvflow/auth/__init__.py`)

### 1. `TokenVault` (Fernet — secrets at rest)
- `TokenVault.create_or_load(key_path)` — load the Fernet key from a `chmod 600` file; if absent,
  create the parent dir, generate a key, write it, `chmod 600`. Returns a `TokenVault`.
- `encrypt(str) -> bytes`, `decrypt(bytes) -> str` (raises `VaultError` on an invalid token).
- `save_blob(path, str)` / `load_blob(path) -> str` — encrypted file persistence for browser
  `storage_state`/cookies and any SSO refresh tokens at rest; each file written `chmod 600`.
- `VaultError` on corrupt key / undecryptable token.

### 2. `OtpCoordinator` (non-blocking deadline + sweep)
Constructed with `(store, notify, timeout_minutes, now=_now_utc)`.
- `request(job_id, destination, now=None) -> dict` — set `otp_deadline = now + timeout_minutes` on
  the app, notify the user ("OTP sent to `<destination>` — reply within N min"), return a
  `{"needs_otp": True, "job_id", "destination", "deadline"}` marker. **Pauses**; status stays
  `approved`.
- `provide(job_id, otp, now=None) -> str | None` — within deadline: clear the deadline and return
  the OTP (caller resumes the fill); past deadline (or no pending deadline): mark `otp_timeout`,
  clear the deadline, notify, return `None`.
- `expire_overdue(now=None) -> list[str]` — pure sweep: every `approved` app whose `otp_deadline`
  has passed → `otp_timeout` + notify; clears the deadline; returns the expired job_ids. Wired to
  the Phase-11 scheduler/heartbeat. Deterministic via the injected clock.

## Storage (additive — gate core untouched)
- New nullable column `otp_deadline TEXT` on `applications` (ISO-8601 string or NULL).
- `set_otp_deadline(job_id, deadline: str | None)`; `list_awaiting_otp()` → apps with a non-NULL
  deadline. Same additive pattern as the Phase-9 proof columns; the state machine / `approve()` path
  is not touched. `Application` gains `otp_deadline: str | None = None`.

## Thin live seam (no fragile site-specific OTP detection — deferred)
- New gated MCP tool **`submit_otp(job_id, otp)`**: `code = coordinator.provide(...)`; on-time →
  `automator.resume(job_id, code)` (which asserts `guard_can_submit`, so the gate still holds);
  expired → `{"otp_timeout": True, "job_id"}`. A **dedicated tool** (not an overload of
  `resume_application`) so there is never ambiguity about whether an answer is an OTP code.
- `OtpCoordinator.expire_overdue` is exposed as a coordinator method for the Phase-11 scheduler; no
  MCP tool is added for it now (YAGNI — the scheduler calls it directly).
- The trigger that *calls* `request` (detecting an OTP page on a live site) is the deferred
  site-specific glue; `request` itself is unit-tested now. **Still no `approve` tool** on the MCP
  surface.

## Wiring
`build_tools` constructs `TokenVault.create_or_load(config.security.fernet_key_path)` and
`OtpCoordinator(store, _telegram_notify, config.auth.otp_timeout_minutes)`, and passes the
coordinator into `CvflowTools` (so `submit_otp` works). The vault is available for the automation
session-state persistence seam.

## Error handling (invariant 3 — never silent)
Every OTP outcome notifies the user: request (asked), expiry (timed out), and `provide`-late
(timed out). `VaultError` raises loudly rather than returning a bad/empty secret.

## Testing (no network, no live Google)
- **TokenVault:** key generated + file mode `0o600`; encrypt→decrypt round-trip; a second
  `create_or_load` on the same path reuses the key (decrypts a token from the first instance);
  `decrypt` of garbage → `VaultError`; `save_blob`/`load_blob` round-trip with file mode `0o600`.
- **OtpCoordinator** (injected clock, real `:memory:` store, fake notify): `request` sets the
  deadline + notifies; `provide` on-time returns the OTP and clears the deadline; `provide` late →
  `otp_timeout` + notify + `None`; `expire_overdue` marks only overdue `approved` apps, returns
  their ids, leaves non-overdue untouched.
- **`submit_otp`** (fake coordinator + fake automator): on-time → routes to `automator.resume`;
  late → `{"otp_timeout": True}` and does NOT resume.

## Exit criteria (build plan)
Token encryption round-trip ✓; OTP-wait timeout → `otp_timeout` + notification ✓; OTP success →
resume ✓.

## Accepted trade-offs (drawbacks, made explicit)
1. **Lazy timeout until Phase 11.** Expiry is detected on `provide` or on the `expire_overdue`
   sweep; with no scheduler yet, a stale wait sits at `approved` until the user replies or a manual
   sweep runs. Phase 11 MUST wire the sweep onto the heartbeat.
2. **Extra tool surface.** `submit_otp` is one more tool the brain must choose correctly vs
   `resume_application`; mitigated by a clear description and the fact both gate through `resume`.
3. **Encryption-at-rest ≠ host-compromise protection.** The Fernet key sits on the same disk as
   the blobs; this defends against repo/backup/disk leakage, not a rooted host. Operational
   mitigations (unprivileged user, `chmod 600`, burner account) remain the real defense; no
   KMS/HSM (cost + scope).
4. **OTP-field detection deferred.** Phase 10 proves the mechanism, not a live OTP login; the
   site-specific trigger lands later (likely surfaces in the Phase-12 dry run).
5. **Browser-SSO has no self-recovery.** Expired Google cookies / forced re-auth can't be
   refreshed programmatically — the agent fails + notifies and waits for a manual headed re-login.
6. **`otp_deadline` lives on the application row** (transient auth state on the domain record) —
   additive and consistent with `confirmation_ref`/proof columns, but a slight mixing of concerns.

## Out of scope (later phases)
Scheduler/heartbeat wiring of `expire_overdue` + systemd (Phase 11); live OTP-page detection and the
end-to-end OTP login dry run (Phase 12); any Google API / Gmail auto-read (explicitly rejected).
