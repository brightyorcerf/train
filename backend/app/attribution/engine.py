"""Attribution (§10): bounded BFS -> every candidate endpoint -> per-VASP evidence -> ranked
VASPs with a confidence index -> one crowned primary target, or an honest abstention.

The difference from a plain trace: the BFS does NOT stop at the first labeled endpoint
(`collect_all=True`), so ranking sees every VASP the funds could have reached inside the budget.
That costs calls, which is why the trace CLI still stops at the first hit — attribution is the
deliberate, more expensive question.

Nothing here re-derives the two axes into one number (§3): `nearest` (hops) and `score` (evidence)
travel side by side all the way to the report.
"""
import re
import time

from app.attribution import aggregate, recommend
from app.scoring.weights import FROZEN_AT, WEIGHTS, weight_hash
from app.trace.engine import Tracer

# §9.3 boundary states. The engine used to collapse every no-candidate outcome into UNATTRIBUTED,
# which says "we reached N hops and found nothing" — a different and weaker claim than "the funds
# entered a mixer and the trail ends there, by design". A CoinJoin boundary stays UNATTRIBUTED:
# §9.3 defines no state for it and the coinjoin flag is a clustering caveat, not a service node.
_BOUNDARY_STATE = {"mixer": "BROKEN_AT_MIXER", "bridge": "BROKEN_AT_BRIDGE", "dex": "BROKEN_AT_DEX"}


def _no_candidate_state(trace: dict) -> str:
    """UNATTRIBUTED, a §9.3 BROKEN_AT_*, or INCOMPLETE when the data never arrived."""
    flags = trace.get("flags") or []
    # An affirmative finding wins: "the funds entered a known mixer at hop 2" is a result, even if
    # some unrelated branch was also short of data (`partial` still rides along on the result).
    hops = {}
    for f in flags:
        m = re.match(r"^(mixer|bridge|dex):[^@]+@[^(]+\(hop (\d+)", str(f))
        if m:
            hops.setdefault(int(m.group(2)), _BOUNDARY_STATE[m.group(1)])
    if hops:
        return hops[min(hops)]          # the boundary the funds hit first
    if trace.get("partial") or any(str(f).startswith("partial:") for f in flags):
        # Provider failure (or an offline store miss) is not abstention. Calling it UNATTRIBUTED
        # reads as "we looked and there was nothing", when the truth is that part of the graph was
        # never fetched — which is how a golden confuser passed on a store miss at hop 0.
        return "INCOMPLETE"
    return trace.get("result", "UNATTRIBUTED")


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
    crowned = next((r for r in rec["ranked"] if r["entity"] == rec["recommended"]), None)
    if crowned:
        # §6.3: reaching a VASP's hot/infra wallet is attribution to reachable infrastructure, NOT
        # to a customer deposit address. The crowned row keeps that distinction, so the state must
        # too — promoting it to ATTRIBUTED is exactly the silent upgrade §6.3 forbids.
        state = "ATTRIBUTED" if crowned.get("best_claim") == "ATTRIBUTED" else "ATTRIBUTED_INFRA"
    elif rec["ambiguous"]:
        state = "AMBIGUOUS"
    elif rec["ranked"]:
        state = "REPORTED_NOT_CROWNED"      # below the MIN_CROWN floor — candidate shown, not named
    else:
        state = _no_candidate_state(trace)
    return {**trace, "state": state, "vasp_candidates": rec["ranked"],
            "recommended": rec["recommended"], "separation": rec["separation"],
            "separation_pts": rec["separation_pts"], "ambiguous": rec["ambiguous"],
            "below_floor": rec["below_floor"], "rationale": rec["rationale"],
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
    # a hot/infra-only hit stays downgraded (§6.3) instead of being promoted to ATTRIBUTED
    infra = cand("someexchange", "H1", 2, "curated", "labeled")
    infra |= {"role": "hot", "result": "ATTRIBUTED_INFRA"}
    ri = attribute_result({"result": "ATTRIBUTED_INFRA", "flags": [], "candidates": [infra]})
    assert ri["state"] == "ATTRIBUTED_INFRA" and ri["recommended"] == "someexchange", ri["state"]
    # a lone weak candidate is listed but not crowned, and there is no gap to call "HIGH"
    weak = cand("weakco", "W1", 3, "heuristic", "cluster_propagated", value=0.00001)
    rw = attribute_result({"result": "ATTRIBUTED", "flags": [], "candidates": [weak]})
    assert rw["recommended"] is None and rw["state"] == "REPORTED_NOT_CROWNED"
    assert rw["separation"] is None and rw["separation_pts"] is None and rw["vasp_candidates"]
    # §9.3: a mixer boundary is not "we found nothing"
    brk = attribute_result({"result": "UNATTRIBUTED", "candidates": [],
                            "flags": ["mixer:Tornado Cash@0xd90e(hop 2, tx 0xabc)"]})
    assert brk["state"] == "BROKEN_AT_MIXER", brk["state"]
    # ...and a provider failure is not abstention either
    inc = attribute_result({"result": "UNATTRIBUTED", "candidates": [], "partial": True,
                            "flags": ["partial:provider_unavailable at 1abc (hop 2): boom"]})
    assert inc["state"] == "INCOMPLETE", inc["state"]
    # a CoinJoin boundary keeps the honest non-answer (DEMO case B)
    cj = attribute_result({"result": "UNATTRIBUTED", "candidates": [],
                           "flags": ["coinjoin_boundary:e5e5(5 equal outputs) from 1abc (hop 1)"]})
    assert cj["state"] == "UNATTRIBUTED", cj["state"]
    assert r["pins"]["weight_hash"].startswith("w-") and r["pins"]["snapshot_block"] == 9
    # the closer, weaker endpoint is still reported — ranked, not hidden
    assert [c["entity"] for c in r["vasp_candidates"]] == ["binance", "kraken"]
    # nothing reached -> honest non-answer, and the trace's own verdict survives
    empty = attribute_result({"result": "UNATTRIBUTED", "candidates": [], "flags": []})
    assert empty["state"] == "UNATTRIBUTED" and empty["recommended"] is None and empty["nearest"] is None


if __name__ == "__main__":
    _selfcheck()
    print("attribution engine selfcheck PASS")
