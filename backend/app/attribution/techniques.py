"""Laundering-technique timeline (§13): name what the funds went through, hop by hop.

Nothing here is a new inference. Every entry restates something the trace already established —
a change output detect_change() picked, a consolidation visible in the tx's own inputs, a boundary
flag the engine raised, a sweep the §6.2c proof verified — and says which kind of claim it is:

  observed   — read straight off the chain (input/output counts, timing, a proven sweep)
  heuristic  — a model's call that can be wrong (change detection, CoinJoin shape, hub threshold)
  label      — a registry fact (OFAC listing, a known mixer/bridge/DEX)

so the panel cannot launder a heuristic into a fact on its way to a slide.

`detect(result, shapes)` is pure; `shapes` is {txid: (distinct input addresses, outputs)} read
from the trace's own BTC hypernode edges by the API.
"""
import re

_HOP = re.compile(r"hop (\d+)")
CONSOLIDATE_MIN_INPUTS = 5      # ponytail: shape thresholds, knobs not findings
FAN_OUT_MIN_OUTPUTS = 5
RAPID_S = 2 * 3600


def _dur(s: int) -> str:
    h, m = divmod(max(0, s) // 60, 60)
    return f"{h}h {m:02d}m" if h else f"{m}m"


def _hop(flag: str) -> int | None:
    m = _HOP.search(flag)
    return int(m.group(1)) if m else None


def _path(r: dict) -> list[dict]:
    top = next((c for c in r.get("vasp_candidates") or [] if c["entity"] == r.get("recommended")), None)
    return ((top or {}).get("nearest") or r.get("nearest") or {}).get("path") or []


def detect(r: dict, shapes: dict[str, tuple[int, int]] | None = None) -> list[dict]:
    shapes, out, path = shapes or {}, [], _path(r)

    def add(hop, kind, title, detail, basis, **kw):
        out.append({"hop": hop, "kind": kind, "title": title, "detail": detail, "basis": basis, **kw})

    run = 0   # consecutive change hops -> a peel chain
    for i, h in enumerate(path, start=1):
        if h.get("change"):
            run += 1
            add(i, "change_output", "Change output",
                f"{h['value']} {h['asset']} followed as the spender's change (confidence {h['change']})",
                "heuristic", tx=h["tx"])
            if run == 2:
                add(i, "peel_chain", "Peel chain",
                    "two or more consecutive spends each peel off a payment and keep the change: "
                    "the classic way to drip a large balance out without one big transfer",
                    "heuristic", tx=h["tx"])
        else:
            run = 0
        n_in, n_out = shapes.get(h["tx"], (0, 0))
        if n_in >= CONSOLIDATE_MIN_INPUTS and n_in > n_out:
            add(i, "consolidation", "Consolidation",
                f"merged with {n_in - 1} other input address{'es' if n_in > 2 else ''} into "
                f"{n_out} output{'s' if n_out > 1 else ''}; the traced coin is one of {n_in} sources",
                "observed", tx=h["tx"])
        elif n_out >= FAN_OUT_MIN_OUTPUTS and n_out > n_in:
            add(i, "fan_out", "Fan-out", f"split into {n_out} outputs in one transaction", "observed", tx=h["tx"])
        if i > 1 and h.get("ts") and path[i - 2].get("ts"):
            dt = h["ts"] - path[i - 2]["ts"]
            if 0 <= dt < RAPID_S:
                add(i, "rapid_hop", "Rapid layering",
                    f"moved on {_dur(dt)} after arriving; funds that sit are spent, funds that "
                    f"are being layered move", "observed", tx=h["tx"])

    for f in r.get("flags") or []:
        kind, _, rest = f.partition(":")
        hop = _hop(f)
        if kind == "coinjoin_boundary":
            add(hop, "coinjoin", "CoinJoin", rest.split(")", 1)[0].split("(", 1)[-1] +
                "; equal outputs break 1:1 value linkage, so the trace stops here", "heuristic")
        elif kind == "service_hub_boundary":
            add(hop, "service_hub", "Custodial hub",
                rest.split("(", 1)[-1].split(",")[0] + "; outflows are other people's money, "
                "so following them would credit a pass-through", "heuristic")
        elif kind == "mixer":
            add(hop, "mixer", "Mixer", rest.split("@")[0] + "; link broken by design; trace stops", "label")
        elif kind == "bridge":
            add(hop, "bridge", "Cross-chain bridge", rest.split("@")[0] + "; funds leave this chain", "label")
        elif kind == "dex":
            add(hop, "dex_swap", "DEX swap", rest.split("@")[0] + "; asset swapped, followed at reduced "
                "confidence", "label")
        elif kind == "ofac":
            add(hop, "sanctioned", "OFAC-listed address", "passes through " + rest.split("@")[0], "label")

    endpoint = path[-1]["to"] if path else None
    names = {c["entity"]: c.get("entity_name") for c in r.get("vasp_candidates") or []}
    for s in r.get("sweep_evidence") or []:
        if s.get("sweep") == "proven" and (endpoint is None or s["address"] == endpoint):
            add(len(path) or None, "deposit_sweep", "Deposit-address sweep",
                f"{s['share'] * 100:.1f}% of the sweep tx forwarded to a labeled "
                f"{names.get(s.get('entity')) or s.get('entity') or 'VASP'} hot wallet; "
                f"{s['distinct_senders']}{'+' if s.get('senders_truncated') else ''} distinct senders "
                f"- the behaviour of an exchange deposit address", "observed",
                tx=s.get("sweep_tx"), address=s["address"])
    return _merge(sorted(out, key=lambda t: (t["hop"] if t["hop"] is not None else 99)))


def _merge(ts: list[dict]) -> list[dict]:
    """One entry per (hop, kind): nine CoinJoins at the same hop are one finding seen nine times, and
    listing them nine times buries everything else. The first instance's detail is kept verbatim."""
    out: dict[tuple, dict] = {}
    for t in ts:
        k = (t["hop"], t["kind"])
        if k in out:
            out[k]["count"] += 1
        else:
            out[k] = {**t, "count": 1}
    for t in out.values():
        if t["count"] > 1:
            t["title"] = f"{t['title']} ×{t['count']}"
    return list(out.values())


def _selfcheck():
    r = {"recommended": "binance", "vasp_candidates": [{"entity": "binance", "nearest": {"path": [
        {"from": "a", "to": "b", "tx": "t1", "value": 4.2, "asset": "BTC", "ts": 1000},
        {"from": "b", "to": "c", "tx": "t2", "value": 1.0, "asset": "BTC", "ts": 1600, "change": 0.7},
        {"from": "c", "to": "d", "tx": "t3", "value": 0.9, "asset": "BTC", "ts": 90000, "change": 0.7},
        {"from": "d", "to": "e", "tx": "t4", "value": 50, "asset": "BTC", "ts": 90100}]}}],
        "flags": ["ofac:Someone@x(hop 2)", "coinjoin_boundary:h(42 equal outputs of 1 sats from 64 "
                  "distinct input addresses) from q (hop 3)"],
        "sweep_evidence": [{"address": "e", "sweep": "proven", "share": 1.0, "hot_label": "binance",
                            "distinct_senders": 27, "senders_truncated": True, "sweep_tx": "s"},
                           {"address": "zz", "sweep": "proven", "share": 1.0, "hot_label": "x",
                            "distinct_senders": 1, "sweep_tx": "s2"}]}
    kinds = [t["kind"] for t in detect(r, {"t4": (13, 1), "t1": (1, 7)})]
    assert kinds.count("change_output") == 2 and kinds.count("peel_chain") == 1, kinds   # hops 2, 3: distinct
    assert "consolidation" in kinds and "fan_out" in kinds and "coinjoin" in kinds and "sanctioned" in kinds
    assert kinds.count("rapid_hop") == 2, kinds          # t1->t2 (10m) and t3->t4; not t2->t3 (24h)
    assert kinds.count("deposit_sweep") == 1, kinds      # only the endpoint's sweep, not an unrelated one
    assert detect({"flags": [], "sweep_evidence": []}) == []
    many = detect({"flags": [f"coinjoin_boundary:h{i}(5 equal outputs of 1 sats from 9 distinct input "
                             f"addresses) from q (hop 0)" for i in range(9)]})
    assert len(many) == 1 and many[0]["count"] == 9 and many[0]["title"] == "CoinJoin ×9", many


if __name__ == "__main__":
    _selfcheck()
    print("techniques selfcheck PASS")
