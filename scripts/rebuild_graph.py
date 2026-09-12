"""Rebuild Neo4j entirely from Postgres (§7.6: Neo4j is a DERIVED index, not the system of record).

    backend/.venv/bin/python scripts/rebuild_graph.py [--chain btc] [--keep] [--label-set ls-...]

--keep skips the wipe (MERGE makes the rebuild idempotent either way). Labels are attached only for
addresses that appear in the edge table, plus the trace-derived labels in `evidence`.
"""
import argparse
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.db import connect  # noqa: E402
from app.db.edges import load_edges, load_txs  # noqa: E402
from app.db.evidence import load_labels  # noqa: E402
from app.graph.client import Graph  # noqa: E402
from app.labels.registry import PgRegistry  # noqa: E402
from app.providers.base import Edge  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--chain")
    ap.add_argument("--label-set")
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args()

    t0 = time.time()
    conn, g = connect(autocommit=True), Graph()
    reg = PgRegistry(a.label_set, conn=conn)
    if not a.keep:
        g.wipe()
    g.init()

    by_chain = defaultdict(list)
    for chain, src, dst, kind, tx_hash, idx, value, asset, dec, block, ts, meta in load_edges(conn, a.chain):
        by_chain[chain].append(Edge(src, dst, kind, tx_hash, float(value), asset, block, ts, idx or None,
                                    {**(meta or {}), "decimals": dec}))

    tot_e = tot_l = 0
    for chain, edges in sorted(by_chain.items()):
        n = g.merge_edges(chain, edges)
        rows = {r[1]: r for r in load_txs(conn, chain)}
        g.merge_txs(chain, [_tx(r) for r in rows.values()])
        addrs = {e.src for e in edges} | {e.dst for e in edges}
        # pinned label set for what the traces reached + what those traces themselves derived (evidence)
        labels = [l for a_ in sorted(addrs) for l in reg.lookup(chain, a_)] + load_labels(conn, chain)
        tot_l += g.merge_labels(chain, labels, reg.entities)
        tot_e += n
        print(f"{chain}: {n} edges, {len(rows)} txs, {len(labels)} labels on {len(addrs)} addresses")
    print(f"rebuilt from Postgres in {time.time() - t0:.1f}s — {tot_e} edges, {tot_l} labels · {g.counts()}")
    g.close()


class _tx:  # noqa: N801 — tiny adapter: a utxo_tx row -> what Graph.merge_txs reads
    def __init__(self, row):
        _, self.hash, self.block, self.ts, self.total_in, self.total_out = row


if __name__ == "__main__":
    main()
