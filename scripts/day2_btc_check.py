"""Day-2 real-data PASS/FAIL: BTC adapter (§8/§7.3), change detection (§7.5), CoinJoin guard +
SAME_OWNER (§7.4), sweep detection (§6.2c), propagation (§6.2b). Every fixture is a real mainnet tx.

    backend/.venv/bin/python scripts/day2_btc_check.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.boundary.change import coinjoin_reason  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.labels.ingest import build_registry  # noqa: E402
from app.labels.propagate import propagate, same_owner_edges  # noqa: E402
from app.labels.registry import DEPOSIT  # noqa: E402
from app.labels.sweep import MIN_SENDERS, btc_sweep_proof  # noqa: E402
from app.providers.esplora import EsploraProvider  # noqa: E402

# Public case (day 1): Zhdanova (OFAC) -> Binance deposit 1KNvwHu -> consolidation into Binance hot 1NDyJt
ZHDANOVA, DEP, HOT = "1Ljk8RNNabkZ9bfDYQBn98XfFozJhTjqcZ", "1KNvwHuZ1wmdyzJHnbPDkdgnFXbAxUGvHP", "1NDyJtNTjmwk5xPNhjgAMu4HDHigtobu1s"
DEPOSIT_TX = "e995334f2e7b81e05e222ff0fc033bd079c49be6b3f022ab127f5e4cda637b82"   # 40.5 BTC -> 1KNvwHu (+ change)
SWEEP_TX = "0d813f9f09210133e9e96b9ad373b3544af27e502a7279790dfc7a608f04df31"     # 34 inputs -> 1NDyJt
# Bitfinex-hack peel chain (2017-01, GraphSense hacks.yaml start 1HpaHr2p…): hop-3 and hop-4 txs are
# the spends of these one-time addresses.
PEEL = [("1Hsu5bTWxU647TAiEQ79ZCHFwt9YhtMfke", "1KYSCSKRbENdGWXmALPVKWVaNWS9T9kcyQ"),   # (spent addr, expected change)
        ("1KYSCSKRbENdGWXmALPVKWVaNWS9T9kcyQ", "1EKo8BL1eU5XAkRzw8Tw812joTJtNVtXVp")]
# github.com/nopara73/WasabiVsSamourai — published CoinJoin txid lists
COINJOINS = {"wasabi": ["ad01fe8c42b415cfb9e70b75cc219685ad825eb9c4de6d27349b5e136398f443",
                        "204a64e26402c7b085fa33b044cf6aee8fb675e15ff7806ce90576b8b752f170"],
             "whirlpool": ["72024630a91bcd48162664af1f5cf26f1ad5629aeb7f8199ac4351afd5f34a06",
                           "f73c5ec27f76396eac945c184fe22480e22a5ac419b9d862775fb3df34e51433"]}
BITFINEX_UNTIL = 719000

results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  {detail}")


def main():
    t0 = time.time()
    p = EsploraProvider()

    # ---- adapter: hypernode shape, until_block, failover ----
    dep, sw = p.get_tx(DEPOSIT_TX), p.get_tx(SWEEP_TX)
    check("get_tx: full inputs/outputs (34-input consolidation)",
          len(sw.inputs) == 34 and len(sw.outputs) == 2 and sw.total_in >= sw.total_out,
          f"{len(sw.inputs)} in ({len(sw.input_addresses)} addrs) / {len(sw.outputs)} out, fee {sw.total_in - sw.total_out} sat")
    edges = p.get_outgoing(ZHDANOVA, until_block=dep.block, since_block=dep.block)
    funds = [e for e in edges if e.kind == "funds"]
    credits = [e for e in edges if e.kind == "credits"]
    check("get_outgoing: Tx hypernode edges only (addr-FUNDS->tx-CREDITS->addr)",
          funds and credits and all(e.dst == e.tx_hash for e in funds) and all(e.src == e.tx_hash for e in credits),
          f"{len(funds)} FUNDS + {len(credits)} CREDITS, 0 address->address")
    nb = p.get_neighbors(ZHDANOVA, dep.block, dep.block)
    check("get_neighbors: payees in, change excluded", DEP in nb and ZHDANOVA not in nb, f"{nb}")
    h = p.history(DEP, until_block=sw.block, max_pages=2, older_than=SWEEP_TX)
    check("until_block bounds history (paged from an anchor tx, 525-tx address)",
          h and max(t.block for t in h) <= sw.block and not p.history(DEP, 0, max_pages=1),
          f"{len(h)} txs, blocks {h and min(t.block for t in h)}..{h and max(t.block for t in h)} <= {sw.block}")
    # store=False: the §12 raw store would answer before the dead primary is ever contacted, and the
    # drill is about the breaker, not the cache (the cache path is drilled in day6_celery_check).
    fo = EsploraProvider(bases=["http://127.0.0.1:9/api", settings.mempool_base_url, settings.esplora_base_url],
                         store=False)
    fo_tx = fo.get_tx(DEPOSIT_TX)
    check("failover drill: dead primary -> breaker trips, next provider serves",
          fo_tx.hash == DEPOSIT_TX and fo.trips["http://127.0.0.1:9/api"] == 1,
          f"trips {dict(fo.trips)}, served by {[b for b in fo.calls_by if b != 'http://127.0.0.1:9/api']}")

    # ---- change detection on real txs ----
    ann = p.annotate(dep)
    check("change: address reuse (Zhdanova deposit tx)", ann["change"] and dep.outputs[ann["change"].vout].address == ZHDANOVA,
          f"{ann['change']}")
    for spent, expect in PEEL:
        t = p.spends(spent, BITFINEX_UNTIL)[0]
        ch = p.annotate(t)["change"]
        got = ch and t.outputs[ch.vout].address
        check(f"change: Bitfinex peel continuation {t.hash[:12]}", got == expect, f"{got} conf={ch and ch.confidence} {ch and ch.reasons}")

    # ---- CoinJoin guard + SAME_OWNER ----
    for kind, txids in COINJOINS.items():
        for txid in txids:
            t = p.get_tx(txid)
            r = coinjoin_reason(t)
            check(f"coinjoin: {kind} {txid[:12]} flagged, no SAME_OWNER", r and same_owner_edges(t) == [], r or "not flagged")
    so = same_owner_edges(sw)
    check("SAME_OWNER: consolidation -> co-input edges", coinjoin_reason(sw) is None and len(so) == len(sw.input_addresses) - 1,
          f"{len(so)} edges, conf {so[0].confidence if so else None}")

    # ---- sweep detection ----
    reg = build_registry(chains=("btc",))
    lab, ev = btc_sweep_proof(p, reg, DEP, [sw], until_block=sw.block, deposit_tx=dep)
    check("sweep: 1KNvwHu proven Binance deposit (>=90% to hot AND >=3 senders)",
          lab and lab.entity == "binance" and lab.basis == "sweep_proven" and ev["distinct_senders"] >= MIN_SENDERS,
          f"share {ev.get('share')}, {ev.get('distinct_senders')} senders, deposit_event {ev.get('deposit_event')}")
    lab2, _ = btc_sweep_proof(p, reg, ZHDANOVA, [dep], until_block=dep.block)
    check("sweep: negative — wallet paying INTO a deposit address is not a deposit address", lab2 is None)

    # ---- propagation ----
    new = propagate(reg, "btc", so)
    co = sw.input_addresses - {DEP}
    ok = {l.address for l in new if l.role == DEPOSIT and l.entity == "binance"} >= co
    check("propagate: sweep-proven deposit -> co-inputs = Binance deposit (cluster_propagated, decayed)",
          ok and all(l.basis == "cluster_propagated" and l.confidence < lab.confidence for l in new),
          f"{len(new)} labels, conf {sorted({l.confidence for l in new})}")

    n_ok = sum(ok for _, ok in results)
    print(f"\n{n_ok}/{len(results)} checks ok · {p.calls + fo.calls} API calls · {time.time() - t0:.1f}s · "
          f"providers {dict(p.calls_by)} · breaker trips {dict(p.trips)}")
    print("OVERALL: PASS" if n_ok == len(results) else "OVERALL: FAIL")
    sys.exit(0 if n_ok == len(results) else 1)


if __name__ == "__main__":
    main()
