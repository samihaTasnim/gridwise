# GridWise LLM — Smart Campus Energy Optimizer
BUP CSE Fest 2026 Hackathon · Online Preliminary · Team `<team name>`

**Live endpoint:** `https://<fill in after deploy — see 06_DEPLOYMENT_AND_OPS.md>`
**Docker image:** `ghcr.io/<user>/gridwise:1.0.0`
**Video:** `<link>`

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

**LLM provider / model:** `<provider>`, `<model-id>` (backup: `<provider>`, `<model-id>`), temperature 0, JSON mode.
See `05_LLM_PROMPT_AND_TEST_NOTES.md` for how to pick and validate the model against the paraphrase set.

## Quickstart (local, Python 3.12)
```bash
git clone https://github.com/<user>/<repo>.git && cd <repo>
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
`tests/public_samples.json` in this repo is a **self-authored local test set** (4 hand-built
scenarios), not the organizers' official `Public_Sample_Cases.json` — that file was not part
of this build pack. Replace it with the real one as soon as it is published, then re-run this
command. Own wording only (see `05_LLM_PROMPT_AND_TEST_NOTES.md` §2.6 on not hard-coding
public sample text into the prompt).

Actual local run (no LLM key configured, so every note went through the deterministic
fallback parser and was still correctly interpreted and scored):
```
health: {'status': 'ok'}
SAMPLE-01 interp=OK valid=OK cost_ratio=1.0000 0.02s
SAMPLE-02 interp=OK valid=OK cost_ratio=1.0000 0.01s
SAMPLE-03 interp=OK valid=OK cost_ratio=1.0000 0.01s
SAMPLE-04 interp=OK valid=OK cost_ratio=1.0000 0.01s

4/4 passed | p95 latency 0.01s
```
Once an `LLM_API_KEY` is configured, re-run `python tests/paraphrases.json` cases through
`app.interpreter.interpret()` (20+ hand-written paraphrases covering the traps in
`05_LLM_PROMPT_AND_TEST_NOTES.md` §3) to measure real interpretation accuracy — the fallback
parser alone is deliberately weak on paraphrased wording; that is the LLM's job.

## Docker fallback
```bash
docker pull ghcr.io/<user>/gridwise:1.0.0
docker run --rm -p 8000:8000 \
  -e LLM_BASE_URL=<url> -e LLM_MODEL=<model> -e LLM_API_KEY=<key> \
  ghcr.io/<user>/gridwise:1.0.0
curl -s http://localhost:8000/health
```
Port 8000, binds 0.0.0.0, no secrets in the image. `/health` works without any variables set.

Build and publish (see `06_DEPLOYMENT_AND_OPS.md` for the full checklist):
```bash
docker build -t ghcr.io/<user>/gridwise:1.0.0 .
docker push ghcr.io/<user>/gridwise:1.0.0     # then set the package to Public in registry settings
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

Sample response fragment (from the local run above, fallback-parser path):
```json
{
  "scenario_id": "SAMPLE-01",
  "directive_interpretation": [
    {
      "note_index": 0,
      "applies": true,
      "directive_type": "solar_reduction",
      "structured_adjustment": {"hours": [11, 12, 13], "factor": 0.2},
      "explanation": "LLM unavailable; deterministic fallback parser used."
    }
  ],
  "total_grid_kwh": 1067.0,
  "total_cost_bdt": 7166.0,
  "peak_grid_kwh": 101.0,
  "plan_summary": "Applied 1 operator directive(s) (solar_reduction); battery charges in low-tariff/solar-surplus hours and discharges in high-tariff hours, returning to its initial energy. Total cost 7166.00 BDT."
}
```

## Dependencies and credits
FastAPI, Uvicorn, Pydantic, httpx, NumPy, SciPy (HiGHS). LLM: `<provider>`.
AI coding assistants used: `<tools>` — architecture and logic reviewed and owned by the team.

## Known limitations
- Interpretation quality depends on the external LLM; if all providers fail, a limited regex fallback is used and flagged in `explanation`.
- Windows that cross midnight are wrapped inside the same 0–23 horizon.
- In-memory cache is per worker and cleared on restart.
- A note implying two directive types at once is mapped to a single type.
- `tests/public_samples.json` in this repo is self-authored, not the organizers' official sample file (not provided in this build pack).

## Secret handling
Secrets are read from environment variables only. `.env` is git- and docker-ignored.
Logs contain scenario id, latency and directive types; never keys, prompts or request bodies.
