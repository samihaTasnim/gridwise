# 05 · LLM Prompt Notes and Paraphrase Test Set

The full system prompt lives in `app/interpreter.py` (see `04_REFERENCE_CODE.md`). This file explains why it is shaped that way and gives you a test set to tune against.

## 1. Choosing the model

You need: JSON/structured output, low latency (sub-2 s typical), generous rate limits, and a second provider as backup. A small/fast tier model from any major provider is sufficient; this is an extraction task, not a reasoning task. Check the current model IDs and rate limits on the provider's docs on the day, run the paraphrase set below against two candidates, and pick the one with the better accuracy-to-latency result. Put the exact provider and model ID in the README (it is scored).

Settings: `temperature: 0`, JSON mode on, max output ~400 tokens, 8 s client timeout.

## 2. Prompt design rules

1. **One call, all notes.** Number them `[0] [1] [2]`; require one item per note in the same order.
2. **No arithmetic in the model.** It reports the window as written and the number as written plus `value_kind`. Code converts.
3. **End-exclusive stated three ways** (rule, example, edge cases for noon/midnight/after/before).
4. **no_op is a first-class answer** with three explicit reasons: unrelated, different day, unsupported energy concept.
5. **"Never invent a type or a number."** If a relevant-looking note has no usable number (e.g. "limit grid import this evening" with no kWh), it cannot be expressed → `no_op`.
6. **Few-shot examples in your own words.** Do not paste public sample notes into the prompt; the rules prohibit hard-coding public wording, and your own paraphrases generalise better.
7. **Short explanations** (≤ 25 words) to save output tokens and latency.

## 3. Traps to test for

| Trap | Example | Expected |
|---|---|---|
| Reduction vs remaining | "cut by 80%" / "down to 20%" / "only a fifth available" | factor 0.2 in all three |
| Fractions in words | "half", "a quarter", "one-fifth", "loses a third" | 0.5 · 0.25 · 0.2 · 0.666667 |
| Total outage | "PV array offline 10–12" | factor 0 |
| 24-hour and spelled times | "13:00–15:00", "one until three in the afternoon" | [13,14] |
| Noon / midnight | "noon until 2 PM", "9 PM until midnight", "midnight to 4 AM" | [12,13] · [21,22,23] · [0,1,2,3] |
| Open-ended | "after 8 PM", "before 6 AM", "for the rest of the day from 5 PM" | [20..23] · [0..5] · [17..23] |
| Single hour | "during the 5 PM hour" | [17] |
| Percent-of-capacity reserve | "hold half the battery from 6 to 9 PM" (capacity 200) | 100 kWh |
| Reserve below base minimum | "keep at least 20 kWh" (base min 40) | report 20; the optimizer uses `max()` |
| Different-day distractor | "Panels will be cleaned next Tuesday from 1 to 3 PM" | no_op |
| Energy-flavoured but unsupported | "Expect 10% higher demand during exams", "Tariff review meeting at 3 PM" | no_op |
| Number-bearing distractor | "The 150-seat auditorium is booked from 2 PM to 4 PM" | no_op |
| Charge vs discharge wording | "charger isolated", "battery cannot supply the campus", "don't draw from storage", "no topping up the battery" | no_charge · no_discharge · no_discharge · no_charge |
| Grid cap synonyms | "feeder limit", "transformer limit", "utility import ceiling", "don't pull more than 150 kWh per hour from the grid" | max_grid_window |

One ambiguity the spec does not resolve: a note such as "battery must stay idle from 2 to 4 PM" implies both no-charge and no-discharge, but each note maps to exactly one type. The organizers say hidden notes map to exactly one type, so this should not appear; don't spend time on it.

## 4. Starter `tests/paraphrases.json`

Write 20–30 of these. Format mirrors the public pack so the same runner logic applies (battery capacity 200 assumed for percentages).

```json
[
  {"note": "PV yield will sit at roughly a quarter of forecast between 10:00 and 12:00 due to haze.",
   "expect": {"directive_type": "solar_reduction", "structured_adjustment": {"hours": [10, 11], "factor": 0.25}}},
  {"note": "Inverter swap: rooftop generation cut by 60% from noon till three.",
   "expect": {"directive_type": "solar_reduction", "structured_adjustment": {"hours": [12, 13, 14], "factor": 0.4}}},
  {"note": "Storage must hold no less than 110 kWh between 5 PM and 8 PM for the hospital wing.",
   "expect": {"directive_type": "minimum_battery_reserve", "structured_adjustment": {"hours": [17, 18, 19], "minimum_energy_kwh": 110}}},
  {"note": "Maintain three-quarters of battery capacity from 9 PM until midnight.",
   "expect": {"directive_type": "minimum_battery_reserve", "structured_adjustment": {"hours": [21, 22, 23], "minimum_energy_kwh": 150}}},
  {"note": "No topping up the battery between 1 AM and 4 AM; the charger is being serviced.",
   "expect": {"directive_type": "no_charge_window", "structured_adjustment": {"hours": [1, 2, 3]}}},
  {"note": "The battery cannot feed the campus from 16:00 to 18:00 during breaker tests.",
   "expect": {"directive_type": "no_discharge_window", "structured_adjustment": {"hours": [16, 17]}}},
  {"note": "Utility asks us to stay under 140 kWh of import each hour after 8 PM.",
   "expect": {"directive_type": "max_grid_window", "structured_adjustment": {"hours": [20, 21, 22, 23], "max_grid_kwh": 140}}},
  {"note": "Panel cleaning is scheduled for next Thursday, 1 PM to 3 PM.",
   "expect": {"directive_type": "no_op", "structured_adjustment": null}},
  {"note": "The 150-seat auditorium is reserved from 2 PM to 4 PM.",
   "expect": {"directive_type": "no_op", "structured_adjustment": null}},
  {"note": "Expect heavier lab demand during exam week.",
   "expect": {"directive_type": "no_op", "structured_adjustment": null}}
]
```

Tiny runner idea: for each entry, call `interpret([note], 200)` directly (no HTTP needed) and compare `directive_type` + `structured_adjustment`, using a 0.01 tolerance on numbers.

## 5. Tuning loop

1. Run the set. 2. For each miss, decide: is it a *rule* the prompt lacks (add one line) or a *pattern* it hasn't seen (add one few-shot)? 3. Re-run the **whole** set, since fixes can regress other cases. 4. Stop at ~95%; remaining time is worth more elsewhere.

Keep the prompt under ~600 tokens. Longer prompts slow every request and rarely help a small model.

## 6. Compliance wording for README and video

Say this plainly, because judges may inspect the repo for it:

> Every operator note is interpreted by the LLM (`app/interpreter.py`). Its output is an intermediate structure that deterministic guardrails (`app/guardrails.py`) validate and convert into the final `structured_adjustment`. The optimizer only consumes guardrail-approved directives. A regex fallback exists solely to keep the API responsive if all LLM providers are unreachable; responses produced that way say so in `explanation`.
