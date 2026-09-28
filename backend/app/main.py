"""FastAPI surface (§14).

TWO CASE SHAPES SHIP SIDE BY SIDE, deliberately:
  * §14 as written  — POST /cases {wallet} -> {case_id} (201), then POST /trace {case_id} (202).
  * day-7d addition — POST /cases {wallets: [...]} traces N complaints under ONE snapshot so
    multi-victim convergence (§8) is reachable end to end. architecture.md predates that
    requirement; the deviation is recorded here rather than resolved by dropping either.
One request body serves both: `wallets` with a single entry behaves as §14's single-wallet case,
and `dispatch` controls whether creation also starts the trace.

/report/{id} renders with WeasyPrint and streams from memory — no writable volume is mounted on
this container, so `reports` records the PDF's content hash instead of a path to a file nobody
outside the container could fetch.
"""
import json
import math
import re
import time
import uuid
from contextlib import asynccontextmanager
from typing import Literal
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.api import sahyog as sahyog_mock
from app.api.intake import extract
from app.api.report import build_pdf
from app.api.report import response_hashes as _response_hashes
from app.api.timeline import summarize
from app.attribution.convergence import converge
from app.attribution.engine import attribute_result
from app.attribution.techniques import detect as detect_techniques
from app.db import connect, init_schema
from app.eval import harness
from app.labels.registry import PgRegistry, valid_address
from app.scoring.engine import score_candidate
from app.scoring.weights import WEIGHTS, perturb, weight_hash
from app.trace.engine import HARD_MAX_HOPS, Tracer
from app.trace.tasks import STALL_AFTER_S, dispatch_trace, job, open_case, sweep_stalled


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create the schema on boot. schema.sql is idempotent (every statement IF NOT EXISTS), and
    without this a fresh clone answers every endpoint with a 500 about a missing relation."""
    init_schema()
    yield


app = FastAPI(
    lifespan=lifespan,
    title="VASP Attribution Engine (SIH26182)",
    description="Evidence-weighted attribution of a suspect wallet to the VASP that can identify "
                "the account holder. Investigative lead, not identity and not evidence (§15).",
)

Chain = Literal["btc", "eth", "polygon", "tron"]


class CaseRequest(BaseModel):
    """Unknown fields are rejected: a typo'd `wallet` (singular, as §14 writes it) silently traced
    nothing before, because the misspelling was ignored and `wallets` fell back to its default."""
    model_config = ConfigDict(extra="forbid")

    wallets: list[str] = Field(min_length=1, max_length=10)
    chain: Chain
    snapshot_block: int | None = Field(default=None, ge=0)  # None -> pin the tip (§12: always pinned)
    max_hops: int = Field(default=HARD_MAX_HOPS, ge=1, le=HARD_MAX_HOPS)   # = the harness walk
    fanout: int = Field(default=5, ge=1, le=50)
    graph: bool = True                    # convergence needs the subgraphs persisted
    dispatch: bool = True                 # False -> §14 shape: create the case, trace later
    # §10 wants every reachable endpoint ranked — the same walk the eval harness scores. On by
    # default since per-task setup stopped dominating (trace/tasks.py level_done); False restores
    # the stop-at-first-hit driver.
    collect_all: bool = True
    force: bool = False                   # re-run a wallet already traced at this snapshot

    @field_validator("wallets", mode="after")
    @classmethod
    def _clean(cls, v: list[str]) -> list[str]:
        # Copy-paste from a case file arrives with whitespace; storing it verbatim produced a case
        # whose wallet could never match a label lookup and whose trace was guaranteed empty.
        return [w.strip() for w in v]


class TraceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: UUID


class RescoreRequest(BaseModel):
    """A what-if over a STORED trace. Nothing here changes the frozen profile (§11.1)."""
    model_config = ConfigDict(extra="forbid")
    weights: dict[str, float] | None = None
    perturb_pct: float | None = Field(default=None, gt=0, le=1)
    seed: int = 0
    profiles: int | None = Field(default=None, ge=1, le=50)


def _address(chain: str, address: str) -> str:
    a = address.strip()
    if not valid_address(chain, a):
        raise HTTPException(400, f"{address!r} is not a valid {chain} address")
    return a


def _result(trace_id) -> dict:
    j = job(trace_id)
    if not j:
        raise HTTPException(404, "unknown trace")
    if j["state"] != "DONE":
        raise HTTPException(409, f"trace is {j['state']}")
    return j["result"]


@app.get("/health")
def health():
    return {"status": "ok"}


# ---------- cases & traces (§14) ----------
@app.post("/cases", status_code=202)
def create_case(req: CaseRequest, response: Response):
    """N wallets -> N cases under ONE snapshot. 202 when dispatched, 201 when only created.

    Idempotent on (wallet, chain, snapshot_block) (§12): re-posting a wallet already traced at this
    snapshot returns the existing case instead of a second one. Repeating the same request produced
    a new case and a fresh provider spend every time, which is how one demo wallet ended up with 32
    duplicate traces. `force: true` re-runs deliberately."""
    wallets = [_address(req.chain, w) for w in req.wallets]
    snapshot = req.snapshot_block or Tracer(req.chain, 10**9).until
    params = {"max_hops": req.max_hops, "fanout": req.fanout}
    cases = []
    for w in wallets:
        if not req.force:
            with connect() as c:
                row = c.execute(
                    "SELECT c.id::text, j.id::text, j.pins, j.state FROM cases c "
                    "JOIN trace_jobs j ON j.case_id = c.id WHERE c.wallet = %s AND c.chain = %s "
                    "AND c.snapshot_block = %s AND j.state <> 'FAILED' "
                    "ORDER BY c.created_at DESC LIMIT 1", (w, req.chain, snapshot)).fetchone()
            if row:
                cases.append({"wallet": w, "case_id": row[0], "trace_id": row[1], "pins": row[2],
                              "state": row[3], "reused": True,
                              "note": "existing case at this snapshot; not re-traced "
                                      "(§12 idempotency; pass force=true to re-run)"})
                continue
        case_id, trace_id, pins = open_case(w, req.chain, snapshot, None, "api", params)
        if req.dispatch:
            dispatch_trace(case_id, trace_id, pins, w, req.chain, snapshot, req.max_hops,
                           req.fanout, graph=req.graph, collect_all=req.collect_all)
        cases.append({"wallet": w, "case_id": case_id, "trace_id": trace_id, "pins": pins,
                      "reused": False, "state": "FETCHING" if req.dispatch else "QUEUED"})
    if not req.dispatch:
        response.status_code = 201
    return {"snapshot_block": snapshot, "chain": req.chain, "cases": cases,
            "trace_ids": [c["trace_id"] for c in cases],
            "case_ids": [c["case_id"] for c in cases]}


@app.get("/cases")
def list_cases(limit: int = Query(50, ge=1, le=500), all_runs: bool = False):
    """Every case with its job state — what CaseList (§13) renders. Newest first.

    One row per (wallet, chain, snapshot) by default: repeated runs of the same wallet at the same
    pin are the same investigation, and showing all of them buried the demo set under duplicates.
    `all_runs=true` returns the full history."""
    cols = ("c.id, c.wallet, c.chain, c.source, c.snapshot_block, c.label_set_version, "
            "c.created_at, j.id, j.state, j.current_hop, j.progress, j.finished_at, "
            "j.result->>'state', j.result->>'recommended'")
    inner = (f"SELECT {cols} FROM cases c JOIN trace_jobs j ON j.case_id = c.id" if all_runs else
             f"SELECT DISTINCT ON (c.wallet, c.chain, c.snapshot_block) {cols} "
             "FROM cases c JOIN trace_jobs j ON j.case_id = c.id "
             "ORDER BY c.wallet, c.chain, c.snapshot_block, c.created_at DESC")
    with connect() as c:
        rows = c.execute(f"SELECT * FROM ({inner}) t ORDER BY 7 DESC LIMIT %s", (limit,)).fetchall()
    keys = ("case_id", "wallet", "chain", "source", "snapshot_block", "label_set_version",
            "created_at", "trace_id", "state", "current_hop", "progress", "finished_at",
            "result_state", "recommended")
    return {"cases": [dict(zip(keys, r, strict=True)) for r in rows]}


@app.post("/trace", status_code=202)
def start(req: TraceRequest):
    """§14: execute a case that already exists. Idempotency (§12) — a case whose job already left
    QUEUED is not re-dispatched; the existing trace_id comes back instead."""
    with connect() as c:
        row = c.execute(
            "SELECT c.wallet, c.chain, c.snapshot_block, c.params, j.id, j.state, j.pins "
            "FROM cases c JOIN trace_jobs j ON j.case_id = c.id WHERE c.id = %s", (str(req.case_id),)
        ).fetchone()
    if not row:
        raise HTTPException(404, "unknown case")
    wallet, chain, snapshot, params, trace_id, state, pins = row
    if state != "QUEUED":
        return {"case_id": str(req.case_id), "trace_id": str(trace_id), "state": state,
                "note": "already dispatched; not re-run (§12 idempotency)"}
    params = params or {}
    return dispatch_trace(str(req.case_id), str(trace_id), pins, wallet, chain, snapshot,
                          params.get("max_hops", 4), params.get("fanout", 5), graph=True)


@app.get("/trace/{trace_id}/status")
def trace_status(trace_id: UUID):
    j = job(trace_id)
    if not j:
        raise HTTPException(404, "unknown trace")
    if j["state"] in ("QUEUED", "FETCHING", "SCORING"):
        # A poller asking about a job that died with its worker deserves an answer, not a spinner.
        if sweep_stalled(trace_id):
            j = job(trace_id)
    return {k: j[k] for k in ("state", "current_hop", "progress", "error")}


@app.get("/trace/{trace_id}")
def trace_result(trace_id: UUID):
    return _result(trace_id)


@app.get("/trace/{trace_id}/timeline")
def trace_timeline(trace_id: UUID):
    """Per-phase timing (§16). `normalize` is null by design: it is not separable from `fetch`
    inside expand_node, and an invented split would be fiction."""
    return summarize(_result(trace_id))


@app.get("/trace/{trace_id}/provenance")
def trace_provenance(trace_id: UUID):
    """The §13 ProvenanceCard rows — source, tier, role basis and deposit event per candidate."""
    r = _result(trace_id)
    return {"trace_id": str(trace_id), "wallet": r.get("wallet"), "pins": r.get("pins", {}),
            "provenance": sahyog_mock.provenance(r),
            "sweep_evidence": r.get("sweep_evidence", []), "flags": r.get("flags", []),
            "response_hashes": _response_hashes(r)}


# ---------- scoring (§11.1) ----------
@app.get("/wallets/{address}/score")
def wallet_score(address: str, chain: Chain = "btc"):
    """Score a single ADDRESS from its pinned label, with no trace. This is the label's own
    evidence, not an attribution: there is no path, so proximity (§3 Axis 1) does not exist here."""
    address = _address(chain, address)
    reg = PgRegistry()
    lab = reg.best(chain, address)
    if not lab:
        return {"address": address, "chain": chain, "label_set_version": reg.version,
                "score": None, "breakdown": None,
                "note": "no label in the pinned set; absence of a label is not evidence of anything"}
    ent = reg.entities.get(lab.entity)
    cand = {"role": lab.role, "basis": lab.basis, "tier": lab.source, "value": 0.0,
            "asset": chain.upper(), "ts": [], "path": []}
    idx, breakdown = score_candidate(cand)
    return {"address": address, "chain": chain, "label_set_version": reg.version,
            "entity": lab.entity, "entity_name": ent.name if ent else lab.entity,
            "sahyog": ent.sahyog if ent else "unknown", "role": lab.role, "basis": lab.basis,
            "score": idx, "of": 100, "breakdown": breakdown,
            "note": "confidence INDEX, not a probability or a percentage (§11.1). Scored from the "
                    "label alone: no path, so temporal and dust factors are unpopulated."}


@app.post("/trace/{trace_id}/rescore")
def rescore(trace_id: UUID, req: RescoreRequest):
    """Re-rank a FINISHED trace under a different weight profile — the §11.2 perturbation answer,
    live (Q&A #3).

    This re-runs `attribute_result`, the same pure function the eval harness calls, so the slider
    in the UI and the shipped rank-stability number are one test rather than two implementations
    that could drift. It reads a stored result and touches no provider: zero API calls.

    The frozen profile is never written to. `weight_hash` in the response is the PERTURBED hash,
    and `frozen_weight_hash` is what the case is actually pinned to — a report always names the
    profile that produced it (§12)."""
    r = _result(trace_id)
    if req.weights and not all(math.isfinite(v) for v in req.weights.values()):
        raise HTTPException(400, "weights must be finite numbers")   # NaN/inf reached the JSON encoder
    base_order = [c["entity"] for c in r.get("vasp_candidates", [])]
    base_rec = r.get("recommended")

    if req.profiles:
        pct = req.perturb_pct or 0.2
        same = harness.top1_unchanged(r, base_rec, req.profiles, pct)
        return {"trace_id": str(trace_id), "base_recommended": base_rec,
                "ranked": [{"entity": c["entity"], "entity_name": c.get("entity_name"),
                            "score": c["score"]} for c in r.get("vasp_candidates", [])],
                "recommended": base_rec, "rank_unchanged": same == req.profiles,
                "stability": {"unchanged": same, "n": req.profiles, "pct": pct},
                "note": f"top-1 unchanged in {same} of {req.profiles} seeded +/-{int(pct * 100)}% "
                        "profiles. Each profile jitters every weight and renormalizes to 1."}

    w = dict(req.weights) if req.weights else (
        perturb(req.perturb_pct, seed=req.seed) if req.perturb_pct else dict(WEIGHTS))
    if set(w) != set(WEIGHTS):
        raise HTTPException(400, f"weights must name exactly {sorted(WEIGHTS)}")
    if any(v < 0 for v in w.values()) or sum(w.values()) <= 0:
        raise HTTPException(400, "weights must be non-negative and sum above zero")
    total = sum(w.values())
    if not math.isfinite(total):
        # 1e308 each summed to inf, every weight normalized to 0.0, and the response came back 200
        # with a meaningless all-zero profile instead of rejecting the input.
        raise HTTPException(400, "weights overflow: their sum is not a finite number")
    w = {k: v / total for k, v in w.items()}      # renormalized, exactly as perturb() does

    alt = attribute_result(r, weights=w)
    ranked = [{"entity": c["entity"], "entity_name": c.get("entity_name"), "score": c["score"]}
              for c in alt["vasp_candidates"]]
    return {"trace_id": str(trace_id), "weights": w, "weight_hash": weight_hash(w),
            "frozen_weights": dict(WEIGHTS), "frozen_weight_hash": weight_hash(),
            "recommended": alt["recommended"], "base_recommended": base_rec,
            "separation": alt["separation"], "separation_pts": alt["separation_pts"],
            "ranked": ranked, "base_ranked": base_order,
            "rank_unchanged": [c["entity"] for c in alt["vasp_candidates"]] == base_order,
            "note": "what-if only: the case stays pinned to the frozen profile (§11.1)."}


# ---------- the graph surface (§13 GraphView) ----------
# Boundary flags are emitted as "kind:NAME@addr(hop N, tx H)…" by the engine (§9.3) and as
# "coinjoin_boundary:HASH(...)" / "service_hub_boundary:ADDR(...)". Parsing them here rather than in
# TypeScript keeps one reading of the format next to the code that writes it.
_BOUNDARY = re.compile(r"^(mixer|bridge|dex):(?P<name>[^@]+)@(?P<addr>[^(]+)\(hop (?P<hop>\d+)")


def _boundaries(flags: list[str]) -> dict[str, dict]:
    """-> {address: {kind, name, hop, detail}} for the service nodes the trace actually stopped at."""
    out: dict[str, dict] = {}
    for f in flags or []:
        m = _BOUNDARY.match(f)
        if m:
            out[m.group("addr").strip()] = {"kind": f.split(":", 1)[0], "name": m.group("name").strip(),
                                            "hop": int(m.group("hop")), "detail": f}
    return out


# Edges of the txs named in `%s` (the ranked candidates' paths) sort first, so the render cap can
# never cut the path the verdict rests on: Li Jiadong has >1200 edges at hop 0 alone, and the old
# hop-ordered LIMIT returned only those — the attributed hops 1-3 never reached the screen.
_EDGE_SQL = ("SELECT te.hop, e.chain, e.kind, e.src, e.dst, e.value, e.decimals, e.asset, e.tx_hash, e.id "
             "FROM trace_edge te JOIN edge e ON e.id = te.edge_id WHERE te.trace_id = %s "
             "ORDER BY e.tx_hash = ANY(%s) DESC, te.hop, e.id LIMIT %s")


def _shape(rows, nodes: dict, edges: list) -> None:
    """trace_edge rows -> graph nodes/edges, in place. One reading for /graph and /stream, so the live
    view and the final view cannot disagree about what a hypernode is."""
    for hop, ch, kind, src, dst, value, dec, asset, tx, _id in rows:
        amount = float(value) / 10 ** (dec or 0)
        if kind in ("funds", "credits"):          # BTC: the tx is a node, not an edge
            addr, txid = (src, tx) if kind == "funds" else (dst, tx)
            nodes.setdefault(f"tx:{txid}", {"id": f"tx:{txid}", "kind": "tx", "label": txid[:10],
                                            "hop": hop, "chain": ch})
            nodes.setdefault(addr, {"id": addr, "kind": "address", "hop": hop, "chain": ch})
            a, b = (addr, f"tx:{txid}") if kind == "funds" else (f"tx:{txid}", addr)
            edges.append({"source": a, "target": b, "kind": kind, "amount": round(amount, 8),
                          "asset": asset, "tx": txid, "hop": hop})
        else:
            for a in (src, dst):
                nodes.setdefault(a, {"id": a, "kind": "address", "hop": hop, "chain": ch})
            edges.append({"source": src, "target": dst, "kind": kind, "amount": round(amount, 8),
                          "asset": asset, "tx": tx, "hop": hop})


@app.get("/trace/{trace_id}/graph")
def trace_graph(trace_id: UUID, limit: int = Query(1200, ge=1, le=20000)):
    """The subgraph this trace actually walked, straight from Postgres (§7.6).

    Deliberately NOT Neo4j: the system of record already holds every edge with its hop, and reading
    it here means the centrepiece visual still renders when the derived index is down or rebuilding
    — the same property that keeps /convergence alive (proven 2026-09-13 with neo4j stopped).

    BTC keeps its hypernode shape (address -FUNDS-> tx -CREDITS-> address) instead of being
    flattened to address->address, because the two data models being visibly different IS the
    claim (§7.1-7.3, §13)."""
    r = _result(trace_id)
    chain = r.get("chain") or "btc"
    bounds = _boundaries(r.get("flags", []))
    # The ranked result already knows what it crowned and what it calls each endpoint, and it is the
    # better source than the registry here: a sweep-proven deposit (§6.2c) is labelled in the
    # TRACE's own overlay, not in the pinned set, so a registry lookup alone returns None for
    # exactly the node the demo points at — the crowned one.
    cand: dict[str, dict] = {}
    for c_ in r.get("vasp_candidates", []):
        ep = (c_.get("nearest") or {}).get("endpoint")
        if ep:
            cand[ep] = {"entity": c_["entity"], "entity_name": c_.get("entity_name"),
                        "sahyog": c_.get("sahyog"), "score": c_["score"],
                        "role_basis": (c_.get("nearest") or {}).get("role_basis"),
                        "crowned": c_["entity"] == r.get("recommended")}

    nodes: dict[str, dict] = {}
    edges = []
    with connect() as c:
        path_txs = sorted({h["tx"] for c_ in r.get("vasp_candidates", [])
                           for h in (c_.get("nearest") or {}).get("path") or []})
        rows = c.execute(_EDGE_SQL, (str(trace_id), path_txs, limit)).fetchall()
        total, max_hop = c.execute("SELECT count(*), coalesce(max(hop), 0) FROM trace_edge WHERE trace_id = %s",
                                   (str(trace_id),)).fetchone()
        reg = PgRegistry((r.get("pins") or {}).get("label_set_version"), conn=c)

        _shape(rows, nodes, edges)

        # PgRegistry resolves the pinned label set lazily against THIS connection, so the node
        # enrichment has to happen while it is still open.
        for addr, n in nodes.items():
            if n["kind"] != "address":
                continue
            labs = reg.lookup(chain, addr)
            best = labs[0] if labs else None
            ent = reg.entities.get(best.entity) if best else None
            b = bounds.get(addr)
            cd = cand.get(addr) or {}
            # A ranked candidate's endpoint is a deposit-role endpoint by construction (§6.3, §10),
            # so it keeps that role when the pinned set has no row for it.
            n |= {"role": (b or {}).get("kind") or (best.role if best else
                                                    "deposit" if cd else "unlabeled"),
                  "entity": cd.get("entity") or (best.entity if best else (b or {}).get("name")),
                  "entity_name": (cd.get("entity_name") or (ent.name if ent else None)
                                  or (b or {}).get("name")),
                  "sahyog": cd.get("sahyog") or (ent.sahyog if ent else None),
                  "role_basis": cd.get("role_basis") or (best.basis if best else None),
                  "boundary": b["detail"] if b else None,
                  "is_wallet": addr == r.get("wallet"),
                  "crowned": bool(cd.get("crowned")),
                  "score": cd.get("score")}

    return {"trace_id": str(trace_id), "chain": chain, "wallet": r.get("wallet"),
            "state": r.get("state"), "partial": r.get("partial", False),
            "max_hop": max_hop,
            "nodes": list(nodes.values()), "edges": edges,
            "truncated": total > len(rows), "n_edges_total": total,
            "note": ("Rendered from Postgres, the system of record; not from the Neo4j index. "
                     "BTC keeps the :Tx hypernode shape; EVM is address -> address.")}


@app.get("/trace/{trace_id}/techniques")
def trace_techniques(trace_id: UUID):
    """The laundering techniques the trace passed through, hop by hop, each tagged observed /
    heuristic / label (app.attribution.techniques). BTC tx shapes come from the trace's own
    hypernode edges, so this costs no provider call and works on every stored trace."""
    r = _result(trace_id)
    with connect() as c:
        rows = c.execute(
            "SELECT e.tx_hash, count(DISTINCT e.src) FILTER (WHERE e.kind = 'funds'), "
            "count(*) FILTER (WHERE e.kind = 'credits') FROM trace_edge te JOIN edge e ON e.id = te.edge_id "
            "WHERE te.trace_id = %s AND e.kind IN ('funds', 'credits') GROUP BY e.tx_hash",
            (str(trace_id),)).fetchall()
    return {"trace_id": str(trace_id), "techniques": detect_techniques(r, {t: (i, o) for t, i, o in rows})}


@app.get("/trace/{trace_id}/stream")
def trace_stream(trace_id: UUID):
    """Server-Sent Events: the subgraph as the Celery chords write it (§9.2), not after.

    Each expand_task commits its edges to trace_edge before its chord joins, so polling Postgres is
    watching the workers land. Events: `status` (state/hop/progress), `edges` (new nodes + edges,
    same shape as /graph), and a final `done`. Postgres, not Redis pub/sub, on purpose — the stream
    then needs nothing the rest of the API does not already depend on. Seen edges are tracked by id
    rather than by an id cursor, because an edge another trace already wrote keeps its OLD id."""
    tid = str(trace_id)
    if not job(tid):
        raise HTTPException(404, "unknown trace")

    def events():
        seen, known, last, t0 = set(), set(), None, time.time()
        while time.time() - t0 < STALL_AFTER_S:
            j = job(tid)
            with connect() as c:
                rows = [r for r in c.execute(_EDGE_SQL, (tid, [], 20000)).fetchall() if r[-1] not in seen]
            if rows:
                seen.update(r[-1] for r in rows)
                nodes, edges = {}, []
                _shape(rows, nodes, edges)
                fresh = [n for k, n in nodes.items() if k not in known]
                known.update(nodes)
                yield f"event: edges\ndata: {json.dumps({'nodes': fresh, 'edges': edges})}\n\n"
            st = (j["state"], j["current_hop"], j["progress"])
            if st != last:
                last = st
                msg = {"state": st[0], "hop": st[1],
                       "progress": float(st[2]) if st[2] is not None else None, "error": j["error"]}
                yield f"event: status\ndata: {json.dumps(msg)}\n\n"
            if j["state"] in ("DONE", "FAILED"):
                yield f"event: done\ndata: {json.dumps({'state': j['state']})}\n\n"
                return
            time.sleep(0.4)
        yield f"event: done\ndata: {json.dumps({'state': 'TIMEOUT'})}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------- convergence (§8) ----------
class IntakeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=20_000)
    snapshots: dict[str, int] = {}   # chain -> pinned snapshot; a chain left out pins the tip


@app.post("/intake", status_code=202)
def intake(req: IntakeRequest, response: Response):
    """Complaint text -> every wallet address in it, checksum-validated -> one multi-wallet case per
    chain (POST /cases semantics: idempotent per wallet+snapshot). Where a chain has >= 2 wallets the
    reply carries the /convergence query to run once those traces finish; convergence needs
    finished subgraphs, so it cannot be computed inside this request."""
    found = extract(req.text)
    by_chain: dict[str, list[str]] = {}
    for r in found:
        if r["valid"]:
            by_chain.setdefault(r["chain"], []).append(r["address"])
    cases, conv, dropped = {}, {}, []
    for chain, ws in by_chain.items():
        dropped += ws[10:]   # CaseRequest's cap; said, not silently truncated
        out = create_case(CaseRequest(wallets=ws[:10], chain=chain, snapshot_block=req.snapshots.get(chain)),
                          response)
        cases[chain] = {"trace_ids": out["trace_ids"], "snapshot_block": out["snapshot_block"],
                        "reused": [c["reused"] for c in out["cases"]]}
        if len(out["trace_ids"]) >= 2:
            conv[chain] = f"/convergence?trace_ids={','.join(out['trace_ids'])}&chain={chain}"
    return {"addresses": found, "cases": cases, "convergence": conv, "dropped_over_cap": dropped}


@app.get("/convergence")
def convergence(trace_ids: str, chain: Chain = "btc", min_shared: int = Query(2, ge=1, le=100)):
    """Nodes appearing in >= min_shared of these traces' subgraphs — where separate complaints
    turn out to be one campaign."""
    ids = [t.strip() for t in trace_ids.split(",") if t.strip()]
    if len(ids) < 2:
        raise HTTPException(400, "convergence needs at least two trace_ids")
    for t in ids:                       # non-uuid text used to reach Postgres and 500 there
        try:
            uuid.UUID(t)
        except ValueError:
            raise HTTPException(422, f"{t!r} is not a trace_id") from None
    with connect() as c:
        wallets = [r[0] for r in c.execute(
            "SELECT cs.wallet FROM trace_jobs j JOIN cases cs ON cs.id = j.case_id WHERE j.id = ANY(%s)",
            (ids,))]
        rows = converge(c, ids, chain=chain, min_shared=min_shared, wallets=wallets)
    return {"trace_ids": ids, "wallets": wallets, "shared_nodes": rows,
            "n_shared": len([r for r in rows if not r["is_traced_wallet"]])}


# ---------- benchmark (§11.2) ----------
_BENCH: dict = {}


@app.get("/benchmark")
def benchmark():
    """The eval harness over the whole golden set, served — the same `run_case`/`summary` the CLI
    prints, offline from the raw store (0 upstream calls), so the number on screen is the shipped
    number. Cached per (frozen weights, golden file mtime): recomputing is ~8s of pure replay and
    the answer cannot change unless one of those does."""
    key = (weight_hash(), harness.GOLDEN.stat().st_mtime)
    if key not in _BENCH:
        t0, out = time.time(), []
        for c in harness.cases():
            r = harness.run_case(c, offline=True)
            same, n = harness.stability(r)
            out.append({**r, "verdict": harness.verdict(r), "stable": [same, n], "golden": c})
        m = {k: v for k, v in harness.summary(out).items() if not k.startswith("_")}
        _BENCH.clear()
        _BENCH[key] = {
            "weights": weight_hash(), "wall_s": round(time.time() - t0, 1), "summary": m,
            "cases": [{
                "id": r["id"], "title": r["golden"].get("title") or r["id"],
                "chain": r.get("chain") or r["golden"]["chain"],
                "suspect": r["golden"]["suspect_addr"],
                "snapshot": r["golden"].get("until_block") or harness.SNAPSHOT[r["golden"]["chain"]],
                "expect": r["golden"]["expect"], "expected_entity": r["golden"].get("expected_entity"),
                "verdict": r["verdict"], "state": r["state"], "recommended": r.get("recommended"),
                "hops": r.get("hops"), "calls": r.get("calls"), "upstream": r.get("upstream"),
                "stable": r["stable"], "wall_s": r["wall_s"],
                "why": r["golden"].get("why"), "source_doc": r["golden"].get("source_doc"),
            } for r in out]}
    return _BENCH[key]


# ---------- SAHYOG mock (§17) — documented contract, never a live integration ----------
@app.post("/sahyog/cases", status_code=201)
def sahyog_case(req: CaseRequest, response: Response):
    """Mirrors POST /cases, tagging the case source as 'sahyog' for the audit trail."""
    out = create_case(req, response)
    return {**out, "source": "sahyog", "schema_note": sahyog_mock.SCHEMA_NOTE}


@app.get("/sahyog/onboarding/{entity_id}")
def sahyog_onboarding(entity_id: str):
    """Is this VASP actually on the portal? Usually not — the answer is stated plainly."""
    return sahyog_mock.onboarding(entity_id)


@app.post("/sahyog/disclosure")
def sahyog_disclosure(trace_id: UUID, case_reference: str | None = None):
    """The §17 disclosure payload for a finished trace. If the engine abstained, no target is
    named. If the crowned VASP is not SAHYOG-onboarded, `routable_via_sahyog` is false and the
    payload says to use MLAT / direct legal process instead."""
    payload = sahyog_mock.disclosure_payload(_result(trace_id), case_reference)
    return {"request_id": f"MOCK-{str(trace_id)[:8]}", "accepted": False,
            "note": "MOCK endpoint: nothing was transmitted to SAHYOG or any VASP.",
            "disclosure_payload": payload}


# ---------- report (§13) ----------
@app.get("/report/{trace_id}")
def report(trace_id: UUID):
    """The artifact an investigator files: the four determinism pins on the face, the ranked
    candidates, the provenance, and BOTH routing branches (§17).

    Streamed from memory — see the module docstring. The row in `reports` records the content hash
    so a filed PDF can be matched back to the run that produced it."""
    r = _result(trace_id)
    pdf, digest = build_pdf(r, _response_hashes(r))
    pins = r.get("pins", {})
    if r.get("case_id"):
        # One row per (case, content hash): viewing a report is not filing a new one, and the old
        # unconditional INSERT grew `reports` on every refresh.
        with connect() as c:
            c.execute("INSERT INTO reports (id, case_id, pdf_path, content_hash, adapter_version, "
                      "weight_hash) VALUES (%s, %s, NULL, %s, %s, %s) "
                      "ON CONFLICT (case_id, content_hash) DO NOTHING",
                      (str(uuid.uuid4()), r["case_id"], digest,
                       pins.get("adapter_version"), pins.get("weight_hash")))
    return Response(pdf, media_type="application/pdf", headers={
        "Content-Disposition": f'inline; filename="vasp-attribution-{str(trace_id)[:8]}.pdf"',
        "X-Content-Hash": digest})
