"""VASP entity registry + address labels + entity resolution (§6, §7.6 vasp / address_label).

Registry is the in-memory form ingest.py builds from labels/* and the pinned TagPacks clone;
ingest.persist() writes it to Postgres as one immutable, content-hashed label-set version.
PgRegistry reads a pinned version back (what traces use): entities are loaded whole, address
labels are looked up on demand, and labels a trace derives (sweep-proven, cluster-propagated) live
in an in-memory overlay — they never leak into the pinned set, so a case's result can't depend on
which cases ran before it (§12).
"""
import re
from dataclasses import dataclass, field

# Source tiers (brief §15 / architecture §11.1 source_tier): authoritative > curated > crawled > inferred.
SOURCE_TIER = {"ground_truth": 1.0, "ofac": 1.0, "curated": 0.8, "tagpacks": 0.6, "sweep": 0.5,
               "heuristic": 0.3}
DEPOSIT, HOT, SANCTIONED = "deposit", "hot", "sanctioned"   # §6.3 roles (hot = any VASP infra)
MIXER, DEX, BRIDGE = "mixer", "dex", "bridge"                 # §9.3 service-node boundaries
# A token/LP CONTRACT, not a wallet. Its own role, because TagPacks tags plenty of them as exchange
# infrastructure ("Bitfinex LEO Token", "BitgetToken (BGB)") — and a token contract can never be a
# sweep target (§6.2c), so it must never carry HOT.
TOKEN = "token"


def addr_key(chain: str, address: str) -> str:
    # EVM hex + bech32 are case-insensitive; base58 (BTC 1…/3…, Tron T…) is case-sensitive, kept verbatim.
    return address.lower() if address[:2].lower() == "0x" or address[:3].lower() == "bc1" else address


# Shape only — these say "this string could be an address on this chain", never "this address
# exists". Checked at the API boundary so a typo is a 400 with a reason instead of a trace that
# burns provider calls to discover the address was never real (§15 input validation at the edge).
_BTC = re.compile(r"^(?:[13][a-km-zA-HJ-NP-Z1-9]{25,34}|bc1[a-z0-9]{11,71})$")
_EVM = re.compile(r"^0x[0-9a-fA-F]{40}$")
_TRON = re.compile(r"^T[1-9A-HJ-NP-Za-km-z]{33}$")


def valid_address(chain: str, address: str) -> bool:
    return bool({"btc": _BTC, "tron": _TRON}.get(chain, _EVM).match(address or ""))


def norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


@dataclass
class Label:
    address: str
    chain: str
    role: str              # deposit | hot | sanctioned
    entity: str            # canonical entity id
    source: str            # tier key in SOURCE_TIER
    confidence: float
    basis: str             # labeled | exchange_published_deposit | sweep_proven | ground_truth | cluster_propagated | …
    provenance: str        # human-readable: file/URL/tx that justifies it


@dataclass
class Entity:
    id: str
    name: str
    type: str = "unknown"          # exchange | sanctioned_party | …
    jurisdiction: list[str] = field(default_factory=list)
    aliases: set[str] = field(default_factory=set)
    sahyog: str = "unknown"        # confirmed | unknown


class Registry:
    def __init__(self):
        self.entities: dict[str, Entity] = {}
        self._alias: dict[str, str] = {}
        self.labels: dict[tuple[str, str], list[Label]] = {}

    # ---- entities ----
    def add_entity(self, id: str, name: str, **kw) -> Entity:
        e = self.entities.get(id)
        if e is None:
            e = self.entities[id] = Entity(id, name)
        for k, v in kw.items():
            if v not in (None, "", [], set()):
                setattr(e, k, v)
        for a in {id, name, *e.aliases}:
            self._alias.setdefault(norm(a), id)
        return e

    def resolve(self, name: str) -> str | None:
        """Free-text entity name -> canonical id ('Binance 14', 'BINANCE' -> 'binance')."""
        n = norm(name)
        if n in self._alias:
            return self._alias[n]
        stripped = re.sub(r"\d+$", "", n)  # numbered wallet names: "binance14", "kraken4"
        return self._alias.get(stripped)

    # ---- labels ----
    def add_label(self, lab: Label) -> None:
        k = (lab.chain, addr_key(lab.chain, lab.address))
        cur = self.labels.setdefault(k, [])
        if not any((l.entity, l.role, l.source, l.basis) == (lab.entity, lab.role, lab.source, lab.basis) for l in cur):
            cur.append(lab)

    def lookup(self, chain: str, address: str) -> list[Label]:
        return sorted(self.labels.get((chain, addr_key(chain, address)), []), key=lambda l: -l.confidence)

    def best(self, chain: str, address: str, roles=(DEPOSIT, HOT)) -> Label | None:
        return next((l for l in self.lookup(chain, address) if l.role in roles), None)

    def hot_entity(self, chain: str, address: str) -> str | None:
        """Entity id if address is labeled VASP infrastructure (the sweep target of §6.2c)."""
        lab = self.best(chain, address, roles=(HOT,))
        return lab.entity if lab else None

    def stats(self) -> dict:
        out: dict[str, int] = {}
        for labs in self.labels.values():
            for l in labs:
                out[f"{l.chain}:{l.role}:{l.source}"] = out.get(f"{l.chain}:{l.role}:{l.source}", 0) + 1
        return dict(sorted(out.items()))


# Entity rows for a pinned label-set version, cached for the life of the process. A label set is
# immutable by construction (its version IS the hash of its contents), so re-reading ~4k VASP rows
# per Registry is pure waste — and it is not small waste: measured at 1.27s per construction, paid
# once per Celery task, which is what made a full-frontier trace take half an hour. Only the raw
# rows are shared; every Registry builds its own Entity objects and its own overlay, so nothing one
# trace derives can leak into another (§12).
_ENTITY_ROWS: dict[str, list[tuple]] = {}


class PgRegistry(Registry):
    """A pinned label-set version from Postgres (None = latest)."""

    def __init__(self, version: str | None = None, conn=None):
        super().__init__()   # self.labels = the trace-local overlay
        from app.db import connect
        self.conn = conn or connect(autocommit=True)
        row = self.conn.execute(
            "SELECT version, n_labels FROM label_set " + ("WHERE version = %s" if version else
                                                          "ORDER BY created_at DESC LIMIT 1"),
            (version,) if version else ()).fetchone()
        if not row:
            raise LookupError(f"label set {version or '(any)'} not in Postgres; run python -m app.labels.ingest")
        self.version, self.n_labels = row
        rows = _ENTITY_ROWS.get(self.version)
        if rows is None:
            rows = _ENTITY_ROWS[self.version] = list(self.conn.execute(
                "SELECT id, name, type, jurisdiction, aliases, sahyog FROM vasp WHERE label_set_version = %s",
                (self.version,)))
        for id, name, type, jur, aliases, sahyog in rows:
            self.add_entity(id, name, type=type, jurisdiction=jur, aliases=set(aliases), sahyog=sahyog)
        self._pinned: dict[tuple[str, str], list[Label]] = {}

    def lookup(self, chain: str, address: str) -> list[Label]:
        k = (chain, addr_key(chain, address))
        if k not in self._pinned:  # ponytail: unbounded per-trace cache; a trace touches ~10^3 addresses
            self._pinned[k] = [Label(*r) for r in self.conn.execute(
                "SELECT address, chain, role, entity_id, source, confidence, basis, provenance FROM address_label "
                "WHERE label_set_version = %s AND chain = %s AND addr_key = %s ORDER BY id", (self.version, *k))]
        return sorted(self._pinned[k] + self.labels.get(k, []), key=lambda l: -l.confidence)


def _selfcheck():
    assert valid_address("btc", "12w6v1qAaBc4W8h8C2Cu5SKFaKDSv3erUW")
    assert valid_address("btc", "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq")
    assert valid_address("eth", "0x7F367cC41522cE07553e823bf3be79A889DEbe1B")
    for chain, bad in [("btc", "not-an-address"), ("btc", "0x7F367cC41522cE07553e823bf3be79A889DEbe1B"),
                       ("btc", " 12w6v1qAaBc4W8h8C2Cu5SKFaKDSv3erUW"), ("btc", "1" * 100),
                       ("eth", "12w6v1qAaBc4W8h8C2Cu5SKFaKDSv3erUW"), ("eth", "0xdeadbeef"), ("eth", "")]:
        assert not valid_address(chain, bad), (chain, bad)
    r = Registry()
    r.add_entity("binance", "Binance", aliases={"Binance.com"})
    assert r.resolve("BINANCE") == r.resolve("Binance 14") == r.resolve("binance.com") == "binance"
    assert r.resolve("Kraken") is None
    r.add_label(Label("1Abc", "btc", HOT, "binance", "tagpacks", 0.6, "labeled", "x"))
    r.add_label(Label("1Abc", "btc", HOT, "binance", "tagpacks", 0.6, "labeled", "x"))  # dedupe
    assert len(r.lookup("btc", "1Abc")) == 1 and r.lookup("btc", "1abc") == []   # base58 case-sensitive
    r.add_label(Label("0xAB", "eth", DEPOSIT, "binance", "sweep", 0.5, "sweep_proven", "y"))
    assert r.best("eth", "0xab").role == DEPOSIT and r.hot_entity("btc", "1Abc") == "binance"


if __name__ == "__main__":
    _selfcheck()
    print("registry selfcheck PASS")
