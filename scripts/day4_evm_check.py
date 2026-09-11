"""Day-4 real-data PASS/FAIL: the EVM adapter (§8, §7.2) + sweep-to-hot deposit evidence on EVM (§6.2c).

Every fixture is a real mainnet/Polygon transaction, pinned to a block snapshot.

    backend/.venv/bin/python scripts/day4_evm_check.py
"""
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.labels.registry import PgRegistry  # noqa: E402
from app.labels.sweep import MIN_SENDERS, evm_sweep_proof  # noqa: E402
from app.providers.etherscan_v2 import TOKENS, EtherscanV2Provider  # noqa: E402

S, PS = 25906777, 93439913          # pinned snapshots: ETH ~2026-09, Polygon ~2026-09
B14 = "0x28c6c06298d514db089934071355e5743bf21d60"   # Binance 14 (TagPacks binance.yaml + Etherscan nametag)
# Deposit-address behaviour, found by walking real USDT senders into Binance 14 in [S-300, S]:
DEPOSITS = ["0x4ab63073ad218106583ad64191d59c9db5099984",   # 57 senders
            "0xeae7380dd4cef6fbd1144f49e4d1e6964258a4f4",   # 29 senders, ETH + USDT
            "0x3531569692c92da7b1a47aa460ceb975f81081bf"]   # 22 senders
QUIET = "0x2eafd9325e211a094c0f3076a9627e9417d8ff01"        # same shape, only 2 senders -> must NOT be proven
PAYER = "0xf05e697a00027988b58b0830e8269e4eb3e34c23"        # paid INTO a deposit address (customer, not a VASP)
SAFE = "0x27fd43babfbe83a81d14665b1a6fb8030a60c9b4"         # WazirX Safe: a CONTRACT -> internal txs
SAFE_BLOCK = 20333000                                        # just before the 2024-07-18 compromise
MULTI = "0xcc797f4ca44cc0801d002acf2497fa97ced994d47550d3e4d8f2f78e87457224"   # 3 WETH movements, one hash
MULTI_PARTY = "0xa95e77aae62b5e61b4f6f1b1ecca862dbc9ff7e0"   # both WETH legs of that swap
POLY_HOT = "0xe7804c37c13166ff0b37f5ae0bb07a3aebb6e245"     # busy Polygon USDT receiver (unlabeled in our set)
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  {detail}")


def main():
    t0 = time.time()
    reg = PgRegistry()
    p = EtherscanV2Provider("eth")

    # ---- adapter shape: three tx kinds, edge identity, windowed pagination, snapshot bound ----
    es = p.movements(SAFE, SAFE_BLOCK, 20000000)
    kinds = Counter(e.kind for e in es)
    check("three movement kinds: native + internal + erc20 (WazirX Safe, a contract)",
          kinds["native"] and kinds["internal"] and kinds["erc20"] and p.is_contract(SAFE, SAFE_BLOCK), f"{dict(kinds)}")
    check("internal txs carry their trace id as edge index (§7.2)",
          all(e.index for e in es if e.kind == "internal"),
          f"{sorted({str(e.index) for e in es if e.kind == 'internal'})[:3]}")
    check("snapshot bound respected", max(e.block for e in es) <= SAFE_BLOCK,
          f"max block {max(e.block for e in es)} <= {SAFE_BLOCK}")
    check("ERC-20 amounts stay in base units with decimals in meta (USDT = 6)",
          all(e.meta["decimals"] == 6 and e.meta["contract"] == "0xdac17f958d2ee523a2206206994597c13d831ec7"
              for e in es if e.asset == "USDT"), f"{kinds['erc20']} token edges")

    multi = [e for e in p.movements(MULTI_PARTY, S, S - 500) if e.tx_hash == MULTI]
    check("one tx hash -> many edges, distinct identities (not collapsed, §7.2)",
          len(multi) >= 2 and len({(e.src, e.dst, e.tx_hash, e.kind, e.index) for e in multi}) == len(multi),
          f"{[(e.src[:8], e.dst[:8], e.value, str(e.index)[-12:]) for e in multi]}")

    key = lambda t: (t["hash"], t["from"], t["to"], t["value"])  # noqa: E731
    lo, hi = S - 600, S
    full = p.rows("txlist", B14, lo, hi)                          # > 1,000 rows: paging is exercised
    mid = (lo + hi) // 2
    split = p.rows("txlist", B14, lo, mid) + p.rows("txlist", B14, mid + 1, hi)
    check("windowed pagination past the 1k/request cap is lossless and de-duplicated",
          len(full) > 1000 and len(set(map(key, full))) == len(full) and set(map(key, split)) == set(map(key, full))
          and max(int(t["blockNumber"]) for t in full) <= hi,
          f"{len(full)} rows over {hi - lo} blocks == {len(split)} rows fetched as two windows")

    # ---- conditional fetch: the §9.1 call budget ----
    q = EtherscanV2Provider("eth", store=False)
    q.tip()                                  # pin finality once per provider, not per expansion
    q.calls_by.clear()
    c0 = q.calls
    q.movements(DEPOSITS[0], S, S - 2000)
    check("conditional fetch: an EOA that made contract calls costs txlist + tokentx only (§9.1)",
          q.calls - c0 == 2 and dict(q.calls_by) == {"txlist": 1, "tokentx": 1},
          f"{q.calls - c0} calls {dict(q.calls_by)}")
    q.calls_by.clear()
    c0 = q.calls
    q.movements(SAFE, SAFE_BLOCK, 20000000)  # a contract: + eth_getCode + txlistinternal
    check("conditional fetch: a contract also costs eth_getCode + txlistinternal",
          set(q.calls_by) == {"txlist", "eth_getCode", "txlistinternal", "tokentx"} and q.calls_by["eth_getCode"] == 1,
          f"{q.calls - c0} calls {dict(q.calls_by)} (txlist/tokentx page to the cap on a busy contract)")

    # ---- sweep-to-hot on EVM: share AND distinct senders ----
    for a in DEPOSITS:
        out = p.get_outgoing(a, S, S - 2000)
        lab, ev = evm_sweep_proof(p, reg, a, out, S)
        check(f"sweep: {a[:12]}… proven Binance deposit (>=90% to hot AND >={MIN_SENDERS} senders)",
              lab and lab.entity == "binance" and lab.basis == "sweep_proven" and ev["distinct_senders"] >= MIN_SENDERS,
              f"share {ev.get('share')}, {ev.get('distinct_senders')} senders, {ev.get('asset')} -> "
              f"{str(ev.get('hot_wallet'))[:12]}…")
    lab, ev = evm_sweep_proof(p, reg, QUIET, p.get_outgoing(QUIET, S, S - 2000), S)
    check("sweep negative: same shape, too few senders -> NOT a deposit address",
          lab is None and ev["sweep"] == "share_ok_senders_below_min",
          f"share {ev.get('share')} but {ev.get('distinct_senders')} senders")
    lab, _ = evm_sweep_proof(p, reg, PAYER, p.get_outgoing(PAYER, S, S - 2000), S)
    check("sweep negative: a customer paying INTO a deposit address is not a deposit address", lab is None)
    lab, _ = evm_sweep_proof(p, reg, B14, p.get_outgoing(B14, S, S - 300), S)
    check("sweep negative: the hot wallet itself is not crowned a deposit address", lab is None)

    # ---- Polygon: the second live chain, same adapter ----
    q = EtherscanV2Provider("polygon")
    pe = q.movements(POLY_HOT, PS, PS - 300)
    assets = Counter(e.asset for e in pe)
    check("Polygon (137) on the same adapter: native POL + USDT edges, snapshot bound",
          assets["POL"] and assets["USDT"] and max(e.block for e in pe) <= PS
          and all(e.meta["decimals"] == 6 for e in pe if e.asset == "USDT"), f"{dict(assets)}")
    check("Polygon token allowlist resolves contracts to symbols",
          all(e.meta["contract"] in TOKENS["polygon"] for e in pe if e.kind == "erc20"), "")

    n = sum(results)
    print(f"\n{n}/{len(results)} checks ok · {p.calls + q.calls} logical requests "
          f"({p.upstream + q.upstream} upstream, {p.store_hits + q.store_hits} from the §12 store) · "
          f"{time.time() - t0:.1f}s")
    print("OVERALL: PASS" if n == len(results) else "OVERALL: FAIL")
    sys.exit(0 if n == len(results) else 1)


if __name__ == "__main__":
    main()
