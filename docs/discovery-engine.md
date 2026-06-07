# Discovery engine — operational reference

How the daily discovery pipeline actually runs **as of 2026-06-07**, after the Phase-14
live-run hardening. This reflects current code, not the original 14A–D specs (which assumed
Gemini for distillation and dropped Naukri). For the design rationale see
`docs/superpowers/specs/2026-06-06-phase-14{a,b,c,d}-*.md`; for history see the build-plan
progress log.

## LLM provider map (who does what)

| Task | Provider / model | Why |
|---|---|---|
| **Distillation** (JD → structured `Crux`) | **Mistral `mistral-small-2506`** | strict `json_schema` output, 2.25M TPM, 5 RPS — fast, high-volume, cheap |
| **Fit ranking** (score each cohort) | **Mistral `mistral-small-2506`** | needs a per-job JSON *array*; NIM can't emit it reliably (see below) |
| **JD analysis** (`analyze_jd`, apply flow) | **NIM `meta/llama-3.3-70b-instruct`** | free-form text; large context; no daily cap |
| **Résumé tailoring** (after `/apply`) | **Cerebras `gpt-oss-120b`** | high quality, 65k context, low volume |

**Gemini was dropped** — Google cut the 2.5 Flash free tier to 5 RPM / 20 RPD (shared
across all calls), unusable for ~hundreds of distills/day.

**Why NIM can't do structured ranking:** its `json_schema` mode times out and its
`json_object` mode returns a *single* object, not the array of per-job results. Both were
proven live. Mistral enforces strict `json_schema`, so distillation + fit run there.

All three are OpenAI-compatible (`POST /chat/completions`) and share one `NimProvider`
client. `llm.brain/distillation/tailoring` are one `ProviderConfig` shape in `config.yaml`.
Provider quirks handled in `NimProvider`: Mistral uses `random_seed` (not `seed`) and
rejects `top_p` under `temperature=0`; Cerebras (behind Cloudflare) needs a browser
`User-Agent` or it returns 403/1010.

## Pipeline (cron → digest)

```
python -m cvflow.cron discover
  → SCRAPE (JobSpy: linkedin/indeed/google + our own Naukri adapter; India-pinned,
            LinkedIn descriptions on, hours_old window)           [no LLM; ~16 min]
  → normalize_rows → DETERMINISTIC PREFILTER (no LLM):
        drop excluded title keywords · internships (job_type~"intern")
        · below-floor structured salary · Naukri experience_range > yoe_ceiling
     → cross-day dedup (skip job_ids already in DB unless reconsider_discovered)
  → CAP: take first `max_distill_per_cohort` (400) candidates in GATHER ORDER
        (gather order front-loads earlier/priority search terms; date_posted strings
         are inconsistent across sources, so they are NOT sorted)
  → DISTILL each JD → Crux   [Mistral, strict json_schema, cached in job_cruxes + version]
  → exclude_when GATE (config, deterministic): min_years>N · country=="other" · red_flags
  → PARTITION by crux salary (effective_lpa): >=floor → M · <floor → dropped · none → N
  → FIT-SCORE each cohort     [Mistral, 1 call/cohort, json_schema] → fit 0-100 + concerns
  → BENCHMARK + RANK: M = round(100*(fit_weight*fit/100 + comp_weight*comp)); N = fit
        + code concerns PAY_UNKNOWN / YOE_UNKNOWN · top_n_per_cohort each
  → PERSIST (status=discovered) + digest_slots + two-section Telegram digest
─────────  later, human-driven  ─────────
/apply N → deterministic gate (no LLM can trigger) → record decision
        → tailoring [Cerebras] → submission flow
```

`fit` = LLM role+skill match (role-weight-dominant; missing must-have caps at 40).
`bench` = ranking score: N → `fit`; M → `round(100*(0.70*fit/100 + 0.30*comp))`,
`comp = clamp((LPA-7)/(top_ctc_lpa-7), 0, 1)`.

## Naukri (richest source) — how it works without a browser/proxy/cost

Naukri's `jobapi/v3/search` returns **406 "recaptcha required"** to plain requests because
it needs **`nkparam`** — a one-time anti-bot token its frontend mints by RSA-encrypting
`v0|<ms-timestamp>|121_srp` with a public key embedded in Naukri's own JS. We reproduce
that in pure Python (`pycryptodome`), seed cookies with one page GET, and call the API with
`appid:109 + gid + nkparam`. Proven to return 200 + real jobs **from the AWS datacenter IP**
— it was never an IP/Akamai ban, just the missing token. Code: `src/cvflow/discovery/naukri.py`.
Technique credit: `github.com/Traverser25/NopeRi` (no code copied; the key is Naukri's).

Notes: a fresh token per request; one 403 → retry with a new token; later pages / sparse
locations (e.g. "Remote") return a benign 400 = end-of-pages. If Naukri changes the token
format or rotates the key, this breaks and needs a one-line update (same risk class as all
scraping — treat accounts/endpoints as expendable).

## Config knobs that drive quality/volume (`config.yaml`)

| Key | Effect |
|---|---|
| `discovery.max_distill_per_cohort` | total jobs distilled+ranked per run (400). Higher = more volume + relevant coverage, slower first run (cached after). |
| `discovery.top_n_per_cohort` | jobs shown per 💰/📋 section (5). |
| `discovery.reconsider_discovered` | testing toggle: re-rank already-shown but un-applied jobs (false in production). |
| `preferences.prefer_roles` | role_family → weight (0–1), rendered into the fit prompt; **dominates** fit. |
| `preferences.exclude_when` | declarative crux-field deal-breakers (e.g. `min_years_required > N`, `country == other`). |
| `preferences.min_ctc_lpa` / `top_ctc_lpa` | salary floor (M/N split) and comp-score top anchor. |
| `preferences.yoe_have` + `yoe_buffer` | Naukri pre-distill experience ceiling. |

## Known limits / non-bugs

- **Salary-stated jobs are scarce** in Indian listings (esp. Naukri "Not disclosed"), so the
  💰 M cohort is often small. We never fabricate pay — this is the market, not a bug.
- **Over-experienced jobs can slip through** when neither the Naukri `experience_range` nor
  the JD text states years (crux `min_years_required` = null → never-fabricate → flagged
  `YOE_UNKNOWN`, not dropped).
- **No `min_fit_score` floor** (user declined — prioritises volume). On a thin pool the
  digest may include low-fit (20–35, `STACK_MISMATCH`) fillers; raising `max_distill_per_cohort`
  and Naukri volume is the mitigation.
- **Crux cache** is version-gated (`DISTILL_VERSION`): bump it whenever the distill prompt or
  `Crux` schema changes so cached cruxes auto re-distill.
