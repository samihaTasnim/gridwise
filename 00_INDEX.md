# GridWise LLM — Hackathon Build Pack

BUP CSE Fest 2026 · Online Preliminary · 4-hour window (7:00–11:00 PM)

## Read in this order

| # | File | Use it for |
|---|---|---|
| 01 | `01_PLAN_OF_ACTION.md` | Scoring strategy, minute-by-minute schedule, team split, cut list |
| 02 | `02_SYSTEM_ARCHITECTURE.md` | Components, diagrams, data contracts, LP formulation, design decisions |
| 03 | `03_WORKFLOW.md` | Request lifecycle, failure paths, dev/test/deploy workflows |
| 04 | `04_REFERENCE_CODE.md` | Code for every module, Dockerfile, env template |
| 05 | `05_LLM_PROMPT_AND_TEST_NOTES.md` | Prompt rationale, paraphrase test set, known traps |
| 06 | `06_DEPLOYMENT_AND_OPS.md` | VPS + Nginx + Docker steps, image publishing, ops checklist |
| 07 | `07_README_TEMPLATE_AND_VIDEO.md` | README skeleton that maps to the 10 documentation points, 3-min video script, final checklist |

## The three facts that shape everything

1. **The optimizer is a plain linear program.** No battery efficiency loss, no grid export. A 72-variable LP reproduces the reference optimal cost on all 10 public samples in about 3 ms each (verified). 35 of 100 points rest on this one formulation.
2. **The LLM should extract language, not do math.** It returns `start_hour`, `end_hour`, `value`, `value_kind`; deterministic code builds the `hours` array, the solar `factor`, and percent-of-capacity reserves. This removes the most common LLM errors (end-exclusive windows, "80% reduction → 0.2").
3. **30 points are non-coding work** (Docker fallback, README, reliability). They are lost to the clock, not to difficulty. Deploy a skeleton in the first 20 minutes and freeze features at 2:45.

## Stack

Python 3.12 · FastAPI · scipy (HiGHS) · httpx · any OpenAI-compatible LLM endpoint · Docker · Nginx on your own VPS.
