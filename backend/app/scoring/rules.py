"""The four confidence factors (§11.1). Every factor is in [0,1] and explainable in one sentence.

NOT factors, deliberately:
  proximity        — that is Axis 1 (nearest, §3) and a ranking tiebreak (§10); scoring it twice
                     would let a close weak endpoint outrank a documented far one.
  value magnitude  — that is Axis 3 (priority). Large value is NOT evidence the label is right, so
                     `dust_floor` only ever PENALIZES trailing dust; it caps at 1.0 and never rewards.
"""
import math

from app.labels.registry import DEPOSIT, SOURCE_TIER

# Minimum amount that reads as a real transfer rather than dust/poisoning, per asset (native units).
# ponytail: no price feed in scope, so the floor is per-asset, not USD — stated, not hidden.
DUST_FLOOR = {"BTC": 0.001, "ETH": 0.01, "WETH": 0.01, "POL": 10.0, "WPOL": 10.0,
              "USDT": 10.0, "USDC": 10.0, "USDC.e": 10.0, "DAI": 10.0}
DEPOSIT_BASIS = {"ground_truth": 1.0, "exchange_published_deposit": 1.0, "sweep_proven": 0.7,
                 "cluster_propagated": 0.4}
TEMPORAL_SPAN_DAYS = 365   # a path spanning a year scores 0; same-day scores 1


def source_tier(tier: str) -> float:
    """Who says this address belongs to this entity: OFAC/ground truth 1.0 … heuristic 0.3."""
    return SOURCE_TIER.get(tier, 0.3)


def deposit_basis(role: str, basis: str) -> float:
    """How the endpoint earns the words 'deposit address' — documented 1.0, sweep-proven 0.7,
    reached-the-VASP-but-not-a-customer-deposit-address 0.3."""
    if role != DEPOSIT:
        return 0.3       # a hot/infra wallet: we reached the VASP, not one customer's deposit address
    return DEPOSIT_BASIS.get(basis, 0.5)


def dust_floor(value: float, asset: str | None) -> float:
    """1.0 for any amount at or above the asset's floor; proportional below it. Never above 1.0."""
    floor = DUST_FLOOR.get(asset or "")
    if not floor:
        return 1.0       # unknown asset: no penalty beats a fabricated one
    return min(1.0, max(0.0, value) / floor)


def temporal(timestamps) -> float:
    """How tightly the hops cluster in time: same-day 1.0, decaying logarithmically to 0 at a year."""
    ts = [t for t in timestamps or () if t]
    if len(ts) < 2:
        return 1.0
    days = (max(ts) - min(ts)) / 86400
    return max(0.0, 1 - math.log10(1 + days) / math.log10(1 + TEMPORAL_SPAN_DAYS))


def factors(cand: dict) -> dict:
    return {"deposit_basis": deposit_basis(cand.get("role"), cand.get("basis")),
            "source_tier": source_tier(cand.get("tier")),
            "temporal": temporal(cand.get("ts")),
            "dust_floor": dust_floor(cand.get("value") or 0.0, cand.get("asset"))}


def path_penalty(flags, cand: dict) -> tuple[float, list[str]]:
    """§9.3 service nodes ON THIS path only — a mixer on some other branch is not this candidate's
    problem. Flags look like 'mixer:Tornado Cash@0xabc…(hop 2, tx 0xdef…)'."""
    from app.scoring.weights import PENALTIES
    hops = {p["to"] for p in cand.get("path", ())} | {p["from"] for p in cand.get("path", ())}
    txs = {p["tx"] for p in cand.get("path", ())}
    hit, total = [], 0.0
    for f in flags or ():
        kind = f.split(":", 1)[0]
        if kind not in PENALTIES or kind in [h.split(":", 1)[0] for h in hit]:
            continue
        addr = f.split("@", 1)[1].split("(", 1)[0].lower() if "@" in f else ""
        tx = f.split("tx ", 1)[1].rstrip(")") if "tx " in f else ""
        if addr in hops or (tx and tx in txs):
            hit.append(f)
            total += PENALTIES[kind]
    return total, hit


def _selfcheck():
    assert deposit_basis(DEPOSIT, "sweep_proven") == 0.7 and deposit_basis("hot", "labeled") == 0.3
    assert source_tier("ofac") == 1.0 > source_tier("tagpacks") > source_tier("heuristic")
    # dust_floor penalizes below the floor and NEVER rewards size (0.5 BTC and 500 BTC both 1.0)
    assert dust_floor(0.0001, "BTC") == 0.1 and dust_floor(0.5, "BTC") == dust_floor(500, "BTC") == 1.0
    assert dust_floor(1, "SHIB") == 1.0          # unknown asset -> no fabricated penalty
    day = 86400
    assert temporal([100, 100 + day]) > temporal([100, 100 + 30 * day]) > temporal([100, 100 + 300 * day])
    assert temporal([5]) == 1.0 and temporal([100, 100 + 365 * day]) == 0.0
    path = [{"from": "0xa", "to": "0xb", "tx": "0xt1"}, {"from": "0xb", "to": "0xc", "tx": "0xt2"}]
    on = ["mixer:Tornado@0xb(hop 1, tx 0xt2)", "dex:Uniswap@0xzz(hop 1, tx 0xnope)"]
    p, which = path_penalty(on, {"path": path})
    assert p == 0.30 and which == [on[0]]        # only the flag that touches this path counts
    assert path_penalty(on * 3, {"path": path})[0] == 0.30    # each kind applied once


if __name__ == "__main__":
    _selfcheck()
    print("scoring rules selfcheck PASS")
