"""BTC change-address detection (§7.5) and CoinJoin detection (§7.4 false-positive guard).

Change: forward tracing that treats every output as a payment walks change back to the sender
(a peel chain IS a chain of change outputs). Kept even if the peel typology is cut (§21).
CoinJoin: equal-value outputs from many unrelated inputs — common-input-ownership is false there.
"""
from collections import Counter
from dataclasses import dataclass

from app.providers.base import TxRecord


@dataclass(frozen=True)
class Change:
    vout: int
    confidence: float
    reasons: tuple[str, ...]


def script_type(addr: str) -> str:
    if addr.startswith("bc1p"):
        return "p2tr"
    if addr.startswith("bc1q"):
        return "p2wpkh" if len(addr) == 42 else "p2wsh"
    return {"1": "p2pkh", "3": "p2sh"}.get(addr[:1], "other")


def is_round(sats: int) -> bool:
    return sats % 100_000 == 0  # <= 3 BTC decimals (0.5, 0.123); change is the arithmetic leftover


def detect_change(tx: TxRecord, one_time: dict[str, bool] | None = None) -> Change | None:
    """Which output (if any) returns to the spender. one_time: address -> funded exactly once ever
    (a freshly created address); optional because it costs one lookup per output."""
    outs = [o for o in tx.outputs if o.address]
    if len(outs) < 2:
        return None  # a single output is a full spend (or a sweep), not payment + change
    ins = tx.input_addresses
    reuse = [o for o in outs if o.address in ins]
    if len(reuse) == 1:
        return Change(reuse[0].vout, 0.95, ("output returns to an input address",))
    if reuse or len(outs) != 2:
        return None  # ponytail: only the payment+change shape; batched payouts stay ambiguous
    in_types = {script_type(a) for a in ins}
    feats = {}
    for o in outs:
        f = []
        if len(in_types) == 1 and script_type(o.address) in in_types:
            f.append("same script type as inputs")
        if not is_round(o.value):
            f.append("non-round amount")
        if one_time and one_time.get(o.address):
            f.append("freshly created one-time address")
        feats[o.vout] = f
    (va, fa), (vb, fb) = sorted(feats.items(), key=lambda kv: -len(kv[1]))
    if len(fa) >= 2 and len(fa) > len(fb):
        return Change(va, round(0.4 + 0.15 * len(fa), 2), tuple(fa))
    return None


def coinjoin_reason(tx: TxRecord) -> str | None:
    """Non-None when the tx looks like a CoinJoin (Wasabi / Whirlpool / JoinMarket shapes)."""
    vals = Counter(o.value for o in tx.outputs if o.address)
    if not vals:
        return None
    v, k = vals.most_common(1)[0]
    n_in = len(tx.input_addresses)
    if len(tx.inputs) == 5 and len(tx.outputs) == 5 and k == 5:
        return f"Whirlpool shape: 5 inputs -> 5 equal outputs of {v} sats"
    # ponytail: equal-output count; Wasabi 2.0's mixed denominations can slip under k=3
    if k >= 3 and n_in >= k:
        return f"{k} equal outputs of {v} sats from {n_in} distinct input addresses"
    return None


def _selfcheck():
    from app.providers.base import TxIn, TxOut

    def tx(ins, outs):
        return TxRecord("t", "btc", 1, 1, tuple(TxIn(a, v, None, None) for a, v in ins),
                        tuple(TxOut(a, v, i) for i, (a, v) in enumerate(outs)))
    a, b, c = "1AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", "1BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB", "3CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC"
    assert detect_change(tx([(a, 10**8)], [(c, 5 * 10**7), (a, 49_990_000)])).vout == 1          # reuse
    assert detect_change(tx([(a, 10**8)], [(c, 5 * 10**7), (b, 49_987_654)])).vout == 1          # type + non-round
    assert detect_change(tx([(a, 10**8)], [(b, 12_345_678), (c, 87_000_000)])).vout == 0         # same, reversed
    assert detect_change(tx([(a, 10**8)], [(b, 12_345_678), (b.replace("B", "D"), 87_654_321)])) is None  # tie
    assert detect_change(tx([(a, 10**8)], [(c, 10**8)])) is None                                 # sweep
    mix = tx([(f"1{i}" * 17, 10**7 + i) for i in range(6)], [(f"3{i}" * 17, 10**7) for i in range(6)])
    assert coinjoin_reason(mix)
    assert coinjoin_reason(tx([(a, 10**8), (b, 10**8)], [(c, 10**8), (a, 99_990_000)])) is None  # consolidation-ish
    assert coinjoin_reason(tx([(a, 10**9)], [(b, 10**7), (c, 10**7), (b + "x", 10**7)])) is None   # 1-input batch


if __name__ == "__main__":
    _selfcheck()
    print("change/coinjoin selfcheck PASS")
