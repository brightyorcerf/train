"""Confidence scoring (§11.1): weighted sum of four explainable factors, minus service-node
penalties, on a 0-100 index.

It is an INDEX, not a probability: present it as "87 / 100 attribution confidence", never "87%".
Nothing here knows how far away the endpoint is — proximity is Axis 1 (§3) and only ever a ranking
tiebreak (§10). The breakdown is returned verbatim so the UI can show the arithmetic.
"""
from app.scoring import rules
from app.scoring.weights import WEIGHTS, weight_hash


def clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def score(factors: dict, penalty: float = 0.0, weights: dict | None = None) -> tuple[int, dict]:
    w = weights or WEIGHTS
    contrib = {k: round(w[k] * factors.get(k, 0.0), 4) for k in w}
    idx = round(100 * clamp01(sum(contrib.values()) - penalty))
    return idx, {"factors": {k: round(v, 4) for k, v in factors.items()}, "weights": dict(w),
                 "contributions": contrib, "penalty": round(penalty, 4),
                 "weight_hash": weight_hash(w), "of": 100}


def score_candidate(cand: dict, flags=(), weights: dict | None = None) -> tuple[int, dict]:
    pen, which = rules.path_penalty(flags, cand)
    idx, br = score(rules.factors(cand), pen, weights)
    return idx, {**br, "penalties_applied": which}


def _selfcheck():
    from app.labels.registry import DEPOSIT
    day = 86400
    strong = {"role": DEPOSIT, "basis": "sweep_proven", "tier": "ofac", "value": 7112.0, "asset": "ETH",
              "ts": [100, 100 + day], "path": [{"from": "0xa", "to": "0xb", "tx": "0xt"}]}
    dusty = {**strong, "tier": "heuristic", "basis": "cluster_propagated", "value": 0.00001,
             "ts": [100, 100 + 300 * day]}
    s_strong, br = score_candidate(strong)
    s_dusty, _ = score_candidate(dusty)
    assert 0 <= s_dusty < s_strong <= 100, (s_dusty, s_strong)
    assert abs(sum(br["contributions"].values()) * 100 - s_strong) < 1
    # a mixer ON the path costs 30 points; the same mixer on another branch costs nothing
    s_mix, _ = score_candidate(strong, ["mixer:Tornado@0xb(hop 1, tx 0xt)"])
    s_else, _ = score_candidate(strong, ["mixer:Tornado@0xelsewhere(hop 1, tx 0xother)"])
    assert s_mix == max(0, s_strong - 30) and s_else == s_strong
    # size is NOT evidence: 100x the value cannot change the score once above the dust floor
    assert score_candidate({**strong, "value": 711200.0})[0] == s_strong


if __name__ == "__main__":
    rules._selfcheck()
    _selfcheck()
    print("scoring engine selfcheck PASS")
