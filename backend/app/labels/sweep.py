"""Sweep-to-hot deposit evidence (§6.2c) — our PRIMARY deposit mechanism (TagPacks carry no
deposit roles; scripts/tagpacks_inspection.md).

An address A is a deposit address of entity X when
  (i)  A receives from >= MIN_SENDERS distinct senders, and
  (ii) one of A's first k spends sends >= SWEEP_SHARE of its output value to labeled hot/infra
       addresses of X (BTC: one tx; EVM: the first k transfers of one asset — evm_sweep_proof).
Why a share and not "any"/"all" (validated day 1 on real data): "any outflow to X" labeled a
*customer paying into* BitMEX as a BitMEX deposit address; "all outflows" missed a real Binance
consolidation that piggy-backed a 0.0097 BTC side output. _selfcheck pins all three cases.
"""
from dataclasses import dataclass

from app.labels.registry import DEPOSIT, SOURCE_TIER, Label, Registry
from app.providers.base import TxRecord

SWEEP_SHARE = 0.9
MIN_SENDERS = 3   # ponytail: "many" as a knob; per-user deposit addresses can be quiet — see report
K_SPENDS = 3


@dataclass
class Outflow:
    """One spend group: a BTC tx's outputs, or an EVM address's per-asset outflows in the window."""
    tx_hash: str
    block: int
    ts: int
    asset: str
    outputs: list[tuple[str, float]]   # (to, value)


def sweep_target(groups: list[Outflow], hot_entity) -> tuple[str, float, Outflow] | None:
    """First group sending >= SWEEP_SHARE of its value to one entity's hot wallets -> (entity, share, group).
    hot_entity: address -> entity id | None."""
    for g in groups:
        total = sum(v for _, v in g.outputs)
        to_ent: dict[str, float] = {}
        for to, v in g.outputs:
            e = hot_entity(to)
            if e:
                to_ent[e] = to_ent.get(e, 0) + v
        for e, v in sorted(to_ent.items(), key=lambda kv: -kv[1]):
            if total and v / total >= SWEEP_SHARE:
                return e, v / total, g
    return None


def distinct_senders(address: str, receipts: list[TxRecord]) -> int:
    """Funding txs from distinct spenders (self-change excluded; one sender = one input set)."""
    return len({min(t.input_addresses) for t in receipts
                if t.input_addresses and address not in t.input_addresses})


def btc_groups(spends: list[TxRecord]) -> list[Outflow]:
    return [Outflow(t.hash, t.block, t.ts, "BTC",
                    [(o.address, o.value / 1e8) for o in t.outputs if o.address and o.address not in t.input_addresses])
            for t in spends]


def btc_sweep_proof(prov, reg: Registry, address: str, spends: list[TxRecord], until_block: int,
                    deposit_tx: TxRecord | None = None) -> tuple[Label | None, dict]:
    """Test A=address given its spend txs (already fetched by the trace). Costs API calls only when
    the share test passes (history pages for the sender count). -> (Label or None, evidence)."""
    hit = sweep_target(btc_groups(spends[:K_SPENDS]), lambda a: reg.hot_entity("btc", a))
    if not hit:
        return None, {"sweep": None}
    entity, share, g = hit
    hot = max((o for o in g.outputs if reg.hot_entity("btc", o[0]) == entity), key=lambda o: o[1])
    # Senders BEFORE the sweep (inside the snapshot; later receipts would leak post-snapshot data).
    # Anchor paging at the sweep tx — it is in A's history — so busy addresses cost 1-2 pages.
    receipts = prov.receipts(address, until_block, max_pages=2, older_than=g.tx_hash)
    n = distinct_senders(address, receipts)
    hot_lab = reg.best("btc", hot[0])
    ev = {"entity": entity, "share": round(share, 4), "sweep_tx": g.tx_hash, "sweep_ts": g.ts,
          "hot_wallet": hot[0], "hot_label": f"{hot_lab.basis} <- {hot_lab.provenance}",
          "distinct_senders": n, "senders_truncated": address in prov.truncated}
    if deposit_tx:  # §6.4 deposit-event artifact: the funds we followed in
        ev["deposit_event"] = {"tx": deposit_tx.hash, "ts": deposit_tx.ts,
                               "amount_btc": sum(o.value for o in deposit_tx.outputs if o.address == address) / 1e8}
    if n < MIN_SENDERS:
        return None, {**ev, "sweep": "share_ok_senders_below_min"}
    lab = Label(address, "btc", DEPOSIT, entity, "sweep", SOURCE_TIER["sweep"], "sweep_proven",
                f"{n} distinct senders; {share:.1%} of tx {g.tx_hash} -> {entity} hot {hot[0]} "
                f"({hot_lab.basis} <- {hot_lab.provenance})")
    reg.add_label(lab)
    return lab, {**ev, "sweep": "proven"}


def evm_sweep_proof(prov, reg: Registry, address: str, out_edges, until_block: int,
                    deposit_edge=None) -> tuple[Label | None, dict]:
    """Account model: per asset, the first K_SPENDS outgoing movements must send >= SWEEP_SHARE of
    their value to one entity's hot wallets (a single EVM transfer has one recipient, so the share
    is taken over the first k spends, not one tx), AND the address must have received from
    >= MIN_SENDERS distinct senders before the sweep. The entity's own infra (gas top-ups from
    its feeder wallets) does not count as a sender."""
    chain = prov.chain
    by_asset: dict[str, list] = {}
    for e in out_edges:
        by_asset.setdefault(e.asset, []).append(e)
    groups = []
    for asset, es in sorted(by_asset.items()):
        first = sorted(es, key=lambda e: (e.block, e.tx_hash, str(e.index)))[:K_SPENDS]
        groups.append(Outflow(first[-1].tx_hash, first[-1].block, first[-1].ts, asset,
                              [(e.dst, e.value / 10 ** e.meta["decimals"]) for e in first]))
    hit = sweep_target(groups, lambda a: reg.hot_entity(chain, a))
    if not hit:
        return None, {"sweep": None}
    entity, share, g = hit
    hot = max((o for o in g.outputs if reg.hot_entity(chain, o[0]) == entity), key=lambda o: o[1])
    rx = prov.incoming_before(address, g.block)
    senders = {e.src for e in rx if reg.hot_entity(chain, e.src) != entity}
    hot_lab = reg.best(chain, hot[0])
    ev = {"entity": entity, "share": round(share, 4), "asset": g.asset, "sweep_tx": g.tx_hash, "sweep_ts": g.ts,
          "hot_wallet": hot[0], "hot_label": f"{hot_lab.basis} <- {hot_lab.provenance}",
          "distinct_senders": len(senders), "senders_truncated": address.lower() in prov.truncated}
    if deposit_edge is not None:  # §6.4 deposit-event artifact
        ev["deposit_event"] = {"tx": deposit_edge.tx_hash, "ts": deposit_edge.ts, "asset": deposit_edge.asset,
                               "amount": deposit_edge.value / 10 ** deposit_edge.meta["decimals"]}
    if len(senders) < MIN_SENDERS:
        return None, {**ev, "sweep": "share_ok_senders_below_min"}
    lab = Label(address, chain, DEPOSIT, entity, "sweep", SOURCE_TIER["sweep"], "sweep_proven",
                f"{len(senders)} distinct senders; {share:.1%} of first {K_SPENDS} {g.asset} spends -> {entity} "
                f"hot {hot[0]} (last {g.tx_hash}; {hot_lab.basis} <- {hot_lab.provenance})")
    reg.add_label(lab)
    return lab, {**ev, "sweep": "proven"}


def _selfcheck():
    hot = {"hot": "X", "hot2": "Y"}.get
    g = lambda *outs: [Outflow("t", 1, 1, "BTC", list(outs))]  # noqa: E731
    assert sweep_target(g(("hot", 78.9), ("other", 0.01)), hot)[0] == "X"   # consolidation + side output
    assert sweep_target(g(("hot", 1.6), ("other", 0.35)), hot) is None       # customer paying X (BitMEX FP)
    assert sweep_target(g(("hot", 5), ("hot2", 5)), hot) is None             # split across entities
    from app.providers.base import TxIn
    rx = lambda *ins: TxRecord("r", "btc", 1, 1, tuple(TxIn(a, 1, None, None) for a in ins), ())  # noqa: E731
    assert distinct_senders("A", [rx("s1"), rx("s1"), rx("s2", "s3"), rx("A", "s4")]) == 2


if __name__ == "__main__":
    _selfcheck()
    print("sweep selfcheck PASS")
