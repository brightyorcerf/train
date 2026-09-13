"""FastAPI surface (§14).

TWO CASE SHAPES SHIP SIDE BY SIDE, deliberately:
  * §14 as written  — POST /cases {wallet} -> {case_id} (201), then POST /trace {case_id} (202).
  * day-7d addition — POST /cases {wallets: [...]} traces N complaints under ONE snapshot so
    multi-victim convergence (§8) is reachable end to end. architecture.md predates that
    requirement; the deviation is recorded here rather than resolved by dropping either.
One request body serves both: `wallets` with a single entry behaves as §14's single-wallet case,
and `dispatch` controls whether creation also starts the trace.

/report/{id} is NOT implemented — reports are day 12 (WeasyPrint). It returns 501 with the
disclosure payload's location, rather than a placeholder PDF that looks like a deliverable.
"""
from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel, Field

from app.api import sahyog as sahyog_mock
from app.api.timeline import summarize
from app.attribution.convergence import converge
from app.db import connect
from app.labels.registry import PgRegistry
from app.scoring.engine import score_candidate
from app.trace.engine import Tracer
from app.trace.tasks import dispatch_trace, job, open_case, start_trace

app = FastAPI(
    title="VASP Attribution Engine (SIH26182)",
    description="Evidence-weighted attribution of a suspect wallet to the VASP that can identify "
                "the account holder. Investigative lead, not identity and not evidence (§15).",
)

CHAINS = ("btc", "eth", "polygon")


class CaseRequest(BaseModel):
    wallets: list[str] = Field(min_length=1, max_length=10)
    chain: str
    snapshot_block: int | None = None     # None -> pin the current tip (§12: every case pins one)
    max_hops: int = 4
    fanout: int = 5
    graph: bool = True                    # convergence needs the subgraphs persisted
    dispatch: bool = True                 # False -> §14 shape: create the case, trace later


class TraceRequest(BaseModel):
    case_id: str


def _chain(chain: str) -> str:
    if chain not in CHAINS:
        raise HTTPException(400, f"unsupported chain {chain} (have {', '.join(CHAINS)})")
    return chain


def _result(trace_id: str) -> dict:
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
    """N wallets -> N cases under ONE snapshot. 202 when dispatched, 201 when only created."""
    _chain(req.chain)
    snapshot = req.snapshot_block or Tracer(req.chain, 10**9).until
    params = {"max_hops": req.max_hops, "fanout": req.fanout}
    cases = []
    for w in req.wallets:
        case_id, trace_id, pins = open_case(w, req.chain, snapshot, None, "api", params)
        if req.dispatch:
            dispatch_trace(case_id, trace_id, pins, w, req.chain, snapshot, req.max_hops,
                           req.fanout, graph=req.graph)
        cases.append({"wallet": w, "case_id": case_id, "trace_id": trace_id, "pins": pins,
                      "state": "FETCHING" if req.dispatch else "QUEUED"})
    if not req.dispatch:
        response.status_code = 201
    return {"snapshot_block": snapshot, "chain": req.chain, "cases": cases,
            "trace_ids": [c["trace_id"] for c in cases],
            "case_ids": [c["case_id"] for c in cases]}


@app.post("/trace", status_code=202)
def start(req: TraceRequest):
    """§14: execute a case that already exists. Idempotency (§12) — a case whose job already left
    QUEUED is not re-dispatched; the existing trace_id comes back instead."""
    with connect() as c:
        row = c.execute(
            "SELECT c.wallet, c.chain, c.snapshot_block, c.params, j.id, j.state, j.pins "
            "FROM cases c JOIN trace_jobs j ON j.case_id = c.id WHERE c.id = %s", (req.case_id,)
        ).fetchone()
    if not row:
        raise HTTPException(404, "unknown case")
    wallet, chain, snapshot, params, trace_id, state, pins = row
    if state != "QUEUED":
        return {"case_id": req.case_id, "trace_id": str(trace_id), "state": state,
                "note": "already dispatched — not re-run (§12 idempotency)"}
    params = params or {}
    return dispatch_trace(req.case_id, str(trace_id), pins, wallet, chain, snapshot,
                          params.get("max_hops", 4), params.get("fanout", 5), graph=True)


@app.get("/trace/{trace_id}/status")
def trace_status(trace_id: str):
    j = job(trace_id)
    if not j:
        raise HTTPException(404, "unknown trace")
    return {k: j[k] for k in ("state", "current_hop", "progress", "error")}


@app.get("/trace/{trace_id}")
def trace_result(trace_id: str):
    return _result(trace_id)


@app.get("/trace/{trace_id}/timeline")
def trace_timeline(trace_id: str):
    """Per-phase timing (§16). `normalize` is null by design: it is not separable from `fetch`
    inside expand_node, and an invented split would be fiction."""
    return summarize(_result(trace_id))


@app.get("/trace/{trace_id}/provenance")
def trace_provenance(trace_id: str):
    """The §13 ProvenanceCard rows — source, tier, role basis and deposit event per candidate."""
    r = _result(trace_id)
    return {"trace_id": trace_id, "wallet": r.get("wallet"), "pins": r.get("pins", {}),
            "provenance": sahyog_mock.provenance(r),
            "sweep_evidence": r.get("sweep_evidence", []), "flags": r.get("flags", [])}


# ---------- scoring (§11.1) ----------
@app.get("/wallets/{address}/score")
def wallet_score(address: str, chain: str = "btc"):
    """Score a single ADDRESS from its pinned label, with no trace. This is the label's own
    evidence, not an attribution: there is no path, so proximity (§3 Axis 1) does not exist here."""
    _chain(chain)
    reg = PgRegistry()
    lab = reg.best(chain, address)
    if not lab:
        return {"address": address, "chain": chain, "label_set_version": reg.version,
                "score": None, "breakdown": None,
                "note": "no label in the pinned set — absence of a label is not evidence of anything"}
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


# ---------- convergence (§8) ----------
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
def sahyog_disclosure(trace_id: str, case_reference: str | None = None):
    """The §17 disclosure payload for a finished trace. If the engine abstained, no target is
    named. If the crowned VASP is not SAHYOG-onboarded, `routable_via_sahyog` is false and the
    payload says to use MLAT / direct legal process instead."""
    payload = sahyog_mock.disclosure_payload(_result(trace_id), case_reference)
    return {"request_id": f"MOCK-{trace_id[:8]}", "accepted": False,
            "note": "MOCK endpoint — nothing was transmitted to SAHYOG or any VASP.",
            "disclosure_payload": payload}


# ---------- report (day 12) ----------
@app.get("/report/{trace_id}")
def report(trace_id: str):
    _result(trace_id)     # 404/409 semantics stay consistent with the other read endpoints
    raise HTTPException(501, "PDF reports are day 12 (WeasyPrint). The machine-readable evidence "
                             f"is available now at /trace/{trace_id}/provenance and "
                             f"/sahyog/disclosure?trace_id={trace_id}")
