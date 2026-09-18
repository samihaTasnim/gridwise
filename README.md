# GridWise LLM — Smart Campus Energy Optimizer
BUP CSE Fest 2026 Hackathon · Online Preliminary · Team **TODO: team name**

**Live endpoint:** TODO — not yet deployed
**Docker image:** `ghcr.io/samihatasnim/gridwise:1.0.0` (linux/amd64)
**Repository:** https://github.com/samihaTasnim/gridwise
**Video:** TODO — not yet recorded

## What it does
Accepts a 24-hour campus energy scenario plus 1–3 natural-language operator notes.
An LLM interprets each note into a structured directive, deterministic guardrails
validate it, a linear program produces the minimum-cost valid schedule, and a
replay validator re-checks every rule before the response is returned.

## Architecture
```
request → schema validation → LLM interpreter → guardrails → LP optimizer → replay validator → response
```
| Stage | File | Role |
|---|---|---|
| LLM interpreter | `app/interpreter.py` | Interprets **every** operator note (type, window, value). Required path. |
| Guardrails | `app/guardrails.py` | Allowed types, note mapping, hours 0–23 unique ascending, factor ∈ [0,1], reserve ≤ capacity, cap ≥ 0. Invalid output → `no_op`, never invented. |
| Optimizer | `app/optimizer.py` | Linear program solved with SciPy HiGHS (72 variables: grid, solar-used, net-battery per hour). |
| Replay validator | `app/validator.py` | Hour-by-hour check of balance, solar, battery, directives, neutrality, totals. |
| Fallback | `app/fallback.py` | Used only if all LLM providers are unreachable; labelled in `explanation`. |

**LLM provider / model:** Google Gemini, `gemini-3.5-flash-lite` (via the OpenAI-compatible endpoint
`https://generativelanguage.googleapis.com/v1beta/openai/`), temperature 0, JSON mode.
Backup provider: `<not yet configured — see Known limitations>`.
**Model choice note:** the plain `gemini-3.5-flash` (and `gemini-3.6-flash`) variants have
thinking enabled by default and took 10–17 s for a trivial call in testing here — far past
the 8 s client timeout. `-flash-lite` responds in ~1–1.7 s and was used instead. Re-check this
tradeoff if you change models (see `05_LLM_PROMPT_AND_TEST_NOTES.md` §1).

## Quickstart (local, Python 3.12)
```bash
git clone https://github.com/samihaTasnim/gridwise.git && cd gridwise
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # then fill in the values
export $(grep -v '^#' .env | xargs)
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Verified locally on this machine (Python 3.9.6, same dependency versions):
```
$ curl -s http://localhost:8000/health
{"status":"ok"}
```
`/health` returns `200` with **zero environment variables set** — confirmed by running the app with a clean environment.

## Configuration
| Variable | Required | Description |
|---|---|---|
| `LLM_BASE_URL` | yes | OpenAI-compatible base URL |
| `LLM_MODEL` | yes | Model ID |
| `LLM_API_KEY` | yes | API key (never commit) |
| `BACKUP_LLM_*` | no | Second provider, same three variables |
| `PORT` | no | Default 8000 |

## Test it
```bash
curl -s http://localhost:8000/health
# {"status":"ok"}

python - <<'PY' > /tmp/case1.json
import json; print(json.dumps(json.load(open("tests/public_samples.json"))["cases"][0]["input"]))
PY
curl -s -X POST http://localhost:8000/optimize-energy \
  -H "Content-Type: application/json" -d @/tmp/case1.json | head -c 600
```

### Run all sample cases
```bash
python tests/run_samples.py http://localhost:8000 tests/public_samples.json
```
`tests/public_samples.json` is the organizers' official public sample pack
(`BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json`, 10 cases).

Actual result, real Gemini interpreter in the loop (not the fallback parser):
```
health: {'status': 'ok'}
SAMPLE-01 interp=OK valid=OK cost_ratio=1.0000 1.36s
SAMPLE-02 interp=OK valid=OK cost_ratio=1.0000 1.09s
SAMPLE-03 interp=OK valid=OK cost_ratio=1.0000 1.10s
SAMPLE-04 interp=OK valid=OK cost_ratio=1.0000 1.34s
SAMPLE-05 interp=OK valid=OK cost_ratio=1.0000 1.09s
SAMPLE-06 interp=OK valid=OK cost_ratio=1.0000 3.04s
SAMPLE-07 interp=OK valid=OK cost_ratio=1.0000 1.26s
SAMPLE-08 interp=OK valid=OK cost_ratio=1.0000 1.23s
SAMPLE-09 interp=OK valid=OK cost_ratio=1.0000 1.36s
SAMPLE-10 interp=OK valid=OK cost_ratio=1.0000 1.99s

10/10 passed | p95 latency 1.99s
```
The LP optimizer was separately verified against the reference directives directly (bypassing
the LLM entirely, per the judge-style math check): all 10 cases reproduce the reference optimal
cost exactly (cost diff 0.0000) with zero replay violations.
**Actual paraphrase-set result with `gemini-3.5-flash-lite`: 23/23 (100%), avg latency 1.18 s,
max 1.41 s** (paced under the free-tier rate limit — see Known limitations below). Run it
yourself with:
```bash
python -c "
import asyncio, json
from app.interpreter import interpret
cases = json.load(open('tests/paraphrases.json'))
async def main():
    for c in cases:
        out = await interpret([c['note']], 200)
        print(out[0]['directive_type'], out[0]['structured_adjustment'])
asyncio.run(main())
"
```

## Docker fallback
```bash
docker pull ghcr.io/samihatasnim/gridwise:1.0.0
docker run --rm -p 8000:8000 \
  -e LLM_BASE_URL=<url> -e LLM_MODEL=<model> -e LLM_API_KEY=<key> \
  ghcr.io/samihatasnim/gridwise:1.0.0
curl -s http://localhost:8000/health
```
Port 8000, binds 0.0.0.0, no secrets in the image. `/health` works without any variables set.

Build and publish (see `06_DEPLOYMENT_AND_OPS.md` for the full checklist):
```bash
docker build -t ghcr.io/samihatasnim/gridwise:1.0.0 .
docker push ghcr.io/samihatasnim/gridwise:1.0.0     # then set the package to Public in registry settings
```

**Verified locally**, mirroring the judges' key-less Docker check:
```
$ docker run --rm -d -p 8000:8000 gridwise:1.0.0     # no env vars
$ curl -s http://localhost:8000/health
{"status":"ok"}                                       # HTTP 200
$ curl -s -X POST http://localhost:8000/optimize-energy -d @case1.json
{"...", "total_cost_bdt": 7166.0, ...}                # HTTP 200, fallback-labelled
```
Also confirmed: Docker `HEALTHCHECK` reports `healthy`; 2 uvicorn worker processes start;
container logs contain only method/path/status (no keys, prompts, or bodies); `docker history`
shows no application secrets baked into any layer; no `.env` present inside the image
filesystem. Image `gridwise:1.0.0` built with `buildx` default (arm64 on this machine) —
rebuild with `docker buildx build --platform linux/amd64` before publishing if the judge
environment is x86_64.

## API
`GET /health` → `200 {"status":"ok"}`
`POST /optimize-energy` → `200` result · `400` malformed/invalid request · `422` infeasible scenario · `500` controlled internal error.

Real response for official public sample SAMPLE-01, produced by the Gemini interpreter
(`hourly_plan` abridged to 2 of its 24 rows). Note 0 is an applicable directive; note 1 is a
distractor the model correctly returns as `no_op`:

**Request notes:**
```json
["Facilities will wash the rooftop solar panels from noon until 2 PM. During cleaning, usable solar should be treated as roughly 25% of the forecast.",
 "The sports office moved next month's registration deadline."]
```

**Response:**
```json
{
  "scenario_id": "SAMPLE-01",
  "directive_interpretation": [
    {
      "note_index": 0,
      "applies": true,
      "directive_type": "solar_reduction",
      "structured_adjustment": {"hours": [12, 13], "factor": 0.25},
      "explanation": "Rooftop solar cleaning reduces usable solar output to 25% of forecast from noon to 2 PM today."
    },
    {
      "note_index": 1,
      "applies": false,
      "directive_type": "no_op",
      "structured_adjustment": null,
      "explanation": "Note refers to a registration deadline next month, which is unrelated to today's energy operations."
    }
  ],
  "hourly_plan": [
    {"hour": 0, "grid_kwh": 40.0, "solar_used_kwh": 0.0, "battery_action": "discharge", "battery_kwh": 50.0, "battery_energy_after_kwh": 60.0},
    {"hour": 1, "grid_kwh": 95.0, "solar_used_kwh": 0.0, "battery_action": "charge", "battery_kwh": 10.0, "battery_energy_after_kwh": 70.0}
  ],
  "total_grid_kwh": 2692.5,
  "total_cost_bdt": 38365.0,
  "peak_grid_kwh": 187.5,
  "plan_summary": "Applied 1 operator directive(s) (solar_reduction); battery charges in low-tariff/solar-surplus hours and discharges in high-tariff hours, returning to its initial energy. Total cost 38365.00 BDT."
}
```
`total_cost_bdt` here (38365.0) matches the organizers' reference optimal cost for SAMPLE-01 exactly.

## Dependencies and credits
FastAPI, Uvicorn, Pydantic, httpx, NumPy, SciPy (HiGHS). LLM: Google Gemini.
AI coding assistants used: Claude Code (Anthropic) — architecture and logic reviewed and owned by the team.

## Known limitations
- **The Gemini key currently configured is on the free tier: 15 requests/minute for
  `gemini-3.5-flash-lite`.** Verified directly against the provider — the 11th call inside
  60 s returns HTTP 429, which the app correctly downgrades to the labelled fallback parser
  (never a 5xx), but that silently trades LLM-interpretation accuracy for availability during
  a burst. **Before submission: either upgrade this key's project to a paid tier, or obtain
  one that already has a higher limit**, and set `BACKUP_LLM_*` to a second provider — neither
  is done yet in this repo's `.env`.
- Interpretation quality depends on the external LLM; if all providers fail (or are
  rate-limited), a limited regex fallback is used and flagged in `explanation`.
- Windows that cross midnight are wrapped inside the same 0–23 horizon.
- In-memory cache is per worker and cleared on restart.
- A note implying two directive types at once is mapped to a single type.

## Secret handling
Secrets are read from environment variables only. `.env` is git- and docker-ignored.
Logs contain scenario id, latency and directive types; never keys, prompts or request bodies.
