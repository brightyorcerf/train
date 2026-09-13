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
import uuid

from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel, Field

from app.api import sahyog as sahyog_mock
from app.api.report import build_pdf
from app.api.timeline import summarize
from app.attribution.convergence import converge
from app.attribution.engine import attribute_result
from app.db import connect
from app.labels.registry import PgRegistry
from app.scoring.engine import score_candidate
from app.scoring.weights import WEIGHTS, perturb, weight_hash
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


class RescoreRequest(BaseModel):
    """A what-if over a STORED trace. Nothing here changes the frozen profile (§11.1)."""
    weights: dict[str, float] | None = None
    perturb_pct: float | None = Field(default=None, gt=0, le=1)
    seed: int = 0
    profiles: int | None = Field(default=None, ge=1, le=50)


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


def _response_hashes(r: dict, limit: int = 12) -> list[dict]:
    """The §13 'response hash' rows: the cached provider bytes this trace was computed from.

    Keyed by the trace's own pinned snapshot, so a trace served from an earlier snapshot's rows
    legitimately returns none — the surface says that rather than widening the query until
    something matches."""
    block = (r.get("pins") or {}).get("snapshot_block")
    addrs = [r.get("wallet")] + [(c.get("nearest") or {}).get("endpoint")
                                 for c in r.get("vasp_candidates", [])]
    addrs = [a for a in addrs if a]
    if block is None or not addrs:
        return []
    with connect() as c:
        rows = c.execute(
            "SELECT request_key, request, content_hash, provider, fetched_at FROM raw_response "
            "WHERE scope = %s AND request ILIKE ANY(%s) ORDER BY fetched_at LIMIT %s",
            (f"snapshot:{block}", [f"%{a}%" for a in addrs], limit)).fetchall()
    keys = ("request_key", "request", "content_hash", "provider", "fetched_at")
    return [dict(zip(keys, x)) for x in rows]


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


@app.get("/cases")
def list_cases(limit: int = 50):
    """Every case with its job state — what CaseList (§13) renders. Newest first."""
    with connect() as c:
        rows = c.execute(
            "SELECT c.id, c.wallet, c.chain, c.source, c.snapshot_block, c.label_set_version, "
            "c.created_at, j.id, j.state, j.current_hop, j.progress, j.finished_at, "
            "j.result->>'state', j.result->>'recommended' "
            "FROM cases c JOIN trace_jobs j ON j.case_id = c.id "
            "ORDER BY c.created_at DESC LIMIT %s", (limit,)).fetchall()
    keys = ("case_id", "wallet", "chain", "source", "snapshot_block", "label_set_version",
            "created_at", "trace_id", "state", "current_hop", "progress", "finished_at",
            "result_state", "recommended")
    return {"cases": [dict(zip(keys, r)) for r in rows]}


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
            "sweep_evidence": r.get("sweep_evidence", []), "flags": r.get("flags", []),
            "response_hashes": _response_hashes(r)}


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


@app.post("/trace/{trace_id}/rescore")
def rescore(trace_id: str, req: RescoreRequest):
    """Re-rank a FINISHED trace under a different weight profile — the §11.2 perturbation answer,
    live (Q&A #3).

    This re-runs `attribute_result`, the same pure function the eval harness calls, so the slider
    in the UI and the shipped rank-stability number are one test rather than two implementations
    that could drift. It reads a stored result and touches no provider: zero API calls.

    The frozen profile is never written to. `weight_hash` in the response is the PERTURBED hash,
    and `frozen_weight_hash` is what the case is actually pinned to — a report always names the
    profile that produced it (§12)."""
    r = _result(trace_id)
    base_order = [c["entity"] for c in r.get("vasp_candidates", [])]
    base_rec = r.get("recommended")

    if req.profiles:
        pct = req.perturb_pct or 0.2
        same = sum(attribute_result(r, weights=perturb(pct, seed=s))["recommended"] == base_rec
                   for s in range(req.profiles))
        return {"trace_id": trace_id, "base_recommended": base_rec,
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
    w = {k: v / total for k, v in w.items()}      # renormalized, exactly as perturb() does

    alt = attribute_result(r, weights=w)
    ranked = [{"entity": c["entity"], "entity_name": c.get("entity_name"), "score": c["score"]}
              for c in alt["vasp_candidates"]]
    return {"trace_id": trace_id, "weights": w, "weight_hash": weight_hash(w),
            "frozen_weights": dict(WEIGHTS), "frozen_weight_hash": weight_hash(),
            "recommended": alt["recommended"], "base_recommended": base_rec,
            "separation": alt["separation"], "separation_pts": alt["separation_pts"],
            "ranked": ranked, "base_ranked": base_order,
            "rank_unchanged": [c["entity"] for c in alt["vasp_candidates"]] == base_order,
            "note": "what-if only — the case stays pinned to the frozen profile (§11.1)."}


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


# ---------- report (§13) ----------
@app.get("/report/{trace_id}")
def report(trace_id: str):
    """The artifact an investigator files: the four determinism pins on the face, the ranked
    candidates, the provenance, and BOTH routing branches (§17).

    Streamed from memory — see the module docstring. The row in `reports` records the content hash
    so a filed PDF can be matched back to the run that produced it."""
    r = _result(trace_id)
    pdf, digest = build_pdf(r, _response_hashes(r))
    pins = r.get("pins", {})
    if r.get("case_id"):
        with connect() as c:
            c.execute("INSERT INTO reports (id, case_id, pdf_path, content_hash, adapter_version, "
                      "weight_hash) VALUES (%s, %s, NULL, %s, %s, %s)",
                      (str(uuid.uuid4()), r["case_id"], digest,
                       pins.get("adapter_version"), pins.get("weight_hash")))
    return Response(pdf, media_type="application/pdf", headers={
        "Content-Disposition": f'inline; filename="vasp-attribution-{trace_id[:8]}.pdf"',
        "X-Content-Hash": digest})
