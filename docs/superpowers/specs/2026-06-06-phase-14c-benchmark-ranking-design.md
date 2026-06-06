# Phase 14C — Hybrid Benchmark, Ranking & Two-Section Digest

> **Status:** design (2026-06-06). Stage-2 of the discovery engine. Consumes the cruxes from **14B**
> for the M and N cohorts from **14A**, produces a benchmark score per job, ranks each cohort, and
> renders the two-section digest. Engine: **NIM llama-3.3-70b** for the single fuzzy `fit` sub-score;
> everything objective (comp, gates) is deterministic Python.
>
> **India-focused (locked 2026-06-06):** overseas relocation is deliberately NOT modelled — no currency
> conversion, no location-vs-pay trade-off. This removed the most fragile part of the original design.
> See §8 "Deferred: foreign-reach" for how to re-enable later.

## 1. Principle

Hybrid by design (matches this project's deterministic-gate ethos): the LLM does ONLY the fuzzy part
(a `fit` score from the crux); code does the objective part (comp) and the hard gates (14A §5). The
blend is a formula WE control and can tune — transparent and reproducible.

## 2. The single LLM sub-score: `Fit` (0-100)

One NIM call PER COHORT (M and N), each carrying the whole cohort's cruxes → cross-job calibration.

**Input to llama:**
- A compact **candidate fingerprint** (cached) — NOT the full profile, for speed + consistency:
  structured `preferences` (yoe, role leanings, product-company pref, deal-breakers) + a short
  distilled skills/stack summary. (Fingerprint is built once and cached; rebuilt only when
  `profile/` or `preferences` change. A Gemini one-shot distill of the profile, or hand-assembled
  from config + `preferences.md` — implementer's choice; it must be byte-stable across runs.)
- The cohort's cruxes, **sorted by job_id** (stable order), trimmed to fit-relevant fields:
  `job_id, role_family, seniority_signal, tech_stack, work_mode, country, company_type, one_line`.
  (Comp is NOT sent — code owns it.)

**Output (response_format json_object; schema described in prompt):**
```json
[{"job_id": "...", "fit_score": 0-100, "fit_reason": "<=120 chars",
  "concern_codes": ["STACK_MISMATCH","SERVICE_COMPANY", ...]}]
```
`concern_codes` is a fixed enum (no prose): `STACK_MISMATCH, SERVICE_COMPANY, SENIORITY_BORDERLINE,
ROLE_ADJACENT`. Code adds its own deterministic concerns later (`PAY_UNKNOWN, YOE_UNKNOWN`).

### Fit rubric (in the fixed prompt — numeric anchors for reproducibility)
```
90-100 : direct match — role_family in {devops,sre,platform,infra,backend} AND stack overlaps
         the candidate's core tools AND seniority is fresher/junior/mid.
70-89  : adjacent — right family but partial stack overlap, or generalist SWE with infra stack.
40-69  : weak — frontend/data/other family, or little stack overlap.
0-39   : poor — unrelated role or stack.
Modifiers: -10 company_type service/staffing (candidate prefers product);
           +5 modern infra stack (docker/k8s/ci-cd/cloud) present;
           -10 app/maintenance-flavoured but not a hard exclude.
Output the integer after modifiers, clamped 0-100. Cite the driver in fit_reason.
```

### NIM call settings (probed live — all accepted)
```
temperature=0, seed=BENCH_SEED, top_p=0.1, max_tokens sized to cohort,
response_format={"type":"json_object"}     # guided_json is IGNORED by this model — do NOT use it
```

## 3. Deterministic sub-score (Python) — `Comp` only

**Comp `C` ∈ [0,1]** (M cohort only) from the stated **INR** annual salary:
```
ctc_lpa = annual_max (INR) / 100_000          # M is INR-only by construction (14A §6) — no FX
C = clamp((ctc_lpa - MIN_CTC_LPA) / (TOP_CTC_LPA - MIN_CTC_LPA), 0, 1)   # MIN=7, TOP=40
```
No currency conversion exists anywhere in the engine — foreign-currency salaries are routed to N by the
partition (14A §6), so `Comp` only ever sees INR. The digest shows the real stated salary as-is.

There is **no location score** — the engine is India-focused (see header + §8). `crux.work_mode` /
`crux.country` are carried for display only and do not affect the benchmark.

## 4. Benchmark formulas

```
M (stated INR salary):  Benchmark = round(100 * (0.70*Fit/100 + 0.30*C))
N (no INR salary):      Benchmark = Fit            # i.e. round(100 * Fit/100)
```
Weights are module constants (tunable; candidates for config later). Worked examples:

| Job | Fit | C | M-score | N-score |
|---|---|---|---|---|
| Great fit, floor pay (7 LPA) | 85 | 0.00 | 60 | — |
| Great fit, mid pay (~23 LPA) | 85 | 0.48 | 74 | — |
| Great fit, top pay (≥40 LPA) | 85 | 1.00 | 90 | — |
| Good fit, no INR salary | 80 | — | — | 80 |
| Weak fit, no INR salary | 45 | — | — | 45 |

→ In M, higher pay lifts a job (comp is a 30% bonus on top of fit), but a strong-fit floor-pay job still
beats a weak-fit high-pay one — fit stays dominant. → N ranks purely on fit (honest; no pay to weigh).

**Cohorts are scored on different scales/meanings → NEVER merged or cross-compared.** Each is sorted by
its own Benchmark, descending.

## 5. Two-section digest

```
🗞️ cvflow — new jobs today

💰 With stated pay (ranked by value)
1. <title> @ <company>  (bench 74 · fit 85 · 23 LPA · Bengaluru)
   <url>
   <fit_reason>
   /apply linkedin:li-… | /skip …

📋 Pay not stated (ranked by fit)
3. <title> @ <company>  (bench 80 · fit 80 · Remote)
   <url>
   <fit_reason>
   ⚠️ PAY_UNKNOWN; YOE_UNKNOWN
   /apply … | /skip …

Reply: /apply 1 2 4  •  /skip 3  •  /apply all
```
- **Continuous numbering across both sections** (M first, then N) → `digest_slots` (Phase 13B) records
  ordinal→job_id over the combined list, so `/apply N` and `resolve_targets` work unchanged.
- Per-cohort top-N: present up to `top_n_per_cohort` (config §9, default 5) from EACH section → ≤10
  total. Location text after the score is **display only** (not scored).
- Empty section is omitted with a one-liner ("No stated-pay jobs today.").

## 6. Module shape

`src/cvflow/discovery/benchmark.py`:
- constants: `W_FIT_M=0.70`, `W_COMP_M=0.30`, `MIN_CTC_LPA=7`, `TOP_CTC_LPA=40`.
- `comp_score(job) -> float` (INR only; clamp).
- `fit_scores(cruxes, fingerprint, provider) -> dict[job_id, FitResult]` (one NIM call; isolates failure).
- `benchmark_cohort(jobs, cruxes, fits, cohort: "M"|"N") -> list[BenchmarkedJob]` (sorted).
- `BenchmarkedJob(posting, crux, benchmark, fit_score, fit_reason, concerns, cohort, ctc_lpa?)`.

`DiscoveryService.discover()` orchestrates: scrape → gates → partition → cap → distill (14B) →
post-distill gates → benchmark both cohorts → return `{"M":[...], "N":[...]}` (or a flat ordered list
tagged by cohort). `format_digest` renders the two sections + sets `digest_slots`.

`NimProvider` (Phase 9) gains optional generation settings on `generate`:
`generate(prompt, *, temperature=0, seed=None, top_p=None, max_tokens=None, json_object=False)` —
additive; existing callers unaffected.

## 7. Robustness (invariant 3)

- A cohort's fit call failing → that cohort falls back to **deterministic-only** ranking (M by `Comp`
  then recency; N by recency) so the digest still shows jobs, flagged `RANKING_DEGRADED`. The other
  cohort is unaffected.
- All deterministic drops/caps logged; PAY/YOE unknowns surfaced as concerns.

## 9. Test plan (TDD, mocked)

- `comp_score`: INR floor (7 LPA)→0, top (40 LPA)→1, mid (~23 LPA)→~0.48, below floor clamps to 0.
- `benchmark_cohort` M vs N: the worked-example rows produce the tabulated scores; sorted desc.
- `fit_scores`: parses the json_object array, drops unknown job_ids, fills concern_codes; one bad call
  → degraded fallback (no crash).
- digest: two sections, continuous numbering, `digest_slots` set over the combined order; empty section
  omitted; `/apply 2` resolves to the 2nd combined row.
- NIM call carries `temperature=0, seed, response_format=json_object` (assert on a fake post_fn).

## 8. Deferred: foreign-reach (NOT built now)

Overseas relocation targeting was intentionally cut for simplicity (2026-06-06). To re-enable later:
1. Add target countries to Indeed search (multi-country) or broaden `Remote` searches.
2. Re-introduce a `location_score L ∈ [0,1]` (India/remote = 1.0, foreign-onsite < 1.0) and a static
   FX table to convert foreign salaries to INR-LPA.
3. Switch the M formula back to a 3-term blend `0.50*Fit + 0.35*Comp + 0.15*L` and let foreign-currency
   jobs into M (FX-converted, flagged `FX_ESTIMATED`); give N a `+0.25*L` term.
The crux already records `country`/`work_mode`, so no distillation change is needed — only the
benchmark + partition + config. Until then: India-focused, no FX, no location scoring.

## 10. Config additions
```yaml
discovery:
  top_n_per_cohort: 5         # surface up to N from each of M and N
preferences:
  top_ctc_lpa: 40            # comp-score upper anchor (C=1.0)
```
Benchmark weights start as module constants (YAGNI; promote to config only if tuning demands).
