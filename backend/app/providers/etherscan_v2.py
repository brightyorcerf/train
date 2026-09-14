"""EVM via Etherscan V2 (§8, §7.2): one key, `chainid` selects ETH (1) or Polygon (137).

Account model: address->address edges of three kinds, each with its own identity (§7.2 edge key
(from, to, tx_hash, kind, log_index | trace id)) — one tx hash can emit many movements:
  native    txlist            index None
  internal  txlistinternal    index = traceId   (contract-originated value; e.g. a Safe's execTransaction)
  erc20     tokentx           index = "<contract>:<value>:<n>"  (from/to/value from the Transfer EVENT
                              LOG, not the tx). Etherscan V2 tokentx no longer returns logIndex
                              (observed 2026-09-12), so identity is content + ordinal among IDENTICAL
                              transfers in the tx — both parties' queries see that same set, so the
                              key is stable whichever side fetched it.
Token amounts stay in base units on the Edge; meta carries decimals + contract (normalize at display).

Conditional fetch (§9.1 — calls/expansion is the budget):
  txlist                   always
  eth_getCode              only if the address sent no tx in the window (contract, or a pure receiver)
  txlistinternal           only for contracts — EOAs cannot originate internal txs
  tokentx                  for contracts, and for EOAs that made a contract call in the window (an
                           EOA's own token transfer is always a call). ponytail: a transferFrom by an
                           approved spender with the approval outside the window is missed.
Pagination: 1,000 rows/request (free tier); windows advance by startblock and dedupe the boundary
block. More than `page_cap` pages -> address marked truncated (a hub; §9.1 deterministic truncation).
Free tier (measured, day1_ratetest.py): 3 req/s server-enforced, paced 2.5/s + retry.
"""
import time
from collections import Counter

import httpx

from app.core.config import settings
from app.core.ratelimit import open_limiter
from app.providers.base import IMMUTABLE, BlockchainProvider, Edge, ProviderError, TxIn, TxOut, TxRecord
from app.providers.store import open_store

ES = "https://api.etherscan.io/v2/api"
CHAIN_ID = {"eth": 1, "polygon": 137}
NATIVE = {"eth": "ETH", "polygon": "POL"}
MIN_INTERVAL = 1 / 2.5
PAGE = 1000
# Real tokens only: tokentx also returns address-poisoning spam and zero-value spoofed transfers
# "from" any address. ponytail: static allowlist; grow it from a token registry when needed.
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
class EtherscanV2Provider(BlockchainProvider):
    def __init__(self, chain: str, store="auto", offline=False, page_cap: int = 5, finality: int = 128,
                 limiter="auto"):
        if chain not in CHAIN_ID:
            raise ValueError(f"{chain}: Etherscan free tier here covers {sorted(CHAIN_ID)} (BNB is not free)")
        if not settings.etherscan_api_key:
            raise ProviderError("ETHERSCAN_API_KEY is empty in .env (free: etherscan.io/myapikey)")
        self.chain, self.page_cap, self.finality, self.offline = chain, page_cap, finality, offline
        self.store = open_store() if store == "auto" else store
        self.limiter = open_limiter() if limiter == "auto" else limiter
        self.calls, self.upstream, self.store_hits, self.retries = 0, 0, 0, 0
        self.stale: list[str] = []
        self.requests: list[str] = []        # every logical read, for the audit-log upstream hash (§12)
        self.calls_by = Counter()          # per action
        self.truncated: set[str] = set()
        self._tip: int | None = None
        self._last = 0.0
        self._memo: dict = {}
        self._http = httpx.Client(timeout=30)

    # ---------- transport ----------
    def _get(self, cacheable: bool, **params):
        """One Etherscan call. cacheable = the answer can't change (bounded by a final block)."""
        req = f"etherscan:{CHAIN_ID[self.chain]}?" + "&".join(f"{k}={params[k]}" for k in sorted(params))
        if req in self._memo:
            return self._memo[req]
        self.calls += 1
        self.requests.append(req)
        if cacheable and self.store:
            body = self.store.get(req, IMMUTABLE)
            if body is not None:
                self.store_hits += 1
                self._memo[req] = body
                return body
        if self.offline:
            raise ProviderError(f"offline: {req} not in the raw store")
        try:
            res = self._fetch(params)
        except ProviderError:
            old = self.store.latest(req) if self.store else None   # §8: 429 -> cache, mark partial
            if not old:
                raise
            body, sc, at = old
            self.stale.append(f"{req[:60]}… (stored {at:%Y-%m-%d %H:%M})")
            return body
        if cacheable:
            self._memo[req] = res
            if self.store:
                self.store.put(req, IMMUTABLE, "etherscan_v2", res)
        return res

    def _fetch(self, params):
        for attempt in range(5):
            if self.limiter:
                self.limiter.acquire("etherscan")
                self.limiter.spend_quota("etherscan")
            wait = self._last + MIN_INTERVAL - time.time()
            if wait > 0:
                time.sleep(wait)
            self._last = time.time()
            self.upstream += 1
            self.calls_by[params.get("action")] += 1
            try:
                r = self._http.get(ES, params={"chainid": CHAIN_ID[self.chain],
                                               "apikey": settings.etherscan_api_key, **params})
            except httpx.HTTPError as e:
                err = f"{type(e).__name__}"
                time.sleep(2 * (attempt + 1))
                continue
            j = r.json() if r.status_code == 200 else {"status": "0", "result": f"HTTP {r.status_code}"}
            res = j.get("result")
            if r.status_code == 429 or (isinstance(res, str) and "rate limit" in res.lower()):
                self.retries += 1   # arrival jitter at 2.5/s still trips the 3/s window ~15% (day 1)
                err = str(res)
                time.sleep(1.0 * (attempt + 1))
                continue
            if "jsonrpc" in j:
                if "error" in j:
                    raise ProviderError(f"etherscan {params.get('action')}: {j['error']}")
                return res
            if j.get("status") == "1" or res == [] or "no transactions found" in str(j.get("message", "")).lower():
                return res or []
            raise ProviderError(f"etherscan {params.get('action')}: {j.get('message')} {str(res)[:200]}")
        raise ProviderError(f"etherscan {params.get('action')}: failed after 5 attempts ({err})")

    # ---------- chain facts ----------
    def tip(self) -> int:
        if self._tip is None:
            # not a logical call: live and offline replays must spend the same trace budget
            self._tip = int(self._fetch({"module": "proxy", "action": "eth_blockNumber"}), 16)
        return self._tip

    def _final(self, block: int) -> bool:
        return self.offline or block <= self.tip() - self.finality   # offline: only stored answers exist

    def is_contract(self, address: str, until_block: int) -> bool:
        """Has code NOW. The free tier has no archive state — eth_getCode at a past block returns
        'historical state ... is not available' (observed 2026-09-12) — so this is as-of-latest, not
        as-of-snapshot. Wrong only for code deployed after the snapshot (costs 2 spare calls) or a
        selfdestructed contract (would miss its internal outflows; selfdestruct is dead since EIP-6780)."""
        code = self._get(True, module="proxy", action="eth_getCode", address=address.lower(), tag="latest")
        return code not in (None, "0x", "")

    def rows(self, action: str, address: str, since_block: int, until_block: int, sort="asc",
             max_pages: int | None = None) -> list[dict]:
        """All rows of a by-address list in [since, until], windowed past the 1k cap."""
        out, seen, start, end = [], set(), since_block, until_block
        for _ in range(max_pages or self.page_cap):
            page = self._get(self._final(until_block), module="account", action=action, address=address.lower(),
                             startblock=start, endblock=end, page=1, offset=PAGE, sort=sort)
            for t in page:
                k = (t["hash"], t.get("logIndex"), t.get("traceId"), t["from"], t["to"], t["value"])
                if k not in seen:
                    seen.add(k)
                    out.append(t)
            if len(page) < PAGE:
                return out
            b = int(page[-1]["blockNumber"])   # next window re-reads the boundary block; dedupe above
            if sort == "asc":
                start = b
            else:
                end = b
            if start > end:
                return out
        self.truncated.add(address.lower())
        return out

    # ---------- movements ----------
    def movements(self, address: str, until_block: int, since_block: int = 0) -> list[Edge]:
        """Every value movement touching address in the window (both directions), conditionally fetched."""
        key = (address.lower(), since_block, until_block)
        if key in self._memo:
            return self._memo[key]
        a = address.lower()
        txs = self.rows("txlist", a, since_block, until_block)
        sent = [t for t in txs if t["from"].lower() == a]
        edges = [_edge("native", t, NATIVE[self.chain], 18, None, self.chain) for t in txs
                 if t.get("isError") == "0" and int(t["value"]) > 0]
        contract = not sent and self.is_contract(a, until_block)
        if contract:
            edges += [_edge("internal", t, NATIVE[self.chain], 18, t.get("traceId") or "", self.chain)
                      for t in self.rows("txlistinternal", a, since_block, until_block)
                      if t.get("isError") == "0" and int(t["value"]) > 0]
        if contract or any(t.get("input", "0x") != "0x" for t in sent):
            edges += _erc20(self.rows("tokentx", a, since_block, until_block), self.chain)
        edges.sort(key=lambda e: (e.block, e.tx_hash, e.kind, str(e.index)))
        self._memo[key] = edges
        return edges

    def get_outgoing(self, address: str, until_block: int, since_block: int = 0) -> list[Edge]:
        return [e for e in self.movements(address, until_block, since_block) if e.src == address.lower()]

    def incoming_before(self, address: str, before_block: int, max_pages: int = 1) -> list[Edge]:
        """Receipts up to before_block, newest first (native + allowlisted tokens) — the §6.2c senders test.
        Always fetches tokentx: a token deposit address receives without sending a single tx."""
        a = address.lower()
        out = [_edge("native", t, NATIVE[self.chain], 18, None, self.chain)
               for t in self.rows("txlist", a, 0, before_block, "desc", max_pages)
               if t["to"].lower() == a and t.get("isError") == "0" and int(t["value"]) > 0]
        out += [e for e in _erc20(self.rows("tokentx", a, 0, before_block, "desc", max_pages), self.chain)
                if e.dst == a]
        return out

    def get_tx(self, tx_hash: str) -> TxRecord:
        """Account-model tx as a 1-in/1-out record (native value only)."""
        t = self._get(False, module="proxy", action="eth_getTransactionByHash", txhash=tx_hash)
        block = int(t["blockNumber"], 16) if t.get("blockNumber") else None
        ts = None
        if block is not None:
            ts = int(self._get(self._final(block), module="proxy", action="eth_getBlockByNumber", tag=hex(block),
                               boolean="false")["timestamp"], 16)
        v = int(t["value"], 16)
        return TxRecord(tx_hash, self.chain, block, ts, (TxIn(t["from"].lower(), v, None, None),),
                        (TxOut((t.get("to") or "").lower() or None, v, 0),))

    def get_neighbors(self, address: str, until_block: int, since_block: int = 0) -> list[str]:
        return sorted({e.dst for e in self.get_outgoing(address, until_block, since_block) if e.dst != address.lower()})


def _erc20(rows, chain) -> list[Edge]:
    """Allowlisted, non-zero Transfer rows -> erc20 edges, identity = contract:value:ordinal (see header)."""
    allowed, n, out = TOKENS[chain], {}, []
    for t in sorted(rows, key=lambda t: (int(t["blockNumber"]), t["hash"])):   # stable: Etherscan's in-tx order kept
        c = t["contractAddress"].lower()
        if c not in allowed or int(t["value"]) <= 0:
            continue
        k = (t["hash"], t["from"].lower(), t["to"].lower(), c, t["value"])
        n[k] = n.get(k, -1) + 1
        out.append(_edge("erc20", t, allowed[c], int(t["tokenDecimal"]), f"{c}:{t['value']}:{n[k]}", chain, c))
    return out


def _edge(kind, t, asset, decimals, index, chain, contract=None) -> Edge:
    return Edge(t["from"].lower(), (t["to"] or t.get("contractAddress") or "").lower(), kind, t["hash"],
                int(t["value"]), asset, int(t["blockNumber"]), int(t["timeStamp"]), index,
                {"decimals": decimals, **({"contract": contract} if contract else {})})
