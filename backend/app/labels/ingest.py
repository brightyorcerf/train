"""Load every label source into a Registry (§6), then persist it to Postgres as one immutable,
content-hashed label-set version (§12).

    python -m app.labels.ingest        # from backend/: build + persist; prints the pinned version

Sources and tiers (registry.SOURCE_TIER):
  ground_truth  labels/ground_truth_deposits.yaml   our manufactured deposits (+ their sweep targets)
  ofac          labels/ofac_sdn_crypto.csv          sanctioned; the OFAC_VASPS parties are exchanges
  curated       labels/sahyog_vasps.yaml            SAHYOG status (+ any sourced curated addresses)
  tagpacks      vendor/graphsense-tagpacks          actor registry; exchange-published reserve/hot
                                                    wallets (exchange-wallets-*, binance.yaml); per-tag
                                                    exchange / mixer / CoinJoin / DEX categories
"""
import csv
import hashlib
import json
import os
import re
import sys
from pathlib import Path

import yaml

from app.labels.registry import (DEPOSIT, DEX, HOT, MIXER, SANCTIONED, SOURCE_TIER, TOKEN, Label, Registry,
                                 addr_key, norm)

# Containers mount labels/ and vendor/ under REPO_ROOT (compose); host scripts use the checkout.
REPO = Path(os.environ.get("REPO_ROOT") or Path(__file__).resolve().parents[3])
LABELS = REPO / "labels"
PACKS = REPO / "vendor" / "graphsense-tagpacks"
TAGPACKS_COMMIT = "7f9a5d1f"  # inspected 2026-09-11 (scripts/tagpacks_inspection.md)
EVM = ("eth", "polygon")
# OFAC parties Treasury itself describes as virtual-currency exchanges / OTC desks in the
# designation press releases (SUEX jy0364, Chatex jy0471, Garantex jy0701, Cryptex jy2623,
# Grinex sb0225, Zedcex 2026-01-30). Curated judgment, not derivable from the SDN data.
OFAC_VASPS = {"GARANTEX EUROPE OU", "Grinex", "CHATEX", "SUEX OTC, S.R.O.", "Cryptex", "Zedcex Exchange Ltd"}
BITMEX_DOC = "https://blog.bitmex.com/reissuing-btc-wallet-addresses/"


def _chains_for(address: str, chain: str) -> tuple[str, ...]:
    # One EVM key controls the same address on every EVM chain; OFAC lists it under ETH.
    return EVM if address.startswith("0x") else (chain,)


def load_actors(reg: Registry) -> None:
    path = PACKS / "actors" / "graphsense.actorpack.yaml"
    if not path.exists():
        sys.exit(f"missing TagPacks: git clone https://github.com/graphsense/graphsense-tagpacks {PACKS} "
                 f"&& git -C {PACKS} checkout {TAGPACKS_COMMIT}")
    for a in yaml.load(path.read_text(), Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader))["actors"]:
        cats = a.get("categories") or ["unknown"]
        reg.add_entity(a["id"], a.get("label") or a["id"], type="exchange" if "exchange" in cats else cats[0],
                       jurisdiction=a.get("jurisdictions") or [])


def load_sahyog(reg: Registry) -> None:
    for v in yaml.safe_load((LABELS / "sahyog_vasps.yaml").read_text()) or []:
        reg.add_entity(v["entity"], v["name"], type="exchange", sahyog=v["sahyog"],
                       jurisdiction=[v["jurisdiction"]] if v.get("jurisdiction") else [])
        for a in v.get("addresses") or []:
            for c in _chains_for(a["address"], a["chain"]):
                reg.add_label(Label(a["address"], c, a["role"], v["entity"], "curated", SOURCE_TIER["curated"],
                                    "labeled", a["source"]))


def load_ofac(reg: Registry) -> None:
    for r in csv.DictReader(open(LABELS / "ofac_sdn_crypto.csv")):
        name, vasp = r["entity_name"], r["entity_name"] in OFAC_VASPS
        eid = reg.resolve(name) or "ofac-" + norm(name)
        reg.add_entity(eid, name, type="exchange" if vasp else "sanctioned_party")
        prov = f"{r['source']}; listed {r['date_added']}; {r['category']}"
        for c in _chains_for(r["address"], r["chain"]):
            reg.add_label(Label(r["address"], c, HOT if vasp else SANCTIONED, eid, "ofac", SOURCE_TIER["ofac"],
                                "labeled", prov))


def load_ground_truth(reg: Registry) -> None:
    p = LABELS / "ground_truth_deposits.yaml"
    for e in (yaml.safe_load(p.read_text()) or []) if p.exists() else []:
        eid = reg.resolve(e["exchange"]) or norm(e["exchange"])
        reg.add_entity(eid, e["exchange"], type="exchange")
        prov = f"ground_truth_manufacture {e['date']} (our deposit tx {e['our_deposit_tx']})"
        reg.add_label(Label(e["deposit_address"], e["chain"], DEPOSIT, eid, "ground_truth", 1.0, "ground_truth", prov))
        if e.get("sweep_destination"):
            reg.add_label(Label(e["sweep_destination"], e["chain"], HOT, eid, "ground_truth", 1.0,
                                "sweep_of_our_deposit", prov + f" swept in {e['sweep_tx']}"))


def load_tagpacks(reg: Registry, chains=("btc",) + EVM) -> None:
    packs = PACKS / "packs"
    for p in sorted(packs.glob("exchange-wallets-*.yaml")) + [packs / "binance.yaml"]:
        text = p.read_text()
        actor = re.search(r"^actor: *(\S+)", text, re.M).group(1)
        source = re.search(r"^source: *(\S+)", text, re.M).group(1)
        pack_cur = (re.search(r"^currency: *(\S+)", text, re.M) or [None, None])[1]
        eid = reg.resolve(actor) or actor
        prov = f"graphsense-tagpacks@{TAGPACKS_COMMIT}/{p.name} <- {source}"
        # ponytail: regex over the pack instead of yaml.load — 336k BitMEX tags parse in <1s
        for addr, cur in re.findall(r"^- address: '?([^'\s]+)'?(?:[ \t]*\n[ \t]+currency: (\S+))?", text, re.M):
            cur = cur or pack_cur
            chain = "btc" if cur == "BTC" else "eth" if cur == "ETH" or addr.startswith("0x") else None
            if chain is None:
                continue
            for c in _chains_for(addr, chain):
                if c not in chains:
                    continue
                if actor == "bitmex" and addr.startswith(("3BMEX", "bc1qmex")):
                    # BitMEX per-user deposit scheme, from BitMEX's own PoR file + docs.
                    reg.add_label(Label(addr, c, DEPOSIT, eid, "tagpacks", SOURCE_TIER["tagpacks"],
                                        "exchange_published_deposit", f"{prov}; prefix scheme {BITMEX_DOC}"))
                else:
                    reg.add_label(Label(addr, c, HOT, eid, "tagpacks", SOURCE_TIER["tagpacks"], "labeled", prov))


# Per-tag categories from every other pack -> role. Exchange-controlled but role unknown -> HOT (infra,
# downgraded claim); mixers / CoinJoin coordinators / DEX contracts are §9.3 trace boundaries.
CATEGORY_ROLE = {"exchange": HOT, "mixing_service": MIXER, "coinjoin": MIXER, "defi_dex": DEX}


def load_tagpacks_category_tags(reg: Registry, chains=("btc",) + EVM) -> None:
    """walletexplorer, chaininfo, richest_addresses, hacks, interpol-real_services, tornado_cash,
    defi-protocols-csh, … ; web_crawl packs drop to the heuristic tier."""
    done = {p.name for p in (PACKS / "packs").glob("exchange-wallets-*.yaml")} | {"binance.yaml"}
    for p in sorted((PACKS / "packs").glob("*.yaml")):
        text = p.read_text()
        if p.name in done or not any(f"category: {c}" in text for c in CATEGORY_ROLE):
            continue
        pack = yaml.load(text, Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader))
        tier = "heuristic" if pack.get("confidence") == "web_crawl" else "tagpacks"
        for t in pack.get("tags") or []:
            cat = t.get("category", pack.get("category"))
            role = CATEGORY_ROLE.get(cat)
            cur = t.get("currency", pack.get("currency"))
            chain = "btc" if cur == "BTC" else "eth" if cur == "ETH" else None
            name = t.get("label") or pack.get("label") or ""
            eid = t.get("actor") or pack.get("actor") or reg.resolve(name) or (role != HOT and norm(name))
            if role is None or chain is None or not eid:
                continue
            if eid not in reg.entities:
                reg.add_entity(eid, name or eid, type=cat)
            for c in _chains_for(str(t["address"]), chain):
                if c in chains:
                    reg.add_label(Label(str(t["address"]), c, role, eid, tier, SOURCE_TIER[tier], "labeled",
                                        f"graphsense-tagpacks@{TAGPACKS_COMMIT}/{p.name} '{t.get('label')}' "
                                        f"<- {t.get('source', pack.get('source'))}"))


def load_bridges(reg: Registry) -> None:
    """Hand-curated cross-chain bridges (§9.3). Pinned to the chain they were curated on — an EVM
    address is the same key everywhere, but the Polygon PoS bridge on Polygon is not that bridge."""
    from app.boundary.bridge import entities as bridge_names  # noqa: PLC0415
    from app.boundary.bridge import labels as bridge_labels  # noqa: PLC0415
    names = bridge_names()
    for l in bridge_labels():
        reg.add_entity(l.entity, names.get(l.entity, l.entity), type="bridge")
        reg.add_label(l)


def load_polygon_wallets(reg: Registry) -> None:
    """Polygon-specific exchange wallets — the EVM mirror does not cover them (see the file header)."""
    p = LABELS / "polygon_exchange_wallets.yaml"
    for v in (yaml.safe_load(p.read_text()) or []) if p.exists() else []:
        reg.add_entity(v["entity"], v["name"], type="exchange")
        tier = v.get("tier", "heuristic")
        for a in v.get("addresses") or []:
            prov = f"{a['source']} ({a.get('tag', '')}); corroborated on-chain: {' '.join(a.get('verified', '').split())}"
            reg.add_label(Label(a["address"], a["chain"], a["role"], v["entity"], tier, SOURCE_TIER[tier],
                                "labeled", prov))


def suppress_token_contracts(reg: Registry) -> int:
    """A token contract can never be a sweep target, a deposit address, or a service boundary (§6.2c).

    TagPacks tags token contracts with their issuer's name ("BitgetToken (BGB)", "KuCoin Token (KCS)",
    "Binance: BNB Token"), and that became a `hot` label — i.e. a legitimate sweep target. Any address
    that sent tokens there would then be "proven" that exchange's deposit address. Runs LAST so it can
    strip labels every other source added; the addresses were confirmed by on-chain evidence, not by
    their names (labels/token_contracts.yaml)."""
    p = LABELS / "token_contracts.yaml"
    n = 0
    for t in (yaml.safe_load(p.read_text()) or []) if p.exists() else []:
        for c in _chains_for(t["address"], t.get("chain", "eth")):
            k = (c, addr_key(c, t["address"]))
            kept = [l for l in reg.labels.get(k, []) if l.role not in (HOT, DEPOSIT, DEX)]
            n += len(reg.labels.get(k, [])) - len(kept)
            reg.labels[k] = kept
            reg.add_label(Label(t["address"], c, TOKEN, t.get("tagged_entity") or "unknown", "curated",
                                SOURCE_TIER["curated"], "labeled",
                                f"token contract (on-chain verified 2026-09-12), tagged {t.get('tag')!r} "
                                f"<- labels/token_contracts.yaml"))
    return n


def build_registry(chains=("btc",) + EVM) -> Registry:
    reg = Registry()
    load_actors(reg)
    load_sahyog(reg)
    load_ofac(reg)
    load_ground_truth(reg)
    load_tagpacks(reg, chains)
    load_tagpacks_category_tags(reg, chains)
    load_bridges(reg)
    load_polygon_wallets(reg)
    suppress_token_contracts(reg)   # last: it strips what the sources above got wrong
    return reg


def _rows(reg: Registry):
    labels = [(l.chain, l.address, addr_key(l.chain, l.address), l.role, l.entity, l.source, l.confidence,
               l.basis, l.provenance) for labs in reg.labels.values() for l in labs]
    ents = [(e.id, e.name, e.type, json.dumps(sorted(e.jurisdiction)), json.dumps(sorted(e.aliases)), e.sahyog)
            for e in reg.entities.values()]
    return labels, ents


def label_set_version(reg: Registry) -> str:
    """Same label content -> same version, whatever the build order (canonical sorted rows)."""
    labels, ents = _rows(reg)
    h = hashlib.sha256()
    for r in sorted(ents) + sorted(labels):
        h.update(json.dumps(r, separators=(",", ":")).encode() + b"\n")
    return "ls-" + h.hexdigest()[:12]


def _file_hashes() -> dict:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()[:12] for p in sorted(LABELS.glob("*")) if p.is_file()}


def persist(reg: Registry, conn=None) -> tuple[str, bool]:
    """Write reg as a label-set version. Idempotent: an existing version is left untouched.
    -> (version, created)."""
    from app.db import connect, init_schema
    init_schema()
    version = label_set_version(reg)
    labels, ents = _rows(reg)
    with conn or connect() as c:
        if c.execute("SELECT 1 FROM label_set WHERE version = %s", (version,)).fetchone():
            return version, False
        c.execute("INSERT INTO label_set (version, n_labels, n_entities, sources) VALUES (%s, %s, %s, %s)",
                  (version, len(labels), len(ents),
                   json.dumps({"tagpacks_commit": TAGPACKS_COMMIT, "labels_dir": _file_hashes()})))
        with c.cursor().copy("COPY vasp (label_set_version, id, name, type, jurisdiction, aliases, sahyog) "
                             "FROM STDIN") as cp:
            for r in ents:
                cp.write_row((version, *r))
        with c.cursor().copy("COPY address_label (label_set_version, chain, address, addr_key, role, entity_id, "
                             "source, confidence, basis, provenance) FROM STDIN") as cp:
            for r in labels:   # registry insertion order -> ascending ids -> deterministic tie order
                cp.write_row((version, *r))
    return version, True


if __name__ == "__main__":
    import time
    t = time.time()
    r = build_registry()
    print(f"{len(r.entities)} entities, {sum(map(len, r.labels.values()))} labels in {time.time() - t:.1f}s")
    v, created = persist(r)
    print(f"label set {v}: {'persisted' if created else 'already in Postgres (unchanged content)'} "
          f"({time.time() - t:.1f}s)")
    for k, v in r.stats().items():
        print(f"  {k:<32} {v}")
    for q in ("Binance 14", "WazirX", "KUCOIN", "GARANTEX EUROPE OU"):
        e = r.entities.get(r.resolve(q) or "")
        print(f"  resolve {q!r:<22} -> {e and (e.id, e.type, e.sahyog)}")
