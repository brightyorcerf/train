"""Tron via TronGrid v1: USDT (TRC-20) movements only — the Indian fraud rail (§21 scope-down).

Account model, same shape as the EVM adapter so the engine's account-chain path drives it unchanged:
  trc20    /v1/accounts/{a}/transactions/trc20   index = "<contract>:<value>:<n>" (TronGrid returns no
           event index, so identity is content + ordinal among IDENTICAL transfers in the tx, exactly
           as etherscan_v2 does for tokentx)
No TRX-native tracing, no contract decoding.

Snapshot coordinate: TronGrid's TRC-20 rows carry block_timestamp but NO block number, and the
endpoint bounds by min/max_timestamp. So on Tron `until_block` / `Edge.block` are UNIX SECONDS, not
block heights. ponytail: one extra call per tx would recover heights; add it if a report must print one.

Addresses stay base58 (T…), verbatim — base58 is case-sensitive; lowercasing it corrupts it.
Pagination: 200 rows/page via meta.fingerprint; > page_cap pages -> truncated (a hub, §9.1).
Keyless free tier (measured 2026-09-27): sequential 2-3 req/s all 200; a 30-way burst is 29/30 429
and leaves a penalty window of ~30s. Paced 1.5/s through the shared Redis bucket, capacity 1.
Zero-value transfers are address-poisoning spam and are dropped.
"""
import time

import httpx

from app.core.config import settings
from app.providers.base import IMMUTABLE, Edge, Provider, ProviderError

TG = "https://api.trongrid.io"
USDT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"   # Tether USD, 6 decimals (Tronscan-verified)
PAGE = 200


class TronGridProvider(Provider):
    chain = "tron"

    def __init__(self, store="auto", offline=False, page_cap: int = 5, finality_s: int = 600, limiter="auto"):
        super().__init__(store, offline, limiter)
        self.page_cap, self.finality_s = page_cap, finality_s
        self._memo: dict = {}
        self._http = httpx.Client(timeout=30, headers={"TRON-PRO-API-KEY": settings.trongrid_api_key}
                                  if settings.trongrid_api_key else {})

    # ---------- transport ----------
    def _get(self, cacheable: bool, path: str, **params):
        req = f"trongrid:{path}?" + "&".join(f"{k}={params[k]}" for k in sorted(params))
        if req in self._memo:
            return self._memo[req]
        self.calls += 1
        self.requests.append(req)
        if cacheable and self.store:
            body = self.store.get(req, IMMUTABLE)
            if body is not None:
                self.store_hits += 1
                self._memo[req] = body
                return self._seen(body)
        if self.offline:
            raise ProviderError(f"offline: {req} not in the raw store")
        try:
            res = self._fetch(path, params)
        except ProviderError:
            if (body := self._stale(req, f"{req[:60]}…")) is None:
                raise
            return body
        if cacheable:
            self._memo[req] = res
            if self.store:
                self.store.put(req, IMMUTABLE, "trongrid", res)
        return self._seen(res)

    def _fetch(self, path, params):
        err = ""
        for attempt in range(5):
            if self.limiter:
                self.limiter.acquire("trongrid")
            self.upstream += 1
            self.calls_by["trc20"] += 1
            try:
                r = self._http.get(TG + path, params=params)
            except httpx.HTTPError as e:
                err = type(e).__name__
                time.sleep(2 * (attempt + 1))
                continue
            if r.status_code in (429, 403):   # 403 = keyless penalty window after a burst
                err = f"HTTP {r.status_code}"
                time.sleep(5 * (attempt + 1))
                continue
            j = r.json() if r.status_code == 200 else {}
            if not j.get("success"):
                raise ProviderError(f"trongrid {path}: HTTP {r.status_code} {str(j or r.text)[:200]}")
            return {"data": j.get("data") or [], "fingerprint": (j.get("meta") or {}).get("fingerprint")}
        raise ProviderError(f"trongrid {path}: failed after 5 attempts ({err})")

    # ---------- chain facts ----------
    def tip(self) -> int:
        return int(time.time())

    def _final(self, until_s: int) -> bool:
        return self.offline or until_s <= self.tip() - self.finality_s

    def rows(self, address: str, since_s: int, until_s: int, order="asc", max_pages: int | None = None,
             **only) -> list[dict]:
        """Confirmed USDT transfer rows touching address in [since, until] (seconds), fingerprint-paged."""
        out, fp = [], None
        for _ in range(max_pages or self.page_cap):
            p = {"contract_address": USDT, "limit": PAGE, "only_confirmed": "true",
                 "min_timestamp": since_s * 1000, "max_timestamp": until_s * 1000,
                 "order_by": f"block_timestamp,{order}", **only}
            if fp:
                p["fingerprint"] = fp
            page = self._get(self._final(until_s), f"/v1/accounts/{address}/transactions/trc20", **p)
            out += page["data"]
            fp = page["fingerprint"]
            if not fp or len(page["data"]) < PAGE:
                return out
        self.truncated.add(address)
        return out

    # ---------- movements ----------
    def movements(self, address: str, until_block: int, since_block: int = 0) -> list[Edge]:
        key = (address, since_block, until_block)
        if key not in self._memo:
            self._memo[key] = _trc20(self.rows(address, since_block, until_block))
        return self._memo[key]

    def get_outgoing(self, address: str, until_block: int, since_block: int = 0) -> list[Edge]:
        return [e for e in self.movements(address, until_block, since_block) if e.src == address]

    def incoming_before(self, address: str, before_block: int, max_pages: int = 1) -> list[Edge]:
        """USDT receipts up to before_block, newest first — the §6.2c senders test."""
        return [e for e in _trc20(self.rows(address, 0, before_block, "desc", max_pages, only_to="true"))
                if e.dst == address]


def _trc20(rows) -> list[Edge]:
    n, out = {}, []
    for t in sorted(rows, key=lambda t: (t["block_timestamp"], t["transaction_id"])):
        c, v = t["token_info"]["address"], int(t["value"])
        if c != USDT or v <= 0 or t.get("type") != "Transfer":
            continue
        k = (t["transaction_id"], t["from"], t["to"], v)
        n[k] = n.get(k, -1) + 1
        ts = t["block_timestamp"] // 1000
        out.append(Edge(t["from"], t["to"], "trc20", t["transaction_id"], v, "USDT", ts, ts,
                        f"{c}:{v}:{n[k]}", {"decimals": int(t["token_info"]["decimals"]), "contract": c}))
    return out
