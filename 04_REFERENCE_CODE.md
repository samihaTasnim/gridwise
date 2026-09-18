# 04 · Reference Code

Copy these into the repo layout from `02_SYSTEM_ARCHITECTURE.md`. Status of each file is marked honestly:

| File | Status |
|---|---|
| `app/guardrails.py` | ✅ tested in sandbox against all 10 public samples |
| `app/optimizer.py` | ✅ tested in sandbox against all 10 public samples — cost matches reference 10/10, ~3 ms per solve |
| `app/validator.py` | ✅ tested in sandbox against all 10 public samples — also accepts all 10 reference plans |
| `app/fallback.py` | ✅ tested in sandbox against all 10 public samples (10/10 public notes; deliberately weak on paraphrases — that is the LLM's job) |
| `app/schemas.py`, `app/interpreter.py`, `app/main.py`, `tests/run_samples.py` | ⚠️ compile-checked only (FastAPI/pydantic/network not available in my sandbox) — run it once locally before trusting it |

> Rulebook note: AI assistants are allowed, but "core architecture and logic should be the team's own work". Read, understand and adapt these — you will have to explain them in the video — and credit tools in the README.

## requirements.txt
```text
fastapi>=0.115
uvicorn[standard]>=0.32
pydantic>=2.7
httpx>=0.27
scipy>=1.13
numpy>=1.26
```

After the first successful local run: `pip freeze > requirements.lock.txt` and install from the lock file in Docker for reproducibility.

## app/schemas.py — request validation
```python
from typing import List
from pydantic import BaseModel, Field, field_validator, model_validator


def Num():
    # finite, non-negative (json.loads accepts NaN/Infinity, so reject them here)
    return Field(ge=0, allow_inf_nan=False)


class Hour(BaseModel):
    hour: int = Field(ge=0, le=23)
    demand_kwh: float = Num()
    solar_kwh: float = Num()
    tariff_bdt_per_kwh: float = Num()


class Battery(BaseModel):
    capacity_kwh: float = Num()
    initial_energy_kwh: float = Num()
    minimum_energy_kwh: float = Num()
    max_charge_kwh_per_hour: float = Num()
    max_discharge_kwh_per_hour: float = Num()

    @model_validator(mode="after")
    def _consistent(self):
        if not (self.minimum_energy_kwh <= self.initial_energy_kwh <= self.capacity_kwh):
            raise ValueError("need minimum_energy <= initial_energy <= capacity")
        return self


class Scenario(BaseModel):
    scenario_id: str = Field(min_length=1)
    operator_notes: List[str] = Field(min_length=1, max_length=3)
    hours: List[Hour] = Field(min_length=24, max_length=24)
    battery: Battery

    @field_validator("operator_notes")
    @classmethod
    def _non_empty(cls, v):
        if any(not n.strip() for n in v):
            raise ValueError("operator notes must be non-empty")
        return v

    @field_validator("hours")
    @classmethod
    def _all_hours(cls, v):
        if sorted(h.hour for h in v) != list(range(24)):
            raise ValueError("hours must cover 0..23 exactly once")
        return sorted(v, key=lambda h: h.hour)
```

## app/interpreter.py — LLM step (prompt, provider chain, cache, retry)
Works with any OpenAI-compatible `/chat/completions` endpoint, so switching provider is an env-var change. If your provider does not support `response_format: json_object`, drop that line; the code already strips code fences before `json.loads`.
```python
"""LLM interpretation step. Works with any OpenAI-compatible chat endpoint
(set LLM_BASE_URL / LLM_MODEL / LLM_API_KEY). One call per request, all notes."""
import json, os, hashlib, logging
import httpx
from .guardrails import normalize_all
from .fallback import parse_notes

log = logging.getLogger("gridwise.llm")
_cache: dict[str, list] = {}

SYSTEM_PROMPT = """You convert campus energy operator notes into structured directives.
Return ONLY a JSON object: {"items":[...]} with exactly one item per note, same order.

Each item:
{"note_index": int, "directive_type": str, "start_hour": int|null, "end_hour": int|null,
 "value": number|null, "value_kind": str, "explanation": str (max 25 words)}

directive_type (choose exactly one):
- solar_reduction: usable solar/PV output is reduced during a time window TODAY.
- minimum_battery_reserve: battery must hold at least some energy during a window.
- no_charge_window: battery charging unavailable/forbidden during a window.
- no_discharge_window: battery discharging unavailable/forbidden during a window.
- max_grid_window: grid import limited to a stated kWh per hour during a window.
- no_op: anything else.

Use no_op when the note is unrelated to energy, refers to a different day
(tomorrow, next week, last month), or describes something none of the five types
can express (e.g. demand or tariff changes). Never invent a type or a number.

Time: 24-hour clock. start_hour is inclusive, end_hour is EXCLUSIVE.
"1 PM to 3 PM" -> start 13, end 15. "noon" = 12. "until midnight"/"end of day" -> end 24.
"from midnight"/"start of day" -> start 0. "after 8 PM" -> 20..24. "before 6 AM" -> 0..6.
"during the 5 PM hour" -> 17..18. Report the window as written; do not list hours.

value / value_kind - report what the note SAYS, do no arithmetic:
- solar: "drops to 20%" -> 20, percent_remaining | "80% reduction" -> 80, percent_reduction
         "one-fifth of normal" -> 0.2, fraction_remaining | "loses a third" -> 0.333333, fraction_reduction
         "solar offline" -> 0, percent_remaining
- reserve: "120 kWh" -> 120, kwh | "half the battery" -> 50, percent_of_capacity
- grid cap: "no more than 155 kWh" -> 155, kwh
- no_charge / no_discharge / no_op -> value null, value_kind "none"
"""


def _key(notes, capacity):
    return hashlib.sha256(json.dumps([notes, capacity]).encode()).hexdigest()


async def _call(client, base_url, api_key, model, notes, feedback=None):
    user = "Operator notes:\n" + "\n".join(f"[{i}] {n}" for i, n in enumerate(notes))
    if feedback:
        user += "\n\nYour previous answer failed validation: " + "; ".join(feedback) + "\nFix it."
    r = await client.post(
        f"{base_url.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json={"model": model, "temperature": 0,
              "response_format": {"type": "json_object"},
              "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                           {"role": "user", "content": user}]},
    )
    r.raise_for_status()
    text = r.json()["choices"][0]["message"]["content"]
    text = text.replace("```json", "").replace("```", "").strip()
    return json.loads(text).get("items")


def _providers():
    out = []
    for p in ("LLM", "BACKUP_LLM"):
        key = os.getenv(f"{p}_API_KEY")
        if key:
            out.append((os.getenv(f"{p}_BASE_URL", "https://api.openai.com/v1"),
                        key, os.getenv(f"{p}_MODEL", "")))
    return out


async def interpret(notes, capacity_kwh):
    k = _key(notes, capacity_kwh)
    if k in _cache:
        return _cache[k]
    async with httpx.AsyncClient(timeout=httpx.Timeout(8.0, connect=3.0)) as client:
        for base_url, key, model in _providers():
            feedback = None
            for attempt in range(2):                     # 1 try + 1 guardrail-feedback retry
                try:
                    raw = await _call(client, base_url, key, model, notes, feedback)
                    out, problems = normalize_all(raw, len(notes), capacity_kwh)
                    if not problems or attempt == 1:
                        _cache[k] = out
                        return out
                    feedback = problems
                except Exception as e:                   # timeout, 429, bad JSON...
                    log.warning("LLM provider failed: %s", type(e).__name__)   # no secrets
                    break                                # go to next provider
    # every provider failed -> safe, clearly-labelled fallback (not cached)
    out, _ = normalize_all(parse_notes(notes), len(notes), capacity_kwh)
    return out
```

## app/guardrails.py — deterministic validation + hour/number math
```python
"""Deterministic guardrails: turn the LLM's *intermediate* output into final,
validated directives. LLM output is untrusted until it passes here."""
import math

TYPES = {"solar_reduction", "minimum_battery_reserve", "no_charge_window",
         "no_discharge_window", "max_grid_window", "no_op"}


class GuardrailError(ValueError):
    pass


def _num(x, name):
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x):
        raise GuardrailError(f"{name} must be a finite number")
    return float(x)


def build_hours(start, end):
    """start inclusive, end exclusive, 24h clock. end=24 means midnight.
    end <= start is treated as an overnight wrap inside the same horizon."""
    for v in (start, end):
        if isinstance(v, bool) or not isinstance(v, int):
            raise GuardrailError("start_hour/end_hour must be integers")
    if not (0 <= start <= 23 and 1 <= end <= 24):
        raise GuardrailError("hour out of range")
    if end > start:
        hours = list(range(start, end))
    else:
        hours = list(range(start, 24)) + list(range(0, end))
    hours = sorted(set(hours))
    if not hours:
        raise GuardrailError("empty hour window")
    return hours


def _fraction_remaining(value, kind):
    v = _num(value, "value")
    if kind == "percent_remaining":
        f = v / 100
    elif kind == "percent_reduction":
        f = 1 - v / 100
    elif kind == "fraction_remaining":
        f = v
    elif kind == "fraction_reduction":
        f = 1 - v
    else:
        raise GuardrailError(f"value_kind {kind!r} invalid for solar_reduction")
    if not (0 <= f <= 1):
        raise GuardrailError("factor must be within [0, 1]")
    return round(f, 6)          # keep precision: 'a third' -> 0.333333


def normalize_one(raw, capacity_kwh):
    """raw: one intermediate dict from the LLM -> final (type, adjustment)."""
    t = raw.get("directive_type")
    if t not in TYPES:
        raise GuardrailError(f"unsupported directive_type {t!r}")
    if t == "no_op":
        return t, None
    hours = build_hours(raw.get("start_hour"), raw.get("end_hour"))
    kind, value = raw.get("value_kind"), raw.get("value")
    if t == "solar_reduction":
        return t, {"hours": hours, "factor": _fraction_remaining(value, kind)}
    if t == "minimum_battery_reserve":
        v = _num(value, "value")
        if kind == "percent_of_capacity":
            v = capacity_kwh * v / 100
        elif kind == "fraction_of_capacity":
            v = capacity_kwh * v
        elif kind != "kwh":
            raise GuardrailError(f"value_kind {kind!r} invalid for reserve")
        if v < 0 or v > capacity_kwh + 1e-9:
            raise GuardrailError("reserve must be within [0, capacity]")
        return t, {"hours": hours, "minimum_energy_kwh": round(v, 6)}
    if t == "max_grid_window":
        v = _num(value, "value")
        if kind != "kwh" or v < 0:
            raise GuardrailError("max_grid_kwh must be a non-negative kWh value")
        return t, {"hours": hours, "max_grid_kwh": round(v, 6)}
    return t, {"hours": hours}       # no_charge_window / no_discharge_window


def normalize_all(raw_items, n_notes, capacity_kwh):
    """Always returns exactly n_notes entries in note_index order.
    Anything invalid/missing/duplicated is downgraded to no_op (never invented).
    Second return value lists guardrail problems (used for one LLM retry)."""
    by_idx, problems = {}, []
    for raw in raw_items if isinstance(raw_items, list) else []:
        if not isinstance(raw, dict):
            continue
        i = raw.get("note_index")
        if isinstance(i, bool) or not isinstance(i, int) or not (0 <= i < n_notes):
            problems.append(f"bad note_index {i!r}")
            continue
        if i in by_idx:
            problems.append(f"duplicate note_index {i}")
            continue
        by_idx[i] = raw
    out = []
    for i in range(n_notes):
        raw = by_idx.get(i)
        expl = (raw or {}).get("explanation") or ""
        try:
            if raw is None:
                raise GuardrailError("no interpretation returned for this note")
            t, adj = normalize_one(raw, capacity_kwh)
        except GuardrailError as e:
            problems.append(f"note {i}: {e}")
            t, adj = "no_op", None
            expl = f"Interpretation rejected by guardrails ({e}); treated as no_op."
        out.append({
            "note_index": i,
            "applies": t != "no_op",
            "directive_type": t,
            "structured_adjustment": adj,
            "explanation": str(expl)[:300] or "Interpreted by LLM.",
        })
    return out, problems
```

## app/optimizer.py — linear program
```python
"""24-hour LP. No efficiency loss + no export => exact linear program.
Variables per hour: g (grid), s (solar used), b (net battery: + charge, - discharge)."""
import numpy as np
from scipy.optimize import linprog

N = 24
EPS = 1e-6


class Infeasible(Exception):
    pass


def apply_directives(hours, battery, directives):
    """Directives -> per-hour bounds. This is the ONLY place notes touch the math."""
    solar = [float(h["solar_kwh"]) for h in hours]
    e_min = [float(battery["minimum_energy_kwh"])] * N
    g_cap = [None] * N
    b_lo = [-float(battery["max_discharge_kwh_per_hour"])] * N
    b_hi = [float(battery["max_charge_kwh_per_hour"])] * N
    for d in directives:
        adj, t = d["structured_adjustment"], d["directive_type"]
        if not d["applies"] or not adj:
            continue
        for h in adj["hours"]:
            if t == "solar_reduction":
                solar[h] *= adj["factor"]
            elif t == "minimum_battery_reserve":
                e_min[h] = max(e_min[h], adj["minimum_energy_kwh"])
            elif t == "no_charge_window":
                b_hi[h] = 0.0
            elif t == "no_discharge_window":
                b_lo[h] = 0.0
            elif t == "max_grid_window":
                g_cap[h] = adj["max_grid_kwh"] if g_cap[h] is None else min(g_cap[h], adj["max_grid_kwh"])
    return solar, e_min, g_cap, b_lo, b_hi


def optimize(hours, battery, directives):
    hours = sorted(hours, key=lambda h: h["hour"])
    demand = [float(h["demand_kwh"]) for h in hours]
    tariff = [float(h["tariff_bdt_per_kwh"]) for h in hours]
    solar, e_min, g_cap, b_lo, b_hi = apply_directives(hours, battery, directives)
    e0, cap = float(battery["initial_energy_kwh"]), float(battery["capacity_kwh"])

    # x = [g0..g23, s0..s23, b0..b23]
    c = np.r_[tariff, np.zeros(2 * N)]
    a_eq = np.zeros((N + 1, 3 * N)); b_eq = np.zeros(N + 1)
    for h in range(N):                       # g + s - b = demand
        a_eq[h, h] = 1; a_eq[h, N + h] = 1; a_eq[h, 2 * N + h] = -1
        b_eq[h] = demand[h]
    a_eq[N, 2 * N:] = 1                      # sum(b) = 0  (end-of-day neutrality)
    low = np.tril(np.ones((N, N)))           # cumulative sum of b
    a_ub = np.zeros((2 * N, 3 * N))
    a_ub[:N, 2 * N:] = low                   # e0 + cum(b) <= capacity
    a_ub[N:, 2 * N:] = -low                  # e0 + cum(b) >= e_min[h]
    b_ub = np.r_[[cap - e0] * N, [e0 - m for m in e_min]]
    bounds = ([(0, g_cap[h]) for h in range(N)]
              + [(0, max(solar[h], 0.0)) for h in range(N)]
              + [(b_lo[h], b_hi[h]) for h in range(N)])
    res = linprog(c, A_ub=a_ub, b_ub=b_ub, A_eq=a_eq, b_eq=b_eq, bounds=bounds, method="highs")
    if res.status != 0:
        raise Infeasible(res.message)

    s = [round(max(v, 0.0), 4) for v in res.x[N:2 * N]]
    b = [round(v, 4) for v in res.x[2 * N:]]
    b[-1] = round(b[-1] - sum(b), 4)         # kill rounding drift -> exact neutrality
    plan, e = [], e0
    for h in range(N):
        bh = 0.0 if abs(b[h]) < EPS else b[h]
        sh = min(s[h], solar[h])
        g = demand[h] + bh - sh              # recompute grid from the balance
        if g < 0:                            # rounding edge: curtail solar instead
            sh, g = round(sh + g, 4), 0.0
        e = round(e + bh, 4)
        plan.append({
            "hour": h,
            "grid_kwh": round(g, 4),
            "solar_used_kwh": sh,
            "battery_action": "charge" if bh > 0 else "discharge" if bh < 0 else "idle",
            "battery_kwh": abs(bh),
            "battery_energy_after_kwh": e,
        })
    grid = [p["grid_kwh"] for p in plan]
    return {
        "hourly_plan": plan,
        "total_grid_kwh": round(sum(grid), 4),
        "total_cost_bdt": round(sum(g * t for g, t in zip(grid, tariff)), 4),
        "peak_grid_kwh": round(max(grid), 4),
    }
```

## app/validator.py — judge-style replay
```python
"""Independent replay of the final plan - mirrors what the judge does.
Returns a list of violations; empty list == valid."""
from .optimizer import apply_directives

TOL = 0.01


def replay(hours, battery, directives, result):
    hours = sorted(hours, key=lambda h: h["hour"])
    solar, e_min, g_cap, b_lo, b_hi = apply_directives(hours, battery, directives)
    plan, errs = result["hourly_plan"], []
    if [p["hour"] for p in plan] != list(range(24)):
        return ["hourly_plan must contain hours 0..23 exactly once, in order"]
    e = battery["initial_energy_kwh"]
    for p, hr in zip(plan, hours):
        h, act, bk = p["hour"], p["battery_action"], p["battery_kwh"]
        if min(p["grid_kwh"], p["solar_used_kwh"], bk, p["battery_energy_after_kwh"]) < -TOL:
            errs.append(f"h{h}: negative value")
        if act not in ("charge", "discharge", "idle"):
            errs.append(f"h{h}: bad action"); continue
        if act == "idle" and abs(bk) > TOL:
            errs.append(f"h{h}: idle with non-zero battery_kwh")
        ch = bk if act == "charge" else 0.0
        dis = bk if act == "discharge" else 0.0
        if ch > b_hi[h] + TOL:
            errs.append(f"h{h}: charge limit / no_charge_window violated")
        if dis > -b_lo[h] + TOL:
            errs.append(f"h{h}: discharge limit / no_discharge_window violated")
        if p["solar_used_kwh"] > solar[h] + TOL:
            errs.append(f"h{h}: solar overuse")
        if abs(p["grid_kwh"] + p["solar_used_kwh"] + dis - hr["demand_kwh"] - ch) > TOL:
            errs.append(f"h{h}: energy balance")
        if g_cap[h] is not None and p["grid_kwh"] > g_cap[h] + TOL:
            errs.append(f"h{h}: max_grid_window violated")
        e = e + ch - dis
        if abs(e - p["battery_energy_after_kwh"]) > TOL:
            errs.append(f"h{h}: battery transition mismatch")
        if e < e_min[h] - TOL or e > battery["capacity_kwh"] + TOL:
            errs.append(f"h{h}: battery bounds / reserve violated")
    if abs(e - battery["initial_energy_kwh"]) > TOL:
        errs.append("end-of-day neutrality")
    grid = [p["grid_kwh"] for p in plan]
    cost = sum(g * hr["tariff_bdt_per_kwh"] for g, hr in zip(grid, hours))
    if abs(sum(grid) - result["total_grid_kwh"]) > TOL: errs.append("total_grid_kwh mismatch")
    if abs(cost - result["total_cost_bdt"]) > TOL: errs.append("total_cost_bdt mismatch")
    if abs(max(grid) - result["peak_grid_kwh"]) > TOL: errs.append("peak_grid_kwh mismatch")
    return errs
```

## app/fallback.py — last-resort parser (only when every LLM provider fails)
```python
"""LAST-RESORT parser, used only when every LLM provider fails.
Emits the same intermediate format as the LLM so it goes through the same
guardrails. It is NOT the interpreter - document it as a failure fallback."""
import re

WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
         "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12}
T = r"(noon|midday|midnight|\d{1,2}(?::00)?\s*(?:am|pm)?|" + "|".join(WORDS) + r")"
WINDOW = re.compile(rf"(?:from|between)?\s*{T}\s*(?:until|till|to|and|-|–|through)\s*{T}", re.I)


def _hour(tok, is_end=False, hint_pm=None):
    tok = tok.lower().strip()
    if tok in ("noon", "midday"): return 12
    if tok == "midnight": return 24 if is_end else 0
    m = re.match(r"(\d{1,2}|[a-z]+)(?::00)?\s*(am|pm)?", tok)
    n = WORDS.get(m.group(1)) if not m.group(1).isdigit() else int(m.group(1))
    ap = m.group(2) or hint_pm
    if ap == "pm" and n < 12: n += 12
    if ap == "am" and n == 12: n = 0
    return n


def parse_note(i, note):
    low = note.lower()
    base = {"note_index": i, "explanation": "LLM unavailable; deterministic fallback parser used."}
    m = WINDOW.search(low)
    if not m or re.search(r"next (week|month)|tomorrow|yesterday|last week", low):
        return {**base, "directive_type": "no_op"}
    ap2 = re.search(r"(am|pm)", m.group(2)); ap1 = re.search(r"(am|pm)", m.group(1))
    start = _hour(m.group(1), hint_pm=None if ap1 else (ap2.group(1) if ap2 else None))
    end = _hour(m.group(2), is_end=True)
    base.update(start_hour=start, end_hour=end)
    pct = re.search(r"(\d+(?:\.\d+)?)\s*%", low)
    kwh = re.search(r"(\d+(?:\.\d+)?)\s*kwh", low)
    if re.search(r"solar|pv|panel|rooftop", low):
        if pct: v, k = float(pct.group(1)), ("percent_reduction" if re.search(r"reduc|drop by|less|lose|cut", low) else "percent_remaining")
        elif "half" in low: v, k = 50, "percent_remaining"
        else: return {**base, "directive_type": "no_op"}
        return {**base, "directive_type": "solar_reduction", "value": v, "value_kind": k}
    if re.search(r"grid|import|feeder|transformer|substation|intake", low) and kwh:
        return {**base, "directive_type": "max_grid_window", "value": float(kwh.group(1)), "value_kind": "kwh"}
    if re.search(r"reserve|at least|remain|stored|keep", low) and (kwh or pct):
        if kwh: return {**base, "directive_type": "minimum_battery_reserve", "value": float(kwh.group(1)), "value_kind": "kwh"}
        return {**base, "directive_type": "minimum_battery_reserve", "value": float(pct.group(1)), "value_kind": "percent_of_capacity"}
    if re.search(r"discharg", low):
        return {**base, "directive_type": "no_discharge_window", "value": None, "value_kind": "none"}
    if re.search(r"charg", low):
        return {**base, "directive_type": "no_charge_window", "value": None, "value_kind": "none"}
    return {**base, "directive_type": "no_op"}


def parse_notes(notes):
    return [parse_note(i, n) for i, n in enumerate(notes)]
```

## app/main.py — API layer
FastAPI's automatic body parsing returns 422 for bad JSON; the spec wants **400**, so the body is parsed manually here.
```python
import json, logging, os
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from .schemas import Scenario
from .interpreter import interpret
from .optimizer import optimize, Infeasible
from .validator import replay

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("gridwise")
app = FastAPI(title="GridWise", docs_url=None, redoc_url=None)


def err(code, msg):
    return JSONResponse(status_code=code, content={"error": msg})


@app.get("/health")
async def health():
    return {"status": "ok"}          # must work with NO api key configured


@app.post("/optimize-energy")
async def optimize_energy(request: Request):
    try:
        body = json.loads(await request.body())
        if not isinstance(body, dict):
            raise ValueError
    except Exception:
        return err(400, "Malformed JSON body")
    try:
        sc = Scenario.model_validate(body)
    except ValidationError as e:
        first = e.errors()[0]
        return err(400, f"Invalid request: {'.'.join(map(str, first['loc']))}: {first['msg']}")

    hours = [h.model_dump() for h in sc.hours]
    battery = sc.battery.model_dump()
    try:
        directives = await interpret(sc.operator_notes, battery["capacity_kwh"])
        result = optimize(hours, battery, directives)
        violations = replay(hours, battery, directives, result)
        if violations:                                   # should never happen
            log.error("replay failed %s: %s", sc.scenario_id, violations[:3])
            return err(500, "Internal validation failed")
    except Infeasible:
        return err(422, "Scenario is infeasible under the interpreted directives")
    except Exception as e:
        log.exception("unhandled: %s", type(e).__name__)
        return err(500, "Internal error")

    active = [d["directive_type"] for d in directives if d["applies"]]
    summary = (f"Applied {len(active)} operator directive(s) ({', '.join(active) or 'none'}); "
               f"battery charges in low-tariff/solar-surplus hours and discharges in high-tariff hours, "
               f"returning to its initial energy. Total cost {result['total_cost_bdt']:.2f} BDT.")
    return {"scenario_id": sc.scenario_id, "directive_interpretation": directives,
            **result, "plan_summary": summary}
```

## tests/run_samples.py — public-sample runner (replays against ground truth like the judge)
```python
"""Usage: python tests/run_samples.py http://localhost:8000 path/to/Public_Sample_Cases.json"""
import json, sys, time, httpx
sys.path.insert(0, ".")
from app.validator import replay

base, path = sys.argv[1].rstrip("/"), sys.argv[2]
cases, passed, lat = json.load(open(path))["cases"], 0, []
print("health:", httpx.get(f"{base}/health", timeout=10).json())
for c in cases:
    exp, t0 = c["expected_output"], time.time()
    r = httpx.post(f"{base}/optimize-energy", json=c["input"], timeout=30)
    lat.append(time.time() - t0)
    if r.status_code != 200:
        print(c["id"], "HTTP", r.status_code, r.text[:200]); continue
    got = r.json()
    interp_ok = all((g["applies"], g["directive_type"], g["structured_adjustment"]) ==
                    (e["applies"], e["directive_type"], e["structured_adjustment"])
                    for g, e in zip(got["directive_interpretation"], exp["directive_interpretation"])) \
        and len(got["directive_interpretation"]) == len(exp["directive_interpretation"])
    # replay against GROUND TRUTH directives, exactly like the judge
    errs = replay(c["input"]["hours"], c["input"]["battery"], exp["directive_interpretation"], got)
    ratio = min(1, exp["total_cost_bdt"] / got["total_cost_bdt"]) if got["total_cost_bdt"] else 1
    ok = interp_ok and not errs and ratio > 0.9999
    passed += ok
    print(f"{c['id']} interp={'OK' if interp_ok else 'FAIL'} valid={'OK' if not errs else errs[:2]} "
          f"cost_ratio={ratio:.4f} {lat[-1]:.2f}s")
lat.sort()
print(f"\n{passed}/{len(cases)} passed | p95 latency {lat[int(len(lat) * 0.95) - 1]:.2f}s")
```

## Dockerfile
```dockerfile
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PORT=8000
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
RUN useradd -m runner && chown -R runner /srv
USER runner
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=5s \
  CMD python -c "import urllib.request,os;urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/health')"
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 2"]
```

## .env.example  (commit this, never `.env`)
```bash
LLM_BASE_URL=https://api.your-provider.com/v1
LLM_MODEL=your-fast-model-id
LLM_API_KEY=
BACKUP_LLM_BASE_URL=
BACKUP_LLM_MODEL=
BACKUP_LLM_API_KEY=
PORT=8000
LOG_LEVEL=INFO
```

## .gitignore / .dockerignore
```text
.env
__pycache__/
*.pyc
.venv/
.git/
```
