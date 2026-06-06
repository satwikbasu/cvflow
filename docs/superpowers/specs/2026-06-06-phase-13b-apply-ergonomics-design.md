# Phase 13B — Apply Ergonomics (select jobs by number) — Design

Date: 2026-06-06
Status: approved (brainstorm), pre-implementation
Related: `[[approval-gate-wiring]]`; depends on 13A's digest. Part of Phase 13.

## Problem
Approving requires `/apply <job_id>` with a long, uncopyable id (e.g.
`indeed:in-fa8681c5568e6fb0`). The user wants to approve by the digest's ordinal — e.g.
"apply 1, 2 and 4" — for one or many jobs at once.

## Constraint (non-negotiable)
The approval gate must stay **deterministic and human-initiated, off the agent loop** (invariant 1).
So selection-by-number is delivered as an **ordinal-aware slash command handled by the existing
`/apply` /`/skip` hook**, NOT by letting the brain interpret natural language and approve (the brain
has no approve tool and must never gain one). A natural-language "apply for 1,2,4" sent to the brain
will be answered by guiding the user to send `/apply 1 2 4` (the brain still cannot approve).

## Components

### 1. Persisted digest → ordinal map (storage, additive)
- New table `digest_slots(slot INTEGER PRIMARY KEY, job_id TEXT NOT NULL, presented_at TEXT)`.
- `ApplicationStore.set_digest_slots(job_ids: list[str])` — clears the table and inserts the job_ids
  in presentation order (slot 1..N).
- `ApplicationStore.get_digest_slot(slot: int) -> str | None`.
- `ApplicationStore.digest_slots() -> list[str]` (ordered) — for `all`.
- The discovery digest (`cvflow.cron run_job("discover")`) calls `set_digest_slots([rj.posting.job_id
  for rj in ranked])` after ranking, so the numbers the user sees map to real job_ids. Additive — the
  gate/state-machine core is untouched.

### 2. Ordinal resolution (gate)
- New `cvflow.gate.resolve_targets(args: str, store) -> tuple[list[str], list[str]]` →
  (resolved job_ids in order, unrecognized tokens). Accepts, in one command, any mix of:
  - ordinals: `1 2 4` or `1,2,4`
  - ranges: `1-3`
  - `all` (→ every current digest slot)
  - raw job_ids (contain `:`) — back-compat, passed through
  Ordinals resolve via `get_digest_slot`; out-of-range / unknown tokens go to the unrecognized list.
  De-duplicates while preserving order.

### 3. Multi-target gate command (`handle_gate_command`)
- Resolve `args` via `resolve_targets`; apply `approve`/`skip` to **each** resolved job_id; build one
  aggregated reply, e.g.:
  `✅ Approved: 1 (indeed:…7), 2 (…d). ⏭️ already handled: 4 is applied. ⚠️ unknown: 9.`
  Each job still routes through the exact same `store.approve` / `store.set_status` calls — the gate
  logic per job is unchanged; only batching + ordinal lookup are added.
- Empty/`""` args → usage hint. Unauthorized user → ignored (unchanged).

### 4. Digest format (13A digest, extended)
Number each job `1.`, `2.`, … and end with: `Reply: /apply 1 2 4  •  /skip 3  •  /apply all`.

## Data flow
discover → rank → `set_digest_slots(order)` → digest with numbers → user sends `/apply 1 2 4` →
hook → `handle_gate_command` → `resolve_targets` → per-job `approve` → aggregated reply.

## Error handling
Out-of-range or stale ordinals (digest changed) → reported in the reply's "unknown" bucket, never a
crash. Mixed valid/invalid → valid ones still applied; invalid ones reported (never silent).

## Testing (no network)
- `resolve_targets`: ordinals, commas, ranges, `all`, raw ids, out-of-range, dedupe/order.
- `set_digest_slots`/`get_digest_slot`/`digest_slots`: round-trip + replace-on-new-digest.
- `handle_gate_command`: `/apply 1 2` approves both; `/apply 1-2`; `/apply all`; out-of-range token
  reported; raw job_id still works (back-compat); unauthorized ignored.
- `run_job("discover")` persists slots in ranked order.

## Exit criteria
From the digest, `/apply 1 2 4` (and `/skip`, ranges, `all`, raw ids) approve/skip the right jobs via
the deterministic hook, with one aggregated reply; the gate invariant is intact (no approve tool;
brain cannot approve).

## Accepted trade-offs
- Ordinals are relative to the **most recent** digest; a new digest reassigns numbers (documented in
  the reply hint). Stale numbers resolve to "unknown", never the wrong job (slots are replaced atomically).
- True natural-language approval ("apply for 1 and 2") is intentionally NOT wired to the brain —
  gate stays deterministic; the short slash command is the ergonomic path.

## Out of scope
Relevance/ranking (13A); preference learning (13C).
