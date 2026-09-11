"""Day-3 real-data PASS/FAIL: the label & entity subsystem as a Postgres system of record (§6, §7.6, §12).

    docker compose up -d postgres      # then, from the repo root:
    backend/.venv/bin/python scripts/day3_labels_check.py
"""
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from day1_chain import Chain  # noqa: E402  (puts backend/ on sys.path)

from app.db import connect  # noqa: E402
from app.labels.ingest import build_registry, label_set_version, persist  # noqa: E402
from app.labels.propagate import propagate, same_owner_edges  # noqa: E402
from app.labels.registry import DEPOSIT, DEX, HOT, MIXER, SANCTIONED, PgRegistry  # noqa: E402
from app.labels.sweep import btc_sweep_proof  # noqa: E402
from app.providers.esplora import EsploraProvider  # noqa: E402

# (chain, address, expected role, expected entity) — every one a real, sourced label
SAMPLE = [
    ("btc", "1NDyJtNTjmwk5xPNhjgAMu4HDHigtobu1s", HOT, "binance"),            # TagPacks binance.yaml
    ("btc", "3DNsaQnaUz7wkQny1ZDSmtz6QfbEShxoDD", SANCTIONED, None),          # OFAC Polyanin
    ("eth", "0x27fD43BABfbe83a81d14665b1a6fB8030A60C9b4", HOT, "wazirx"),     # curated, WazirX report
    ("polygon", "0x27fd43babfbe83a81d14665b1a6fb8030a60c9b4", HOT, "wazirx"),  # same key on every EVM chain
    ("eth", "0x1Db92e2EeBC8E0c075a02BeA49a2935BcD2dFCF4", HOT, "bybit"),      # curated, NCC Group
    ("eth", "0x910Cbd523D972eb0a6f4cAe4618aD62622b39DbF", MIXER, "tornado"),  # tornado_cash.yaml (§9.3 boundary)
    ("eth", "0x7a250d5630b4cf539739df2c5dacb4c659f2488d", DEX, "uniswap"),    # defi-protocols-csh.yaml (§9.3)
]
CURATED_VALID_UNTIL = {"0x27fD43BABfbe83a81d14665b1a6fB8030A60C9b4": 1721283540,   # 2024-07-18 06:19 UTC
                       "0x1Db92e2EeBC8E0c075a02BeA49a2935BcD2dFCF4": 1740147371}   # 2025-02-21 14:16 UTC
DEP, SWEEP_TX, DEPOSIT_TX = ("1KNvwHuZ1wmdyzJHnbPDkdgnFXbAxUGvHP",
                             "0d813f9f09210133e9e96b9ad373b3544af27e502a7279790dfc7a608f04df31",
                             "e995334f2e7b81e05e222ff0fc033bd079c49be6b3f022ab127f5e4cda637b82")
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  {detail}")


def main():
    t0 = time.time()
    mem = build_registry()
    n_mem = sum(map(len, mem.labels.values()))
    v1, _ = persist(mem)
    v2 = label_set_version(build_registry())
    check("label-set version is content-deterministic (two independent builds)", v1 == v2, v1)
    v3, created = persist(mem)
    with connect() as c:
        n_db = c.execute("SELECT count(*) FROM address_label WHERE label_set_version = %s", (v1,)).fetchone()[0]
        n_sets = c.execute("SELECT count(*) FROM label_set WHERE version = %s", (v1,)).fetchone()[0]
    check("persist is idempotent (re-ingest writes nothing)", v3 == v1 and not created and n_sets == 1,
          f"created={created}")
    check("Postgres holds every label of the version", n_db == n_mem, f"{n_db} rows == {n_mem} in memory")

    pg = PgRegistry(v1)
    for chain, addr, role, ent in SAMPLE:
        got = [l for l in pg.lookup(chain, addr) if l.role == role]
        check(f"lookup {chain}:{addr[:12]}… -> {role}:{ent or '*'}", got and (ent is None or got[0].entity == ent),
              got and f"{got[0].entity} ({got[0].source}, {got[0].provenance[:70]})")
    rnd = random.Random(26182)
    keys = rnd.sample(sorted(mem.labels), 500)
    same = all([(l.entity, l.role, l.source, l.basis, l.confidence) for l in mem.lookup(*k)] ==
               [(l.entity, l.role, l.source, l.basis, l.confidence) for l in pg.lookup(*k)] for k in keys)
    t = time.time()
    for k in keys:
        PgRegistry.lookup(pg, *k)
    check("PgRegistry == in-memory registry on 500 random labeled keys (same order)", same,
          f"{(time.time() - t) / 500 * 1e3:.2f} ms/lookup cached")

    # ---- entity resolution + SAHYOG ----
    sah = {e.id: e for e in pg.entities.values() if e.sahyog == "confirmed"}
    check("SAHYOG-confirmed entities pinned in the version", set(sah) == {"wazirx", "kucoin", "bybit", "bitget"}
          and sah["wazirx"].jurisdiction == ["IN"], f"{sorted(sah)}")
    check("entity resolution across name variants",
          pg.resolve("Binance 14") == pg.resolve("BINANCE") == "binance" and pg.resolve("Wazir X") == "wazirx"
          and pg.resolve("GARANTEX EUROPE OU") == "ofac-garantexeuropeou", "")
    n_sah = {e: sum(1 for k in mem.labels for l in mem.labels[k] if l.entity == e and l.chain in ("btc", "eth"))
             for e in sorted(sah)}
    print(f"       SAHYOG label coverage (btc+eth, all sources): {n_sah}")

    # ---- curated addresses are real, and were exchange infra before their documented compromise ----
    ch = Chain()
    for addr, until in CURATED_VALID_UNTIL.items():
        first = ch.es("eth", module="account", action="txlist", address=addr, page=1, offset=1, sort="asc")
        ok = first and int(first[0]["timeStamp"]) < until
        check(f"curated {addr[:12]}… active on-chain before its validity cutoff",
              ok, first and f"first tx {first[0]['hash'][:14]}… ts {first[0]['timeStamp']} < {until}")

    # ---- propagation over SAME_OWNER through the pinned registry; overlay never leaks ----
    p = EsploraProvider()
    sw, dep = p.get_tx(SWEEP_TX), p.get_tx(DEPOSIT_TX)
    lab, ev = btc_sweep_proof(p, pg, DEP, [sw], until_block=sw.block, deposit_tx=dep)
    new = propagate(pg, "btc", same_owner_edges(sw))
    check("sweep + propagation work on PgRegistry (real 34-input Binance consolidation)",
          lab and lab.entity == "binance" and len({l.address for l in new if l.role == DEPOSIT}) == 27,
          f"{ev.get('distinct_senders')} senders, {len(new)} propagated labels")
    fresh = PgRegistry(v1)
    with connect() as c:
        n_after = c.execute("SELECT count(*) FROM address_label WHERE label_set_version = %s", (v1,)).fetchone()[0]
    check("trace-derived labels stay trace-local (pinned set unchanged)",
          fresh.best("btc", DEP, (DEPOSIT,)) is None and n_after == n_db, f"{n_after} rows")

    print(f"\n{sum(results)}/{len(results)} checks ok · {ch.calls + p.calls} API calls · {time.time() - t0:.1f}s · "
          f"label set {v1}")
    print("OVERALL: PASS" if all(results) else "OVERALL: FAIL")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
