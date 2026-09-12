"""Case verification CLI: does a wallet reach a documented / sweep-provable VASP endpoint on
free-tier historical data? (§20 Case A / Case B, §11.2 fixtures.)

    backend/.venv/bin/python scripts/day1_verify.py <wallet> <btc|eth|polygon> [--since-block N]
        [--until-block N] [--max-hops 4] [--fanout 5] [--expect ATTRIBUTED] [--graph] [--offline]
        [--record controlled_case|public_case|discovery_case|evm_case --source-doc URL]

The engine is app.trace.engine (§9) — the same one Celery drives as chords (app.trace.tasks); this
is the thin driver. Results (§6.3): ATTRIBUTED (deposit: ground_truth | exchange_published_deposit |
sweep_proven) · ATTRIBUTED_INFRA (labeled hot/infra or cluster-propagated — downgraded) · UNATTRIBUTED.
"""
import argparse
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.trace.engine import HARD_MAX_HOPS, trace  # noqa: E402

REPO = Path(__file__).resolve().parents[1]


def graph_sink(chain: str):
    """-> (sink, trace_id, close). Persists edges to Postgres (system of record) and MERGEs Neo4j."""
    from app.db import connect, init_schema
    from app.db.edges import save_edges, save_txs
    from app.db.evidence import save_labels
    from app.graph.client import Graph
    init_schema()
    trace_id, conn, g = str(uuid.uuid4()), connect(autocommit=True), Graph()
    g.init()

    def sink(hop, edges, txs, labels=()):
        save_edges(conn, chain, edges, trace_id, hop)
        save_txs(conn, chain, txs)
        g.merge_edges(chain, edges)
        g.merge_txs(chain, txs)
        if labels:
            save_labels(conn, trace_id, chain, labels)
            g.merge_labels(chain, labels, {})

    return sink, trace_id, lambda: (g.close(), conn.close())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("wallet")
    ap.add_argument("chain", choices=["btc", "eth", "polygon"])
    ap.add_argument("--since-block", type=int, default=0)
    ap.add_argument("--until-block", type=int, default=10**9, help="block snapshot; ignore later txs (§12)")
    ap.add_argument("--max-hops", type=int, default=4)
    ap.add_argument("--fanout", type=int, default=5)
    ap.add_argument("--expect", choices=["ATTRIBUTED", "ATTRIBUTED_INFRA", "UNATTRIBUTED"])
    ap.add_argument("--record", choices=["controlled_case", "public_case", "discovery_case", "evm_case"])
    ap.add_argument("--source-doc", default="")
    ap.add_argument("--graph", action="store_true", help="persist edges to Postgres and MERGE into Neo4j")
    ap.add_argument("--offline", action="store_true", help="serve every read from the §12 raw store")
    a = ap.parse_args()
    if a.max_hops > HARD_MAX_HOPS:
        sys.exit(f"--max-hops capped at {HARD_MAX_HOPS}")

    sink = trace_id = close = None
    if a.graph:
        sink, trace_id, close = graph_sink(a.chain)
        print(f"graph: trace_id {trace_id}")
    r = trace(a.wallet, a.chain, a.since_block, a.until_block, a.max_hops, a.fanout, sink=sink, offline=a.offline)
    if trace_id:
        r["trace_id"] = trace_id
        close()

    print(json.dumps(r, indent=2, default=str))
    print(f"\n{r['result']}: " + (f"{r['entity']} via {r['endpoint']} ({r['role_basis']}) in {r['hops']} hop(s)"
                                  if r["endpoint"] else f"no labeled endpoint — {r['reason']}")
          + f" · {r['api_calls']} API calls ({r['upstream_calls']} upstream) · {r['wall_clock_s']}s")
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
    _selfcheck()   # pins the 90% sweep rule (day-1 BitMEX false positive) before any trace runs
    main()
