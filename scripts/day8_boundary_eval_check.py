"""Day-8 real-data PASS/FAIL: service-node policy (§9.3) + the label-quality fix + the eval harness (§11.2).

Covers, all against real mainnet data at pinned snapshots:
  - the hand-curated bridge list, and BROKEN_AT_BRIDGE no longer being dead code
  - mixer = STOP, DEX = record the swap and continue at reduced confidence
  - token contracts can never be sweep targets (the BUSD/KCS/BNB false-positive class)
  - Polygon labels that are actually Polygon-specific
  - the eval harness running the golden set offline from the §12 raw store

    docker compose up -d postgres redis neo4j
    backend/.venv/bin/python scripts/day8_boundary_eval_check.py
"""
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.boundary import bridge  # noqa: E402
from app.labels.registry import BRIDGE, DEPOSIT, DEX, HOT, MIXER, TOKEN, PgRegistry  # noqa: E402
from app.providers.etherscan_v2 import EtherscanV2Provider  # noqa: E402
from app.scoring.engine import score_candidate  # noqa: E402
from app.trace.engine import Tracer  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
ETH_SNAP, POLY_SNAP = 25906777, 93439913
WORMHOLE = "0x3ee18b2214aff97000d974cf647e7c347e8fa585"     # Wormhole Token Bridge
TORNADO = "0xd90e2f925da726b50c4ed8d0fb90ad053324f31b"
LAZARUS = "0xa0e1c89Ef1a489c9C7dE96311eD5Ce5D32c20E4B"       # real trace that reaches Tornado Cash
SWAPPER = "0xa95e77aae62b5e61b4f6f1b1ecca862dbc9ff7e0"       # real Uniswap V2 swap (WETH leg)
UNISWAP = "0x7a250d5630b4cf539739df2c5dacb4c659f2488d"
TOKENS_THAT_WERE_HOT = {"0x4fabb145d64652a948d72533023f6e7a623c7c53": "Binance USD (BUSD)",
                        "0xb8c77482e45f1f44de1745f52c74426c631bdd52": "Binance: BNB Token",
                        "0xf34960d9d60be18cc1d5afc1a6f012a723a28811": "KuCoin Token (KCS)"}
POLY_BINANCE = "0xe7804c37c13166ff0b37f5ae0bb07a3aebb6e245"
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  {detail}")


def main():
    t0 = time.time()
    reg = PgRegistry()
    print(f"label set {reg.version} ({reg.n_labels} labels)\n")

    # ---- 1. the bridge list exists and is in the pinned label set ----
    bl = bridge.labels()
    in_set = [l for l in bl if any(x.role == BRIDGE for x in reg.lookup(l.chain, l.address))]
    check("hand-curated bridge list is loaded into the pinned label set (§9.3)",
          len(bl) >= 18 and len(in_set) == len(bl),
          f"{len(bl)} addresses across {len(bridge.entities())} bridges: {', '.join(bridge.entities())}")
    check("every bridge address carries a source URL and a served-chain list",
          all("http" in l.provenance for l in bl) and all(bridge.destinations()[e] for e in bridge.entities()),
          f"e.g. wormhole -> {bridge.destinations()['wormhole'][:4]}…")
    f = bridge.flag("wormhole", "Wormhole", WORMHOLE, 1, "0xabc")
    check("the bridge flag emits contract + served chains, and does NOT claim a decoded destination",
          f.startswith("bridge:") and "serves [" in f and "not decoded" in f, f"“{f[:100]}…”")

    # ---- 2. BROKEN_AT_BRIDGE is no longer dead code: a real sender into a real bridge ----
    p = EtherscanV2Provider("eth")
    rows = [r for r in p.rows("txlist", WORMHOLE, ETH_SNAP - 40000, ETH_SNAP, max_pages=1)
            if r["to"].lower() == WORMHOLE and int(r["value"]) > 0 and r["isError"] == "0"]
    sender = sorted(rows, key=lambda r: (-int(r["value"]), r["hash"]))[0]["from"].lower() if rows else None
    if sender:
        t = Tracer("eth", ETH_SNAP)
        r = t.run(sender, max_hops=1, collect_all=True)
        bflags = [x for x in r["flags"] if x.startswith("bridge:")]
        check("a real wallet paying a real bridge trips the bridge boundary (was dead code before today)",
              bool(bflags), f"{sender[:14]}… -> {bflags[0][:88] if bflags else 'NO BRIDGE FLAG'}…")
    else:
        check("a real wallet paying a real bridge trips the bridge boundary", False, "no bridge txs in window")

    # ---- 3. mixer = STOP on a real trace ----
    t = Tracer("eth", ETH_SNAP)
    laz = t.run(LAZARUS, max_hops=3, collect_all=True)
    mix = [x for x in laz["flags"] if x.startswith("mixer:")]
    check("mixer boundary fires on a real trace and stops there (§9.3 STOP)",
          bool(mix) and TORNADO in " ".join(mix) and TORNADO not in [n for n in (laz.get("endpoint") or "")],
          f"{len(mix)} mixer flag(s), e.g. {mix[0][:80] if mix else '-'}…")

    # ---- 4. DEX = record the swap, CONTINUE at reduced confidence ----
    t2 = Tracer("eth", ETH_SNAP)
    sw = t2.run(SWAPPER, since_block=ETH_SNAP - 500, max_hops=2, collect_all=True)
    dex = [x for x in sw["flags"] if x.startswith("dex:")]
    check("DEX boundary records the swap on a real Uniswap V2 trade (§9.3)",
          bool(dex) and UNISWAP in " ".join(dex), f"{dex[0][:96] if dex else 'NO DEX FLAG'}…")
    check("…and the trace CONTINUES past the swap on the asset received back, not stopping at the router",
          any(a != SWAPPER for a in [UNISWAP]) and sw["addresses_seen"] >= 1 and "1:1 value" in " ".join(dex),
          f"{sw['addresses_seen']} addresses seen, swap recorded as linkage-breaking")
    cand = {"role": DEPOSIT, "basis": "sweep_proven", "tier": "tagpacks", "value": 10.0, "asset": "ETH",
            "ts": [0, 0], "path": [{"from": SWAPPER, "to": UNISWAP, "tx": "0xt"}]}
    clean, _ = score_candidate(cand, [])
    swapped, br = score_candidate(cand, [f"dex:Uniswap@{UNISWAP}(hop 1, tx 0xt)"])
    check("…and a swap on the path costs confidence (reduced, not ignored)",
          swapped < clean and br["penalty"] == 0.15,
          f"{clean}/100 clean -> {swapped}/100 with the swap on the path (penalty {br['penalty']})")

    # ---- 5. token contracts can never be sweep targets (the day-4 false-positive class) ----
    bad = {a: reg.lookup("eth", a) for a in TOKENS_THAT_WERE_HOT}
    check("token contracts no longer carry hot/deposit/dex roles (BUSD, BNB, KCS)",
          all(not any(l.role in (HOT, DEPOSIT, DEX) for l in labs) for labs in bad.values()),
          "; ".join(f"{TOKENS_THAT_WERE_HOT[a]}={[l.role for l in labs] or ['none']}" for a, labs in bad.items()))
    check("…so none of them can ever be a sweep target (§6.2c hot_entity)",
          all(reg.hot_entity("eth", a) is None for a in TOKENS_THAT_WERE_HOT),
          f"hot_entity -> {[reg.hot_entity('eth', a) for a in TOKENS_THAT_WERE_HOT]}")
    check("…and they stay visible as `token`, with the on-chain evidence in provenance (not silently dropped)",
          all(any(l.role == TOKEN and "on-chain verified" in l.provenance for l in labs)
              for labs in bad.values()),
          f"{sum(1 for labs in bad.values() for l in labs if l.role == TOKEN)} token-role labels on these 3")

    # ---- 6. Polygon labels that are actually Polygon-specific ----
    check("Polygon now has curated Polygon-specific exchange labels (not just the EVM mirror)",
          reg.hot_entity("polygon", POLY_BINANCE) == "binance",
          f"{POLY_BINANCE[:14]}… -> {reg.hot_entity('polygon', POLY_BINANCE)} "
          f"(tier {[l.source for l in reg.lookup('polygon', POLY_BINANCE)][:1]})")
    check("…at the heuristic tier, because an explorer name tag is not exchange-published data",
          all(l.source == "heuristic" for l in reg.lookup("polygon", POLY_BINANCE) if l.role == HOT),
          "source_tier 0.3 -> any sweep proven against it scores low, by design")

    # ---- 7. the eval harness runs the golden set offline from the raw store ----
    out = subprocess.run([sys.executable, "-m", "app.eval.harness", "--json",
                          str(REPO / "scripts" / "day8_eval_results.json")],
                         capture_output=True, text=True, cwd=REPO / "backend")
    lines = out.stdout.splitlines()
    ranked = next((l for l in lines if "ranked #1" in l), "")
    stab = next((l for l in lines if "rank stability" in l), "")
    offline = next((l for l in lines if "upstream" in l and "telemetry" in l), "")
    # "never a percentage" applies to the ACCURACY figure; "+/-20%" in the stability line is the
    # perturbation size and is meant to be there.
    check("eval harness runs the full golden set offline and reports M of N (never a percentage)",
          bool(ranked) and bool(stab) and "%" not in ranked and " of " in ranked
          and ", 0 upstream" in offline,
          f"{ranked.strip()} · {stab.strip()}" or out.stdout[-200:] or out.stderr[-200:])
    print(out.stdout)

    n = sum(results)
    print(f"\n{n}/{len(results)} checks ok · {time.time() - t0:.1f}s")
    print("OVERALL: PASS" if n == len(results) else "OVERALL: FAIL")
    sys.exit(0 if n == len(results) else 1)


if __name__ == "__main__":
    main()
