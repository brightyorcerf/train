"""Forward BFS trace engine (§9) — one place, two drivers: the CLI walks it level by level in
process, Celery walks the same levels as chords (trace/tasks.py).

All node and move state is JSON-primitive so a frontier can cross a queue boundary unchanged.
Per-UTXO provenance (`prev`) is carried on the move itself, so a path is the chain of moves that
actually carried the funds — not a walk of node parents, which merges lie about.

Results (§6.3): ATTRIBUTED (deposit: ground_truth | exchange_published_deposit | sweep_proven) ·
ATTRIBUTED_INFRA (labeled hot/infra or cluster-propagated — downgraded) · UNATTRIBUTED.
"""
import time

from app.labels.propagate import SameOwner, propagate, same_owner_edges
from app.labels.registry import DEPOSIT, DEX, MIXER, SANCTIONED, PgRegistry
from app.labels.sweep import btc_sweep_proof, evm_sweep_proof
from app.providers.base import Edge
from app.providers.esplora import EsploraProvider, ProviderError
from app.providers.etherscan_v2 import EtherscanV2Provider

HARD_MAX_HOPS, MAX_CALLS = 5, 200
HUB_MIN_RECEIPTS = 1000   # ponytail: §9.3 unlabeled-hub threshold; a knob, not a finding
RANK = {"ATTRIBUTED": 2, "ATTRIBUTED_INFRA": 1}
FULL_CLAIM = {"ground_truth", "exchange_published_deposit", "sweep_proven"}


def make_provider(chain: str, until_block: int, offline=False, **kw):
    if chain == "btc":
        return EsploraProvider(snapshot=until_block, offline=offline, **kw)
    return EtherscanV2Provider(chain, offline=offline, **kw)


class Tracer:
    """One trace's context. Expansion of a single node is `expand_node` — the unit a Celery task runs."""

    def __init__(self, chain: str, until_block: int, label_set=None, fanout=5, max_calls=MAX_CALLS,
                 offline=False, conn=None, prov=None, reg=None):
        self.chain, self.fanout, self.max_calls = chain, fanout, max_calls
        self.reg = reg or PgRegistry(label_set, conn=conn)
        self.prov = prov or make_provider(chain, until_block, offline)
        self.until = until_block if until_block != 10**9 else self.tip()
        if chain == "btc":
            self.prov.snapshot = self.until

    def tip(self) -> int:
        return int(self.prov._get("/blocks/tip/height")) if self.chain == "btc" else self.prov.tip()

    # ---------- one node ----------
    def expand_node(self, node: dict) -> dict:
        """Fetch this address's outgoing value, run the §6.2c sweep proof and the §9.3 boundaries.
        -> {moves, flags, so_edges, hit, edges, txs, evidence, stop}. JSON-primitive except edges/txs,
        which the caller hands to the graph sink."""
        out = {"moves": [], "flags": [], "so_edges": [], "hit": None, "edges": [], "txs": [],
               "evidence": None, "stop": False}
        try:
            spends, moves = self._expand(node)
        except ProviderError as e:
            out["flags"].append(f"partial:provider_unavailable at {node['addr']} (hop {node['hop']}): {str(e)[:120]}")
            out["stop"] = True
            return out
        for m in [m for m in moves if "coinjoin" in m]:
            out["flags"].append(f"coinjoin_boundary:{m['hash']}({m['coinjoin']}) from {node['addr']} "
                                f"(hop {node['hop']})")
        moves = [m for m in moves if "coinjoin" not in m]
        if self.chain == "btc":
            out["txs"] = spends
            out["edges"] = [e for t in spends for e in self.prov.tx_edges(t)]
            for t in spends:
                out["so_edges"] += [(e.a, e.b, e.confidence, e.tx_hash) for e in same_owner_edges(t)]
        else:
            out["edges"] = spends
        if node["hop"] > 0:
            try:
                out["hit"], out["evidence"], out["stop"] = self._check(node, spends, moves)
            except ProviderError as e:
                out["flags"].append(f"partial:provider_unavailable at {node['addr']} (hop {node['hop']}): "
                                    f"{str(e)[:120]}")
                out["stop"] = True
                return out
            if out["stop"] and not out["hit"]:
                out["flags"].append(out["evidence"].pop("boundary"))
        out["moves"] = moves
        return out

    def _expand(self, node):
        if self.chain == "btc":
            return self._expand_btc(node)
        return self._expand_evm(node)

    def _expand_btc(self, node):
        """Follows the exact UTXOs that brought funds here (hypernode walk); the start wallet uses
        every tx it spent in within [since, until]."""
        prov, prev_of = self.prov, {}
        if node.get("utxos"):
            for txid, vout in node["utxos"]:
                s = prov.outspend(txid, vout, self.until)
                if s:
                    prev_of.setdefault(s, node["utxo_via"].get(f"{txid}:{vout}"))
            spends = sorted((prov.get_tx(t) for t in prev_of), key=lambda t: (t.block, t.hash))
        else:
            spends = prov.spends(node["addr"], self.until, node["since"])
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
                                  "ts": t.ts, "block": t.block, "out": [t.hash, o.vout], "from": node["addr"],
                                  "change": ann["change"].confidence if chg else None, "decimals": 8,
                                  "prev": prev_of.get(t.hash)})
        return spends, moves

    def _expand_evm(self, node):
        edges = self.prov.get_outgoing(node["addr"], self.until, node["since"])
        if node.get("asset"):   # follow the asset that arrived (no DEX-swap following yet — §9.3)
            edges = [e for e in edges if e.asset == node["asset"]]
        return edges, [{"to": e.dst, "value": e.value / 10 ** e.meta["decimals"], "asset": e.asset,
                        "hash": e.tx_hash, "ts": e.ts, "block": e.block, "from": e.src, "out": None,
                        "change": None, "decimals": e.meta["decimals"], "base_value": e.value,
                        "index": e.index, "prev": node.get("via")} for e in edges]

    def _check(self, node, spends, moves):
        """Sweep proof (§6.2c) then the service boundary (§9.3). -> (label, evidence, stop)."""
        addr = node["addr"]
        if self.chain == "btc":
            dep = self.prov.get_tx(node["via"]["hash"]) if node.get("via") else None
            lab, ev = btc_sweep_proof(self.prov, self.reg, addr, spends, self.until, dep)
        else:
            lab, ev = evm_sweep_proof(self.prov, self.reg, addr, spends, self.until, _edge_of(node.get("via")))
        ev = {"address": addr, **ev} if ev.get("sweep") else None
        if lab:
            return lab, ev, True
        # An unlabeled high-degree hub that is not a proven deposit address is a custodial service:
        # its outflows are other people's money, and following them credits a pass-through (§11.2).
        if self.chain == "btc":
            n_rx = self.prov.address_stats(addr)["funded_txo_count"]
            if n_rx >= HUB_MIN_RECEIPTS:
                return None, {"boundary": f"service_hub_boundary:{addr}({n_rx} receipts, hop {node['hop']})"}, True
        elif addr.lower() in self.prov.truncated:
            return None, {"boundary": f"service_hub_boundary:{addr}(>{self.prov.page_cap}k movements, "
                                      f"hop {node['hop']})"}, True
        return None, ev, False

    # ---------- whole trace (sequential driver; tasks.py runs the same levels as chords) ----------
    def run(self, wallet: str, since_block=0, max_hops=4, sink=None, on_level=None) -> dict:
        t0 = time.time()
        chain, reg = self.chain, self.reg
        start = {"addr": wallet, "hop": 0, "since": since_block, "via": None, "utxos": None, "utxo_via": {}}
        nodes, hits, flags, so_edges, evidence = {wallet: start}, [], [], [], []

        lab = reg.best(chain, wallet)
        if lab:   # the start is itself labeled
            hits.append(_hit(start, lab, 0, self.prov.calls))
        frontier, reason = ([] if hits else [start]), "budget"
        for hop in range(1, min(max_hops, HARD_MAX_HOPS) + 1):
            nxt = []
            for node in sorted(frontier, key=lambda n: n["addr"]):
                if self.prov.calls >= self.max_calls:
                    break
                r = self.expand_node(node)
                flags += r["flags"]
                so_edges += r["so_edges"]
                if sink:
                    sink(hop, r["edges"], r["txs"])
                if r["evidence"]:
                    evidence.append(r["evidence"])
                if r["hit"]:
                    hits.append(_hit(node, r["hit"], node["hop"], self.prov.calls))
                if r["stop"]:
                    continue
                nxt += self.absorb(r["moves"], node, hop, nodes, hits, flags)
            new_labels = propagate(reg, chain, [SameOwner(*e) for e in so_edges])
            if on_level:
                on_level(hop, nxt, new_labels)
            if hits:
                reason = "hit"
                break
            if self.prov.calls >= self.max_calls:
                reason = f"api-call budget ({self.max_calls}) exhausted"
                break
            if not nxt:
                reason = "no further outgoing value"
                break
            frontier = nxt
        else:
            reason = f"hop budget ({max_hops}) exhausted"
        if sink:
            sink(hop, [Edge(a, b, "same_owner", tx, 0, "", 0, 0, None, {"confidence": c})
                       for a, b, c, tx in so_edges], [])
            sink(hop, [], [], [l for labs in reg.labels.values() for l in labs])
        return self.result(wallet, hits, flags, nodes, so_edges, evidence, reason, round(time.time() - t0, 1))

    def absorb(self, moves, node, hop, nodes, hits, flags) -> list[dict]:
        """Deterministic truncation (§9.1: value desc -> ts -> hash, top `fanout` per asset), then
        label / boundary classification of each child."""
        chain, reg, nxt = self.chain, self.reg, []
        by_asset: dict[str, list] = {}
        for m in sorted(moves, key=lambda m: (-m["value"], m["ts"], m["hash"])):
            by_asset.setdefault(m["asset"], []).append(m)
        for m in [m for ms in by_asset.values() for m in ms[:self.fanout]]:
            k = m["to"]
            child = {"addr": k, "hop": hop, "since": m["block"], "via": m,
                     "asset": m["asset"] if chain != "btc" else None,
                     "utxos": [m["out"]] if m["out"] else None,
                     "utxo_via": {f"{m['out'][0]}:{m['out'][1]}": m} if m["out"] else {}}
            svc = reg.best(chain, k, roles=(MIXER, DEX))
            lab = reg.best(chain, k)
            if lab:
                hits.append(_hit(child, lab, hop, self.prov.calls))
            elif svc:   # §9.3: mixer = STOP; DEX = record the swap, 1:1 value linkage broken
                flags.append(f"{svc.role}:{reg.entities[svc.entity].name}@{k}(hop {hop}, tx {m['hash']})")
            elif k in nodes:
                if m["out"] and nodes[k]["hop"] == hop:   # same-level UTXO merge keeps its own provenance
                    nodes[k]["utxos"].append(m["out"])
                    nodes[k]["utxo_via"][f"{m['out'][0]}:{m['out'][1]}"] = m
            else:
                san = reg.best(chain, k, roles=(SANCTIONED,))
                if san:
                    flags.append(f"ofac:{reg.entities[san.entity].name}@{k}(hop {hop})")
                nodes[k] = child
                nxt.append(child)
        return nxt

    def result(self, wallet, hits, flags, nodes, so_edges, evidence, reason, wall) -> dict:
        prov, reg = self.prov, self.reg
        hits = [h for h in hits if h["calls_at_hit"] <= self.max_calls]   # budget honesty
        if not hits and reason == "hit":
            reason = f"api-call budget ({self.max_calls}) exhausted before the first hit"
        meta = {"until_block": self.until, "label_set_version": reg.version,
                "partial": any(f.startswith("partial:") for f in flags) or bool(prov.stale),
                "stale_reads": prov.stale[:5], "same_owner_edges": len(so_edges),
                "providers": dict(prov.calls_by), "upstream_calls": prov.upstream,
                "store_hits": prov.store_hits, "sweep_evidence": evidence[:10]}
        base = {"wallet": wallet, "chain": self.chain, "api_calls": prov.calls, "wall_clock_s": wall,
                "addresses_seen": len(nodes), "flags": flags, **meta}
        if not hits:
            return {**base, "result": "UNATTRIBUTED", "hops": None, "endpoint": None, "role_basis": None,
                    "reason": reason}
        best = sorted(hits, key=lambda h: (-RANK[h["result"]], h["hops"], -h["confidence"], h["endpoint"]))[0]
        path, v = [], best["via"]
        while v:   # the moves that actually carried the funds (per-UTXO provenance)
            path.append({"from": v["from"], "to": v["to"], "tx": v["hash"], "value": v["value"],
                         "asset": v["asset"], "ts": v["ts"], **({"change": v["change"]} if v.get("change") else {})})
            v = v.get("prev")
        ent = reg.entities.get(best["entity"])
        sweep = next((e for e in evidence if e["address"] == best["endpoint"]), None)
        return {**base, "result": best["result"], "hops": best["hops"], "endpoint": best["endpoint"],
                "calls_at_hit": best["calls_at_hit"], "entity": best["entity"],
                "entity_sahyog": ent.sahyog if ent else "unknown", "role": best["role"],
                "role_basis": best["role_basis"], "label_confidence": best["confidence"],
                "label_source": best["source"], "deposit_event": (sweep or {}).get("deposit_event"),
                "path": path[::-1],
                "other_hits": [{k: h[k] for k in ("result", "endpoint", "entity", "hops", "role_basis")}
                               for h in hits if h is not best][:10]}


def _hit(node, lab, hops, calls) -> dict:
    full = lab.role == DEPOSIT and lab.basis in FULL_CLAIM
    return {"result": "ATTRIBUTED" if full else "ATTRIBUTED_INFRA", "endpoint": node["addr"], "hops": hops,
            "role_basis": lab.basis if lab.role == DEPOSIT else f"{lab.role}:{lab.basis}", "entity": lab.entity,
            "role": lab.role, "source": lab.provenance, "confidence": lab.confidence, "calls_at_hit": calls,
            "via": node.get("via")}


def _edge_of(move):
    """Rebuild the Edge that funded this node from its (JSON-primitive) move — the deposit-event artifact."""
    if not move:
        return None
    return Edge(move["from"], move["to"], "native", move["hash"], move.get("base_value", 0), move["asset"],
                move["block"], move["ts"], move.get("index"), {"decimals": move.get("decimals", 18)})


def trace(wallet, chain, since_block=0, until_block=10**9, max_hops=4, fanout=5, label_set=None, sink=None,
          offline=False, conn=None) -> dict:
    t = Tracer(chain, until_block, label_set, fanout, offline=offline, conn=conn)
    return t.run(wallet, since_block, max_hops, sink=sink)
