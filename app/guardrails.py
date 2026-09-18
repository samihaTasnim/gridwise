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
