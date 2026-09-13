"""Neo4j: the DERIVED graph index (§7.6). Every write is a MERGE with the §7.2 edge identity, so
re-running a trace or rebuilding from Postgres is idempotent.

Both data models live here side by side (§7.1-7.3), which is the point:
    account (EVM)   (:Address)-[:SENT {tx_hash, kind, idx, value, asset}]->(:Address)
    UTXO   (BTC)    (:Address)-[:FUNDS {vin}]->(:Tx)-[:CREDITS {vout, value}]->(:Address)
    clustering      (:Address)-[:SAME_OWNER {confidence, tx_hash}]->(:Address)   (heuristic, §7.4)
Labels are attached only for addresses the trace actually reached — the pinned label set has
~376k rows and Neo4j is an index, not a copy of it.
"""
from neo4j import GraphDatabase

from app.core.config import settings

CONSTRAINTS = [
    "CREATE CONSTRAINT address_key IF NOT EXISTS FOR (a:Address) REQUIRE (a.chain, a.address) IS UNIQUE",
    "CREATE CONSTRAINT tx_key IF NOT EXISTS FOR (t:Tx) REQUIRE (t.chain, t.hash) IS UNIQUE",
    "CREATE CONSTRAINT vasp_key IF NOT EXISTS FOR (v:VASP) REQUIRE v.id IS UNIQUE",
    "CREATE INDEX address_labeled IF NOT EXISTS FOR (a:Address) ON (a.is_labeled)",
]
ACCOUNT_KINDS = ("native", "internal", "erc20")


class Graph:
    def __init__(self, uri=None, user=None, password=None):
        self.driver = GraphDatabase.driver(uri or settings.neo4j_uri,
                                           auth=(user or settings.neo4j_user, password or settings.neo4j_password))

    def close(self):
        self.driver.close()

    def init(self):
        with self.driver.session() as s:
            for c in CONSTRAINTS:
                s.run(c)

    def wipe(self):
        with self.driver.session() as s:
            s.run("MATCH (n) DETACH DELETE n")

    # ---------- writes ----------
    def merge_edges(self, chain: str, edges, batch: int = 500) -> int:
        """edges: providers.base.Edge (or any object with the same fields). Account kinds become SENT;
        funds/credits become the :Tx hypernode shape; same_owner becomes the clustering edge."""
        rows = [{"src": e.src, "dst": e.dst, "kind": e.kind, "tx_hash": e.tx_hash, "idx": str(e.index or ""),
                 "value": float(e.value), "asset": e.asset, "block": e.block, "ts": e.ts,
                 "conf": float(e.meta.get("confidence", 0)) if e.meta else 0.0} for e in edges]
        n = 0
        with self.driver.session() as s:
            for kinds, q in ((ACCOUNT_KINDS, _SENT), (("funds",), _FUNDS), (("credits",), _CREDITS),
                             (("same_owner",), _SAME_OWNER)):
                part = [r for r in rows if r["kind"] in kinds]
                for i in range(0, len(part), batch):
                    s.run(q, chain=chain, rows=part[i:i + batch])
                n += len(part)
        return n

    def merge_txs(self, chain: str, txs, batch: int = 500) -> int:
        rows = [{"hash": t.hash, "block": t.block, "ts": t.ts, "total_in": float(t.total_in),
                 "total_out": float(t.total_out)} for t in txs]
        with self.driver.session() as s:
            for i in range(0, len(rows), batch):
                s.run("UNWIND $rows AS r MERGE (t:Tx {chain:$chain, hash:r.hash}) "
                      "SET t.block=r.block, t.ts=r.ts, t.total_in=r.total_in, t.total_out=r.total_out",
                      chain=chain, rows=rows[i:i + batch])
        return len(rows)

    def merge_labels(self, chain: str, labels, entities: dict, batch: int = 500) -> int:
        rows = [{"address": l.address, "role": l.role, "entity": l.entity, "source": l.source,
                 "basis": l.basis, "confidence": float(l.confidence), "provenance": l.provenance[:400],
                 "name": entities[l.entity].name if l.entity in entities else l.entity,
                 "type": entities[l.entity].type if l.entity in entities else "unknown",
                 "sahyog": entities[l.entity].sahyog if l.entity in entities else "unknown"} for l in labels]
        with self.driver.session() as s:
            for i in range(0, len(rows), batch):
                s.run(_LABELS, chain=chain, rows=rows[i:i + batch])
        return len(rows)

    # ---------- reads ----------
    def counts(self) -> dict:
        with self.driver.session() as s:
            out = {r["l"]: r["n"] for r in s.run("MATCH (n) UNWIND labels(n) AS l RETURN l, count(*) AS n")}
            out |= {r["t"]: r["n"] for r in s.run("MATCH ()-[r]->() RETURN type(r) AS t, count(*) AS n")}
        return out

    def candidates(self, chain: str, start: str, max_hops: int = 12) -> list[dict]:
        """Every labeled endpoint reachable from start, with a REPRESENTATIVE path (§10 — this is not
        the winner selector; ranking is). BTC paths alternate Address/Tx, so hops = relationships/2."""
        with self.driver.session() as s:
            return [r.data() for r in s.run(_CANDIDATES.replace("$$max", str(int(max_hops))),
                                            chain=chain, start=start)]

    def path(self, chain: str, start: str, endpoint: str, max_hops: int = 12) -> list[dict]:
        with self.driver.session() as s:
            r = s.run(_PATH.replace("$$max", str(int(max_hops))), chain=chain, start=start, dst=endpoint).single()
            return r and r.data()


_SENT = """
UNWIND $rows AS r
MERGE (a:Address {chain:$chain, address:r.src})
MERGE (b:Address {chain:$chain, address:r.dst})
MERGE (a)-[e:SENT {tx_hash:r.tx_hash, kind:r.kind, idx:r.idx}]->(b)
SET e.value=r.value, e.asset=r.asset, e.block=r.block, e.ts=r.ts
"""
_FUNDS = """
UNWIND $rows AS r
MERGE (a:Address {chain:$chain, address:r.src})
MERGE (t:Tx {chain:$chain, hash:r.tx_hash})
MERGE (a)-[e:FUNDS {vin:r.idx}]->(t)
SET e.value=r.value, e.block=r.block, e.ts=r.ts
"""
_CREDITS = """
UNWIND $rows AS r
MERGE (t:Tx {chain:$chain, hash:r.tx_hash})
MERGE (b:Address {chain:$chain, address:r.dst})
MERGE (t)-[e:CREDITS {vout:r.idx}]->(b)
SET e.value=r.value, e.block=r.block, e.ts=r.ts
"""
_SAME_OWNER = """
UNWIND $rows AS r
MERGE (a:Address {chain:$chain, address:r.src})
MERGE (b:Address {chain:$chain, address:r.dst})
MERGE (a)-[e:SAME_OWNER {tx_hash:r.tx_hash}]->(b)
SET e.confidence=r.conf
"""
_LABELS = """
UNWIND $rows AS r
MERGE (a:Address {chain:$chain, address:r.address}) SET a.is_labeled = true
MERGE (v:VASP {id:r.entity}) SET v.name=r.name, v.type=r.type, v.sahyog=r.sahyog
MERGE (a)-[:HAS_LABEL]->(l:Label {role:r.role, entity:r.entity, source:r.source, basis:r.basis})
SET l.confidence=r.confidence, l.provenance=r.provenance
MERGE (l)-[:OF]->(v)
"""
_CANDIDATES = """
MATCH (s:Address {chain:$chain, address:$start})
MATCH (d:Address {chain:$chain, is_labeled:true}) WHERE d <> s
MATCH p = shortestPath( (s)-[:SENT|FUNDS|CREDITS|SAME_OWNER*..$$max]->(d) )
MATCH (d)-[:HAS_LABEL]->(l:Label)-[:OF]->(v:VASP)
RETURN d.address AS endpoint, l.role AS role, l.basis AS basis, l.source AS source,
       l.confidence AS confidence, v.id AS entity, v.sahyog AS sahyog,
       length(p) AS rels, CASE WHEN $chain = 'btc' THEN length(p)/2 ELSE length(p) END AS hops
ORDER BY hops, endpoint
"""
_PATH = """
MATCH (s:Address {chain:$chain, address:$start}), (d:Address {chain:$chain, address:$dst})
MATCH p = shortestPath( (s)-[:SENT|FUNDS|CREDITS|SAME_OWNER*..$$max]->(d) )
RETURN [n IN nodes(p) | coalesce(n.address, n.hash)] AS nodes,
       [r IN relationships(p) | type(r)] AS rels, length(p) AS len
ORDER BY len LIMIT 1
"""
