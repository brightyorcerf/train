"""Candidate endpoints -> per-VASP evidence (§10).

The rule that matters: aggregate with **max + a saturating path count, never sum**. Fifty dusty
paths to fifty Binance deposit addresses must not outrank one strong documented path — with a sum
they would, by arithmetic alone.

The max is taken over **whole paths, not over factors**. Maxing each factor independently produced
a score no real path had: a dust path with tight timing plus a large-value path with scattered
timing scored 70 while the better of the two actually scored 61. The entity's number is now the
best single path's number, and `factor_from` stays in the breakdown as a diagnostic showing which
endpoint would have contributed each factor's maximum. The penalty and `penalties_applied`
therefore also come from one path and always reconcile.

Corroboration (several independent paths to the same entity) is real but weak: it saturates at
TOP_K and is a RANKING TIEBREAK only — it never enters the score.
"""
from app.scoring import rules
from app.scoring.engine import score

TOP_K = 3


def by_entity(candidates: list[dict], flags=(), weights: dict | None = None) -> list[dict]:
    """-> one row per entity, ranked later by recommend.rank(). Pure function of its inputs."""
    groups: dict[str, list[dict]] = {}
    for c in candidates:
        groups.setdefault(c["entity"], []).append(c)

    out = []
    for entity, cands in groups.items():
        per = []
        for c in cands:
            f = rules.factors(c)
            pen, which = rules.path_penalty(flags, c)
            s, _ = score(f, pen, weights)
            per.append({"cand": c, "factors": f, "penalty": pen, "penalties_applied": which, "score": s})
        source_of = {}
        for k in rules.factors(cands[0]):
            top = max(per, key=lambda p: (p["factors"][k], -p["cand"]["hops"]))
            source_of[k] = top["cand"]["endpoint"]
        # The entity scores as its best single path — one coherent set of evidence, not a blend.
        best = sorted(per, key=lambda p: (-p["score"], p["cand"]["hops"], p["cand"]["endpoint"]))[0]
        idx, breakdown = score(best["factors"], best["penalty"], weights)
        nearest = min(cands, key=lambda c: (c["hops"], c["endpoint"]))
        out.append({
            "entity": entity, "entity_name": cands[0]["entity_name"], "sahyog": cands[0]["entity_sahyog"],
            "score": idx, "breakdown": {**breakdown, "factor_from": source_of,
                                        "scored_endpoint": best["cand"]["endpoint"],
                                        "penalties_applied": best["penalties_applied"]},
            "n_paths": len(cands), "corroboration": round(min(len(cands), TOP_K) / TOP_K, 4),
            "best_claim": max((c["result"] for c in cands), key=lambda r: r == "ATTRIBUTED"),
            "nearest": {"hops": nearest["hops"], "endpoint": nearest["endpoint"],
                        "role_basis": nearest["role_basis"], "deposit_event": nearest["deposit_event"],
                        "path": nearest["path"]},
            "endpoints": sorted(c["endpoint"] for c in cands)})
    return out


def _selfcheck():
    from app.labels.registry import DEPOSIT
    day = 86400

    def cand(entity, endpoint, value, hops=2, basis="sweep_proven", tier="tagpacks"):
        return {"entity": entity, "entity_name": entity, "entity_sahyog": "unknown", "endpoint": endpoint,
                "result": "ATTRIBUTED", "hops": hops, "role": DEPOSIT, "basis": basis, "role_basis": basis,
                "tier": tier, "label_confidence": 0.5, "label_source": "x", "value": value, "asset": "BTC",
                "ts": [100, 100 + day], "path": [{"from": "a", "to": endpoint, "tx": f"t{endpoint}"}],
                "deposit_event": None, "sweep": None}

    # THE scatter case: 50 dusty paths to 50 deposit addresses vs one strong documented path.
    scatter = [cand("scatterco", f"d{i}", 0.00001) for i in range(50)]
    strong = [cand("onegood", "D1", 12.5, hops=4, basis="exchange_published_deposit", tier="curated")]
    ranked = {r["entity"]: r for r in by_entity(scatter + strong)}
    assert ranked["onegood"]["score"] > ranked["scatterco"]["score"], ranked
    assert ranked["scatterco"]["n_paths"] == 50 and ranked["scatterco"]["corroboration"] == 1.0
    # corroboration saturates and never enters the score: 50 paths score the same as 3 identical ones
    assert by_entity(scatter[:3])[0]["score"] == ranked["scatterco"]["score"]
    # per-factor max is attributed to the endpoint that earned it (diagnostic only)
    mixed = [cand("x", "cheap", 0.00001, tier="ofac"), cand("x", "rich", 50.0, tier="heuristic")]
    row = by_entity(mixed)[0]
    b = row["breakdown"]
    assert b["factor_from"]["source_tier"] == "cheap" and b["factor_from"]["dust_floor"] == "rich"
    # ...but the SCORE is a real path's score, never a blend of both
    from app.scoring.engine import score_candidate
    assert row["score"] == max(score_candidate(mixed[0])[0], score_candidate(mixed[1])[0])
    assert b["scored_endpoint"] in ("cheap", "rich")
    # penalty and its explanation come from the same path, so the breakdown reconciles
    clean = cand("p", "C", 5.0)
    dirty = cand("p", "D", 0.00001)
    dirty["path"] = [{"from": "s", "to": "mix", "tx": "t2"}, {"from": "mix", "to": "D", "tx": "t3"}]
    br = by_entity([clean, dirty], flags=["mixer:Tornado@mix(hop 1, tx t2)"])[0]["breakdown"]
    assert (br["penalty"] > 0) == bool(br["penalties_applied"]), br


if __name__ == "__main__":
    _selfcheck()
    print("aggregate selfcheck PASS")
