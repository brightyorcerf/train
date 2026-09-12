"""Day-6 real-data PASS/FAIL: BFS as Celery chords, the global rate limiter, 429 -> cache,
snapshot/label-set pinning, and idempotency (§9.2, §8, §12).

Needs the stack up:
    docker compose up -d postgres redis neo4j worker
    backend/.venv/bin/python scripts/day6_celery_check.py
"""
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.core.ratelimit import LIMITS, RateLimiter  # noqa: E402
from app.db import connect, init_schema  # noqa: E402
from app.trace.engine import trace  # noqa: E402
from app.trace.tasks import job, start_trace  # noqa: E402
from worker.celery_app import app  # noqa: E402

ZHDANOVA, BTC_SNAPSHOT = "1Ljk8RNNabkZ9bfDYQBn98XfFozJhTjqcZ", 966553
SAME = ("result", "endpoint", "hops", "entity", "role_basis", "label_source", "addresses_seen")
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  {detail}")


def wait(trace_id, timeout=600) -> dict:
    seen, t0 = [], time.time()
    while time.time() - t0 < timeout:
        j = job(trace_id)
        if j.get("state") and (not seen or seen[-1] != j["state"]):
            seen.append(j["state"])
        if j.get("state") in ("DONE", "FAILED"):
            return {**j, "states": seen}
        time.sleep(1)
    return {"state": "TIMEOUT", "states": seen}


def main():
    t0 = time.time()
    init_schema()
    if not app.control.ping(timeout=5):
        sys.exit("no Celery worker answered ping — start it first:  docker compose up -d worker\n"
                 "(STOP, per the day's rules: the chord path cannot be verified without a real worker)")

    # ---- 1. the same trace through Celery chords and through the sequential engine ----
    seq = trace(ZHDANOVA, "btc", until_block=BTC_SNAPSHOT, max_hops=3)
    ids = start_trace(ZHDANOVA, "btc", BTC_SNAPSHOT, max_hops=3)
    j = wait(ids["trace_id"])
    cel = j.get("result") or {}
    check("Celery chord BFS reaches the same verdict as the sequential engine",
          j["state"] == "DONE" and all(cel.get(k) == seq.get(k) for k in SAME),
          f"{cel.get('result')} {str(cel.get('endpoint'))[:14]}… hops={cel.get('hops')} "
          f"(sequential: {seq['result']} hops={seq['hops']})")
    check("…including the path that carried the funds (per-UTXO provenance)",
          [p["tx"] for p in cel.get("path", [])] == [p["tx"] for p in seq.get("path", [])],
          f"{len(cel.get('path', []))} moves")
    check("job state machine recorded: QUEUED/FETCHING -> DONE (§12)",
          j["states"][-1] == "DONE" and "FETCHING" in j["states"], f"{j['states']}")
    check("case pins recorded: block snapshot + label-set version (§12)",
          cel.get("pins", {}).get("snapshot_block") == BTC_SNAPSHOT
          and cel["pins"]["label_set_version"] == cel.get("label_set_version"), f"{cel.get('pins')}")

    with connect() as c:
        n_audit = c.execute("SELECT count(*) FROM audit_log WHERE trace_id = %s", (ids["trace_id"],)).fetchone()[0]
        n_hash = c.execute("SELECT count(DISTINCT upstream_hash) FROM audit_log WHERE trace_id = %s",
                           (ids["trace_id"],)).fetchone()[0]
        n_edge = c.execute("SELECT count(*) FROM edge").fetchone()[0]
    check("every expansion appended an audit_log row with an upstream hash (§12)", n_audit > 0 and n_hash > 0,
          f"{n_audit} rows, {n_hash} distinct upstream hashes")

    # ---- 2. idempotency: the same case again writes nothing new ----
    ids2 = start_trace(ZHDANOVA, "btc", BTC_SNAPSHOT, max_hops=3)
    j2 = wait(ids2["trace_id"])
    with connect() as c:
        n_edge2 = c.execute("SELECT count(*) FROM edge").fetchone()[0]
    check("re-running the case is idempotent (no new edges) and gives the same result",
          n_edge2 == n_edge and all((j2.get("result") or {}).get(k) == cel.get(k) for k in SAME),
          f"{n_edge} -> {n_edge2} edge rows")

    # ---- 3. the global rate limiter actually binds under concurrency ----
    rl = RateLimiter(prefix="rlcheck")
    rl.r.delete("rlcheck:etherscan")
    rate, cap = LIMITS["etherscan"]
    n, t = 12, time.time()
    threads = [threading.Thread(target=lambda: [rl.acquire("etherscan") for _ in range(n // 4)])
               for _ in range(4)]
    [th.start() for th in threads]
    [th.join() for th in threads]
    took, floor = time.time() - t, (n - cap) / rate
    check("Redis token bucket binds 4 concurrent workers to one provider rate (§8)",
          took >= floor * 0.9, f"{n} permits across 4 threads took {took:.1f}s (>= {floor:.1f}s at {rate}/s)")
    check("daily free-quota counter is tracked (Etherscan 100k/day)",
          rl.used_today("etherscan") >= 0, f"{rl.used_today('etherscan')} calls today")

    # ---- 4. provider down / 429 -> serve from the store, mark the result partial (§8) ----
    from app.providers.esplora import EsploraProvider  # noqa: PLC0415
    from app.trace.engine import Tracer  # noqa: PLC0415
    dead = EsploraProvider(bases=["http://127.0.0.1:9/api"], snapshot=BTC_SNAPSHOT + 1, cooldown=0.1)
    t_dead = Tracer("btc", BTC_SNAPSHOT + 1, prov=dead)          # +1: volatile reads miss their scope
    degraded = t_dead.run(ZHDANOVA, max_hops=2)
    check("every provider down -> stale store answers, result marked partial, no crash (§8)",
          degraded["partial"] and degraded["stale_reads"] and degraded["result"] == seq["result"],
          f"{degraded['result']}, {len(dead.stale)} stale reads, e.g. {degraded['stale_reads'][:1]}")

    # ---- 5. offline replay from the store alone (§11.2 offline fixture mode) ----
    off = trace(ZHDANOVA, "btc", until_block=BTC_SNAPSHOT, max_hops=3, offline=True)
    check("offline replay from the raw store reproduces the result with zero upstream calls",
          all(off.get(k) == seq.get(k) for k in SAME) and off["upstream_calls"] == 0,
          f"{off['result']} · {off['api_calls']} logical requests, {off['upstream_calls']} upstream")

    n_ok = sum(results)
    print(f"\n{n_ok}/{len(results)} checks ok · {time.time() - t0:.1f}s")
    print("OVERALL: PASS" if n_ok == len(results) else "OVERALL: FAIL")
    sys.exit(0 if n_ok == len(results) else 1)


if __name__ == "__main__":
    main()
