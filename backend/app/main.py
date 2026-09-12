"""FastAPI surface (§14). Day 9 fills this out; day 7 needs the multi-wallet case endpoint so
multi-victim convergence (§8 of the brief) is reachable end to end.

POST /cases takes a LIST of wallets: N complaints are traced in parallel as separate Celery
chord traces, and their subgraphs are then intersected — that intersection is the point.
"""
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.attribution.convergence import converge
from app.db import connect
from app.trace.engine import Tracer
from app.trace.tasks import job, start_trace

app = FastAPI(title="VASP Attribution Engine (SIH26182)")


class CaseRequest(BaseModel):
    wallets: list[str] = Field(min_length=1, max_length=10)
    chain: str
    snapshot_block: int | None = None     # None -> pin the current tip (§12: every case pins one)
    max_hops: int = 4
    fanout: int = 5
    graph: bool = True                    # convergence needs the subgraphs persisted


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/cases", status_code=202)
def create_case(req: CaseRequest):
    """N wallets -> N traces under one snapshot. Returns immediately (§14: 202, async)."""
    if req.chain not in ("btc", "eth", "polygon"):
        raise HTTPException(400, f"unsupported chain {req.chain}")
    snapshot = req.snapshot_block or Tracer(req.chain, 10**9).until
    traces = [start_trace(w, req.chain, snapshot, req.max_hops, req.fanout, graph=req.graph, source="api")
              for w in req.wallets]
    return {"snapshot_block": snapshot, "chain": req.chain,
            "cases": [{"wallet": w, **t} for w, t in zip(req.wallets, traces)],
            "trace_ids": [t["trace_id"] for t in traces]}


@app.get("/trace/{trace_id}/status")
def trace_status(trace_id: str):
    j = job(trace_id)
    if not j:
        raise HTTPException(404, "unknown trace")
    return {k: j[k] for k in ("state", "current_hop", "progress", "error")}


@app.get("/trace/{trace_id}")
def trace_result(trace_id: str):
    j = job(trace_id)
    if not j:
        raise HTTPException(404, "unknown trace")
    if j["state"] != "DONE":
        raise HTTPException(409, f"trace is {j['state']}")
    return j["result"]


@app.get("/convergence")
def convergence(trace_ids: str, chain: str = "btc", min_shared: int = 2):
    """Nodes appearing in >= min_shared of these traces' subgraphs — where separate complaints
    turn out to be one campaign."""
    ids = [t.strip() for t in trace_ids.split(",") if t.strip()]
    if len(ids) < 2:
        raise HTTPException(400, "convergence needs at least two trace_ids")
    with connect() as c:
        wallets = [r[0] for r in c.execute(
            "SELECT cs.wallet FROM trace_jobs j JOIN cases cs ON cs.id = j.case_id WHERE j.id = ANY(%s)",
            (ids,))]
        rows = converge(c, ids, chain=chain, min_shared=min_shared, wallets=wallets)
    return {"trace_ids": ids, "wallets": wallets, "shared_nodes": rows,
            "n_shared": len([r for r in rows if not r["is_traced_wallet"]])}
