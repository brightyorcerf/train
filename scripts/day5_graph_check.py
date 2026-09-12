"""Day-5 real-data PASS/FAIL: Postgres as system of record + Neo4j as a derived index (§7.6, §12),
both data models, correct MERGE identity, candidate enumeration, and rebuild_graph.

Runs two real traces (BTC UTXO + EVM account) through the engine with the graph sink, then checks
what landed. Re-runs are served from the §12 raw store, so this costs almost no provider calls.

    docker compose up -d postgres neo4j
    backend/.venv/bin/python scripts/day5_graph_check.py
"""
import subprocess
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from day1_verify import trace  # noqa: E402

from app.db import connect, init_schema  # noqa: E402
from app.db.edges import save_edges, save_txs  # noqa: E402
from app.db.evidence import save_labels  # noqa: E402
from app.graph.client import Graph  # noqa: E402
from app.labels.registry import PgRegistry  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
ZHDANOVA, BTC_SNAPSHOT = "1Ljk8RNNabkZ9bfDYQBn98XfFozJhTjqcZ", 966553
EVM_SUSPECT, ETH_SNAPSHOT = "0x4ab63073ad218106583ad64191d59c9db5099984", 25906777   # sweep-proven deposit addr
MULTI = "0xcc797f4ca44cc0801d002acf2497fa97ced994d47550d3e4d8f2f78e87457224"    # 3 WETH movements, one hash
MULTI_PARTY = "0xa95e77aae62b5e61b4f6f1b1ecca862dbc9ff7e0"
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  {detail}")


def run(wallet, chain, until, hops, conn, g):
    tid = str(uuid.uuid4())
    n = [0]

    def sink(hop, edges, txs, labels=()):
        save_edges(conn, chain, edges, tid, hop)
        save_txs(conn, chain, txs)
        g.merge_edges(chain, edges)
        g.merge_txs(chain, txs)
        if labels:
            save_labels(conn, tid, chain, labels)
            g.merge_labels(chain, labels, {})
        n[0] += len(edges)

    r = trace(wallet, chain, until_block=until, max_hops=hops, sink=sink)
    return tid, r, n[0]


def main():
    t0 = time.time()
    init_schema()
    conn, g = connect(autocommit=True), Graph()
    conn.execute("TRUNCATE trace_edge, edge, evidence, utxo_tx")   # self-contained: count only this run
    g.wipe()
    g.init()

    btc_id, btc_r, btc_n = run(ZHDANOVA, "btc", BTC_SNAPSHOT, 2, conn, g)
    evm_id, evm_r, evm_n = run(EVM_SUSPECT, "eth", ETH_SNAPSHOT, 1, conn, g)
    print(f"   traces: btc {btc_r['result']} ({btc_n} edges) · eth {evm_r['result']} ({evm_n} edges)")

    n_pg = conn.execute("SELECT count(*) FROM edge").fetchone()[0]
    counts = g.counts()
    check("Postgres holds every emitted edge (system of record)", n_pg == btc_n + evm_n,
          f"{n_pg} rows == {btc_n} btc + {evm_n} eth")
    check("both data models in one graph: :Tx hypernodes for BTC, :SENT for EVM (§7.1-7.3)",
          counts.get("Tx", 0) > 0 and counts.get("FUNDS", 0) > 0 and counts.get("CREDITS", 0) > 0
          and counts.get("SENT", 0) > 0, f"{counts}")
    with g.driver.session() as s:
        bad = s.run("MATCH (a:Address {chain:'btc'})-[r]->(b:Address) RETURN count(r) AS n").single()["n"]
        so = s.run("MATCH (:Address)-[r:SAME_OWNER]->(:Address) RETURN count(r) AS n").single()["n"]
    check("BTC never gets fabricated address->address edges (§7.3)", bad == so,
          f"{bad} address->address rels, all {so} of them SAME_OWNER (clustering, not value flow)")

    # MERGE identity: re-running the same trace must not duplicate anything (§12 idempotency)
    before = (n_pg, counts.get("SENT", 0), counts.get("CREDITS", 0))
    run(ZHDANOVA, "btc", BTC_SNAPSHOT, 2, conn, g)
    run(EVM_SUSPECT, "eth", ETH_SNAPSHOT, 1, conn, g)
    after = (conn.execute("SELECT count(*) FROM edge").fetchone()[0],
             g.counts().get("SENT", 0), g.counts().get("CREDITS", 0))
    check("re-running a trace is idempotent in Postgres AND Neo4j (MERGE, not CREATE)", before == after,
          f"{before} -> {after}")

    # one tx hash emits many movements and they survive as separate edges (§7.2 edge identity)
    from app.providers.etherscan_v2 import EtherscanV2Provider  # noqa: PLC0415
    multi = [e for e in EtherscanV2Provider("eth").movements(MULTI_PARTY, ETH_SNAPSHOT, ETH_SNAPSHOT - 500)
             if e.tx_hash == MULTI]
    save_edges(conn, "eth", multi)
    g.merge_edges("eth", multi)
    dup = conn.execute("SELECT tx_hash, count(*) FROM edge WHERE tx_hash = %s GROUP BY 1", (MULTI,)).fetchone()
    with g.driver.session() as s:
        in_graph = s.run("MATCH ()-[r:SENT {tx_hash:$h}]->() RETURN count(r) AS n", h=dup[0]).single()["n"]
    check("one tx hash -> many edges, none collapsed by the MERGE key (§7.2)", in_graph == dup[1] and dup[1] >= 2,
          f"tx {dup[0][:14]}… has {dup[1]} movements in Postgres and {in_graph} in Neo4j")
    check("…and Postgres agrees with the adapter on that tx", dup[1] == len(multi), f"{len(multi)} from the adapter")

    # candidate enumeration (§10) — the graph must find the endpoint the engine reported.
    # Label policy is the same one rebuild_graph uses: pinned labels for every address reached.
    reg = PgRegistry(conn=conn)
    for chain in ("btc", "eth"):
        addrs = [r[0] for r in conn.execute(
            "SELECT src FROM edge WHERE chain=%s UNION SELECT dst FROM edge WHERE chain=%s", (chain, chain))]
        g.merge_labels(chain, [l for a in sorted(addrs) for l in reg.lookup(chain, a)], reg.entities)
    cands = g.candidates("btc", ZHDANOVA)
    got = [c for c in cands if c["endpoint"] == btc_r["endpoint"]]
    check("Cypher candidate enumeration finds the engine's endpoint at the same hop count (§10)",
          got and got[0]["hops"] == btc_r["hops"],
          f"{len(cands)} candidates; {btc_r['endpoint'][:12]}… at {got and got[0]['hops']} hops "
          f"(engine: {btc_r['hops']})")
    p = g.path("btc", ZHDANOVA, btc_r["endpoint"])
    check("representative path alternates Address -> Tx -> Address (hypernode walk)",
          p and p["rels"] == ["FUNDS", "CREDITS"] * (p["len"] // 2), f"{p and p['rels']}")

    # rebuild from Postgres alone (§7.6) — Neo4j is derived, and provably so
    before = g.counts()
    g.wipe()
    out = subprocess.run([sys.executable, str(REPO / "scripts" / "rebuild_graph.py")],
                         capture_output=True, text=True, cwd=REPO)
    rebuilt = g.counts()
    check("rebuild_graph.py restores the graph from Postgres alone",
          out.returncode == 0 and rebuilt.get("Tx") == before.get("Tx")
          and rebuilt.get("SENT") == before.get("SENT") and rebuilt.get("CREDITS") == before.get("CREDITS"),
          f"{out.stdout.strip().splitlines()[-1][:120] if out.stdout else out.stderr[-200:]}")
    cands2 = g.candidates("btc", ZHDANOVA)
    check("candidates identical after the rebuild",
          [c["endpoint"] for c in cands2] == [c["endpoint"] for c in cands],
          f"{len(cands2)} candidates")

    mem = subprocess.run(["docker", "stats", "--no-stream", "--format", "{{.Name}} {{.MemUsage}}",
                          "train-neo4j-1"], capture_output=True, text=True).stdout.strip()
    n = sum(results)
    print(f"\n{n}/{len(results)} checks ok · {time.time() - t0:.1f}s · neo4j {mem or 'n/a'}")
    print("OVERALL: PASS" if n == len(results) else "OVERALL: FAIL")
    g.close()
    sys.exit(0 if n == len(results) else 1)


if __name__ == "__main__":
    main()
