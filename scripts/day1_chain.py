"""Day-1 chain access shared by day1_groundtruth.py and day1_verify.py.

Raw free-tier calls with a call counter. Deliberately not the §8 provider abstraction —
that lands day 2/4; this is the throwaway data-path probe.
"""
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.core.config import settings  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
ES = "https://api.etherscan.io/v2/api"
CHAIN_ID = {"eth": 1, "polygon": 137}
NATIVE = {"eth": "ETH", "polygon": "POL"}
ES_MIN_INTERVAL = 1 / 2.5  # free tier is 3/s server-side; 2.5/s paced = zero rejections (day1_ratetest.py)

# Real tokens only: tokentx also returns address-poisoning spam and zero-value spoofed
# transfers "from" any address. ponytail: static allowlist; token registry arrives with §6.
TOKENS = {
    "eth": {"0xdac17f958d2ee523a2206206994597c13d831ec7": "USDT",
            "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": "USDC",
            "0x6b175474e89094c44da98b954eedeac495271d0f": "DAI",
            "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2": "WETH"},
    "polygon": {"0xc2132d05d31c914a87c6611c10748aeb04b58e8f": "USDT",
                "0x3c499c542cef5e3811e1192ce70d8cc03d5c3359": "USDC",
                "0x2791bca1f2de4661ed88a30c99a7a9449aa84174": "USDC.e",
                "0x7ceb23fd6bc0add59e62ac25578270cff1b9f619": "WETH",
                "0x0d500b1d8e8ef31e21c99d1db9a6444d3adf1270": "WPOL"},
}


class RateLimited(Exception):
    pass


class Chain:
    def __init__(self):
        self.calls = 0
        self._http = httpx.Client(timeout=30)
        self._last_es = 0.0

    # ---------- Etherscan V2 ----------
    def es(self, chain: str, **params):
        if not settings.etherscan_api_key:
            sys.exit("ETHERSCAN_API_KEY is empty in .env — EVM chains need it (free: etherscan.io/myapikey)")
        for attempt in range(4):
            wait = self._last_es + ES_MIN_INTERVAL - time.time()
            if wait > 0:
                time.sleep(wait)
            self._last_es = time.time()
            self.calls += 1
            r = self._http.get(ES, params={"chainid": CHAIN_ID[chain], "apikey": settings.etherscan_api_key,
                                           **params})
            j = r.json() if r.status_code != 429 else {"status": "0", "result": "rate limit (HTTP 429)"}
            res = j.get("result")
            if isinstance(res, str) and "rate limit" in res.lower():
                time.sleep(1.5 * (attempt + 1))
                continue
            if "jsonrpc" in j:  # proxy module
                return res
            if j.get("status") == "1":
                return res
            if "no transactions found" in str(j.get("message", "")).lower() or res == []:
                return []
            raise RuntimeError(f"etherscan {params.get('action')}: {j.get('message')} {str(res)[:200]}")
        raise RateLimited("etherscan: still rate-limited after 4 attempts")

    def evm_outgoing(self, chain: str, addr: str, since_block: int, extra_tokens=()) -> list[dict]:
        """Outgoing value movements from addr at/after since_block: native + internal + allowlisted ERC-20.

        First page only (1,000 rows, ascending from since_block) — i.e. the movements closest in
        time to the funds' arrival. ponytail: no block-window pagination yet; hubs get truncated.
        """
        a = addr.lower()
        page = dict(module="account", address=addr, startblock=since_block, endblock=99_999_999,
                    page=1, offset=1000, sort="asc")
        moves = []
        txs = self.es(chain, action="txlist", **page)
        for t in txs:
            if t["from"].lower() == a and t.get("isError") == "0" and int(t["value"]) > 0:
                moves.append(_mv("native", t, NATIVE[chain], int(t["value"]) / 1e18))
        # Contracts never originate txs, so an address that sent nothing in txlist may be a
        # contract whose outflows are internal txs. EOAs can't make internal txs: skip the call.
        if txs and not any(t["from"].lower() == a for t in txs):
            for t in self.es(chain, action="txlistinternal", **page):
                if t["from"].lower() == a and t.get("isError") == "0" and int(t["value"]) > 0:
                    moves.append(_mv("internal", t, NATIVE[chain], int(t["value"]) / 1e18))
        allowed = TOKENS[chain] | {c.lower(): "?" for c in extra_tokens}
        for t in self.es(chain, action="tokentx", **page):
            c = t["contractAddress"].lower()
            if t["from"].lower() == a and c in allowed and int(t["value"]) > 0:
                moves.append(_mv("erc20", t, t["tokenSymbol"], int(t["value"]) / 10 ** int(t["tokenDecimal"])))
        moves.sort(key=lambda m: (m["block"], m["hash"]))
        return moves

    # ---------- BTC: mempool.space → Blockstream Esplora failover ----------
    def btc(self, path: str):
        err = None
        for base in (settings.mempool_base_url, settings.esplora_base_url):
            self.calls += 1
            try:
                r = self._http.get(f"{base}{path}")
                if r.status_code == 200:
                    return r.json()
                err = f"{base}: HTTP {r.status_code} {r.text[:100]}"
            except httpx.HTTPError as e:
                err = f"{base}: {e!r}"
        raise RuntimeError(f"btc {path}: both providers failed — {err}")

    def btc_spends(self, addr: str, since_height: int, max_pages: int = 4) -> list[dict]:
        """Confirmed txs where addr is an input, at/after since_height (newest-first pages of 25)."""
        out, last = [], None
        for _ in range(max_pages):
            page = self.btc(f"/address/{addr}/txs/chain" + (f"/{last}" if last else ""))
            if not page:
                break
            for t in page:
                if t["status"]["block_height"] >= since_height and any(
                        (v.get("prevout") or {}).get("scriptpubkey_address") == addr for v in t["vin"]):
                    out.append(t)
            last = page[-1]["txid"]
            if page[-1]["status"]["block_height"] < since_height or len(page) < 25:
                break
        return sorted(out, key=lambda t: (t["status"]["block_height"], t["txid"]))


def _mv(kind, t, asset, value):
    return {"kind": kind, "from": t["from"], "to": t["to"], "value": value, "asset": asset,
            "hash": t["hash"], "block": int(t["blockNumber"]), "ts": int(t["timeStamp"])}
