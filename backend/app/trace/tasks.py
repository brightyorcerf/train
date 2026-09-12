"""BFS as Celery chords (§9.2): each hop fans the frontier out in parallel, joins, decides the next
frontier, and recurses. Same engine as the CLI (trace/engine.py) — only the driver differs.

Idempotency (§12): every task writes through MERGE / ON CONFLICT, and every upstream read goes to the
content-addressed store, so a retried task costs nothing and changes nothing. A killed worker can
re-run its level.

Determinism note: a hit's `calls_at_hit` is the budget spent up to the END of its level. Parallel
tasks have no ordering, so a per-call number would be fiction; the level boundary is real.
"""
import hashlib
import json
import uuid

from celery import chord

from app.attribution.engine import attribute_result
from app.db import connect
from app.db.edges import save_edges, save_txs
from app.db.evidence import save_labels
from app.graph.client import Graph
from app.labels.propagate import SameOwner, propagate
from app.labels.registry import PgRegistry
from app.trace.engine import HARD_MAX_HOPS, MAX_CALLS, Tracer, _hit
from worker.celery_app import app

STATES = ("QUEUED", "FETCHING", "SCORING", "DONE", "FAILED")


# ---------- case / job bookkeeping (§7.6, §12 pins) ----------
def open_case(wallet: str, chain: str, snapshot: int, label_set: str | None = None, source="manual",
              params: dict | None = None) -> tuple[str, str, dict]:
    with connect() as c:
        ls = label_set or c.execute("SELECT version FROM label_set ORDER BY created_at DESC LIMIT 1").fetchone()[0]
        case_id, trace_id = str(uuid.uuid4()), str(uuid.uuid4())
        pins = {"snapshot_block": snapshot, "label_set_version": ls, "adapter_version": "day6",
                "max_calls": MAX_CALLS}
        c.execute("INSERT INTO cases (id, wallet, chain, source, snapshot_block, label_set_version, params) "
                  "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                  (case_id, wallet, chain, source, snapshot, ls, json.dumps(params or {})))
        c.execute("INSERT INTO trace_jobs (id, case_id, state, pins, started_at) "
                  "VALUES (%s, %s, 'QUEUED', %s, now())", (trace_id, case_id, json.dumps(pins)))
    return case_id, trace_id, pins


def set_state(trace_id, state, hop=None, progress=None, result=None, error=None):
    with connect() as c:
        c.execute("UPDATE trace_jobs SET state = %s, current_hop = coalesce(%s, current_hop), "
                  "progress = coalesce(%s, progress), result = coalesce(%s, result), "
                  "error = coalesce(%s, error), finished_at = CASE WHEN %s IN ('DONE','FAILED') "
                  "THEN now() ELSE finished_at END WHERE id = %s",
                  (state, hop, progress, json.dumps(result) if result else None, error, state, trace_id))


def job(trace_id) -> dict:
    with connect() as c:
        r = c.execute("SELECT state, current_hop, progress, result, error FROM trace_jobs WHERE id = %s",
                      (trace_id,)).fetchone()
    return dict(zip(("state", "current_hop", "progress", "result", "error"), r)) if r else {}


# ---------- the two tasks ----------
@app.task(queue="fetch", bind=True, max_retries=2)
def expand_task(self, ctx: dict, node: dict) -> dict:
    """One frontier node: fetch its outgoing value, run sweep + boundary checks, persist its edges."""
    t = _tracer(ctx)
    r = t.expand_node(node)
    conn = connect(autocommit=True)
    try:
        save_edges(conn, ctx["chain"], r["edges"], ctx["trace_id"], node["hop"])
        save_txs(conn, ctx["chain"], r["txs"])
        if ctx.get("graph"):
            g = Graph()
            g.merge_edges(ctx["chain"], r["edges"])
            g.merge_txs(ctx["chain"], r["txs"])
            g.close()
        conn.execute("INSERT INTO audit_log (trace_id, input_params, upstream_hash, actor) VALUES (%s,%s,%s,%s)",
                     (ctx["trace_id"], json.dumps({"address": node["addr"], "hop": node["hop"],
                                                   "chain": ctx["chain"], "snapshot": ctx["until"]}),
                      hashlib.sha256("\n".join(t.prov.requests).encode()).hexdigest(), "worker"))
    finally:
        conn.close()
    return {"node": node, "moves": r["moves"], "flags": r["flags"], "so_edges": r["so_edges"],
            "hit": _hit(node, r["hit"], node["hop"], 0) if r["hit"] else None,
            "evidence": r["evidence"], "stop": r["stop"], "calls": t.prov.calls,
            "upstream": t.prov.upstream, "stale": t.prov.stale}


@app.task(queue="trace")
def level_done(results: list[dict], ctx: dict, state: dict) -> dict:
    """Chord callback: merge one hop's results, decide the next frontier, recurse or finish."""
    t = _tracer(ctx)
    hop = state["hop"]
    state["calls"] += sum(r["calls"] for r in results)
    state["upstream"] += sum(r["upstream"] for r in results)
    state["stale"] += [s for r in results for s in r["stale"]]
    nodes = {n["addr"]: n for n in state["nodes"]}
    nxt = []
    for r in sorted(results, key=lambda r: r["node"]["addr"]):
        state["flags"] += r["flags"]
        state["so_edges"] += r["so_edges"]
        if r["evidence"]:
            state["evidence"].append(r["evidence"])
        if r["hit"]:
            state["hits"].append({**r["hit"], "calls_at_hit": state["calls"]})
        if not r["stop"]:
            nxt += t.absorb(r["moves"], r["node"], hop, nodes, state["hits"], state["flags"])
    for h in state["hits"]:
        h.setdefault("calls_at_hit", state["calls"])
    new = propagate(t.reg, ctx["chain"], [SameOwner(*e) for e in state["so_edges"]])
    state["nodes"] = list(nodes.values())

    done = bool(state["hits"]) or not nxt or hop >= ctx["max_hops"] or state["calls"] >= MAX_CALLS
    if not done:
        set_state(ctx["trace_id"], "FETCHING", hop=hop + 1, progress=round(hop / ctx["max_hops"], 2))
        state["hop"] = hop + 1
        return _dispatch(ctx, nxt, state)

    reason = ("hit" if state["hits"] else "no further outgoing value" if not nxt
              else f"api-call budget ({MAX_CALLS}) exhausted" if state["calls"] >= MAX_CALLS
              else f"hop budget ({ctx['max_hops']}) exhausted")
    set_state(ctx["trace_id"], "SCORING", hop=hop, progress=1.0)
    t.prov.calls, t.prov.upstream, t.prov.stale = state["calls"], state["upstream"], state["stale"]
    derived = [l for labs in t.reg.labels.values() for l in labs] + new
    out = t.result(ctx["wallet"], state["hits"], state["flags"], {n["addr"]: n for n in state["nodes"]},
                   state["so_edges"], state["evidence"], reason, state.get("wall", 0))
    out["trace_id"], out["case_id"], out["pins"] = ctx["trace_id"], ctx["case_id"], ctx["pins"]
    out = attribute_result(out)   # §10/§11: ranked VASPs + one crowned target (or an honest abstention)
    conn = connect(autocommit=True)
    try:
        if derived:
            save_labels(conn, ctx["trace_id"], ctx["chain"], derived)
        if ctx.get("graph"):
            g = Graph()
            g.merge_edges(ctx["chain"], [])
            g.merge_labels(ctx["chain"], derived, t.reg.entities)
            g.close()
    finally:
        conn.close()
    set_state(ctx["trace_id"], "DONE", result=out)
    return out


def _dispatch(ctx, frontier, state):
    return chord([expand_task.s(ctx, n) for n in sorted(frontier, key=lambda n: n["addr"])])(
        level_done.s(ctx, state)).id


def _tracer(ctx) -> Tracer:
    return Tracer(ctx["chain"], ctx["until"], ctx["pins"]["label_set_version"], ctx["fanout"],
                  offline=ctx.get("offline", False))


def start_trace(wallet: str, chain: str, snapshot: int, max_hops=4, fanout=5, label_set=None, graph=False,
                offline=False, source="manual") -> dict:
    """Create the case + job (pins recorded), then kick off hop 1. Returns ids immediately (202, §14)."""
    case_id, trace_id, pins = open_case(wallet, chain, snapshot, label_set, source,
                                        {"max_hops": max_hops, "fanout": fanout})
    ctx = {"wallet": wallet, "chain": chain, "until": snapshot, "max_hops": min(max_hops, HARD_MAX_HOPS),
           "fanout": fanout, "trace_id": trace_id, "case_id": case_id, "pins": pins, "graph": graph,
           "offline": offline}
    reg = PgRegistry(pins["label_set_version"])
    start = {"addr": wallet, "hop": 0, "since": 0, "via": None, "utxos": None, "utxo_via": {}}
    state = {"hop": 1, "nodes": [start], "hits": [], "flags": [], "so_edges": [], "evidence": [],
             "calls": 0, "upstream": 0, "stale": []}
    lab = reg.best(chain, wallet)
    if lab:
        state["hits"].append(_hit(start, lab, 0, 0))
    set_state(trace_id, "FETCHING", hop=1, progress=0.0)
    _dispatch(ctx, [start], state)
    return {"case_id": case_id, "trace_id": trace_id, "pins": pins}
