# 01 · Plan of Action

## 1. Requirement summary

Build one public HTTP service with two endpoints:

- `GET /health` → `{"status":"ok"}` within 60 s of start.
- `POST /optimize-energy` → takes 24 hourly rows (demand, solar, tariff), a battery spec, and 1–3 free-text operator notes. Returns one `directive_interpretation` entry per note plus a valid, minimum-cost 24-hour `hourly_plan` and totals.

Hard requirements: the LLM must be in the note-interpretation path (disqualifying if not); LLM output must pass deterministic guardrails; every request under 30 s; Docker fallback image; self-contained README; 3-minute video (tie-break only).

## 2. Where the 100 points are and how to take them

| Pts | Category | How we win it | Risk |
|---|---|---|---|
| 25 | LLM interpretation (relevance, type, hours, values, paraphrase) | LLM returns an intermediate form; code computes hours/factor/kWh. Few-shot prompt, temperature 0 | Medium — paraphrases, distractors |
| 25 | Directive application + constraints | LP bounds per directive + independent replay validator before responding | Low — verified |
| 10 | Optimization quality | Same LP; exact optimum | Low — verified 10/10 |
| 10 | API contract | Pydantic schema, manual JSON parse for 400, fixed response shape | Low |
| 10 | Performance & reliability | One LLM call per request, cache, 8 s timeout, backup provider, fallback parser, no 5xx | Medium — provider rate limits |
| 10 | Deployment & Docker | Own VPS + published image that reaches `/health` with no key | Low if done early |
| 10 | Documentation | README written from template in `07_…` | Low if time is reserved |

Judge behaviour to remember: the schedule is replayed against the **organizer's true directive**, not your interpretation. A wrong interpretation therefore costs you twice (interpretation points *and* the case's application + optimization credit). Interpretation accuracy is the highest-leverage work after the first hour.

## 3. Schedule (single developer)

| Clock | Block | Tasks | Exit criterion |
|---|---|---|---|
| 0:00–0:20 | **Bootstrap** | New private GitHub repo · folder layout · `/health` only · Dockerfile · `docker compose up` on VPS · subdomain + HTTPS · get 2 LLM keys | `curl https://<domain>/health` works from your phone |
| 0:20–1:00 | **Math core** | `schemas.py`, `optimizer.py`, `validator.py` · temporary test that feeds the *reference* directives | 10/10 sample costs match, 0 replay violations |
| 1:00–1:50 | **Language core** | `interpreter.py` prompt + provider call · `guardrails.py` · wire into `main.py` · `run_samples.py` · paraphrase set from `05_…` | 10/10 interpretations; ≥ 90% on your paraphrases |
| 1:50–2:20 | **Hardening** | 400/422/500 paths · provider chain + fallback · cache · malformed-input tests · concurrency test (10 parallel requests) | Nothing returns a stack trace or hangs |
| 2:20–2:45 | **Ship** | Build + push image with exact tag · redeploy · run sample runner against the public URL · test image with **no** API key | p95 < 5 s measured; image reaches `/health` |
| 2:45–3:15 | **README** | Fill template · paste real curl output · docker pull/run lines · limitations · credits | Someone else could run it cold |
| 3:15–3:40 | **Video** | Record per script in `07_…` | ≤ 3:00, link opens in incognito |
| 3:40–4:00 | **Buffer + submit** | Final checklist · submit URL, repo, image ref, video | Submitted by 3:55 |

**Feature freeze at 2:45.** After that: bug fixes only.

## 4. If you have teammates

| Person | 0:00–1:50 | 1:50–2:45 | 2:45–4:00 |
|---|---|---|---|
| A (backend/math) | schemas, optimizer, validator | hardening, concurrency | code walkthrough for video |
| B (LLM) | prompt, interpreter, guardrails, paraphrase tests | provider chain, cache, fallback | limitations + architecture section of README |
| C (DevOps/docs) | repo, Docker, VPS, HTTPS, CI-less deploy script | image publish, external tests | README, video recording, submission |

Merge point is 1:50. Agree the intermediate JSON format (see `02_…` §4) in the first 10 minutes so A and B can work independently.

## 5. Perspectives checklist

### User end (the judge harness, and a hypothetical campus operator)
- Exact field names and types; `scenario_id` echoed; entries in `note_index` order.
- `applies=false` ⇔ `no_op` ⇔ `structured_adjustment=null`. No other combination ever leaves the service.
- Bad input → `400` with `{"error": "..."}`; infeasible → `422`; anything else → `500` with a generic message.
- Accept `hours` in any order; sort internally.
- `explanation` from the LLM (short), `plan_summary` from a template (no second LLM call).
- Totals are computed from the rounded plan that is actually returned.

### System admin
- Container boots and answers `/health` with zero env vars set.
- Secrets only via env; `.env` git- and docker-ignored; `.env.example` committed.
- Nginx `proxy_read_timeout 35s`; uvicorn 2 workers; restart policy `unless-stopped`.
- Logs: scenario id, latency, directive types, provider failure class. Never keys, prompts, or bodies.
- LLM quota: paid or high-limit key; backup provider configured; cache for repeats.
- Keep endpoint, image and video reachable through the whole judging window, not just until 11 PM.

### Developer
- Pure functions for guardrails/optimizer/validator → trivially testable without network.
- One sample runner that behaves like the judge (replays against ground truth).
- Directives touch the math in exactly one function (`apply_directives`), shared by optimizer and validator.
- Provider is config, not code (OpenAI-compatible endpoint + env vars).

## 6. Cut list (drop in this order if you fall behind)

1. Demo web page / charts.
2. Guardrail-feedback retry to the LLM (keep the downgrade-to-no_op).
3. Backup provider (keep the deterministic fallback).
4. Cache.

Never cut: replay validator, Docker image test without a key, README quickstart.

## 7. Risks and mitigations

| Risk | Mitigation |
|---|---|
| LLM provider rate-limits or stalls during judging | 8 s timeout → backup provider → fallback parser; cache; paid key |
| Off-by-one hour windows | Code builds `hours` from start/end; prompt has explicit end-exclusive examples |
| "Reduction" vs "remaining" confusion | `value_kind` field; code converts |
| Distractors that sound energy-related or refer to another day | Explicit no_op rules + few-shot examples |
| Floating-point drift breaks balance/neutrality | Round battery + solar, recompute grid and energy, fix drift on last hour, replay before returning |
| Judge runs Docker image without your key | Service still returns 200 via fallback parser; `/health` independent of LLM |
| Cold start on free PaaS > 60 s | Use own always-on VPS |
| Secrets leak | `.gitignore` first commit; generic error bodies; no body logging |
