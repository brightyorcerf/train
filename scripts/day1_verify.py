"""Case A verification: does an unknown wallet reach a documented / sweep-provable VASP
endpoint on free-tier historical data?

    backend/.venv/bin/python scripts/day1_verify.py <wallet> <btc|eth|polygon> [--since-block N]
        [--until-block N] [--max-hops 4] [--fanout 5] [--expect ATTRIBUTED]
        [--record controlled_case|public_case|discovery_case --source-doc URL]

Forward BFS, level by level; stops at the first hop level that reaches any labeled endpoint.
Day 2: a thin CLI over the backend modules —
  labels   app.labels.registry.PgRegistry — a pinned label-set version from Postgres (ingest.persist)
  BTC      app.providers.esplora (Tx hypernodes, change + CoinJoin annotation, failover/breaker)
  sweep    app.labels.sweep (§6.2c: >=90% of a spend to one entity's hot wallets AND >=3 senders)
  cluster  app.labels.propagate (SAME_OWNER from co-inputs; CoinJoin txs excluded)
Results (§6.3): ATTRIBUTED (deposit: ground_truth | exchange_published_deposit | sweep_proven) ·
ATTRIBUTED_INFRA (labeled hot/infra or cluster-propagated — downgraded) · UNATTRIBUTED.
OFAC non-VASP addresses are flags, traced through. A CoinJoin tx is a trace boundary (flag).
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from day1_chain import REPO, Chain  # noqa: E402

from app.labels.propagate import propagate, same_owner_edges  # noqa: E402
from app.labels.registry import DEPOSIT, SANCTIONED, Label, PgRegistry  # noqa: E402
from app.labels.sweep import Outflow, btc_sweep_proof, sweep_target  # noqa: E402
from app.providers.esplora import EsploraProvider, ProviderError  # noqa: E402

HARD_MAX_HOPS, MAX_CALLS = 5, 200
HUB_MIN_RECEIPTS = 1000   # ponytail: §9.3 unlabeled-hub threshold; a knob, not a finding
RANK = {"ATTRIBUTED": 2, "ATTRIBUTED_INFRA": 1}
FULL_CLAIM = {"ground_truth", "exchange_published_deposit", "sweep_proven"}


def expand_btc(prov: EsploraProvider, node: dict, until: int):
    """-> (spend txs, moves). Follows the exact UTXOs that brought funds here (hypernode walk);
    the start wallet uses every tx it spent in within [since, until]."""
    prev_of: dict[str, dict | None] = {}   # spending txid -> the move that created the UTXO it spends
    if node.get("utxos"):
        for utxo in node["utxos"]:
            s = prov.outspend(*utxo, until)
            if s:
                prev_of.setdefault(s, node["utxo_via"][utxo])
        spends = sorted((prov.get_tx(t) for t in prev_of), key=lambda t: (t.block, t.hash))
    else:
        spends = prov.spends(node["addr"], until, node["since"])
    moves = []
    for t in spends:
        ann = prov.annotate(t)
        if ann["coinjoin"]:
            moves.append({"coinjoin": ann["coinjoin"], "hash": t.hash})
            continue
        for o in t.outputs:
            if o.address and o.address not in t.input_addresses:  # reuse change: owner keeps it
                chg = ann["change"] and ann["change"].vout == o.vout
                moves.append({"to": o.address, "value": o.value / 1e8, "asset": "BTC", "hash": t.hash,
                              "ts": t.ts, "block": t.block, "out": (t.hash, o.vout), "from": node["addr"],
                              "change": ann["change"].confidence if chg else None, "tx": t,
                              "prev": prev_of.get(t.hash)})
    return spends, moves


def expand_evm(ch: Chain, chain: str, node: dict):
    moves = ch.evm_outgoing(chain, node["addr"], node["since"])
    if node.get("asset"):  # follow the asset that arrived (no DEX-swap following yet)
        moves = [m for m in moves if m["asset"] == node["asset"]]
    return [], [{**m, "out": None, "change": None, "prev": node.get("via")} for m in moves]


def trace(wallet, chain, since_block=0, until_block=10**9, max_hops=4, fanout=5, label_set=None):
    t0 = time.time()
    reg = PgRegistry(label_set)
    print(f"labels: {reg.version} — {reg.n_labels} across {len(reg.entities)} entities ({time.time() - t0:.1f}s)")
    ch, prov = Chain(), EsploraProvider()
    calls = lambda: ch.calls + prov.calls  # noqa: E731
    start = {"addr": wallet, "hop": 0, "since": since_block, "parent": None, "via": None}
    nodes = {wallet: start}
    hits, flags, so_edges, sweep_evidence = [], [], [], []

    def hit(node, lab, hops, extra=None):
        full = lab.role == DEPOSIT and lab.basis in FULL_CLAIM
        hits.append({"result": "ATTRIBUTED" if full else "ATTRIBUTED_INFRA", "endpoint": node["addr"],
                     "hops": hops, "role_basis": lab.basis if lab.role == DEPOSIT else f"{lab.role}:{lab.basis}",
                     "entity": lab.entity, "role": lab.role, "source": lab.provenance,
                     "confidence": lab.confidence, "calls_at_hit": calls(), "node": node, **(extra or {})})

    def check_node(node, spends, moves) -> bool:
        """Sweep check (§6.2c) then hub boundary (§9.3). True = endpoint/boundary: don't expand."""
        if chain == "btc":
            dep_tx = node["via"]["tx"] if node.get("via") else None
            lab, ev = btc_sweep_proof(prov, reg, node["addr"], spends, until_block, dep_tx)
            if ev.get("sweep"):
                sweep_evidence.append({"address": node["addr"], **ev})
        else:  # ponytail: EVM share test only; the senders test lands with the EVM adapter (day 4)
            groups = [Outflow(a, 0, 0, a, [(m["to"], m["value"]) for m in moves if m["asset"] == a])
                      for a in sorted({m["asset"] for m in moves})]
            got = sweep_target(groups, lambda a: reg.hot_entity(chain, a))
            lab = got and Label(node["addr"], chain, DEPOSIT, got[0], "sweep", 0.5, "sweep_proven",
                                f"{got[1]:.1%} of {got[2].asset} outflow -> {got[0]} hot (senders test pending, EVM)")
        if lab:
            hit(node, lab, node["hop"], {"sweep": sweep_evidence[-1] if chain == "btc" else None})
            return True
        # An unlabeled high-degree hub that is not a proven deposit address is a custodial service:
        # its outflows are other people's money, and following them credits a pass-through (§11.2).
        if chain == "btc":
            n_rx = prov.address_stats(node["addr"])["funded_txo_count"]
            if n_rx >= HUB_MIN_RECEIPTS:
                flags.append(f"service_hub_boundary:{node['addr']}({n_rx} receipts, hop {node['hop']})")
                return True
        return False

    lab = reg.best(chain, wallet)
    if lab:  # start is itself labeled
        hit(start, lab, 0)

    frontier, reason = ([] if hits else [start]), "budget"
    for hop in range(1, min(max_hops, HARD_MAX_HOPS) + 1):
        nxt = []
        print(f"hop {hop}: expanding {len(frontier)} address(es)  [calls so far {calls()}]")
        for node in sorted(frontier, key=lambda n: n["addr"]):
            if calls() >= MAX_CALLS:
                break
            try:
                spends, moves = expand_btc(prov, node, until_block) if chain == "btc" else expand_evm(ch, chain, node)
            except Exception as e:  # noqa: BLE001 — record and keep tracing other branches
                print(f"   ! expand {node['addr']}: {e}")
                continue
            for m in [m for m in moves if "coinjoin" in m]:
                flags.append(f"coinjoin_boundary:{m['hash']}({m['coinjoin']}) from {node['addr']} (hop {hop})")
            moves = [m for m in moves if "coinjoin" not in m]
            for t in spends:
                so_edges += same_owner_edges(t)
            # Sweep check (§6.2c) on every non-start node before expanding it.
            try:
                stop = node is not start and check_node(node, spends, moves)
            except ProviderError as e:
                flags.append(f"partial:provider_unavailable at {node['addr']} (hop {node['hop']}): {str(e)[:120]}")
                continue
            if stop:
                continue
            # Deterministic truncation (§9.1): value desc -> ts -> hash, top `fanout` per asset.
            by_asset: dict[str, list] = {}
            for m in sorted(moves, key=lambda m: (-m["value"], m["ts"], m["hash"])):
                by_asset.setdefault(m["asset"], []).append(m)
            for m in [m for ms in by_asset.values() for m in ms[:fanout]]:
                k = m["to"]
                child = {"addr": m["to"], "hop": hop, "since": m["block"], "parent": node, "via": m,
                         "asset": m["asset"] if chain != "btc" else None,
                         "utxos": [m["out"]] if m["out"] else None, "utxo_via": {m["out"]: m}}
                lab = reg.best(chain, k)
                if lab:
                    hit(child, lab, hop)
                elif k in nodes:
                    if m["out"] and nodes[k]["hop"] == hop:  # same-level UTXO merge (keeps its own provenance)
                        nodes[k]["utxos"].append(m["out"])
                        nodes[k]["utxo_via"][m["out"]] = m
                    continue
                else:
                    san = reg.best(chain, k, roles=(SANCTIONED,))
                    if san:
                        flags.append(f"ofac:{reg.entities[san.entity].name}@{k}(hop {hop})")
                    nodes[k] = child
                    nxt.append(child)
        propagate(reg, chain, so_edges)  # co-input labels become visible to the next level
        if hits:
            reason = "hit"
            break
        if calls() >= MAX_CALLS:
            reason = f"api-call budget ({MAX_CALLS}) exhausted"
            break
        if not nxt:
            reason = "no further outgoing value"
            break
        frontier = nxt
    else:
        reason = f"hop budget ({max_hops}) exhausted"

    wall = round(time.time() - t0, 1)
    # Budget honesty: a hit found after the call budget was spent does not count.
    hits = [h for h in hits if h["calls_at_hit"] <= MAX_CALLS]
    if not hits and reason == "hit":
        reason = f"api-call budget ({MAX_CALLS}) exhausted before the first hit"
    meta = {"until_block": until_block, "label_set_version": reg.version, "partial": any(f.startswith("partial:") for f in flags),
            "same_owner_edges": len(so_edges),
            "providers": dict(prov.calls_by), "sweep_evidence": sweep_evidence[:10]}
    if not hits:
        return {"wallet": wallet, "chain": chain, "result": "UNATTRIBUTED", "hops": None,
                "api_calls": calls(), "wall_clock_s": wall, "endpoint": None, "role_basis": None,
                "reason": reason, "addresses_seen": len(nodes), "flags": flags, **meta}
    best = sorted(hits, key=lambda h: (-RANK[h["result"]], h["hops"], -h["confidence"], h["endpoint"]))[0]
    path, v = [], best["node"]["via"]
    while v:  # walk the moves that actually carried the funds (per-UTXO provenance), not node parents
        path.append({"from": v["from"], "to": v["to"], "tx": v["hash"], "value": v["value"],
                     "asset": v["asset"], "ts": v["ts"], **({"change": v["change"]} if v.get("change") else {})})
        v = v.get("prev")
    ent = reg.entities.get(best["entity"])
    return {"wallet": wallet, "chain": chain, "result": best["result"], "hops": best["hops"],
            "api_calls": calls(), "wall_clock_s": wall, "endpoint": best["endpoint"],
            "calls_at_hit": best["calls_at_hit"],
            "entity": best["entity"], "entity_sahyog": ent.sahyog if ent else "unknown",
            "role": best["role"], "role_basis": best["role_basis"], "label_confidence": best["confidence"],
            "label_source": best["source"], "deposit_event": (best.get("sweep") or {}).get("deposit_event"),
            "path": path[::-1], "addresses_seen": len(nodes), "flags": flags,
            "other_hits": [{k: h[k] for k in ("result", "endpoint", "entity", "hops", "role_basis")}
                           for h in hits if h is not best][:10], **meta}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("wallet")
    ap.add_argument("chain", choices=["btc", "eth", "polygon"])
    ap.add_argument("--since-block", type=int, default=0)
    ap.add_argument("--until-block", type=int, default=10**9, help="snapshot bound (BTC); ignore later txs")
    ap.add_argument("--max-hops", type=int, default=4)
    ap.add_argument("--fanout", type=int, default=5)
    ap.add_argument("--expect", choices=["ATTRIBUTED", "ATTRIBUTED_INFRA", "UNATTRIBUTED"])
    ap.add_argument("--record", choices=["controlled_case", "public_case", "discovery_case"])
    ap.add_argument("--source-doc", default="")
    a = ap.parse_args()
    if a.max_hops > HARD_MAX_HOPS:
        sys.exit(f"--max-hops capped at {HARD_MAX_HOPS}")

    r = trace(a.wallet, a.chain, a.since_block, a.until_block, a.max_hops, a.fanout)
    print(json.dumps(r, indent=2, default=str))
    print(f"\n{r['result']}: " + (f"{r['entity']} via {r['endpoint']} ({r['role_basis']}) in {r['hops']} hop(s)"
                                 if r["endpoint"] else f"no labeled endpoint — {r['reason']}")
          + f" · {r['api_calls']} API calls · {r['wall_clock_s']}s")
    if a.record:
        out = REPO / "scripts" / "day1_results.json"
        allr = json.loads(out.read_text()) if out.exists() else {}
        allr[a.record] = {**r, **({"source_doc": a.source_doc} if a.source_doc else {})}
        out.write_text(json.dumps(allr, indent=2, default=str) + "\n")
        print(f"recorded -> {out} [{a.record}]")
    ok = a.expect is None or r["result"] == a.expect
    print("PASS" if ok else f"FAIL (expected {a.expect}, got {r['result']})")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    from app.labels.sweep import _selfcheck
    _selfcheck()  # pins the 90% sweep rule (day-1 BitMEX false positive) before any trace runs
    main()
