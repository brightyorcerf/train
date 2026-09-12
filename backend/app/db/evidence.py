"""Labels a TRACE derived (sweep-proven deposits, cluster propagation) — evidence, not label set (§7.6).

They stay out of address_label on purpose (§12: a case must not depend on which cases ran before it),
but they are what makes an endpoint a candidate, so they are persisted per trace and re-attached when
Neo4j is rebuilt from Postgres.
"""
import json

from app.labels.registry import Label


def save_labels(conn, trace_id, chain: str, labels) -> int:
    rows = [(trace_id, l.entity, l.basis, float(l.confidence),
             json.dumps({"chain": chain, "address": l.address, "role": l.role, "source": l.source,
                         "provenance": l.provenance})) for l in labels]
    with conn.cursor() as cur:
        cur.execute("DELETE FROM evidence WHERE trace_id = %s", (trace_id,))   # a re-run replaces its own
        cur.executemany("INSERT INTO evidence (trace_id, candidate_entity, factor, value, provenance) "
                        "VALUES (%s, %s, %s, %s, %s)", rows)
    return len(rows)


def load_labels(conn, chain: str | None = None, trace_id=None) -> list[Label]:
    q = ("SELECT candidate_entity, factor, value, provenance FROM evidence WHERE true"
         + (" AND provenance->>'chain' = %(chain)s" if chain else "")
         + (" AND trace_id = %(trace)s" if trace_id else "") + " ORDER BY id")
    return [Label(p["address"], p["chain"], p["role"], ent, p["source"], conf, basis, p["provenance"])
            for ent, basis, conf, p in conn.execute(q, {"chain": chain, "trace": trace_id})]
