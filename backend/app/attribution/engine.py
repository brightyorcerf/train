"""Attribution (§10): bounded BFS -> every candidate endpoint -> per-VASP evidence -> ranked
VASPs with a confidence index -> one crowned primary target, or an honest abstention.

The difference from a plain trace: the BFS does NOT stop at the first labeled endpoint
(`collect_all=True`), so ranking sees every VASP the funds could have reached inside the budget.
That costs calls, which is why the trace CLI still stops at the first hit — attribution is the
deliberate, more expensive question.

Nothing here re-derives the two axes into one number (§3): `nearest` (hops) and `score` (evidence)
travel side by side all the way to the report.
"""
import time

from app.attribution import aggregate, recommend
from app.scoring.weights import FROZEN_AT, WEIGHTS, weight_hash
from app.trace.engine import Tracer


def attribute(wallet: str, chain: str, since_block=0, until_block=10**9, max_hops=4, fanout=5,
              label_set=None, sink=None, offline=False, conn=None, weights: dict | None = None,
              collect_all=True, tracer: Tracer | None = None) -> dict:
    t0 = time.time()
    t = tracer or Tracer(chain, until_block, label_set, fanout, offline=offline, conn=conn)
    trace = t.run(wallet, since_block, max_hops, sink=sink, collect_all=collect_all)
    return attribute_result(trace, weights, round(time.time() - t0, 1))


def attribute_result(trace: dict, weights: dict | None = None, wall: float | None = None) -> dict:
    """The pure half: a finished trace -> ranked VASPs + recommendation. No I/O, so the Celery
    driver (trace/tasks.py) and the eval harness can call it on a stored trace result."""
    rows = aggregate.by_entity(trace.get("candidates", []), trace.get("flags", ()), weights)
    rec = recommend.recommend(rows)
    state = ("ATTRIBUTED" if rec["recommended"] else
             "AMBIGUOUS" if rec["ambiguous"] else trace.get("result", "UNATTRIBUTED"))
    return {**trace, "state": state, "vasp_candidates": rec["ranked"],
            "recommended": rec["recommended"], "separation": rec["separation"],
            "separation_pts": rec["separation_pts"], "ambiguous": rec["ambiguous"],
            "rationale": rec["rationale"],
            "nearest": rec["ranked"][0]["nearest"] if rec["ranked"] else None,
            "pins": {**trace.get("pins", {}), "snapshot_block": trace.get("until_block"),
                     "label_set_version": trace.get("label_set_version"),
                     "weight_hash": weight_hash(weights or WEIGHTS), "weights_frozen_at": FROZEN_AT},
            "attribution_wall_s": wall if wall is not None else trace.get("wall_clock_s")}


def _selfcheck():
    """Pure-half check on a hand-built trace dict: ranking, abstention and the empty case."""
    from app.labels.registry import DEPOSIT
    day = 86400

    def cand(entity, endpoint, hops, tier, basis=DEPOSIT and "sweep_proven", value=5.0):
        return {"entity": entity, "entity_name": entity.title(), "entity_sahyog": "unknown",
                "endpoint": endpoint, "result": "ATTRIBUTED", "hops": hops, "role": "deposit",
                "basis": basis, "role_basis": basis, "tier": tier, "label_confidence": 0.5,
                "label_source": "x", "value": value, "asset": "BTC", "ts": [100, 100 + day],
                "path": [{"from": "s", "to": endpoint, "tx": f"t{endpoint}"}], "deposit_event": None,
                "sweep": None}

    tr = {"result": "ATTRIBUTED", "flags": [], "until_block": 9, "label_set_version": "ls-test",
          "candidates": [cand("binance", "B1", 4, "curated", "exchange_published_deposit"),
                         cand("kraken", "K1", 1, "heuristic", "cluster_propagated")]}
    r = attribute_result(tr)
    assert r["recommended"] == "binance" and r["state"] == "ATTRIBUTED"
    assert r["nearest"]["hops"] == 4 and r["separation"] == "HIGH"
    assert r["pins"]["weight_hash"].startswith("w-") and r["pins"]["snapshot_block"] == 9
    # the closer, weaker endpoint is still reported — ranked, not hidden
    assert [c["entity"] for c in r["vasp_candidates"]] == ["binance", "kraken"]
    # nothing reached -> honest non-answer, and the trace's own verdict survives
    empty = attribute_result({"result": "UNATTRIBUTED", "candidates": [], "flags": []})
    assert empty["state"] == "UNATTRIBUTED" and empty["recommended"] is None and empty["nearest"] is None


if __name__ == "__main__":
    _selfcheck()
    print("attribution engine selfcheck PASS")
