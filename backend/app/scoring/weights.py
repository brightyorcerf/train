"""FROZEN expert priors (§11.1). Hand-set, committed, hashed — never tuned on the golden set.

If these change after an eval run, that run is train-on-test and must be reported as such: the
weight hash is pinned into every case (§12), so a report names the exact profile that produced it.

Why these numbers (one line each, defensible under questioning):
  deposit_basis 0.40  the claim that earns the words "deposit-accepting VASP" — the whole product
  source_tier   0.35  who says so: OFAC/exchange-published outranks a crawled tag pack
  temporal      0.15  hops days apart are a weaker story than hops minutes apart
  dust_floor    0.10  a penalty for trailing dust only; size is Axis 3 (priority), never confidence
"""
import hashlib
import json

WEIGHTS = {"deposit_basis": 0.40, "source_tier": 0.35, "temporal": 0.15, "dust_floor": 0.10}
PENALTIES = {"mixer": 0.30, "bridge": 0.20, "dex": 0.15}   # each applied once, per §11.1
FROZEN_AT = "2026-09-12"        # frozen BEFORE the first golden-set run (§11.2)
SEPARATION_TAU = 10             # top1 - top2 below this (of 100) -> ambiguous, don't crown (§10)


def weight_hash(weights: dict | None = None, penalties: dict | None = None) -> str:
    """Pinned per case alongside the block snapshot and label-set version (§12)."""
    blob = json.dumps({"w": weights or WEIGHTS, "p": penalties or PENALTIES}, sort_keys=True)
    return "w-" + hashlib.sha256(blob.encode()).hexdigest()[:12]


def perturb(pct: float, seed: int = 0) -> dict:
    """±pct weight profile for the §11.2 rank-stability run. Deterministic, renormalized to 1."""
    import random
    r = random.Random(seed)
    w = {k: v * (1 + r.uniform(-pct, pct)) for k, v in WEIGHTS.items()}
    total = sum(w.values())
    return {k: v / total for k, v in w.items()}


def _selfcheck():
    assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9
    assert weight_hash() == weight_hash() and weight_hash({"a": 1}) != weight_hash()
    p = perturb(0.2, seed=1)
    assert abs(sum(p.values()) - 1.0) < 1e-9 and p != WEIGHTS
    assert perturb(0.2, seed=1) == p          # deterministic


if __name__ == "__main__":
    _selfcheck()
    print("weights selfcheck PASS")
