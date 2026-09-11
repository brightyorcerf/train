"""Load every label source into a Registry (§6). Flat files only; Postgres/Neo4j writes come later.

Sources and tiers (registry.SOURCE_TIER):
  ground_truth  labels/ground_truth_deposits.yaml   our manufactured deposits (+ their sweep targets)
  ofac          labels/ofac_sdn_crypto.csv          sanctioned; the OFAC_VASPS parties are exchanges
  curated       labels/sahyog_vasps.yaml            SAHYOG status (+ any sourced curated addresses)
  tagpacks      vendor/graphsense-tagpacks          actor registry; exchange-published reserve/hot
                                                    wallets (exchange-wallets-*, binance.yaml)
"""
import csv
import re
import sys
from pathlib import Path

import yaml

from app.labels.registry import DEPOSIT, HOT, SANCTIONED, SOURCE_TIER, Label, Registry, norm

REPO = Path(__file__).resolve().parents[3]
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
        reg.add_entity(v["entity"], v["name"], type="exchange", sahyog=v["sahyog"])
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


def load_tagpacks_exchange_tags(reg: Registry, chains=("btc",) + EVM) -> None:
    """Per-tag `category: exchange` addresses from every other pack (walletexplorer, chaininfo,
    richest_addresses, hacks, interpol-real_services, …). Exchange-controlled but role unknown -> HOT
    (infra, downgraded claim). web_crawl packs drop to the heuristic tier."""
    done = {p.name for p in (PACKS / "packs").glob("exchange-wallets-*.yaml")} | {"binance.yaml"}
    for p in sorted((PACKS / "packs").glob("*.yaml")):
        if p.name in done or "category: exchange" not in (text := p.read_text()):
            continue
        pack = yaml.load(text, Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader))
        tier = "heuristic" if pack.get("confidence") == "web_crawl" else "tagpacks"
        for t in pack.get("tags") or []:
            if t.get("category", pack.get("category")) != "exchange":
                continue
            cur = t.get("currency", pack.get("currency"))
            chain = "btc" if cur == "BTC" else "eth" if cur == "ETH" else None
            eid = (t.get("actor") or pack.get("actor") or reg.resolve(t.get("label", "")))
            if chain is None or not eid:
                continue
            for c in _chains_for(str(t["address"]), chain):
                if c in chains:
                    reg.add_label(Label(str(t["address"]), c, HOT, eid, tier, SOURCE_TIER[tier], "labeled",
                                        f"graphsense-tagpacks@{TAGPACKS_COMMIT}/{p.name} '{t.get('label')}' "
                                        f"<- {t.get('source', pack.get('source'))}"))


def build_registry(chains=("btc",) + EVM) -> Registry:
    reg = Registry()
    load_actors(reg)
    load_sahyog(reg)
    load_ofac(reg)
    load_ground_truth(reg)
    load_tagpacks(reg, chains)
    load_tagpacks_exchange_tags(reg, chains)
    return reg


if __name__ == "__main__":
    import time
    t = time.time()
    r = build_registry()
    print(f"{len(r.entities)} entities, {sum(map(len, r.labels.values()))} labels in {time.time() - t:.1f}s")
    for k, v in r.stats().items():
        print(f"  {k:<32} {v}")
    for q in ("Binance 14", "WazirX", "KUCOIN", "GARANTEX EUROPE OU"):
        e = r.entities.get(r.resolve(q) or "")
        print(f"  resolve {q!r:<22} -> {e and (e.id, e.type, e.sahyog)}")
