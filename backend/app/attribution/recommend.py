"""Crown ONE primary target, transparently (§10).

The policy, in the order it is applied — and it is a POLICY, printed on the report, not a model:
  1. evidence tier first   — the confidence index (§11.1), which knows nothing about distance
  2. proximity as tiebreak — fewer hops wins a tie
  3. corroboration         — saturating path count breaks what is still tied
  4. entity id             — deterministic last resort

We deliberately do NOT fuse confidence and proximity into one scalar (no `(1/hops) x confidence`):
that manufactures false precision and re-buries the two axes the whole design keeps apart (§3).

Three honest non-answers: `ambiguous` when top1 - top2 < SEPARATION_TAU (don't crown a coin flip),
`below_floor` when the only thing reached scores under MIN_CROWN, and `UNATTRIBUTED` when nothing
was reached.

Separation is a gap between TWO candidates. With one candidate there is no gap, so `separation` is
None and `separation_pts` is None — reporting the sole candidate's own score as a "gap" (what this
did before) reads as strong evidence of nothing.
"""
from app.scoring.weights import MIN_CROWN, SEPARATION_TAU


def rank(rows: list[dict]) -> list[dict]:
    return sorted(rows, key=lambda r: (-r["score"], r["nearest"]["hops"], -r["corroboration"], r["entity"]))


def recommend(rows: list[dict], tau: int = SEPARATION_TAU) -> dict:
    ranked = rank(rows)
    if not ranked:
        return {"recommended": None, "separation": None, "separation_pts": None, "ambiguous": False,
                "below_floor": False,
                "rationale": "no labeled or sweep-provable endpoint reached within the budget", "ranked": []}
    top = ranked[0]
    gap = top["score"] - ranked[1]["score"] if len(ranked) > 1 else None
    ambiguous = gap is not None and gap < tau
    below_floor = not ambiguous and top["score"] < MIN_CROWN
    out = {"recommended": None if (ambiguous or below_floor) else top["entity"],
           "separation": None if gap is None else ("HIGH" if gap >= tau else "LOW"),
           "separation_pts": gap, "ambiguous": ambiguous, "below_floor": below_floor,
           "ranked": ranked}
    if ambiguous:
        out["rationale"] = (f"ambiguous: {top['entity_name']} and {ranked[1]['entity_name']} are "
                            f"{gap} points apart (threshold {tau}) — abstaining rather than crowning one")
    elif below_floor:
        out["rationale"] = (f"{top['entity_name']} is the only endpoint reached and scores "
                            f"{top['score']}/100, below the {MIN_CROWN}/100 floor for naming a "
                            f"disclosure target — reported, not crowned")
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
    # one candidate: there is no second score, so there is no gap to report
    solo = recommend([row("binance", 70, 2)])
    assert solo["recommended"] == "binance" and solo["separation"] is None
    assert solo["separation_pts"] is None and not solo["ambiguous"]
    # a lone weak endpoint is reported but not crowned (§6.3 — the engine does not always answer)
    weak = recommend([row("solo", 23, 2)])
    assert weak["recommended"] is None and weak["below_floor"] and weak["ranked"][0]["score"] == 23
    assert "below the" in weak["rationale"]
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
