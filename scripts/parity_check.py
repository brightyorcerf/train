"""Driver parity gate: the distributed Celery driver must decide every golden case exactly as the
sequential reference the eval harness scores — same state, same crowned VASP, same call count.

The two drivers share Tracer.expand_node but not their bookkeeping, and they drifted once: the chord
driver re-counted reads a cold per-task memo could not see and stamped hits with the level-END
total, so Li Jiadong crowned Binance live while the harness abstained (2026-09-26). This is the
check that would have caught it. It traces for real through the workers (force=true), replaying
from the raw store.

    docker compose run --rm -e PYTHONPATH=/app \
        -v "$PWD/scripts:/repo/scripts:ro" api python /repo/scripts/parity_check.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from fastapi.testclient import TestClient  # noqa: E402

from app.eval import harness  # noqa: E402
from app.main import app  # noqa: E402

api = TestClient(app)
bench = {c["id"]: c for c in api.get("/benchmark").json()["cases"]}
bad = 0
for g in harness.cases():
    ref = bench[g["id"]]
    tid = api.post("/cases", json={"wallets": [g["suspect_addr"]], "chain": g["chain"], "force": True,
                                   "snapshot_block": ref["snapshot"]}).json()["trace_ids"][0]
    t0 = time.time()
    while api.get(f"/trace/{tid}/status").json()["state"] not in ("DONE", "FAILED"):
        if time.time() - t0 > 300:
            break
        time.sleep(0.5)
    r = api.get(f"/trace/{tid}").json()
    live = (r["recommended"], r["api_calls"])
    want = (ref["recommended"], ref["calls"])
    ok = live == want
    bad += not ok
    print(f"[{'PASS' if ok else 'FAIL'}] {g['id']:<32} live {live}  harness {want}  {r['wall_clock_s']}s")
print(f"\n{len(bench) - bad} of {len(bench)} golden cases: live driver == sequential reference")
sys.exit(1 if bad else 0)
