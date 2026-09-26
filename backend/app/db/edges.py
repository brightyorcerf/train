"""Normalized edges in Postgres — the system of record Neo4j is rebuilt from (§7.6, scripts/rebuild_graph.py).

Writes are idempotent on the §7.2 edge identity (chain, src, dst, tx_hash, kind, idx), so a retried
Celery task or a re-run trace adds nothing.
"""
import json

# One statement per batch, not one round trip per edge: the per-row upsert was the entire `graph`
# phase of a trace (3.4s of worker time on 72 logical calls). unnest() carries the batch as arrays;
# RETURNING yields an id for inserted AND conflicting rows (DO UPDATE touches them), and the ids are
# only ever used as a set, so row order does not matter.
UPSERT = """
WITH up AS (
  INSERT INTO edge (chain, src, dst, kind, tx_hash, idx, value, asset, decimals, block, ts, meta)
  SELECT %(chain)s, * FROM unnest(%(src)s::text[], %(dst)s::text[], %(kind)s::text[], %(tx)s::text[],
      %(idx)s::text[], %(value)s::numeric[], %(asset)s::text[], %(dec)s::int[], %(block)s::bigint[],
      %(ts)s::bigint[], %(meta)s::jsonb[])
  ON CONFLICT (chain, src, dst, tx_hash, kind, idx) DO UPDATE SET value = EXCLUDED.value
  RETURNING id
), te AS (
  INSERT INTO trace_edge (trace_id, edge_id, hop)
  SELECT %(trace)s::uuid, id, %(hop)s FROM up WHERE %(trace)s::uuid IS NOT NULL
  ON CONFLICT DO NOTHING
)
SELECT id FROM up
"""


def save_edges(conn, chain: str, edges, trace_id=None, hop: int = 0) -> list[int]:
    # ON CONFLICT DO UPDATE refuses to touch one row twice in a statement, so collapse repeats of the
    # §7.2 identity first (last write wins, as the old row-by-row loop did).
    rows = {}
    for e in edges:
        meta = dict(e.meta or {})
        rows[(e.src, e.dst, e.tx_hash, e.kind, str(e.index or ""))] = (
            e.value, e.asset, int(meta.get("decimals", 8 if chain == "btc" else 18)), e.block, e.ts,
            json.dumps(meta))
    if not rows:
        return []
    cols = list(zip(*[(*k, *v) for k, v in rows.items()], strict=True))
    names = ("src", "dst", "tx", "kind", "idx", "value", "asset", "dec", "block", "ts", "meta")
    params = {"chain": chain, "trace": str(trace_id) if trace_id is not None else None, "hop": hop,
              **{n: list(c) for n, c in zip(names, cols, strict=True)}}
    return [r[0] for r in conn.execute(UPSERT, params).fetchall()]


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
