"""BTC via Esplora: mempool.space primary -> blockstream.info failover (§8).

Transactions are :Tx hypernodes (§7.3): get_outgoing emits address -FUNDS-> tx and
tx -CREDITS-> address edges, never fabricated address->address pairs. Every CREDITS edge is
annotated with change detection (§7.5) and the tx with a CoinJoin check (§7.4).

Transport: per-provider pacing + circuit breaker. A provider that times out / 429s / 5xx is
skipped for `cooldown` seconds instead of costing a full timeout on every call (day 2: mempool.space
went unresponsive from our IP mid-survey and each call burned 30 s before failing over).
Free-tier ceilings are low: Blockstream caps unauthenticated use at 700 req/hour/IP (since 2025-07-15,
per its 429 body); mempool.space throttles bursts without a published number. Pace, cache, fail over.
"""
import time
from collections import Counter

import httpx

from app.boundary.change import coinjoin_reason, detect_change
from app.core.config import settings
from app.providers.base import BlockchainProvider, Edge, TxIn, TxOut, TxRecord


class ProviderError(RuntimeError):
    pass


class EsploraProvider(BlockchainProvider):
    chain = "btc"

    def __init__(self, bases=None, min_interval=0.5, timeout=10.0, cooldown=120.0, slow=8.0):
        self.bases = bases or [settings.mempool_base_url, settings.esplora_base_url]
        self.min_interval, self.cooldown, self.slow = min_interval, cooldown, slow
        self.calls, self.calls_by, self.trips = 0, Counter(), Counter()
        self.down_until: dict[str, float] = {}
        self.truncated: set[str] = set()   # addresses whose history exceeded the page cap
        self._last: dict[str, float] = {}
        self._cache: dict[str, object] = {}  # ponytail: in-process; content-addressed store is §12/day 6
        self._http = httpx.Client(timeout=timeout)

    # ---------- transport ----------
    def _get(self, path: str, cache: bool = False):
        if path in self._cache:
            return self._cache[path]
        now = time.time()
        up = [b for b in self.bases if self.down_until.get(b, 0) <= now]
        # all tripped -> half-open: retry whichever recovers first rather than failing outright
        order = up or [min(self.bases, key=lambda b: self.down_until[b])]
        errs = []
        for base in order:
            wait = self._last.get(base, 0) + self.min_interval - time.time()
            if wait > 0:
                time.sleep(wait)
            self._last[base] = time.time()
            self.calls += 1
            self.calls_by[base] += 1
            try:
                r = self._http.get(base + path)
            except httpx.HTTPError as e:
                errs.append(f"{base}: {type(e).__name__}")
                self._trip(base)
                continue
            if time.time() - self._last[base] > self.slow:
                self._trip(base)  # throttling often shows as trickled responses (httpx timeout is per-read)
            if r.status_code == 200:
                j = r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text
                if cache:
                    self._cache[path] = j
                return j
            errs.append(f"{base}: HTTP {r.status_code} {r.text[:80]}")
            if r.status_code == 429 or r.status_code >= 500:
                self._trip(base)
        raise ProviderError(f"GET {path} failed on every provider — {' | '.join(errs) or 'all tripped'}")

    def _trip(self, base: str) -> None:
        self.down_until[base] = time.time() + self.cooldown
        self.trips[base] += 1

    # ---------- raw queries ----------
    def get_tx(self, tx_hash: str) -> TxRecord:
        j = self._get(f"/tx/{tx_hash}")
        if j["status"]["confirmed"]:
            self._cache[f"/tx/{tx_hash}"] = j
        return _rec(j)

    def address_stats(self, address: str) -> dict:
        return self._get(f"/address/{address}")["chain_stats"]

    def outspend(self, txid: str, vout: int, until_block: int) -> str | None:
        """Txid that spent (txid, vout) at or before until_block, else None."""
        o = self._get(f"/tx/{txid}/outspend/{vout}")
        st = o.get("status") or {}
        if not (o.get("spent") and st.get("confirmed") and st["block_height"] <= until_block):
            return None
        self._cache[f"/tx/{txid}/outspend/{vout}"] = o
        return o["txid"]

    def history(self, address: str, until_block: int, since_block: int = 0, max_pages: int = 8,
                older_than: str | None = None) -> list[TxRecord]:
        """Confirmed txs touching address in [since_block, until_block], oldest first.
        Esplora pages newest-first with no block-range query, so a busy address's historical window
        can sit many pages deep: pass older_than=<a txid of this address> to start paging just
        before it (e.g. a tx the trace already holds) instead of from today."""
        out, last = [], older_than
        for _ in range(max_pages):
            page = self._get(f"/address/{address}/txs/chain" + (f"/{last}" if last else ""))
            if not page:
                return sorted(out, key=_order)
            for t in page:
                if since_block <= t["status"]["block_height"] <= until_block:
                    self._cache[f"/tx/{t['txid']}"] = t
                    out.append(_rec(t))
            last = page[-1]["txid"]
            if page[-1]["status"]["block_height"] < since_block or len(page) < 25:
                return sorted(out, key=_order)
        self.truncated.add(address)
        return sorted(out, key=_order)

    def spends(self, address: str, until_block: int, since_block: int = 0) -> list[TxRecord]:
        return [t for t in self.history(address, until_block, since_block) if address in t.input_addresses]

    def receipts(self, address: str, until_block: int, since_block: int = 0, max_pages: int = 8,
                 older_than: str | None = None) -> list[TxRecord]:
        return [t for t in self.history(address, until_block, since_block, max_pages, older_than)
                if any(o.address == address for o in t.outputs)]

    # ---------- hypernode edges ----------
    def annotate(self, tx: TxRecord) -> dict:
        """{'change': Change|None, 'coinjoin': reason|None}. Looks up output freshness only when the
        cheap features tie (2 calls), so most txs cost nothing extra."""
        cj = coinjoin_reason(tx)
        if cj:
            return {"change": None, "coinjoin": cj}
        ch = detect_change(tx)
        outs = [o for o in tx.outputs if o.address]
        if ch is None and len(outs) == 2 and not (tx.input_addresses & {o.address for o in outs}):
            one_time = {o.address: self.address_stats(o.address)["funded_txo_count"] == 1 for o in outs}
            ch = detect_change(tx, one_time)
        return {"change": ch, "coinjoin": None}

    def tx_edges(self, tx: TxRecord) -> list[Edge]:
        ann = self.annotate(tx)
        chg = ann["change"]
        edges = [Edge(i.address, tx.hash, "funds", tx.hash, i.value, "BTC", tx.block, tx.ts, n)
                 for n, i in enumerate(tx.inputs) if i.address]
        for o in tx.outputs:
            if o.address:
                meta = {"coinjoin": ann["coinjoin"]} if ann["coinjoin"] else {}
                if chg and chg.vout == o.vout:
                    meta = {"change": chg.confidence, "change_reasons": list(chg.reasons)}
                edges.append(Edge(tx.hash, o.address, "credits", tx.hash, o.value, "BTC", tx.block, tx.ts,
                                  o.vout, meta))
        return edges

    def get_outgoing(self, address: str, until_block: int, since_block: int = 0) -> list[Edge]:
        return [e for t in self.spends(address, until_block, since_block) for e in self.tx_edges(t)]

    def get_neighbors(self, address: str, until_block: int, since_block: int = 0) -> list[str]:
        return sorted({e.dst for e in self.get_outgoing(address, until_block, since_block)
                       if e.kind == "credits" and "change" not in e.meta and e.dst != address})


def _order(t: TxRecord):
    return (t.block, t.hash)


def _rec(j: dict) -> TxRecord:
    st = j["status"]
    return TxRecord(
        hash=j["txid"], chain="btc", block=st.get("block_height"), ts=st.get("block_time"),
        inputs=tuple(TxIn((v.get("prevout") or {}).get("scriptpubkey_address"),
                          (v.get("prevout") or {}).get("value", 0), v.get("txid"), v.get("vout"))
                     for v in j["vin"]),
        outputs=tuple(TxOut(o.get("scriptpubkey_address"), o["value"], n) for n, o in enumerate(j["vout"])),
    )
