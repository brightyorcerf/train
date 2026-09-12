"""Crown ONE primary target, transparently (§10).

The policy, in the order it is applied — and it is a POLICY, printed on the report, not a model:
  1. evidence tier first   — the confidence index (§11.1), which knows nothing about distance
  2. proximity as tiebreak — fewer hops wins a tie
  3. corroboration         — saturating path count breaks what is still tied
  4. entity id             — deterministic last resort

We deliberately do NOT fuse confidence and proximity into one scalar (no `(1/hops) x confidence`):
that manufactures false precision and re-buries the two axes the whole design keeps apart (§3).

Two honest non-answers: `ambiguous` when top1 - top2 < SEPARATION_TAU (don't crown a coin flip),
and `UNATTRIBUTED` when nothing was reached.
"""
from app.scoring.weights import SEPARATION_TAU


def rank(rows: list[dict]) -> list[dict]:
    return sorted(rows, key=lambda r: (-r["score"], r["nearest"]["hops"], -r["corroboration"], r["entity"]))


def recommend(rows: list[dict], tau: int = SEPARATION_TAU) -> dict:
    ranked = rank(rows)
    if not ranked:
        return {"recommended": None, "separation": None, "separation_pts": None, "ambiguous": False,
                "rationale": "no labeled or sweep-provable endpoint reached within the budget", "ranked": []}
    top = ranked[0]
    gap = top["score"] - ranked[1]["score"] if len(ranked) > 1 else top["score"]
    ambiguous = len(ranked) > 1 and gap < tau
    out = {"recommended": None if ambiguous else top["entity"],
           "separation": "HIGH" if gap >= tau else "LOW", "separation_pts": gap, "ambiguous": ambiguous,
           "ranked": ranked}
    if ambiguous:
        out["rationale"] = (f"ambiguous: {top['entity_name']} and {ranked[1]['entity_name']} are "
                            f"{gap} points apart (threshold {tau}) — abstaining rather than crowning one")
    else:
        out["rationale"] = _rationale(top)
    return out


def _rationale(r: dict) -> str:
    n = r["nearest"]
    basis = {"exchange_published_deposit": "an exchange-published deposit address",
             "ground_truth": "a ground-truth deposit address",
             "sweep_proven": "a sweep-proven deposit address",
             "cluster_propagated": "a cluster-propagated address"}.get(n["role_basis"],
                                                                       f"a {n['role_basis']} endpoint")
    onboarded = ("a SAHYOG-onboarded VASP" if r["sahyog"] == "confirmed"
                 else "a VASP not confirmed as SAHYOG-onboarded")
    return (f"closest endpoint with {basis} of {r['entity_name']} ({onboarded}), {n['hops']} hop(s) from "
            f"the suspect; {r['score']}/100 on evidence, {r['n_paths']} independent path(s)")


def _selfcheck():
    def row(entity, score, hops, n=1, sahyog="unknown", basis="sweep_proven"):
        return {"entity": entity, "entity_name": entity.title(), "score": score, "sahyog": sahyog,
                "n_paths": n, "corroboration": min(n, 3) / 3,
                "nearest": {"hops": hops, "endpoint": f"{entity}-ep", "role_basis": basis,
                            "deposit_event": None, "path": []}}

    r = recommend([row("binance", 87, 4), row("kraken", 63, 2)])
    assert r["recommended"] == "binance" and r["separation"] == "HIGH" and r["separation_pts"] == 24
    assert "4 hop(s)" in r["rationale"] and "sweep-proven" in r["rationale"]
    # evidence first, proximity only as a tiebreak: the closer endpoint does NOT win on distance
    assert rank([row("far", 90, 5), row("near", 55, 1)])[0]["entity"] == "far"
    assert rank([row("b", 70, 3), row("a", 70, 1)])[0]["entity"] == "a"          # tie -> fewer hops
    # abstain instead of crowning a coin flip
    amb = recommend([row("a", 70, 2), row("b", 65, 3)])
    assert amb["recommended"] is None and amb["ambiguous"] and amb["separation"] == "LOW"
    assert recommend([])["recommended"] is None
    assert "SAHYOG-onboarded VASP" in recommend([row("wazirx", 80, 2, sahyog="confirmed")])["rationale"]


if __name__ == "__main__":
    _selfcheck()
    print("recommend selfcheck PASS")
