"""Day-1 provider gate: real calls against Etherscan V2, mempool.space, Blockstream Esplora.

    backend/.venv/bin/python scripts/day1_ratetest.py

Prints PASS/FAIL per check; exits non-zero if any CRITICAL check fails.
"""
import asyncio
import re
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.core.config import settings  # noqa: E402

ES = "https://api.etherscan.io/v2/api"
BUSY_ETH = "0x28C6c06298d514Db089934071355E5743bf21d60"   # Binance 14 hot wallet: millions of txs
BUSY_POLY = "0x0d500B1d8E8eF31E21C99d1Db9A6444d3ADf1270"  # WPOL contract on Polygon: always busy
BUSY_BNB = "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"   # WBNB contract on BNB chain
BTC_ADDR = "bc1qm34lsc65zpw79lxes69zkqmk6ee3ewf0j77s3h"   # Binance BTC reserve (GraphSense pack)

results: list[tuple[str, bool, bool, str]] = []  # (name, ok, critical, detail)


def check(name, ok, detail="", critical=True):
    results.append((name, ok, critical, detail))
    print(f"[{'PASS' if ok else 'FAIL' if critical else 'INFO'}] {name}  {detail}")


def es_limited(j) -> bool:
    # Etherscan signals rate limits in-band (HTTP 200, status "0"), not always as HTTP 429.
    return j.get("status") == "0" and "rate limit" in str(j.get("result", "")).lower()


async def es(c: httpx.AsyncClient, **params):
    r = await c.get(ES, params={**params, "apikey": settings.etherscan_api_key})
    if r.status_code == 429:
        return {"status": "0", "result": "HTTP 429 rate limit"}
    return r.json()


async def rate_probe(c, rate: float, n: int) -> int:
    """Fire n requests paced at `rate` req/s; return the responses."""
    async def one(i):
        await asyncio.sleep(i / rate)
        return await es(c, chainid=1, module="account", action="balance", address=BUSY_ETH, tag="latest")
    return await asyncio.gather(*(one(i) for i in range(n)))


async def etherscan():
    if not settings.etherscan_api_key:
        check("etherscan: API key present", False, "ETHERSCAN_API_KEY empty in .env — cannot test")
        return
    async with httpx.AsyncClient(timeout=30) as c:
        # 1. Rate ceiling. Warm the connection pool first: cold parallel TLS handshakes bunch
        # arrivals and fake a lower limit (day-1 probe read 0 req/s that way).
        for _ in range(3):
            await es(c, chainid=1, module="account", action="balance", address=BUSY_ETH, tag="latest")
            await asyncio.sleep(1)
        stated = None
        for rate in (2, 3, 5):  # informational: rejections by send pace (arrival jitter makes these noisy)
            await asyncio.sleep(3)  # drain the window between probes
            out = await rate_probe(c, rate, int(rate * 10))
            lim = [j for j in out if es_limited(j)]
            print(f"       probe {rate} req/s x10s: {len(lim)}/{len(out)} rate-limited")
            m = lim and re.search(r"\((\d+)/sec\)", str(lim[0]["result"]))
            stated = stated or (int(m.group(1)) if m else None)
        await asyncio.sleep(3)
        burst = await asyncio.gather(*(es(c, chainid=1, module="account", action="balance",
                                           address=BUSY_ETH, tag="latest") for _ in range(10)))
        print(f"       burst of 10 simultaneous: {sum(map(es_limited, burst))} limited")
        # What the capacity math (§9.1) actually uses: sequential client, paced + retry-on-limit.
        await asyncio.sleep(3)
        n, retries, t0 = 60, 0, time.monotonic()
        for i in range(n):
            while True:
                await asyncio.sleep(max(0, t0 + (i + retries) / 3 - time.monotonic()))
                if not es_limited(await es(c, chainid=1, module="account", action="balance",
                                           address=BUSY_ETH, tag="latest")):
                    break
                retries += 1
        eff = n / (time.monotonic() - t0)
        print(f"       sequential paced@3/s + retry: {n} calls ok, {retries} retries, {eff:.2f} req/s effective")
        check("etherscan: server-enforced limit >= 3 req/s", (stated or 0) >= 3,
              f"rate-limit message states {stated}/sec")
        check("etherscan: effective throughput >= 2.5 req/s (850-call trace <= 6 min)", eff >= 2.5,
              f"{eff:.2f} req/s, {retries}/{n + retries} calls rejected and retried")

        async def get(**p):  # paced single call, retry once on rate limit
            await asyncio.sleep(0.4)
            j = await es(c, **p)
            if es_limited(j):
                await asyncio.sleep(1.5)
                j = await es(c, **p)
            return j

        # 2. Chains.
        for cid, name, addr, crit in ((1, "ETH", BUSY_ETH, True), (137, "Polygon", BUSY_POLY, True),
                                      (56, "BNB (doc says NOT free)", BUSY_BNB, False)):
            j = await get(chainid=cid, module="account", action="txlist", address=addr,
                          page=1, offset=5, sort="desc")
            ok = j.get("status") == "1" and isinstance(j.get("result"), list)
            check(f"etherscan: chainid={cid} {name} txlist", ok,
                  "ok" if ok else f"{j.get('message')}: {str(j.get('result'))[:120]}", critical=crit)

        # 3. The three by-address endpoints on the free key.
        for action in ("txlist", "txlistinternal", "tokentx"):
            j = await get(chainid=1, module="account", action=action, address=BUSY_ETH,
                          page=1, offset=10, sort="desc")
            ok = j.get("status") == "1" and len(j.get("result", [])) > 0
            check(f"etherscan: {action} (ETH)", ok,
                  f"{len(j['result'])} rows" if ok else f"{j.get('message')}: {str(j.get('result'))[:120]}")

        # 4. Record cap: ask for 5,000 in one page, then see if page 2 x 1,000 works.
        j = await get(chainid=1, module="account", action="txlist", address=BUSY_ETH,
                      page=1, offset=5000, sort="asc")
        n5k = len(j["result"]) if isinstance(j.get("result"), list) else None
        print(f"       offset=5000 -> {n5k if n5k is not None else j.get('result')}")
        j1 = await get(chainid=1, module="account", action="txlist", address=BUSY_ETH,
                       page=1, offset=1000, sort="asc")
        n1 = len(j1["result"]) if isinstance(j1.get("result"), list) else 0
        j2 = await get(chainid=1, module="account", action="txlist", address=BUSY_ETH,
                       page=2, offset=1000, sort="asc")
        p2 = j2["result"] if isinstance(j2.get("result"), list) else []
        print(f"       page=1,offset=1000 -> {n1} rows; page=2,offset=1000 -> "
              f"{len(p2) if p2 else j2.get('result')}")
        # Block-window pagination: restart from the last block seen on page 1.
        last_block = int(j1["result"][-1]["blockNumber"]) if n1 else 0
        j3 = await get(chainid=1, module="account", action="txlist", address=BUSY_ETH,
                       startblock=last_block, endblock=99999999, page=1, offset=1000, sort="asc")
        n3 = len(j3["result"]) if isinstance(j3.get("result"), list) else 0
        print(f"       startblock={last_block} window -> {n3} rows")
        check("etherscan: 1,000-record cap is real", n5k is None or n5k <= 1000,
              f"offset=5000 returned {n5k if n5k is not None else 'error'}")
        mech = ("page/offset works past 1k" if p2 else "") + \
               (" + startblock windowing works" if n3 else "")
        check("etherscan: can paginate past 1k (no cursor tokens exist; page/offset or block windows)",
              bool(p2) or n3 > 0, mech.strip(" +"))


def btc_shape_ok(tx) -> tuple[bool, str]:
    need_vin = all("prevout" in v and "scriptpubkey_address" in (v["prevout"] or {}) and "value" in v["prevout"]
                   for v in tx["vin"] if not v.get("is_coinbase"))
    need_vout = all("value" in o and "scriptpubkey_address" in o for o in tx["vout"]
                    if o.get("scriptpubkey_type") != "op_return")
    need_status = {"confirmed", "block_height", "block_time"} <= set(tx["status"])
    return need_vin and need_vout and need_status, \
        f"{len(tx['vin'])} vin / {len(tx['vout'])} vout, vin.prevout.address+value, vout.address+value, status.block_*"


def btc():
    bases = {"mempool.space": settings.mempool_base_url, "blockstream": settings.esplora_base_url}
    got: dict[str, dict] = {}
    with httpx.Client(timeout=30) as c:
        txid = None
        for name, base in bases.items():
            try:
                a = c.get(f"{base}/address/{BTC_ADDR}").json()
                ok = a.get("address") == BTC_ADDR and "chain_stats" in a
                check(f"{name}: GET /address/{{addr}}", ok,
                      f"funded_txo_count={a['chain_stats']['funded_txo_count']}" if ok else str(a)[:120])
                if txid is None:
                    txs = c.get(f"{base}/address/{BTC_ADDR}/txs").json()
                    # pick a confirmed tx where this address spends — the shape BFS walks
                    txid = next(t["txid"] for t in txs if t["status"]["confirmed"]
                                and any((v.get("prevout") or {}).get("scriptpubkey_address") == BTC_ADDR
                                        for v in t["vin"]))
                t = c.get(f"{base}/tx/{txid}").json()
                ok, detail = btc_shape_ok(t)
                check(f"{name}: GET /tx/{{txid}} hypernode shape", ok, f"{txid[:16]}… {detail}")
                osp = c.get(f"{base}/tx/{txid}/outspends").json()
                check(f"{name}: GET /tx/{{txid}}/outspends (forward UTXO walk)",
                      isinstance(osp, list) and len(osp) == len(t["vout"]), f"{len(osp)} entries")
                got[name] = {"addr": a["chain_stats"], "tx": t}
            except Exception as e:  # noqa: BLE001 — report, don't crash the gate
                check(f"{name}: reachable", False, repr(e)[:160])
    if len(got) == 2:
        m, b = got["mempool.space"], got["blockstream"]
        norm = lambda t: (sorted((v["txid"], v["vout"]) for v in t["vin"]),  # noqa: E731
                          [(o.get("scriptpubkey_address"), o["value"]) for o in t["vout"]],
                          t["status"].get("block_hash"))
        check("esplora parity: same tx data on both providers", norm(m["tx"]) == norm(b["tx"]))
        # address stats can differ by a block if one provider is a tip behind — tolerate, but show it
        check("esplora parity: same address chain_stats", m["addr"] == b["addr"],
              "" if m["addr"] == b["addr"] else f"mempool={m['addr']} blockstream={b['addr']}",
              critical=False)


def main():
    t0 = time.time()
    print("== Etherscan V2"); asyncio.run(etherscan())
    print("== BTC (mempool.space / Blockstream Esplora)"); btc()
    crit_fail = [r for r in results if r[2] and not r[1]]
    print(f"\n{len(results) - len(crit_fail)}/{len(results)} checks ok in {time.time() - t0:.1f}s")
    print("OVERALL:", ("FAIL — " + "; ".join(r[0] for r in crit_fail)) if crit_fail else "PASS")
    sys.exit(1 if crit_fail else 0)


if __name__ == "__main__":
    main()
