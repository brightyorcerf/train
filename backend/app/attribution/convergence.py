"""Multi-victim convergence (§8 of the brief): trace N victim/suspect wallets, then find the nodes
that appear in two or more of their subgraphs. Those shared addresses are where separate complaints
turn out to be one campaign — and one SAHYOG request can cover all of them.

Where the intersection runs: over `trace_edge` in Postgres, NOT Neo4j. Subgraph membership
(which trace saw which edge) is only recorded in Postgres — Neo4j is a derived index whose edges
are MERGEd and therefore shared across traces, so tagging nodes with trace ids there would
duplicate mutable state into a rebuildable index for no gain. The operation is the same set
intersection over subgraphs either way.

A shared node is not automatically a suspect: an exchange hot wallet is shared by everyone. The
`role` on each row says which kind of sharing it is, so the operator sees the difference.
"""
from app.labels.registry import PgRegistry

# BTC edges run address -FUNDS-> tx -CREDITS-> address, so only one end of those is an address.
ADDR_ENDS = {"funds": ("src",), "credits": ("dst",)}

ROWS = """
SELECT te.trace_id::text, te.hop, e.chain, e.kind, e.src, e.dst, e.value, e.decimals, e.asset, e.tx_hash
FROM trace_edge te JOIN edge e ON e.id = te.edge_id
WHERE te.trace_id = ANY(%s)
"""


def subgraphs(conn, trace_ids: list[str]) -> dict[str, dict[str, dict]]:
    """-> {trace_id: {address: {received, sent, hop, txs}}}. One row per address per trace."""
    out: dict[str, dict[str, dict]] = {t: {} for t in trace_ids}
    for tid, hop, chain, kind, src, dst, value, decimals, asset, tx in conn.execute(ROWS, (list(trace_ids),)):
        per = out.setdefault(tid, {})
        amount = float(value) / 10 ** (decimals or 0)
        for end in ADDR_ENDS.get(kind, ("src", "dst")):
            addr = src if end == "src" else dst
            n = per.setdefault(addr, {"received": 0.0, "sent": 0.0, "hop": hop, "txs": set(),
                                      "chain": chain, "asset": asset})
            n["txs"].add(tx)
            n["hop"] = min(n["hop"], hop)
            n["received" if end == "dst" else "sent"] += amount
    return out


def intersect(per_trace: dict[str, dict[str, dict]], min_shared: int = 2) -> list[dict]:
    """The pure half: subgraph node sets -> shared nodes, most-shared and highest-value first."""
    shared: dict[str, list[tuple[str, dict]]] = {}
    for tid, nodes in per_trace.items():
        for addr, n in nodes.items():
            shared.setdefault(addr, []).append((tid, n))
    out = []
    for addr, hits in shared.items():
        if len(hits) < min_shared:
            continue
        out.append({"address": addr, "chain": hits[0][1].get("chain"),
                    "shared_by": len(hits), "traces": sorted(t for t, _ in hits),
                    "total_received": round(sum(n["received"] for _, n in hits), 8),
                    "total_sent": round(sum(n["sent"] for _, n in hits), 8),
                    "asset": hits[0][1].get("asset"),
                    "min_hop": min(n["hop"] for _, n in hits),
                    "txs": sorted({t for _, n in hits for t in n["txs"]})[:10]})
    return sorted(out, key=lambda r: (-r["shared_by"], -r["total_received"], r["address"]))


def converge(conn, trace_ids: list[str], chain: str | None = None, label_set=None,
             min_shared: int = 2, reg=None, wallets=()) -> list[dict]:
    """Shared nodes + what we know about each (the VASP attribution the operator actually needs).

    `wallets` = the traced wallets themselves, so a row that is one of them is marked rather than
    read as a discovery: one suspect paying another is real linkage, but it is not a shared
    downstream node."""
    rows = intersect(subgraphs(conn, trace_ids), min_shared)
    reg = reg or PgRegistry(label_set, conn=conn)
    traced = {w.lower() for w in wallets}
    for r in rows:
        r["is_traced_wallet"] = r["address"].lower() in traced
        labs = reg.lookup(chain or r["chain"] or "btc", r["address"])
        best = labs[0] if labs else None
        r["labels"] = [{"role": l.role, "entity": l.entity, "source": l.source, "basis": l.basis,
                        "confidence": l.confidence} for l in labs[:3]]
        r["role"] = best.role if best else "unlabeled"
        r["entity"] = best.entity if best else None
        ent = reg.entities.get(best.entity) if best else None
        r["entity_name"] = ent.name if ent else r["entity"]
        r["sahyog"] = ent.sahyog if ent else "unknown"
        # what kind of sharing this is — an exchange hot wallet is shared by everyone, and saying so
        # is the difference between an insight and a false lead
        r["interpretation"] = (
            "one of the traced wallets — direct linkage between these cases, not a shared downstream node"
            if r["is_traced_wallet"] else
            f"shared {best.role} boundary ({r['entity_name']}) — the trace stops here; downstream is "
            "not followed (§9.3)" if best and best.role in ("mixer", "dex") else
            "common VASP endpoint — one SAHYOG request can cover all of these cases"
            if best and best.role in ("deposit", "hot") else
            "sanctioned address common to these cases" if best else
            "unlabeled address common to these cases — a shared intermediary or shared infrastructure; "
            "worth a closer look")
    return rows


def _selfcheck():
    per = {"t1": {"A": {"received": 1.0, "sent": 0.0, "hop": 1, "txs": {"x"}, "chain": "btc", "asset": "BTC"},
                  "S": {"received": 5.0, "sent": 0.0, "hop": 2, "txs": {"y"}, "chain": "btc", "asset": "BTC"}},
           "t2": {"B": {"received": 2.0, "sent": 0.0, "hop": 1, "txs": {"z"}, "chain": "btc", "asset": "BTC"},
                  "S": {"received": 7.0, "sent": 0.0, "hop": 3, "txs": {"w"}, "chain": "btc", "asset": "BTC"}},
           "t3": {"S": {"received": 1.0, "sent": 0.0, "hop": 9, "txs": {"v"}, "chain": "btc", "asset": "BTC"}}}
    got = intersect(per)
    assert [r["address"] for r in got] == ["S"]          # only the shared node
    assert got[0]["shared_by"] == 3 and got[0]["total_received"] == 13.0
    assert got[0]["min_hop"] == 2 and got[0]["traces"] == ["t1", "t2", "t3"]
    assert intersect(per, min_shared=4) == []
    assert [r["address"] for r in intersect(per, min_shared=1)] == ["S", "B", "A"]   # value-ordered


if __name__ == "__main__":
    _selfcheck()
    print("convergence selfcheck PASS")
