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
import time
import uuid

from celery import chord
from neo4j.exceptions import DriverError, Neo4jError

from app.api.timeline import Phases
from app.attribution.engine import attribute_result
from app.core.config import ADAPTER_VERSION
from app.db import connect
from app.db.edges import save_edges, save_txs
from app.db.evidence import save_labels
from app.graph.client import Graph
from app.labels.propagate import SameOwner, propagate
from app.labels.registry import Label, PgRegistry
from app.trace.engine import HARD_MAX_HOPS, MAX_CALLS, Tracer, _hit
from worker.celery_app import app

# ---------- case / job bookkeeping (§7.6, §12 pins) ----------
def open_case(wallet: str, chain: str, snapshot: int, label_set: str | None = None, source="manual",
              params: dict | None = None) -> tuple[str, str, dict]:
    with connect() as c:
        row = c.execute("SELECT version FROM label_set ORDER BY created_at DESC LIMIT 1").fetchone()
        if not (label_set or row):
            raise LookupError("no label set in Postgres; run `python -m app.labels.ingest` first "
                              "(see README Quickstart)")
        ls = label_set or row[0]
        case_id, trace_id = str(uuid.uuid4()), str(uuid.uuid4())
        pins = {"snapshot_block": snapshot, "label_set_version": ls,
                "adapter_version": ADAPTER_VERSION, "max_calls": MAX_CALLS}
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


@app.task(queue="trace")
def trace_failed(request, exc, tb, trace_id: str):
    """Errback: a dead chord member must land the job in FAILED, not leave it FETCHING forever.

    Celery logs a ChordError and stops there — nothing else writes trace_jobs — so without this the
    row keeps its last in-flight state and every poller spins indefinitely. Observed 2026-09-13 on
    trace 44dfa65d: FETCHING, hop 3, finished_at NULL, error NULL, with the real cause visible only
    in the worker log.

    Guarded on state <> 'DONE' because EVERY header task links here: the first failure records the
    cause, and a straggler that dies after the callback already finished cannot un-finish a good
    run. coalesce keeps the first error rather than the last."""
    with connect() as c:
        c.execute("UPDATE trace_jobs SET state = 'FAILED', finished_at = now(), "
                  "error = coalesce(error, %s) WHERE id = %s AND state <> 'DONE'",
                  (f"{type(exc).__name__}: {str(exc)[:400]}", trace_id))


def _index(chain, edges, txs, labels=None, entities=None) -> list[str]:
    """Write the DERIVED Neo4j index (§7.6) — and never let it kill a trace.

    Postgres is the system of record and is already committed by the time this runs, so a Neo4j
    write-side drop costs us an index, not a result. Observed 2026-09-13: one 2935-edge wallet
    tripped Neo4j's 'Response write failure', the driver raised ServiceUnavailable, and the whole
    chord died with it — discarding a trace whose edges were already durable in Postgres.

    So the failure is recorded as a `partial:` flag (the §8 convention the provider paths already
    use, which engine.result() turns into `partial: true`) and scripts/rebuild_graph.py repopulates
    the index afterward."""
    global _GRAPH
    try:
        if _GRAPH is None:
            _GRAPH = Graph()
        g = _GRAPH
        if edges:
            g.merge_edges(chain, edges)
        if txs:
            g.merge_txs(chain, txs)
        if labels:
            g.merge_labels(chain, labels, entities or {})
        return []
    except (DriverError, Neo4jError) as e:
        _drop_graph()   # a broken driver must not be reused by this worker's next task
        return [f"partial:graph_index_unavailable ({len(edges)} edges, {len(txs)} txs, "
                f"{len(labels or [])} labels): {type(e).__name__} {str(e)[:100]}"]


# One Neo4j driver per worker PROCESS, created lazily (so after Celery's fork, never inherited across
# it). It used to be built and closed per frontier node, and that connect/close cycle was the whole
# `graph` phase: 8.1s of worker time on a 4s collect_all trace. The driver is a connection pool and
# is meant to be long-lived; a failed write drops it so the next task reconnects.
_GRAPH: Graph | None = None


def _drop_graph():
    global _GRAPH
    g, _GRAPH = _GRAPH, None
    if g is not None:
        try:
            g.close()
        except (DriverError, Neo4jError):
            pass


STALL_AFTER_S = 600      # the console gives up at 600s too; past that a job is not "running"


def sweep_stalled(trace_id=None) -> int:
    """Land jobs that stopped making progress in FAILED. -> rows changed.

    The errback only fires when a task RAISES. A worker killed mid-chord raises nothing, so the row
    kept its last in-flight state forever (Redis redelivery is capped by visibility_timeout, and
    the chord callback can be lost outright). Without this, "a trace either finishes or lands
    FAILED" was untrue for the one failure mode a live demo actually hits: a dead worker.
    """
    with connect() as c:
        return c.execute(
            "UPDATE trace_jobs SET state = 'FAILED', finished_at = now(), "
            "error = coalesce(error, 'stalled: no terminal state after "
            f"{STALL_AFTER_S}s; worker lost mid-trace (see the worker log); re-run the case') "
            "WHERE state IN ('QUEUED','FETCHING','SCORING') "
            f"AND coalesce(started_at, now()) < now() - interval '{STALL_AFTER_S} seconds'"
            + (" AND id = %s" if trace_id else ""),
            (str(trace_id),) if trace_id else ()).rowcount


def job(trace_id) -> dict:
    with connect() as c:
        r = c.execute("SELECT state, current_hop, progress, result, error FROM trace_jobs WHERE id = %s",
                      (trace_id,)).fetchone()
    return dict(zip(("state", "current_hop", "progress", "result", "error"), r, strict=True)) if r else {}


# ---------- the two tasks ----------
@app.task(queue="fetch", bind=True, max_retries=2)
def expand_task(self, ctx: dict, node: dict) -> dict:
    """One frontier node: fetch its outgoing value, run sweep + boundary checks, persist its edges."""
    t = _tracer(ctx, ctx.get("labels"))
    ph = Phases()
    with ph.phase("fetch"):          # provider I/O + normalization happen together inside expand_node
        r = t.expand_node(node)
    conn = connect(autocommit=True)
    try:
        # "graph" covers BOTH stores: the batched edge upsert into Postgres (the system of record)
        # and the MERGE into the Neo4j index over this worker's long-lived driver (_index).
        with ph.phase("graph"):
            save_edges(conn, ctx["chain"], r["edges"], ctx["trace_id"], node["hop"])
            save_txs(conn, ctx["chain"], r["txs"])
            if ctx.get("graph"):
                r["flags"] += _index(ctx["chain"], r["edges"], r["txs"])
        conn.execute("INSERT INTO audit_log (trace_id, input_params, upstream_hash, actor) VALUES (%s,%s,%s,%s)",
                     (ctx["trace_id"], json.dumps({"address": node["addr"], "hop": node["hop"],
                                                   "chain": ctx["chain"], "snapshot": ctx["until"]}),
                      hashlib.sha256("\n".join(t.prov.body_hashes).encode()).hexdigest(), "worker"))
    finally:
        conn.close()
    return {"node": node, "moves": r["moves"], "flags": r["flags"], "so_edges": r["so_edges"],
            "hit": _hit(node, r["hit"], node["hop"], 0) if r["hit"] else None,
            "evidence": r["evidence"], "stop": r["stop"], "calls": t.prov.calls,
            "upstream": t.prov.upstream, "store_hits": t.prov.store_hits, "stale": t.prov.stale,
            # Labels this node PROVED (sweep-proven deposits, §6.2c). They live in the registry
            # overlay, which dies with this task's Tracer — so they ride back on the result and are
            # rehydrated by level_done. Without this the chord driver lost every derived label:
            # `evidence` stayed empty, propagation ran against a bare registry, and the next hop
            # could not see a deposit address the previous hop had just proven.
            "labels": [vars(l) for labs in t.reg.labels.values() for l in labs],
            "phases": ph.as_dict(), "requests": t.prov.requests, "memo": _memo_keys(t.prov)}


def _memo_keys(prov) -> list[str]:
    """Request keys this provider can now answer without counting a call, in `requests` spelling."""
    if hasattr(prov, "_cache"):                       # esplora memoizes by path
        return [f"esplora:{k}" for k in prov._cache]
    return [k for k in getattr(prov, "_memo", {}) if isinstance(k, str)]


@app.task(queue="trace")
def level_done(results: list[dict], ctx: dict, state: dict) -> dict:
    """Chord callback: merge one hop's results, decide the next frontier, recurse or finish."""
    t = _tracer(ctx, state.get("labels"))
    hop = state["hop"]
    state["upstream"] += sum(r["upstream"] for r in results)
    state["store_hits"] += sum(r.get("store_hits", 0) for r in results)
    state["stale"] += [s for r in results for s in r["stale"]]
    # §16: each worker timed its own phases; sum them across the level. The sum is work done, not
    # elapsed time (the tasks ran in parallel) — timeline.summarize() states that in its response.
    ph = Phases(state.get("phases"))
    for r in results:
        ph.merge(r.get("phases"))
    state["phases"] = ph.as_dict()
    nodes = {n["addr"]: n for n in state["nodes"]}
    nxt = []
    # BUDGET ACCOUNTING MIRRORS THE SEQUENTIAL DRIVER (engine.Tracer.run), which the eval harness
    # scores. There, one provider memo lives for the whole trace and the budget is checked before each
    # node, in address order. Here every task starts with a cold memo, so its raw `calls` re-count
    # reads an earlier node already made, and stamping hits with the level-END total dropped hits the
    # sequential run keeps: Li Jiadong crowned Binance live while the harness correctly saw Bitfinex
    # 6 points behind and abstained (2026-09-26). So: walk results in the same order, count only
    # reads the trace-wide memo has not seen, stop taking nodes once the budget is spent, and stamp
    # each hit with the running count at that node. Parallel fetch, sequential bookkeeping.
    memo = set(state.get("memo") or [])
    for r in sorted(results, key=lambda r: r["node"]["addr"]):
        if state["calls"] >= MAX_CALLS:
            break                                     # the sequential run never expands this node
        reqs = r.get("requests")
        state["calls"] += r["calls"] if reqs is None else sum(q not in memo for q in reqs)
        memo.update(r.get("memo") or [])
        for d in r.get("labels") or []:               # rehydrate what this node's worker derived
            t.reg.add_label(Label(**d))
        t.prov.calls = state["calls"]                 # child hits in absorb() stamp this count
        state["flags"] += r["flags"]
        state["so_edges"] += r["so_edges"]
        if r["evidence"]:
            state["evidence"].append(r["evidence"])
        if r["hit"]:
            state["hits"].append({**r["hit"], "calls_at_hit": state["calls"]})
        if not r["stop"]:
            nxt += t.absorb(r["moves"], r["node"], hop, nodes, state["hits"], state["flags"])
    state["memo"] = sorted(memo)
    state["labels"] = [vars(l) for labs in t.reg.labels.values() for l in labs]
    for h in state["hits"]:
        h.setdefault("calls_at_hit", state["calls"])
    propagate(t.reg, ctx["chain"], [SameOwner(*e) for e in state["so_edges"]])   # adds to the overlay
    state["nodes"] = list(nodes.values())

    # §10 wants EVERY reachable endpoint ranked, and the eval harness measures exactly that
    # (collect_all=True). The API now defaults to it, so the shipped algorithm and the evaluated one
    # are the same (closes reportscratchpad.md F12). It used to be off because per-task setup made a
    # full walk take 1901s on Case B; with the entity-row cache, one Neo4j driver per worker and
    # batched edge upserts, all 8 golden cases walk in full in 2.2-6.4s warm (measured 2026-09-26).
    # The first-hit stop stays available as collect_all=False.
    first_hit_stop = bool(state["hits"]) and not ctx.get("collect_all", False)
    done = first_hit_stop or not nxt or hop >= ctx["max_hops"] or state["calls"] >= MAX_CALLS
    if not done:
        set_state(ctx["trace_id"], "FETCHING", hop=hop + 1, progress=round(hop / ctx["max_hops"], 2))
        state["hop"] = hop + 1
        return _dispatch({**ctx, "labels": state["labels"]}, nxt, state)

    reason = ("hit" if state["hits"] else "no further outgoing value" if not nxt
              else f"api-call budget ({MAX_CALLS}) exhausted" if state["calls"] >= MAX_CALLS
              else f"hop budget ({ctx['max_hops']}) exhausted")
    set_state(ctx["trace_id"], "SCORING", hop=hop, progress=1.0)
    t.prov.calls, t.prov.upstream, t.prov.stale = state["calls"], state["upstream"], state["stale"]
    t.prov.store_hits = state["store_hits"]
    derived = [l for labs in t.reg.labels.values() for l in labs]   # overlay already includes `new`
    wall = round(time.time() - state["wall_start"], 2) if state.get("wall_start") else 0
    out = t.result(ctx["wallet"], state["hits"], state["flags"], {n["addr"]: n for n in state["nodes"]},
                   state["so_edges"], state["evidence"], reason, wall)
    out["trace_id"], out["case_id"], out["pins"] = ctx["trace_id"], ctx["case_id"], ctx["pins"]
    with ph.phase("score"):
        out = attribute_result(out)   # §10/§11: ranked VASPs + one crowned target (or an abstention)
    out["phases"] = ph.as_dict()
    conn = connect(autocommit=True)
    try:
        if derived:
            save_labels(conn, ctx["trace_id"], ctx["chain"], derived)
        if ctx.get("graph"):
            # `out` was built by t.result() above, which already folded flags into `partial` — so a
            # label-write drop here has to set it directly or the report would under-state itself.
            lost = _index(ctx["chain"], [], [], derived, t.reg.entities)
            if lost:
                out["flags"] += lost
                out["partial"] = True
    finally:
        conn.close()
    set_state(ctx["trace_id"], "DONE", result=out)
    return out


def _dispatch(ctx, frontier, state):
    """link_error goes on BOTH halves on purpose: a header task dying is what actually happened on
    2026-09-13, while the callback raises ChordError separately. Either route must reach FAILED."""
    eb = trace_failed.s(ctx["trace_id"])
    header = [expand_task.s(ctx, n).set(link_error=eb)
              for n in sorted(frontier, key=lambda n: n["addr"])]
    return chord(header)(level_done.s(ctx, state).set(link_error=eb)).id


def _tracer(ctx, labels=()) -> Tracer:
    t = Tracer(ctx["chain"], ctx["until"], ctx["pins"]["label_set_version"], ctx["fanout"],
               offline=ctx.get("offline", False))
    for d in labels or ():        # labels earlier hops derived, so this hop can see them (§6.2b/c)
        t.reg.add_label(Label(**d))
    return t


def dispatch_trace(case_id: str, trace_id: str, pins: dict, wallet: str, chain: str, snapshot: int,
                   max_hops=4, fanout=5, graph=False, offline=False, collect_all=False) -> dict:
    """Kick off hop 1 for a case/job row that ALREADY exists.

    §14 splits case creation (POST /cases -> case_id) from execution (POST /trace {case_id}), so the
    dispatch half has to be callable on its own. open_case already inserts the trace_jobs row in
    state QUEUED; this is the seam where it becomes FETCHING."""
    ctx = {"wallet": wallet, "chain": chain, "until": snapshot, "max_hops": min(max_hops, HARD_MAX_HOPS),
           "fanout": fanout, "trace_id": trace_id, "case_id": case_id, "pins": pins, "graph": graph,
           "offline": offline, "collect_all": collect_all, "labels": []}
    reg = PgRegistry(pins["label_set_version"])
    start = {"addr": wallet, "hop": 0, "since": 0, "via": None, "utxos": None, "utxo_via": {}}
    # Elapsed is measured from here, the moment the job is dispatched, because that is the clock the
    # operator actually experiences — queue wait included. The CLI sets `wall` itself; the chord
    # driver never did, so every chord-driven trace reported wall_clock_s = 0 and the "time to a
    # lead" number had nothing behind it. A float survives the JSON hop between tasks unchanged.
    state = {"hop": 1, "nodes": [start], "hits": [], "flags": [], "so_edges": [], "evidence": [],
             "calls": 0, "upstream": 0, "store_hits": 0, "stale": [], "labels": [], "phases": {},
             "wall_start": time.time()}
    lab = reg.best(chain, wallet)
    if lab:
        state["hits"].append(_hit(start, lab, 0, 0))
    set_state(trace_id, "FETCHING", hop=1, progress=0.0)
    _dispatch(ctx, [start], state)
    return {"case_id": case_id, "trace_id": trace_id, "pins": pins}
