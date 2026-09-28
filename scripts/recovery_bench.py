"""Recovery benchmark (ETH, frozen weights): can the engine walk FORWARD from a wallet some hops
upstream of a known exchange deposit address and name that exchange?

Ground truth, and its limit (say this whenever the number is quoted):
  A deposit address D is an unlabeled sender into a labeled exchange hot wallet that passes the
  same sweep test the engine uses (§6.2c). So the truth is selected BY sweep behaviour and the
  engine re-finds it BY sweep behaviour: this measures traversal and ranking (does the walk reach D
  through fan-out and budget caps, and is D ranked first), NOT whether sweep-proof is correct.

Positives: walk back k in {1,2,3} hops from D along same-asset transfers in time order -> start S_k.
Decoys:    Tornado Cash depositors whose EVERY outflow in the window is into a mixer. Crowning any
           exchange from them is a failure.
Outcomes:  recovered (crowned D's exchange) | different (crowned another exchange; not necessarily
           wrong, S_k may also pay into it, so the crowned endpoint's basis is recorded) | abstained.

    docker compose run --rm -e PYTHONPATH=/app -e PYTHONUNBUFFERED=1 -v "$PWD/backend/app:/app/app" \
      -v "$PWD/scripts:/repo/scripts" api python /repo/scripts/recovery_bench.py [--n 100] [--decoys 20]
Rows stream to recovery_bench.jsonl (resumable: finished start wallets are skipped).
"""
import argparse
import json
from pathlib import Path

from app.attribution.engine import attribute_result
from app.eval import harness
from app.labels.registry import MIXER, PgRegistry, valid_address
from app.labels.sweep import evm_sweep_proof
from app.providers.etherscan_v2 import EtherscanV2Provider
from app.trace.engine import Tracer

HERE = Path(__file__).resolve().parent
CANON = Path(harness.__file__).parent / "results" / "canonical.json"
ROWS = HERE / "recovery_bench.jsonl"
SNAP = 25906777           # the ETH golden snapshot
ENTITIES = ("binance", "huobi", "bitfinex", "kucoin", "okex", "kraken", "coinbase", "gemini", "bitstamp", "poloniex")
EST_CALLS = {"sample": 12, "trace": 200}   # per positive: sweep check + walk-back; trace budget cap


def hot_wallets(reg, conn, per_entity=3):
    rows = conn.execute("SELECT entity_id, address FROM address_label WHERE label_set_version=%s AND chain='eth' "
                        "AND role='hot' AND entity_id = ANY(%s) ORDER BY entity_id, address",
                        (reg.version, list(ENTITIES))).fetchall()
    out = {}
    for ent, addr in rows:
        out.setdefault(ent, [])
        if len(out[ent]) < per_entity:
            out[ent].append(addr.lower())
    return out


def unlabeled(reg, a):
    return not reg.lookup("eth", a)


WINDOW = 30 * 7200   # ~30 days of ETH blocks: an older receipt is not the same money moving on
TRIES = 3            # senders tried per hop, largest first (bounds the calls a hub check costs)


def walk_back(prov, reg, addr, asset, before, hops):
    """Same-asset senders in time order, largest first -> [S_k, ..., S_1] or None.
    A path counts only if an investigator would call it one flow: each hop arrives within WINDOW
    before the next leaves, and no intermediate is a hub (the engine's own >page_cap truncation
    test; a service's outflows are other people's money). Fan-out is deliberately NOT filtered:
    whether the engine follows a transfer outside its top-k is what this measures."""
    path, cur, t = [], addr, before
    for _ in range(hops):
        rx = sorted((e for e in prov.incoming_before(cur, t) if e.asset == asset and t - WINDOW <= e.block < t
                     and unlabeled(reg, e.src) and e.src not in path and e.src != addr),
                    key=lambda e: (-e.value, e.tx_hash))
        e = next((e for e in rx[:TRIES] if prov.get_outgoing(e.src, SNAP) is not None
                  and e.src not in prov.truncated), None)
        if e is None:
            return None
        path.append(e.src)
        cur, t = e.src, e.block
    return path[::-1]


def run_trace(start, label_set):
    t = Tracer("eth", SNAP, label_set, fanout=5)
    r = attribute_result(t.run(start, max_hops=5, collect_all=True))
    top = next((c for c in r["vasp_candidates"] if c["entity"] == r["recommended"]), None)
    return r, t.prov.calls, t.prov.upstream, top


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--decoys", type=int, default=20)
    ap.add_argument("--max-calls", type=int, default=60_000)
    a = ap.parse_args()

    reg = PgRegistry()
    prov = EtherscanV2Provider("eth")
    est = a.n * sum(EST_CALLS.values()) + a.decoys * (6 + EST_CALLS["trace"])
    print(f"label set {reg.version} · snapshot {SNAP} · estimate <= {est} logical calls "
          f"(cap {a.max_calls}); most repeat reads are store hits")
    if est > a.max_calls:
        raise SystemExit("estimate over the cap; lower --n")
    done = {json.loads(line)["start"] for line in ROWS.open()} if ROWS.exists() else set()
    spent = 0

    def emit(row):
        with ROWS.open("a") as f:
            f.write(json.dumps(row) + "\n")
        print(f"[{row['kind']:<8}] {row['outcome']:<10} k={row.get('hops_back')} truth={row.get('truth')} "
              f"got={row['recommended']} calls={row['calls']} ({row['upstream']} up)")

    # ---- positives: round-robin over entities so no one exchange dominates n ----
    hots = hot_wallets(reg, reg.conn)
    pools = {ent: iter(sorted({e.src for h in hs for e in prov.incoming_before(h, SNAP)
                               if unlabeled(reg, e.src)})) for ent, hs in hots.items()}
    prior = [json.loads(line) for line in ROWS.open()] if ROWS.exists() else []
    n = k = sum(r["kind"] == "positive" for r in prior)   # resume: count what is already done
    while n < a.n and pools and spent + prov.calls < a.max_calls:
        for ent in list(pools):
            d = next(pools[ent], None)
            if d is None:
                del pools[ent]
                continue
            lab, ev = evm_sweep_proof(prov, reg, d, prov.get_outgoing(d, SNAP), SNAP)
            if not lab or lab.entity != ent:
                continue
            hops = k % 3 + 1
            path = walk_back(prov, reg, d, ev["asset"], _block_of(prov, d, ev), hops)
            if not path or path[0] in done:
                continue
            k += 1
            done.add(path[0])
            r, calls, up, top = run_trace(path[0], reg.version)
            spent += calls
            rec = r["recommended"]
            emit({"kind": "positive", "start": path[0], "path": path + [d], "hops_back": hops, "truth": ent,
                  "deposit": d, "sweep_tx": ev["sweep_tx"], "recommended": rec, "state": r["state"],
                  "outcome": "recovered" if rec == ent else "different" if rec else "abstained",
                  "crowned_endpoint": top and top["nearest"]["endpoint"],
                  "crowned_basis": top and top["nearest"].get("role_basis"),
                  "reached_deposit": any((c.get("nearest") or {}).get("endpoint") == d for c in r["vasp_candidates"]),
                  "truth_rank": next((i + 1 for i, c in enumerate(r["vasp_candidates"]) if c["entity"] == ent), None),
                  "candidates": [(c["entity"], c["score"]) for c in r["vasp_candidates"][:4]],
                  "calls": calls, "upstream": up, "partial": r.get("partial")})
            n += 1
            if n >= a.n:
                break

    # ---- decoys: depositors whose every outflow is into a mixer ----
    mixers = [x for (x,) in reg.conn.execute(
        "SELECT DISTINCT address FROM address_label WHERE label_set_version=%s AND chain='eth' AND role=%s "
        "ORDER BY address LIMIT 30", (reg.version, MIXER))]
    mixers = [m for m in mixers if valid_address("eth", m)]   # the label set carries a few non-EVM strings
    cands = sorted({e.src for m in mixers for e in prov.incoming_before(m.lower(), SNAP) if unlabeled(reg, e.src)})
    nd = sum(r["kind"] == "decoy" for r in prior)
    for s in cands:
        if nd >= a.decoys or spent + prov.calls >= a.max_calls:
            break
        if s in done:
            continue
        out = prov.get_outgoing(s, SNAP)
        if not out or not all(reg.best("eth", e.dst, roles=(MIXER,)) for e in out):
            continue
        done.add(s)
        r, calls, up, _ = run_trace(s, reg.version)
        spent += calls
        emit({"kind": "decoy", "start": s, "recommended": r["recommended"], "state": r["state"],
              "outcome": "refused" if r["recommended"] is None else "crowned",
              "calls": calls, "upstream": up, "partial": r.get("partial")})
        nd += 1

    summarize(reg.version)


def _block_of(prov, d, ev):
    """Block of the sweep tx (evidence carries its ts; the walk-back bound is a block)."""
    return next(e.block for e in prov.get_outgoing(d, SNAP) if e.tx_hash == ev["sweep_tx"])


def summarize(label_set):
    rows = [json.loads(line) for line in ROWS.open()]
    pos = [r for r in rows if r["kind"] == "positive" and not r.get("partial")]
    dec = [r for r in rows if r["kind"] == "decoy" and not r.get("partial")]
    by = lambda rs, o: sum(r["outcome"] == o for r in rs)  # noqa: E731
    res = {
        "label_set_version": label_set, "snapshot_block": SNAP, "chain": "eth", "weights": "frozen (no change after results)",
        "positives": len(pos), "recovered": by(pos, "recovered"), "different_exchange": by(pos, "different"),
        "different_exchange_sweep_proven": sum(r["outcome"] == "different" and r["crowned_basis"] == "sweep_proven"
                                               for r in pos),
        "abstained": by(pos, "abstained"),
        # abstained, but the truth was tied at the top score: the wallet paid several exchanges
        "abstained_truth_tied_top": sum(bool(r["outcome"] == "abstained" and r.get("candidates")
                                             and dict(r["candidates"]).get(r["truth"]) == r["candidates"][0][1])
                                        for r in pos),
        "abstained_truth_not_reached": sum(r["outcome"] == "abstained" and r.get("truth_rank") is None for r in pos),
        "by_hops_back": {h: {o: sum(r["hops_back"] == h and r["outcome"] == o for r in pos)
                             for o in ("recovered", "different", "abstained")} for h in (1, 2, 3)},
        # the investigator's question: when train names an exchange, did the money really reach one of
        # its deposit addresses? (sweep-proven endpoint on the traced path)
        "named": sum(r["recommended"] is not None for r in pos),
        "named_real_deposit": sum(r["recommended"] is not None and r["crowned_basis"] == "sweep_proven"
                                  for r in pos),
        "decoys": len(dec), "decoys_refused": by(dec, "refused"),
        "partial_excluded": sum(1 for r in rows if r.get("partial")),
        "limitation": "ground truth is selected by sweep behaviour and re-found by sweep-proof: this measures "
                      "traversal and ranking, not sweep-proof correctness. 'different' is not necessarily wrong.",
    }
    print(json.dumps(res, indent=2))
    canon = json.loads(CANON.read_text())
    canon["recovery_benchmark"] = res
    CANON.write_text(json.dumps(canon, indent=2) + "\n")
    print(f"-> {CANON}")


if __name__ == "__main__":
    main()
