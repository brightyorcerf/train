"""Provider data model (architecture §8) and the bookkeeping both chain adapters share.

Deviation from the §8 sketch: methods are sync, not async. Workers are Celery (sync) and
parallelism comes from chord fan-out (§9.2), so async here would only add run()/to_thread glue.
There is no abstract provider interface: the engine drives BTC and EVM through different methods
(hypernode UTXO walk vs account movements), so an ABC over them would describe nothing it calls.
"""
from collections import Counter
from dataclasses import dataclass, field

from app.core.ratelimit import open_limiter
from app.providers.store import body_hash, open_store

IMMUTABLE = "immutable"


class ProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class TxIn:
    address: str | None   # None for coinbase / non-standard scripts
    value: int            # base units (sats / wei)
    prev_txid: str | None
    prev_vout: int | None


@dataclass(frozen=True)
class TxOut:
    address: str | None
    value: int
    vout: int


@dataclass(frozen=True)
class TxRecord:
    """UTXO transaction hypernode (§7.3): the tx paid these outputs from those inputs."""
    hash: str
    chain: str
    block: int | None     # None = unconfirmed
    ts: int | None
    inputs: tuple[TxIn, ...]
    outputs: tuple[TxOut, ...]

    @property
    def input_addresses(self) -> set[str]:
        return {i.address for i in self.inputs if i.address}

    @property
    def total_in(self) -> int:
        return sum(i.value for i in self.inputs)

    @property
    def total_out(self) -> int:
        return sum(o.value for o in self.outputs)


@dataclass(frozen=True)
class Edge:
    """One value movement. Account chains: address->address (kind native|erc20|internal).
    UTXO chains: address -FUNDS-> tx and tx -CREDITS-> address, never address->address."""
    src: str
    dst: str
    kind: str             # native | erc20 | internal | funds | credits
    tx_hash: str
    value: int
    asset: str
    block: int
    ts: int
    index: int | str | None = None    # vin / vout / log_index / trace id — part of the MERGE key (§7.2)
    meta: dict = field(default_factory=dict, compare=False, hash=False)


class Provider:
    """Counters (§12 budget), response-body provenance and the §8 stale fallback, for both adapters.
    `calls` counts logical requests (the trace budget, identical live or replayed), `upstream`
    counts network requests."""

    def __init__(self, store="auto", offline=False, limiter="auto"):
        self.store = open_store() if store == "auto" else store   # None/False = uncached (drills)
        self.offline = offline            # serve only from the store (§11.2 offline fixture mode)
        self.limiter = open_limiter() if limiter == "auto" else limiter
        self.calls, self.upstream, self.store_hits = 0, 0, 0
        self.stale: list[str] = []
        self.requests: list[str] = []        # every logical read, in order (§12)
        # sha256 of each response BODY, in read order. The audit-log provenance hash is built
        # from these: hashing the request URLs proved only which questions were asked, never
        # what came back, so it could not detect changed upstream data (§12 reproduce-and-verify).
        self.body_hashes: list[str] = []
        self.calls_by = Counter()
        self.truncated: set[str] = set()   # addresses whose history exceeded the page cap

    def _seen(self, body):
        """Record the content hash of one response body (§12 provenance) and hand it back."""
        self.body_hashes.append(body_hash(body))
        return body

    def _stale(self, req: str, label: str):
        """§8: on 429 / provider down, the last stored answer under ANY scope, or None. The read is
        recorded in `stale`, which marks the result partial: it may pre-date the snapshot."""
        old = self.store.latest(req) if self.store else None
        if not old:
            return None
        body, sc, at = old
        self.stale.append(f"{label} (stored {at:%Y-%m-%d %H:%M} under {sc})")
        return self._seen(body)
