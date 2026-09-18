"""LLM interpretation step. Works with any OpenAI-compatible chat endpoint
(set LLM_BASE_URL / LLM_MODEL / LLM_API_KEY). One call per request, all notes."""
import json, os, hashlib, logging
import httpx
from .guardrails import normalize_all
from .fallback import parse_notes

log = logging.getLogger("gridwise.llm")
_cache: dict = {}

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
