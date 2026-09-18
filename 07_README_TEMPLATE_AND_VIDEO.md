# 07 · README Template, Video Script, Final Checklist

## Part A — README.md template

The rubric gives 10 points for documentation, itemised as: clean quickstart (3) · env/config/model docs (2) · public-sample test procedure + expected result (2) · architecture explanation (1) · Docker fallback instructions (1) · dependencies/limitations/secrets (1). The headings below map one-to-one. Replace `<...>` and paste **real** output.

````markdown
# GridWise LLM — Smart Campus Energy Optimizer
BUP CSE Fest 2026 Hackathon · Online Preliminary · Team <name>

**Live endpoint:** https://gridwise.<domain>
**Docker image:** `ghcr.io/<user>/gridwise:1.0.0`
**Video:** <link>

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
| Optimizer | `app/optimizer.py` | Linear program solved with SciPy HiGHS (72 variables). |
| Replay validator | `app/validator.py` | Hour-by-hour check of balance, solar, battery, directives, neutrality, totals. |
| Fallback | `app/fallback.py` | Used only if all LLM providers are unreachable; labelled in `explanation`. |

**LLM provider / model:** <provider>, `<model-id>` (backup: <provider>, `<model-id>`), temperature 0, JSON mode.

## Quickstart (local, Python 3.12)
```bash
git clone https://github.com/<user>/<repo>.git && cd <repo>
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # then fill in the values
export $(grep -v '^#' .env | xargs)
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

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

### Run all public samples
```bash
python tests/run_samples.py http://localhost:8000 tests/public_samples.json
```
Expected:
```
SAMPLE-01 interp=OK valid=OK cost_ratio=1.0000 <t>s
...
10/10 passed | p95 latency <x>s
```

## Docker fallback
```bash
docker pull ghcr.io/<user>/gridwise:1.0.0
docker run --rm -p 8000:8000 \
  -e LLM_BASE_URL=<url> -e LLM_MODEL=<model> -e LLM_API_KEY=<key> \
  ghcr.io/<user>/gridwise:1.0.0
curl -s http://localhost:8000/health
```
Port 8000, binds 0.0.0.0, no secrets in the image. `/health` works without any variables set.

## API
`GET /health` → `200 {"status":"ok"}`
`POST /optimize-energy` → `200` result · `400` malformed/invalid request · `422` infeasible scenario · `500` controlled internal error.
<one short sample request/response fragment>

## Dependencies and credits
FastAPI, Uvicorn, Pydantic, httpx, NumPy, SciPy (HiGHS). LLM: <provider>.
AI coding assistants used: <tools> — architecture and logic reviewed and owned by the team.

## Known limitations
- Interpretation quality depends on the external LLM; if all providers fail, a limited regex fallback is used and flagged.
- Windows that cross midnight are wrapped inside the same 0–23 horizon.
- In-memory cache is per worker and cleared on restart.
- A note implying two directive types at once is mapped to a single type.

## Secret handling
Secrets are read from environment variables only. `.env` is git- and docker-ignored.
Logs contain scenario id, latency and directive types; never keys, prompts or request bodies.
````

## Part B — 3-minute video script

Tie-break only, so keep effort proportional: one take, screen recording, your voice. Reviewers look for problem understanding, architecture clarity, the LLM → guardrails → optimizer flow, and how to run/test.

| Time | Screen | Say |
|---|---|---|
| 0:00–0:25 | Title + one sample request | The problem: campus with grid, solar, battery; 24 hours of data plus free-text operator notes. Goal: understand the notes, obey them, minimise grid cost. |
| 0:25–1:05 | Architecture diagram (`02_…` §2) | Walk the pipeline left to right. Stress: "LLM output is untrusted until guardrails pass." |
| 1:05–1:40 | `interpreter.py` prompt + `guardrails.py` | Key idea: the LLM reports the window and the number as written; code builds end-exclusive hours, converts "80% reduction" to 0.2 and "50% of capacity" to kWh. Invalid output becomes no_op. |
| 1:40–2:10 | `optimizer.py` formulation | No efficiency loss → exact LP. Show how each directive is just a bound. Replay validator mirrors the judge. |
| 2:10–2:45 | Terminal | `curl /health`, run `run_samples.py` against the live URL → 10/10, p95 latency. Show one response's `directive_interpretation`. |
| 2:45–3:00 | README top | Docker pull/run, env vars, fallback behaviour when the LLM is down. Done. |

Record at 1080p, font size up, rehearse once with a timer. Upload as unlisted YouTube or a Drive link with "anyone with the link"; test in incognito.

## Part C — Final pre-submit checklist

**Contract**
- [ ] `/health` → `{"status":"ok"}`; `/optimize-energy` works with 1, 2 and 3 notes
- [ ] N notes → N entries, indices 0..N-1 in order
- [ ] `no_op` ⇔ `applies:false` ⇔ `null`; all others `applies:true` with exact keys
- [ ] `scenario_id` echoed; 24 plan rows; totals equal recomputed values
- [ ] Bad JSON → 400, never a stack trace

**Correctness**
- [ ] 10/10 public samples: interpretation, validity, cost ratio 1.0
- [ ] Paraphrase set ≥ 90%
- [ ] Replay validator runs on every response

**Reliability**
- [ ] Wrong API key → still 200 within 30 s
- [ ] 10+ parallel requests → no 5xx; p95 < 5 s
- [ ] Tested from outside your network

**Artifacts**
- [ ] Image public, exact tag, pulls while logged out, `/health` works with no env vars
- [ ] README follows the template; commands copy-paste clean from a fresh clone
- [ ] No secrets in repo, image, logs, README (`git log -p | grep -i "api_key"`)
- [ ] Video ≤ 3:00 and accessible
- [ ] Repo private now → public right after the deadline
- [ ] Endpoint, image and video stay up through judging
