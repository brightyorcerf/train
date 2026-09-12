"""Normalized edges in Postgres — the system of record Neo4j is rebuilt from (§7.6, scripts/rebuild_graph.py).

Writes are idempotent on the §7.2 edge identity (chain, src, dst, tx_hash, kind, idx), so a retried
Celery task or a re-run trace adds nothing.
"""
import json

UPSERT = """
INSERT INTO edge (chain, src, dst, kind, tx_hash, idx, value, asset, decimals, block, ts, meta)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (chain, src, dst, tx_hash, kind, idx) DO UPDATE SET value = EXCLUDED.value
RETURNING id
"""


def save_edges(conn, chain: str, edges, trace_id=None, hop: int = 0) -> list[int]:
    ids = []
    with conn.cursor() as cur:
        for e in edges:
            meta = dict(e.meta or {})
            cur.execute(UPSERT, (chain, e.src, e.dst, e.kind, e.tx_hash, str(e.index or ""), e.value, e.asset,
                                 int(meta.get("decimals", 8 if chain == "btc" else 18)), e.block, e.ts,
                                 json.dumps(meta)))
            ids.append(cur.fetchone()[0])
        if trace_id is not None and ids:
            cur.executemany("INSERT INTO trace_edge (trace_id, edge_id, hop) VALUES (%s, %s, %s) "
                            "ON CONFLICT DO NOTHING", [(trace_id, i, hop) for i in ids])
    return ids


def save_txs(conn, chain: str, txs) -> int:
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO utxo_tx (chain, hash, block, ts, total_in, total_out) VALUES (%s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (chain, hash) DO NOTHING",
            [(chain, t.hash, t.block, t.ts, t.total_in, t.total_out) for t in txs])
    return len(txs)


def load_edges(conn, chain: str | None = None, trace_id=None):
    q = ("SELECT chain, src, dst, kind, tx_hash, idx, value, asset, decimals, block, ts, meta FROM edge e"
         + (" JOIN trace_edge te ON te.edge_id = e.id" if trace_id else "") + " WHERE true"
         + (" AND chain = %(chain)s" if chain else "") + (" AND te.trace_id = %(trace)s" if trace_id else "")
         + " ORDER BY e.id")
    return conn.execute(q, {"chain": chain, "trace": trace_id}).fetchall()


def load_txs(conn, chain: str | None = None):
    return conn.execute("SELECT chain, hash, block, ts, total_in, total_out FROM utxo_tx"
                        + (" WHERE chain = %s" if chain else "") + " ORDER BY chain, hash",
                        (chain,) if chain else ()).fetchall()
