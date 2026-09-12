"""Day-7 real-data PASS/FAIL: attribution engine (§10), confidence scoring (§11.1) and
multi-victim convergence (§8 of the brief).

Every trace here is a real mainnet trace at a pinned snapshot; repeat runs are served from the
§12 raw store, so this costs few provider calls.

    docker compose up -d postgres redis neo4j worker
    backend/.venv/bin/python scripts/day7_attribution_check.py
"""
import sys
import time
import uuid
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.attribution import aggregate, recommend  # noqa: E402
from app.attribution.convergence import converge  # noqa: E402
from app.attribution.engine import attribute  # noqa: E402
from app.db import connect, init_schema  # noqa: E402
from app.db.edges import save_edges, save_txs  # noqa: E402
from app.labels.registry import PgRegistry  # noqa: E402
from app.scoring.engine import score_candidate  # noqa: E402
from app.scoring.weights import FROZEN_AT, SEPARATION_TAU, weight_hash  # noqa: E402
from app.trace.engine import trace  # noqa: E402

ETH_SNAP, BTC_SNAP = 25906777, 966553
POTEKHIN = "0x7F367cC41522cE07553e823bf3be79A889DEbe1B"      # day-7a hand-verified discovery case
BITFINEX_EP = "0xd882cfc20f52f2599d84b8e8d58c7fb62cfe344b"
LIJIADONG = "1EfMVkxQQuZfBdocpJu6RUsCJvenQWbQyE"             # reaches 2 Binance deposit addresses
WHITEBIT = "0x3AD9dB589d201A710Ed237c829c7860Ba86510Fc"
FIXEDFLOAT = "0xeb507efa9ee692a4c774ad1de9f3cb26fc459da3"
TORNADO = "0xd90e2f925da726b50c4ed8d0fb90ad053324f31b"
LAZARUS = ["0x08723392Ed15743cc38513C4925f5e6be5c17243", "0x098B716B8Aaf21512996dC57EB0615e2383E2f96",
           "0x35fB6f6DB4fb05e6A4cE86f2C93691425626d4b1", "0x3Cffd56B47B7b41c56258D9C7731ABaDc360E073",
           "0x3e37627dEAA754090fBFbb8bd226c1CE66D255e9", "0x53b6936513e738f44FB50d2b9476730C0Ab3Bfc1",
           "0xa0e1c89Ef1a489c9C7dE96311eD5Ce5D32c20E4B", "0xF7B31119c2682c88d88D455dBb9d5932c65Cf1bE"]
SECONDEYE = ["0x1da5821544e25c636c1417ba96ade4cf6d2f9b5a", "0x72a5843cc08275C8171E582972Aa4fDa8C397B2A",
             "0x7Db418b5D567A4e0E8c59Ad71BE1FcE48f3E6107", "0x7F19720A857F834887FC9A7bC0a0fBe7Fc7f8102"]
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  {detail}")


def traced(conn, wallets, chain, snapshot, max_hops=3) -> dict:
    """Trace each wallet with the graph sink on -> {trace_id: wallet}. Subgraphs land in trace_edge."""
    ids = {}
    for w in wallets:
        tid = str(uuid.uuid4())

        def sink(hop, edges, txs, labels=(), _t=tid):
            save_edges(conn, chain, edges, _t, hop)
            save_txs(conn, chain, txs)

        trace(w, chain, until_block=snapshot, max_hops=max_hops, sink=sink)
        ids[tid] = w
    return ids


def main():
    t0 = time.time()
    init_schema()
    conn = connect(autocommit=True)
    reg = PgRegistry(conn=conn)

    # ---- 1. the hand-verified EVM discovery case, end to end through attribution (§7a) ----
    evm = attribute(POTEKHIN, "eth", until_block=ETH_SNAP, max_hops=4)
    check("hand-verified EVM discovery case is crowned correctly (Potekhin -> Bitfinex, 3 hops)",
          evm["recommended"] == "bitfinex" and evm["nearest"]["hops"] == 3
          and evm["nearest"]["endpoint"] == BITFINEX_EP and evm["nearest"]["role_basis"] == "sweep_proven",
          f"{evm['state']} {evm['recommended']} {evm['nearest']['hops']}h "
          f"{evm['vasp_candidates'][0]['score']}/100 · {evm['api_calls']} calls")
    check("…and the two axes stay separate: hops on `nearest`, evidence on `score`, never fused",
          "score" not in evm["nearest"] and "hops" not in evm["vasp_candidates"][0]["breakdown"]["factors"],
          f"nearest={ {k: evm['nearest'][k] for k in ('hops', 'role_basis')} } "
          f"factors={list(evm['vasp_candidates'][0]['breakdown']['factors'])}")

    # ---- 2. candidate ENUMERATION, not shortest-path-to-first-label (§10) ----
    li = attribute(LIJIADONG, "btc", until_block=BTC_SNAP, max_hops=4, fanout=8)
    eps = sorted({c["endpoint"] for c in li["candidates"]})
    check("bounded BFS enumerates every reachable endpoint, not just the first one hit",
          len(eps) >= 2 and li["vasp_candidates"][0]["n_paths"] == len(eps),
          f"{len(eps)} endpoints -> {len(li['vasp_candidates'])} entity: {[e[:10] + '…' for e in eps]}")

    # ---- 3. aggregation is max + saturating top-k, NEVER sum (the scatter guard) ----
    cands = li["candidates"]
    many = aggregate.by_entity(cands, li["flags"])[0]
    one = aggregate.by_entity([max(cands, key=lambda c: score_candidate(c, li['flags'])[0])], li["flags"])[0]
    check("N real paths to one entity score exactly as its single best path (max, not sum)",
          many["score"] == one["score"] and many["n_paths"] > one["n_paths"],
          f"{many['n_paths']} paths -> {many['score']}/100 == best single path {one['score']}/100")
    # 50 copies of a real dusty candidate must not outrank one real strong candidate
    dust = deepcopy(min(cands, key=lambda c: c["value"]))
    dust["value"], dust["entity"], dust["entity_name"] = 1e-7, "scatterco", "Scatterco"
    scatter = [{**deepcopy(dust), "endpoint": f"{dust['endpoint']}{i}"} for i in range(50)]
    rows = {r["entity"]: r for r in aggregate.by_entity(scatter + cands, li["flags"])}
    check("50 dusty paths cannot outrank one strong path (real candidate, value set to dust)",
          rows["binance"]["score"] > rows["scatterco"]["score"] and rows["scatterco"]["n_paths"] == 50,
          f"binance {rows['binance']['score']}/100 (2 paths) > scatterco {rows['scatterco']['score']}/100 "
          f"(50 paths, corroboration {rows['scatterco']['corroboration']})")

    # ---- 4. the three axes stay separate (§3, §11.1): proximity and value are NOT confidence ----
    real = deepcopy(evm["candidates"][0])
    base = score_candidate(real, evm["flags"])[0]
    far = score_candidate({**real, "hops": real["hops"] + 3}, evm["flags"])[0]
    rich = score_candidate({**real, "value": real["value"] * 100}, evm["flags"])[0]
    check("proximity is NOT a confidence factor: the same endpoint 3 hops further scores identically",
          far == base, f"{base}/100 at {real['hops']}h == {far}/100 at {real['hops'] + 3}h")
    check("value magnitude is NOT a confidence factor: 100x the amount scores identically",
          rich == base, f"{real['value']:.2f} {real['asset']} -> {base}/100, "
                        f"{real['value'] * 100:.2f} -> {rich}/100")

    # ---- 5. separation / abstention (§10): two real cases whose scores sit within tau ----
    wb = attribute(WHITEBIT, "eth", until_block=ETH_SNAP, max_hops=4)["vasp_candidates"][0]
    ff = attribute(FIXEDFLOAT, "eth", until_block=ETH_SNAP, max_hops=4)["vasp_candidates"][0]
    gap = abs(wb["score"] - ff["score"])
    amb = recommend.recommend([wb, ff])
    check(f"top1 - top2 < tau ({SEPARATION_TAU}) -> ambiguous, abstain instead of crowning "
          "(policy check on two real candidate rows)",
          gap < SEPARATION_TAU and amb["recommended"] is None and amb["ambiguous"],
          f"{wb['entity']} {wb['score']}/100 vs {ff['entity']} {ff['score']}/100 — gap {gap}, "
          f"{amb['separation']}")
    check("a clear winner IS crowned, with a one-line rationale and no fused scalar",
          evm["recommended"] == "bitfinex" and evm["separation"] == "HIGH" and evm["rationale"],
          f"“{evm['rationale'][:96]}…”")

    # ---- 6. frozen weights are pinned into the case (§12, train-on-test discipline) ----
    check("every case pins the weight profile hash + freeze date alongside snapshot and label set",
          evm["pins"]["weight_hash"] == weight_hash() and evm["pins"]["weights_frozen_at"] == FROZEN_AT
          and evm["pins"]["snapshot_block"] == ETH_SNAP and evm["pins"]["label_set_version"],
          f"{evm['pins']['weight_hash']} frozen {FROZEN_AT} · {evm['pins']['label_set_version']} "
          f"@ block {evm['pins']['snapshot_block']}")

    # ---- 7. multi-victim convergence on REAL same-actor OFAC sets (§8 of the brief) ----
    laz = traced(conn, LAZARUS, "eth", ETH_SNAP)
    shared = converge(conn, list(laz), chain="eth", reg=reg, wallets=list(laz.values()))
    tor = next((r for r in shared if r["address"] == TORNADO), None)
    check("8 Lazarus Group OFAC wallets converge on one real shared node (Tornado Cash)",
          tor and tor["shared_by"] == len(LAZARUS) and tor["role"] == "mixer",
          f"{len(shared)} shared nodes; {TORNADO[:12]}… shared by {tor and tor['shared_by']}/8, "
          f"role={tor and tor['role']}, {tor and tor['total_received']:.0f} ETH in")
    check("…and a shared mixer is reported as a trace boundary, not as a VASP to serve (§9.3)",
          tor and "boundary" in tor["interpretation"] and "SAHYOG" not in tor["interpretation"],
          f"“{tor and tor['interpretation'][:88]}…”")

    se = traced(conn, SECONDEYE, "eth", ETH_SNAP)
    se_shared = converge(conn, list(se), chain="eth", reg=reg, wallets=list(se.values()))
    vasp = [r for r in se_shared if r["role"] in ("deposit", "hot") and not r["is_traced_wallet"]]
    check("4 Secondeye Solution OFAC wallets converge on shared VASP endpoints (one SAHYOG request)",
          len(vasp) >= 2 and all("SAHYOG" in r["interpretation"] for r in vasp),
          "; ".join(f"{r['entity']} {r['address'][:10]}… x{r['shared_by']}" for r in vasp[:3]))
    check("a traced wallet that shows up in another wallet's subgraph is marked, not sold as a discovery",
          all(r["is_traced_wallet"] == (r["address"].lower() in {w.lower() for w in se.values()})
              for r in se_shared),
          f"{sum(r['is_traced_wallet'] for r in se_shared)} of {len(se_shared)} shared rows are traced wallets")

    n = sum(results)
    print(f"\n{n}/{len(results)} checks ok · {time.time() - t0:.1f}s")
    print("OVERALL: PASS" if n == len(results) else "OVERALL: FAIL")
    sys.exit(0 if n == len(results) else 1)


if __name__ == "__main__":
    main()
