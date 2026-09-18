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
