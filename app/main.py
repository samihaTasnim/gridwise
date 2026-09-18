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
