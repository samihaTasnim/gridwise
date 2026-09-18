"""Run every public sample case and verify the endpoint output against the
reference expected_output in the official sample pack (test-cases.json).

Usage:
    python tests/run_test_cases.py [--cases PATH] [--base-url URL] [--strict]

Default behaviour:
    * In-process FastAPI TestClient (no live server needed).
    * Judges each case exactly like the official judge:
        - directive_interpretation must match the reference machine-checkable
          semantics (note_index, applies, directive_type, structured_adjustment).
        - the returned hourly_plan is replayed against the REFERENCE (ground
          truth) directives -> zero violations (energy balance, battery limits,
          solar cap, end-of-day neutrality, totals recalculated from the plan).
        - total_grid_kwh and total_cost_bdt must match the reference within the
          official 0.01 tolerance; the plan must be no more expensive than the
          reference optimum.

--strict additionally requires peak_grid_kwh and the hour-by-hour plan to match
the reference within 0.01. The sample pack itself states the hourly action
sequence does not need to match byte-for-byte (equivalent optimal schedules are
accepted), so --strict is opt-in and stricter than the judge.
"""
import argparse
import json
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.validator import replay

TOL = 0.01
COUNT = ("note_index", "applies", "directive_type")


def load_cases(path):
    data = json.load(open(path))
    return data.get("_meta", {}), data.get("cases", [])


def directives_match(got, exp):
    if len(got) != len(exp):
        return False, f"expected {len(exp)} directives, got {len(got)}"
    for g, e in zip(got, exp):
        for k in COUNT:
            if g.get(k) != e.get(k):
                return False, f"directive {e.get('note_index')} {k}: got {g.get(k)}, expected {e.get(k)}"
        if g.get("structured_adjustment") != e.get("structured_adjustment"):
            return False, (f"directive {e.get('note_index')} structured_adjustment differs: "
                           f"got {g.get('structured_adjustment')}, expected {e.get('structured_adjustment')}")
    return True, ""


def plan_diffs(got, exp, tol=TOL):
    diffs = []
    for g, e in zip(got, exp):
        for k in ("grid_kwh", "solar_used_kwh", "battery_kwh", "battery_energy_after_kwh"):
            if abs(float(g[k]) - float(e[k])) > tol:
                diffs.append(f"h{g['hour']} {k}: got {g[k]} vs expected {e[k]}")
        if g.get("battery_action") != e.get("battery_action"):
            diffs.append(f"h{g['hour']} battery_action: got {g.get('battery_action')} vs "
                         f"expected {e.get('battery_action')}")
    return diffs


def run_case(client, case, base_url, strict):
    inp, exp = case["input"], case["expected_output"]
    checks, t0 = {}, time.time()

    if client is not None:
        r = client.post("/optimize-energy", json=inp)
    else:
        import httpx
        r = httpx.post(f"{base_url.rstrip('/')}/optimize-energy", json=inp, timeout=30)
    checks["http"] = (r.status_code == 200, f"HTTP {r.status_code}")
    if r.status_code != 200:
        checks["error_body"] = (False, r.text[:300])
        return checks, time.time() - t0, False
    got = r.json()

    checks["scenario_id"] = (got.get("scenario_id") == exp.get("scenario_id"),
                             f"got {got.get('scenario_id')!r}, expected {exp.get('scenario_id')!r}")

    ok, why = directives_match(got.get("directive_interpretation", []),
                               exp.get("directive_interpretation", []))
    checks["directives"] = (ok, why or "exact match")

    errs = replay(inp["hours"], inp["battery"], exp["directive_interpretation"], got)
    checks["replay_vs_reference_directives"] = (not errs, "; ".join(errs) or "no violations")

    errs_self = replay(inp["hours"], inp["battery"], got.get("directive_interpretation", []), got)
    checks["replay_self_consistent"] = (not errs_self, "; ".join(errs_self) or "no violations")

    for k in ("total_grid_kwh", "total_cost_bdt"):
        checks[k] = (abs(float(got.get(k, 0)) - float(exp.get(k, 0))) <= TOL,
                     f"got {got.get(k)}, expected {exp.get(k)}")

    overprice = float(got.get("total_cost_bdt", 0)) > float(exp.get("total_cost_bdt", 0)) + TOL
    checks["not_more_expensive"] = (not overprice,
                                    f"got {got.get('total_cost_bdt')} vs reference optimum "
                                    f"{exp.get('total_cost_bdt')}")

    peak_match = abs(float(got.get("peak_grid_kwh", 0)) - float(exp.get("peak_grid_kwh", 0))) <= TOL
    checks["peak_grid_kwh"] = (peak_match,
                               f"got {got.get('peak_grid_kwh')}, expected {exp.get('peak_grid_kwh')}")

    pd = plan_diffs(got.get("hourly_plan", []), exp.get("hourly_plan", []))
    checks["hourly_plan"] = (not pd, "exact within tolerance" if not pd else f"{len(pd)} diff(s)")

    checks["plan_summary"] = (bool(got.get("plan_summary")), "present" if got.get("plan_summary") else "missing")

    judge_ok = (checks["http"][0] and checks["scenario_id"][0] and checks["directives"][0]
                and checks["replay_vs_reference_directives"][0]
                and checks["replay_self_consistent"][0] and checks["not_more_expensive"][0]
                and checks["total_grid_kwh"][0] and checks["total_cost_bdt"][0])
    passed = judge_ok and (not strict or (checks["peak_grid_kwh"][0] and checks["hourly_plan"][0]))
    return checks, time.time() - t0, passed


def main():
    ap = argparse.ArgumentParser(description="Run all public GridWise sample cases and compare to reference output.")
    ap.add_argument("--cases", default="test-cases.json", help="path to the sample cases JSON (default: test-cases.json)")
    ap.add_argument("--base-url", default=None, help="POST to a live server instead of using the in-process TestClient")
    ap.add_argument("--strict", action="store_true", help="also require peak_grid_kwh and hour-by-hour plan to match the reference")
    args = ap.parse_args()

    logging.disable(logging.INFO)
    meta, cases = load_cases(args.cases)
    print(f"{meta.get('title', 'GridWise sample cases')} "
          f"({meta.get('case_count', len(cases))} cases)")
    if args.base_url:
        print(f"target: {args.base_url}  |  strict={args.strict}")
        client = None
    else:
        from fastapi.testclient import TestClient
        from app.main import app
        client = TestClient(app)
        print("target: in-process TestClient  |  strict=" + str(args.strict))

    passed, lats = 0, []
    for c in cases:
        checks, lat, ok = run_case(client, c, args.base_url, args.strict)
        lats.append(lat)
        passed += int(ok)
        print(f"\n[{c['id']}] {c['label']}  {'PASS' if ok else 'FAIL'}  ({lat:.2f}s)")
        for name, (good, why) in checks.items():
            print(f"   {'[OK] ' if good else '[WARN]' if name in ('peak_grid_kwh', 'hourly_plan') else '[FAIL]'} {name}: {why}")

    lats.sort()
    total = len(cases)
    p95 = lats[int(total * 0.95) - 1] if total else 0.0
    print(f"\n{passed}/{total} passed | p95 latency {p95:.2f}s")
    if args.strict:
        print("NOTE: strict mode also requires peak/hourly_plan equality. The official")
        print("      judge accepts any equivalent optimal schedule, so strict mismatches")
        print("      on peak or per-hour actions do not reduce the score.")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())