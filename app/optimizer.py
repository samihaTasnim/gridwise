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
