"""Candidate endpoints -> per-VASP evidence (§10).

The rule that matters: aggregate with **max + a saturating path count, never sum**. Fifty dusty
paths to fifty Binance deposit addresses must not outrank one strong documented path — with a sum
they would, by arithmetic alone. Each factor therefore takes its best value across the entity's
candidates, and the breakdown records WHICH endpoint contributed each max, so the number stays
auditable rather than a blend of unrelated evidence.

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
        best_f, source_of = {}, {}
        for k in rules.factors(cands[0]):
            top = max(per, key=lambda p: (p["factors"][k], -p["cand"]["hops"]))
            best_f[k], source_of[k] = top["factors"][k], top["cand"]["endpoint"]
        penalty = max(p["penalty"] for p in per)          # max, not sum: one tainted path, one penalty
        idx, breakdown = score(best_f, penalty, weights)
        rep = sorted(per, key=lambda p: (-p["score"], p["cand"]["hops"], p["cand"]["endpoint"]))[0]["cand"]
        nearest = min(cands, key=lambda c: (c["hops"], c["endpoint"]))
        out.append({
            "entity": entity, "entity_name": cands[0]["entity_name"], "sahyog": cands[0]["entity_sahyog"],
            "score": idx, "breakdown": {**breakdown, "factor_from": source_of,
                                        "penalties_applied": rep and
                                        next((p["penalties_applied"] for p in per if p["cand"] is rep), [])},
            "n_paths": len(cands), "corroboration": round(min(len(cands), TOP_K) / TOP_K, 4),
            "best_claim": max((c["result"] for c in cands), key=lambda r: r == "ATTRIBUTED"),
            "nearest": {"hops": nearest["hops"], "endpoint": nearest["endpoint"],
                        "role_basis": nearest["role_basis"], "deposit_event": nearest["deposit_event"],
                        "path": nearest["path"]},
            "representative": {"endpoint": rep["endpoint"], "hops": rep["hops"], "role": rep["role"],
                               "basis": rep["basis"], "tier": rep["tier"], "role_basis": rep["role_basis"],
                               "label_source": rep["label_source"], "value": rep["value"],
                               "asset": rep["asset"], "path": rep["path"], "sweep": rep["sweep"]},
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
    # per-factor max is attributed to the endpoint that earned it
    mixed = [cand("x", "cheap", 0.00001, tier="ofac"), cand("x", "rich", 50.0, tier="heuristic")]
    b = by_entity(mixed)[0]["breakdown"]
    assert b["factor_from"]["source_tier"] == "cheap" and b["factor_from"]["dust_floor"] == "rich"


if __name__ == "__main__":
    _selfcheck()
    print("aggregate selfcheck PASS")
