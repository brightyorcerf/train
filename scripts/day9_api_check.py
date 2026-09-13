"""Day 9 gate (§14, §16, §17): the FastAPI surface, the per-phase timeline, the provenance
surface, and the SAHYOG mock — against REAL data through the real Celery workers.

Nothing here is mocked. The traces run as chords on the worker container, the labels come from the
pinned label set in Postgres, and the SAHYOG answers come from curated onboarding status. The two
things this gate is really defending:

  1. the §14 case/trace split and the day-7d multi-wallet case BOTH work (they ship side by side);
  2. the SAHYOG mock never claims a disclosure can be routed for a VASP that is not onboarded —
     every endpoint our golden cases reach is sahyog:unknown, and the payload must say so.

Run it in the API container, not the host venv — /report/{id} renders with WeasyPrint, which is
installed in the image and not on the host. PYTHONPATH is required because running a script puts
the SCRIPT's directory on sys.path rather than the workdir where `app` lives:

    docker compose run --rm -e PYTHONPATH=/app \
        -v "$PWD/scripts:/repo/scripts:ro" api python /repo/scripts/day9_api_check.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

SNAPSHOT = 966553
WUHUIHUI = "12w6v1qAaBc4W8h8C2Cu5SKFaKDSv3erUW"      # golden: -> binance deposit, 2 hops
HYDRA = "123WBUDmSJv4GctdVEz6Qq6z8nXSKrJ4KX"        # golden: UNATTRIBUTED at a CoinJoin boundary

ok, fail = [], []


def check(name, cond, detail=""):
    (ok if cond else fail).append(name)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}  {detail}")


def wait(c, trace_id, timeout=420):
    """Poll /trace/{id}/status until the chord finishes."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        s = c.get(f"/trace/{trace_id}/status").json()
        if s["state"] in ("DONE", "FAILED"):
            return s
        time.sleep(2)
    return {"state": "TIMEOUT", "error": f"still running after {timeout}s"}


def main():
    c = TestClient(app)
    check("health", c.get("/health").json()["status"] == "ok")

    # ---- §14 shape: create a case WITHOUT dispatching, then execute it ----
    r = c.post("/cases", json={"wallets": [WUHUIHUI], "chain": "btc", "snapshot_block": SNAPSHOT,
                               "max_hops": 4, "fanout": 4, "dispatch": False})
    check("POST /cases {dispatch:false} -> 201, job QUEUED (§14 split)",
          r.status_code == 201 and r.json()["cases"][0]["state"] == "QUEUED",
          f"{r.status_code} {r.json()['cases'][0]['state']}")
    case_id = r.json()["case_ids"][0]
    pins = r.json()["cases"][0]["pins"]
    check("case pins a snapshot + label set + adapter version (§12)",
          pins["snapshot_block"] == SNAPSHOT and pins["label_set_version"].startswith("ls-")
          and pins.get("adapter_version"), str(pins))

    r = c.post("/trace", json={"case_id": case_id})
    check("POST /trace {case_id} -> 202 dispatch of an existing case",
          r.status_code == 202 and r.json()["trace_id"], str(r.status_code))
    tid = r.json()["trace_id"]

    r2 = c.post("/trace", json={"case_id": case_id})
    check("re-POST /trace is idempotent — not re-dispatched (§12)",
          "already dispatched" in (r2.json().get("note") or ""), r2.json().get("state", ""))
    check("POST /trace on an unknown case -> 404",
          c.post("/trace", json={"case_id": "00000000-0000-0000-0000-000000000000"}).status_code == 404)

    st = wait(c, tid)
    check("the dispatched trace completes through real Celery workers", st["state"] == "DONE",
          st.get("error") or st["state"])
    if st["state"] != "DONE":
        return report()

    res = c.get(f"/trace/{tid}").json()
    check("the golden discovery case still attributes to binance",
          res["recommended"] == "binance" and res["state"] == "ATTRIBUTED",
          f"{res['state']} rec={res['recommended']} hops={res.get('hops')}")

    # ---- §16 timeline ----
    tl = c.get(f"/trace/{tid}/timeline").json()
    measured = {k: v for k, v in tl["phases"].items() if v is not None}
    check("GET /trace/{id}/timeline reports measured phases (§16)",
          {"fetch", "graph", "score"} <= set(measured), str(tl["phases"]))
    check("normalize is null, not fabricated — it is not separable from fetch",
          tl["phases"]["normalize"] is None)
    check("timeline states that summed phase time is work, not elapsed time",
          tl["concurrent"] is True and "not elapsed time" in tl["note"])
    check("timeline carries the call budget, logical vs upstream (§12)",
          tl["calls"]["logical"] is not None and tl["calls"]["upstream"] is not None,
          str(tl["calls"]))

    # ---- §13 provenance surface ----
    pr = c.get(f"/trace/{tid}/provenance").json()
    rows = pr["provenance"]
    check("GET /trace/{id}/provenance returns per-candidate evidence rows (§13)",
          bool(rows) and all("label_source" in r and "role_basis" in r for r in rows),
          f"{len(rows)} row(s)")
    check("provenance pins the reproduction triple (§12)",
          all(pr["pins"].get(k) for k in ("snapshot_block", "label_set_version", "weight_hash")),
          str({k: pr["pins"].get(k) for k in ("snapshot_block", "label_set_version", "weight_hash")}))
    check("sweep evidence is exposed as the deposit-basis artifact (§6.4)",
          any(r.get("role_basis") == "sweep_proven" for r in rows) and bool(pr["sweep_evidence"]))

    # ---- §11.1 single-address score ----
    s = c.get("/wallets/1NDyJtNTjmwk5xPNhjgAMu4HDHigtobu1s/score", params={"chain": "btc"}).json()
    check("GET /wallets/{addr}/score scores a labeled address from the pinned set",
          s["score"] is not None and s["entity"] == "binance", f"{s['score']}/100 {s.get('entity')}")
    check("the score is an index with a factor breakdown, never a percentage (§11.1)",
          s["of"] == 100 and "contributions" in s["breakdown"] and "%" not in s["note"])
    miss = c.get("/wallets/1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2/score", params={"chain": "btc"}).json()
    check("an unlabeled address scores None — absence of a label is not evidence",
          miss["score"] is None and "not evidence" in miss["note"])

    # ---- §17 SAHYOG mock: the honest part ----
    ob = c.get("/sahyog/onboarding/binance").json()
    check("SAHYOG onboarding: binance is NOT reported as portal-routable",
          ob["routable_via_sahyog"] is False and ob["target_vasp_sahyog"] == "unknown",
          ob["route"][:60])
    wz = c.get("/sahyog/onboarding/wazirx").json()
    check("SAHYOG onboarding: a curated onboarded VASP IS routable",
          wz["routable_via_sahyog"] is True and wz["target_vasp_sahyog"] == "confirmed")

    d = c.post("/sahyog/disclosure", params={"trace_id": tid, "case_reference": "FIR-TEST-1"}).json()
    p = d["disclosure_payload"]
    check("POST /sahyog/disclosure builds the §17 payload for the crowned target",
          p["target_vasp"] == "binance" and p["suspect_addresses"] == [WUHUIHUI]
          and len(p["transaction_hashes"]) >= 2, f"{len(p['transaction_hashes'])} tx hashes")
    check("the payload REFUSES to claim SAHYOG routing for an un-onboarded VASP",
          p["routable_via_sahyog"] is False and "MLAT" in p["route"])
    check("the payload is labelled illustrative and the mock transmits nothing",
          "ILLUSTRATIVE" in p["schema_note"] and d["accepted"] is False
          and "nothing was transmitted" in d["note"])
    check("the payload carries the legal basis and the lead-not-evidence disclaimer (§15)",
          "BNSS" in p["legal_basis"] and "not identity and not evidence" in p["disclaimer"])
    check("the payload pins snapshot + label set + weight hash for reproduction (§12)",
          all(p["reproduce"].get(k) for k in
              ("snapshot_block", "label_set_version", "weight_hash", "adapter_version")),
          str(p["reproduce"]))

    # ---- multi-wallet case (day-7d, §8) still works alongside the §14 split ----
    r = c.post("/cases", json={"wallets": [WUHUIHUI, HYDRA], "chain": "btc",
                               "snapshot_block": SNAPSHOT, "max_hops": 4, "fanout": 4})
    check("POST /cases with N wallets -> N traces under ONE snapshot (§8)",
          r.status_code == 202 and len(r.json()["trace_ids"]) == 2
          and r.json()["snapshot_block"] == SNAPSHOT, f"{len(r.json()['trace_ids'])} traces")
    tids = r.json()["trace_ids"]
    states = [wait(c, t)["state"] for t in tids]
    check("both wallets in the multi-wallet case complete", set(states) == {"DONE"}, str(states))

    if set(states) == {"DONE"}:
        hyd = c.get(f"/trace/{tids[1]}").json()
        check("the CoinJoin confuser is still an honest non-answer through the API",
              hyd["state"] == "UNATTRIBUTED" and hyd["recommended"] is None,
              f"{hyd['state']} reason={hyd.get('reason')}")
        dh = c.post("/sahyog/disclosure", params={"trace_id": tids[1]}).json()["disclosure_payload"]
        check("an abstained trace yields NO named target in the disclosure payload",
              dh["target_vasp"] is None and dh["routable_via_sahyog"] is False,
              dh["route"][:70])
        cv = c.get("/convergence", params={"trace_ids": ",".join(tids), "chain": "btc"}).json()
        check("GET /convergence answers for the multi-wallet case (§8)",
              "shared_nodes" in cv and cv["trace_ids"] == tids, f"{cv.get('n_shared')} shared")

    # ---- the report is built now (§13): assert the real artifact, not a placeholder ----
    # This check used to assert 501 ("reports are day 12 — say so, don't fake it"). The point was
    # never the status code: it was that nothing may look like a deliverable until it is one. So
    # it now verifies the genuine PDF and the routing honesty inside it.
    rep = c.get(f"/report/{tid}")
    check("GET /report/{id} -> a real PDF, not a placeholder (§13)",
          rep.status_code == 200 and rep.headers["content-type"] == "application/pdf"
          and rep.content[:5] == b"%PDF-" and len(rep.content) > 5000,
          f"{rep.status_code} · {len(rep.content)} bytes · "
          f"hash {rep.headers.get('x-content-hash', '')[:12]}")

    from app.api.report import build_html          # noqa: E402
    doc = build_html(c.get(f"/trace/{tid}").json())
    check("the report prints BOTH routing branches — portal for an onboarded VASP, MLAT for ours",
          "ROUTABLE — SAHYOG portal" in doc and "NOT ROUTABLE — NOT on the SAHYOG portal" in doc
          and all(p in doc for p in ("snapshot block", "label set version", "weight hash",
                                     "adapter version")),
          "both branches + four determinism pins on the face")
    check("unknown trace -> 404 on the read endpoints",
          c.get("/trace/00000000-0000-0000-0000-000000000000").status_code == 404)

    # ---- the OpenAPI contract is the SAHYOG deliverable (§14) ----
    spec = c.get("/openapi.json").json()
    check("OpenAPI documents the /sahyog/* contract (§14: the contract IS the deliverable)",
          all(p in spec["paths"] for p in
              ("/sahyog/cases", "/sahyog/disclosure", "/sahyog/onboarding/{entity_id}")))
    return report()


def report():
    n = len(ok) + len(fail)
    print(f"\n{len(ok)}/{n} checks ok")
    if fail:
        print("FAILED: " + ", ".join(fail))
    print("OVERALL: " + ("PASS" if not fail else "FAIL"))
    return 0 if not fail else 1


if __name__ == "__main__":
    sys.exit(main())
