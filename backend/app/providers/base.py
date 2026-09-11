"""Provider abstraction (architecture §8). A new chain is a new provider file, not a new engine.

Deviation from the §8 sketch: methods are sync, not async. Workers are Celery (sync) and
parallelism comes from chord fan-out (§9.2), so async here would only add run()/to_thread glue.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


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
    index: int | None = None          # vin / vout / log_index — part of the MERGE key (§7.2)
    meta: dict = field(default_factory=dict, compare=False, hash=False)


class BlockchainProvider(ABC):
    chain: str

    @abstractmethod
    def get_outgoing(self, address: str, until_block: int, since_block: int = 0) -> list[Edge]:
        """Every outgoing movement from address with since_block <= block <= until_block."""

    @abstractmethod
    def get_tx(self, tx_hash: str) -> TxRecord:
        """Full transaction (UTXO: every input and output)."""

    @abstractmethod
    def get_neighbors(self, address: str, until_block: int, since_block: int = 0) -> list[str]:
        """Addresses that received value from address in the window."""
