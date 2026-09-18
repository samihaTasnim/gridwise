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
