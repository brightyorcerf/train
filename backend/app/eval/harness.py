"""Evaluation harness (§11.2) — the honest metric.

    python -m app.eval.harness              # offline, from the §12 raw store (the reproducibility run)
    python -m app.eval.harness --online     # allow live fetches (populates the store for later)
    python -m app.eval.harness --cases a,b  # a subset

Three rules this file exists to enforce:

1. **Never a percentage.** The result ships as "the correct VASP ranked #1 in M of N cases", verbatim.
   N is ~6 here; a percentage on n=6 is false precision, and a "93% accurate" claim is exactly the
   kind of number a judge should attack.
2. **Weights are frozen before the run** (scoring/weights.py, FROZEN_AT). The harness reads them; it
   never tunes them. If they are ever changed after seeing these results, that is train-on-test and
   must be reported as such.
3. **Rank stability is the defensible number.** Each case is re-scored under perturbed weight
   profiles (±20%, deterministic seeds) WITHOUT re-tracing, and we report in how many profiles the
   #1 entity is unchanged. That answers "is this calibrated?" far better than the raw count.

A case that cannot be served from the store is reported as STORE_MISS — never silently re-fetched,
and never counted as a pass.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

import yaml

from app.attribution.engine import attribute_result
from app.scoring.weights import FROZEN_AT, SEPARATION_TAU, WEIGHTS, perturb, weight_hash
from app.trace.engine import Tracer

REPO = Path(os.environ.get("REPO_ROOT") or Path(__file__).resolve().parents[3])
GOLDEN = REPO / "labels" / "golden_set.yaml"
SNAPSHOT = {"btc": 966553, "eth": 25906777, "polygon": 93439913}
N_PROFILES = 20


def cases(only=()) -> list[dict]:
    rows = yaml.safe_load(GOLDEN.read_text()) or []
    return [c for c in rows if not only or c["id"] in only]


def run_case(c: dict, offline=True, max_hops=5, fanout=5) -> dict:
    """One golden case -> trace + attribution + telemetry. No scoring decisions here."""
    chain = c["chain"]
    until = c.get("until_block") or SNAPSHOT[chain]
    t0 = time.time()
    try:
        t = Tracer(chain, until, fanout=fanout, offline=offline)
        trace = t.run(c["suspect_addr"], max_hops=min(max_hops, c.get("max_hops", max_hops)),
                      collect_all=True)
    except Exception as e:                               # noqa: BLE001
        return {"id": c["id"], "state": "STORE_MISS" if offline else "ERROR", "error": str(e)[:160],
                "wall_s": round(time.time() - t0, 1)}
    r = attribute_result(trace)
    return {"id": c["id"], "chain": chain, "expect": c["expect"], "expected_entity": c.get("expected_entity"),
            "state": r["state"], "recommended": r["recommended"], "ambiguous": r["ambiguous"],
            "ranked": [(v["entity"], v["score"]) for v in r["vasp_candidates"]],
            "hops": r["nearest"]["hops"] if r["nearest"] else None, "min_hops": c.get("min_hops"),
            "endpoint": r["nearest"]["endpoint"] if r["nearest"] else None,
            "expected_endpoint": c.get("endpoint"),
            "calls": trace["api_calls"], "upstream": trace["upstream_calls"],
            "store_hits": trace["store_hits"], "wall_s": round(time.time() - t0, 1),
            "flags": trace["flags"][:3], "_trace": trace}


def verdict(r: dict) -> str:
    """CORRECT / WRONG / ABSTAINED / MISSED — scored against what the case documents."""
    if r["state"] in ("STORE_MISS", "ERROR"):
        return r["state"]
    if r["expect"] == "UNATTRIBUTED":
        # a confuser: crowning anyone is the failure mode being tested
        return "CORRECT" if r["recommended"] is None else "WRONG"
    if r["recommended"] == r["expected_entity"]:
        return "CORRECT"
    return "ABSTAINED" if r["ambiguous"] else ("WRONG" if r["recommended"] else "MISSED")


def stability(r: dict, n=N_PROFILES) -> tuple[int, int]:
    """Re-score the SAME trace under n perturbed (±20%) weight profiles. -> (unchanged, n)."""
    if "_trace" not in r or r["state"] in ("STORE_MISS", "ERROR"):
        return 0, 0
    base = r["recommended"]
    same = 0
    for seed in range(n):
        alt = attribute_result(r["_trace"], weights=perturb(0.2, seed=seed))
        same += alt["recommended"] == base
    return same, n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--online", action="store_true", help="allow live provider calls (default: store only)")
    ap.add_argument("--cases", default="")
    ap.add_argument("--max-hops", type=int, default=5)
    ap.add_argument("--json", default="")
    a = ap.parse_args()

    rows = cases(tuple(x for x in a.cases.split(",") if x))
    print(f"golden set: {len(rows)} cases · weights {weight_hash()} FROZEN {FROZEN_AT} · "
          f"tau {SEPARATION_TAU} · {'ONLINE' if a.online else 'OFFLINE (raw store only)'}\n")
    out, t0 = [], time.time()
    for c in rows:
        r = run_case(c, offline=not a.online, max_hops=a.max_hops)
        v = verdict(r)
        same, n = stability(r)
        r["verdict"], r["stable"] = v, f"{same}/{n}"
        out.append(r)
        print(f"[{v:<10}] {r['id']:<32} {r['state']:<13} rec={str(r['recommended']):<12} "
              f"hops={r['hops']} calls={r.get('calls')} ({r.get('upstream')} upstream) "
              f"{r['wall_s']}s  rank-stable {same}/{n}")
        if v in ("WRONG", "MISSED"):
            print(f"             expected {r['expected_entity']} @ {str(r['expected_endpoint'])[:16]}… "
                  f"got {r['ranked']}")
        if r["state"] in ("STORE_MISS", "ERROR"):
            print(f"             {r.get('error')}")

    scored = [r for r in out if r["state"] not in ("STORE_MISS", "ERROR")]
    correct = [r for r in scored if r["verdict"] == "CORRECT"]
    discovery = [r for r in scored if r["expect"] == "ATTRIBUTED"]
    disc_ok = [r for r in discovery if r["verdict"] == "CORRECT"]
    st = [stability(r) for r in scored]
    stable = sum(s for s, _ in st)
    total_p = sum(n for _, n in st)

    print(f"\n{'=' * 78}")
    print(f"the correct VASP ranked #1 in {len(disc_ok)} of {len(discovery)} discovery cases")
    print(f"all cases (discovery + confusers) decided as documented: {len(correct)} of {len(scored)}")
    if len(out) != len(scored):
        print(f"NOT SCORED: {len(out) - len(scored)} case(s) could not be served "
              f"({'/'.join(sorted({r['state'] for r in out if r not in scored}))})")
    print(f"rank stability under +/-20% weight perturbation: top-1 unchanged in {stable} of {total_p} "
          f"case-profiles ({N_PROFILES} profiles x {len(scored)} cases)")
    print(f"telemetry: {sum(r.get('calls', 0) for r in scored)} logical requests, "
          f"{sum(r.get('upstream', 0) for r in scored)} upstream, {time.time() - t0:.1f}s total")
    print("n is small — treat these as a held-out smoke test, not an accuracy figure. Never a percentage.")
    if a.json:
        Path(a.json).write_text(json.dumps([{k: v for k, v in r.items() if k != "_trace"} for r in out],
                                           indent=2, default=str))
        print(f"-> {a.json}")
    sys.exit(0 if len(scored) == len(out) and not [r for r in scored if r["verdict"] in ("WRONG",)] else 1)


if __name__ == "__main__":
    main()
