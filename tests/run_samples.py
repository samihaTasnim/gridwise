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
