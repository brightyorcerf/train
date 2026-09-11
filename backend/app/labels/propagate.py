"""SAME_OWNER edges from common-input ownership (§7.4) and cluster->entity label propagation (§6.2b).

Common-input ownership is a HEURISTIC with a confidence, never "same owner" (§18): CoinJoin
breaks it, so a CoinJoin-shaped tx yields no edges at all.
"""
from collections import deque
from dataclasses import dataclass

from app.boundary.change import coinjoin_reason
from app.labels.registry import Label, Registry
from app.providers.base import TxRecord

CO_INPUT_CONF = 0.9
PAYJOIN_SHAPE_CONF = 0.6  # 2-in/2-out can be a PayJoin (receiver contributes an input)
DECAY = 0.8               # per propagation hop
MIN_CONF = 0.2


@dataclass(frozen=True)
class SameOwner:
    a: str
    b: str
    confidence: float
    tx_hash: str


def same_owner_edges(tx: TxRecord) -> list[SameOwner]:
    """Star over the co-inputs (n-1 edges, not n²): enough for connectivity."""
    ins = sorted(tx.input_addresses)
    if len(ins) < 2 or coinjoin_reason(tx):
        return []
    conf = PAYJOIN_SHAPE_CONF if len(tx.inputs) == 2 and len(tx.outputs) == 2 else CO_INPUT_CONF
    return [SameOwner(ins[0], b, conf, tx.hash) for b in ins[1:]]


def propagate(reg: Registry, chain: str, edges: list[SameOwner]) -> list[Label]:
    """Push every existing label across SAME_OWNER edges with decaying confidence
    (label conf × edge conf × DECAY per hop). Returns the new labels (also added to reg)."""
    # Edges are stored as a star per tx, but all co-inputs of one tx are equally related:
    # propagate per tx (every co-input is one hop away), not along the star.
    members: dict[str, set[str]] = {}
    tx_conf: dict[str, float] = {}
    txs_of: dict[str, set[str]] = {}
    for e in edges:
        members.setdefault(e.tx_hash, set()).update((e.a, e.b))
        tx_conf[e.tx_hash] = e.confidence
        txs_of.setdefault(e.a, set()).add(e.tx_hash)
        txs_of.setdefault(e.b, set()).add(e.tx_hash)
    new = []
    for seed in sorted(txs_of):
        for lab in [l for l in reg.lookup(chain, seed) if l.basis != "cluster_propagated"]:
            seen, q = {seed}, deque([(seed, lab.confidence, 0)])
            while q:
                node, conf, hops = q.popleft()
                for tx in sorted(txs_of.get(node, ())):
                    c = round(conf * tx_conf[tx] * DECAY, 4)
                    if c < MIN_CONF:
                        continue
                    for nxt in sorted(members[tx] - seen):
                        seen.add(nxt)
                        nl = Label(nxt, chain, lab.role, lab.entity, "heuristic", c, "cluster_propagated",
                                   f"co-input of {seed} ({lab.role}:{lab.entity}, {lab.basis}) via tx {tx}, "
                                   f"{hops + 1} hop(s)")
                        reg.add_label(nl)
                        new.append(nl)
                        q.append((nxt, c, hops + 1))
    return new


def _selfcheck():
    from app.labels.registry import DEPOSIT
    from app.providers.base import TxIn, TxOut
    t = lambda h, ins, n_out=1: TxRecord(h, "btc", 1, 1, tuple(TxIn(a, 10**6 + i, None, None) for i, a in enumerate(ins)),  # noqa: E731
                                          tuple(TxOut(f"3out{h}{i}", 10**6 + i, i) for i in range(n_out)))
    e = same_owner_edges(t("c", ["d1", "d2", "d3"])) + same_owner_edges(t("c2", ["d3", "d4"]))
    assert len(e) == 3 and e[-1].confidence == CO_INPUT_CONF
    mix = TxRecord("m", "btc", 1, 1, tuple(TxIn(f"i{i}", 10**7, None, None) for i in range(5)),
                   tuple(TxOut(f"o{i}", 10**7, i) for i in range(5)))
    assert same_owner_edges(mix) == []                           # CoinJoin: no edges
    r = Registry()
    r.add_label(Label("d1", "btc", DEPOSIT, "binance", "sweep", 0.5, "sweep_proven", "x"))
    got = {l.address: l.confidence for l in propagate(r, "btc", e)}
    assert got["d2"] == got["d3"] == 0.36 and got["d4"] == 0.2592 and "d1" not in got   # decays per hop
    assert r.best("btc", "d4").basis == "cluster_propagated"
    r2 = Registry()   # seed off the star's centre: every co-input is still one hop away
    r2.add_label(Label("d3", "btc", DEPOSIT, "binance", "sweep", 0.5, "sweep_proven", "x"))
    got = {l.address: l.confidence for l in propagate(r2, "btc", e)}
    assert got == {"d1": 0.36, "d2": 0.36, "d4": 0.36}


if __name__ == "__main__":
    _selfcheck()
    print("propagate selfcheck PASS")
