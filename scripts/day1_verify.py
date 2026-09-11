"""Day-1 Case A verification: does an unknown wallet reach a documented / sweep-provable VASP
endpoint on free-tier historical data?

    backend/.venv/bin/python scripts/day1_verify.py <wallet> <btc|eth|polygon> [--since-block N]
        [--max-hops 4] [--fanout 5] [--expect ATTRIBUTED]
        [--record controlled|public --source-doc URL]

Forward BFS, level by level; stops at the first hop level that reaches any labeled endpoint.
Labels (all local lookups, zero API calls):
  1. labels/ground_truth_deposits.yaml — deposit addresses (deposit) + their sweep destinations (hot)
  2. labels/ofac_sdn_crypto.csv        — sanctioned addresses; entities in OFAC_VASPS are exchanges
  3. vendor/graphsense-tagpacks exchange-wallets-*.yaml + binance.yaml — exchange-published
     reserve/hot wallets. BitMEX 3BMEX…/bc1qmex… addresses are per-user DEPOSIT addresses
     (BitMEX's own PoR file + BitMEX docs), so they load as deposit, not hot.
Sweep-proven (§6.2c, day-1 form): an expanded address whose outflow value (per asset) goes
>= SWEEP_SHARE to hot/VASP-infra addresses of one entity X is treated as a deposit address of X.
A partial payment into X's infra is not enough — that's a customer paying X. (Exchange
consolidations can piggy-back a small withdrawal output, hence a share, not "all".) (Day 3 adds the "many distinct senders" test.)

Results (§6.3 taxonomy): ATTRIBUTED (deposit: ground_truth | sweep_proven) · ATTRIBUTED_INFRA
(reached labeled VASP infra — downgraded claim) · UNATTRIBUTED (hop/call budget exhausted).
OFAC non-VASP addresses are not endpoints: they're recorded as flags and traced through.
"""
import argparse
import csv
import json
import re
import sys
import time
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from day1_chain import REPO, Chain  # noqa: E402

HARD_MAX_HOPS, MAX_CALLS = 5, 200
# OFAC parties Treasury itself describes as virtual-currency exchanges / OTC desks in the
# designation press releases (SUEX jy0364, Chatex jy0471, Garantex jy0701, Cryptex jy2623,
# Grinex sb0225, Zedcex 2026-01-30). Curated judgment, not derivable from the SDN data.
OFAC_VASPS = {"GARANTEX EUROPE OU", "Grinex", "CHATEX", "SUEX OTC, S.R.O.", "Cryptex",
              "Zedcex Exchange Ltd"}
RANK = {"ATTRIBUTED": 2, "ATTRIBUTED_INFRA": 1}
SWEEP_SHARE = 0.9
BITMEX_DOC = "https://blog.bitmex.com/reissuing-btc-wallet-addresses/"


def key(addr: str) -> str:
    return addr.lower()  # EVM case-insensitive; base58 collisions under lower() are negligible


def load_labels(chain: str):
    """-> (deposit, infra, sanctioned): dicts of address-key -> label info."""
    evm = chain != "btc"
    same_chain = (lambda c, a: a.startswith("0x")) if evm else (lambda c, a: c == "btc")
    deposit, infra, sanctioned = {}, {}, {}
    gt_path = REPO / "labels" / "ground_truth_deposits.yaml"
    for e in (yaml.safe_load(gt_path.read_text()) or []) if gt_path.exists() else []:
        if e["chain"] == chain:
            src = f"ground_truth_manufacture {e['date']} (tx {e['our_deposit_tx']})"
            deposit[key(e["deposit_address"])] = {"entity": e["exchange"], "role": "deposit",
                                                 "role_basis": e["role_basis"], "source": src}
            if e.get("sweep_destination"):
                infra[key(e["sweep_destination"])] = {"entity": e["exchange"], "role": "hot",
                                                     "source": src + f" sweep {e['sweep_tx']}"}
    for r in csv.DictReader(open(REPO / "labels" / "ofac_sdn_crypto.csv")):
        if same_chain(r["chain"], r["address"]):
            lab = {"entity": r["entity_name"], "category": r["category"],
                   "source": f"{r['source']}; listed {r['date_added']}"}
            if r["entity_name"] in OFAC_VASPS:
                infra[key(r["address"])] = {**lab, "role": "vasp_infra(ofac)"}
            else:
                sanctioned[key(r["address"])] = {**lab, "role": "sanctioned"}
    cur = "ETH" if evm else "BTC"
    packs = REPO / "vendor" / "graphsense-tagpacks" / "packs"
    if not packs.is_dir():
        sys.exit("missing TagPacks: git clone --depth 1 https://github.com/graphsense/graphsense-tagpacks vendor/graphsense-tagpacks")
    for p in sorted(packs.glob("exchange-wallets-*.yaml")) + [packs / "binance.yaml"]:
        text = p.read_text()
        actor = re.search(r"^actor: *(\S+)", text, re.M).group(1)
        source = re.search(r"^source: *(\S+)", text, re.M).group(1)
        pack_cur = (re.search(r"^currency: *(\S+)", text, re.M) or [None, None])[1]
        # ponytail: regex over the pack instead of yaml.load — 336k BitMEX tags parse in <1s
        for addr, c in re.findall(r"^- address: '?([^'\s]+)'?(?:[ \t]*\n[ \t]+currency: (\S+))?", text, re.M):
            if not ((c or pack_cur) == cur or (evm and addr.startswith("0x"))):
                continue
            if actor == "bitmex" and addr.startswith(("3BMEX", "bc1qmex")):
                deposit.setdefault(key(addr), {"entity": actor, "role": "deposit",
                                               "role_basis": "exchange_published_deposit",
                                               "source": f"graphsense-tagpacks/{p.name} <- {source}; "
                                                         f"prefix = BitMEX per-user deposit scheme ({BITMEX_DOC})"})
            else:
                infra.setdefault(key(addr), {"entity": actor, "role": "hot(exchange-published)",
                                             "source": f"graphsense-tagpacks/{p.name} <- {source}"})
    return deposit, infra, sanctioned


def expand(ch: Chain, chain: str, node: dict) -> list[dict]:
    """Outgoing movements from node -> [{to, value, asset, hash, ts, block, out}]."""
    if chain != "btc":
        moves = ch.evm_outgoing(chain, node["addr"], node["since"])
        if node.get("asset"):  # follow the asset that arrived (no DEX-swap following on day 1)
            moves = [m for m in moves if m["asset"] == node["asset"]]
        return [{**m, "out": None} for m in moves]
    # BTC: follow the exact UTXOs that brought funds here (Tx hypernode walk), or for the
    # start wallet, every tx it spent in at/after --since-block.
    if node.get("utxos"):
        spends = []
        for txid, vout in node["utxos"]:
            osp = ch.btc(f"/tx/{txid}/outspend/{vout}")
            if osp.get("spent") and osp.get("status", {}).get("confirmed"):
                spends.append(ch.btc(f"/tx/{osp['txid']}"))
    else:
        spends = ch.btc_spends(node["addr"], node["since"])
    out = []
    for t in spends:
        ins = {(v.get("prevout") or {}).get("scriptpubkey_address") for v in t["vin"]}
        for i, o in enumerate(t["vout"]):
            a = o.get("scriptpubkey_address")
            if a and a not in ins:  # ponytail: naive change rule (output back to an input address); §7.5 day 2
                out.append({"to": a, "value": o["value"] / 1e8, "asset": "BTC", "hash": t["txid"],
                            "ts": t["status"]["block_time"], "block": t["status"]["block_height"],
                            "out": (t["txid"], i), "from": node["addr"]})
    return out


def sweep_of(moves, infra):
    """The move that shows `moves` sweeping >= SWEEP_SHARE of an asset's value to one entity's infra."""
    for asset in {m["asset"] for m in moves}:
        ms = [m for m in moves if m["asset"] == asset]
        total = sum(m["value"] for m in ms)
        to_ent: dict[str, float] = {}
        for m in ms:
            if key(m["to"]) in infra:
                e = infra[key(m["to"])]["entity"]
                to_ent[e] = to_ent.get(e, 0) + m["value"]
        for e, v in to_ent.items():
            if total and v / total >= SWEEP_SHARE:
                return max((m for m in ms if key(m["to"]) in infra and infra[key(m["to"])]["entity"] == e),
                           key=lambda m: m["value"])
    return None


def trace(wallet, chain, since_block=0, max_hops=4, fanout=5):
    t0 = time.time()
    deposit, infra, sanctioned = load_labels(chain)
    print(f"labels: {len(deposit)} deposit · {len(infra)} VASP-infra/hot · {len(sanctioned)} sanctioned "
          f"({time.time() - t0:.1f}s)")
    ch = Chain()
    start = {"addr": wallet, "hop": 0, "since": since_block, "parent": None, "via": None}
    nodes = {key(wallet): start}
    hits, flags = [], []

    def hit(node, result, lab, basis, hops):
        hits.append({"result": result, "endpoint": node["addr"], "hops": hops, "role_basis": basis,
                     **{k: lab[k] for k in ("entity", "role", "source") if k in lab}, "node": node})

    if key(wallet) in deposit:  # start is itself a known deposit address
        d = deposit[key(wallet)]
        hit(start, "ATTRIBUTED", d, d["role_basis"], 0)

    frontier, reason = ([] if hits else [start]), "budget"
    for hop in range(1, min(max_hops, HARD_MAX_HOPS) + 1):
        nxt = []
        print(f"hop {hop}: expanding {len(frontier)} address(es)  [calls so far {ch.calls}]")
        for node in sorted(frontier, key=lambda n: n["addr"]):
            if ch.calls >= MAX_CALLS:
                break
            try:
                moves = expand(ch, chain, node)
            except Exception as e:  # noqa: BLE001 — record and keep tracing other branches
                print(f"   ! expand {node['addr']}: {e}")
                continue
            # Sweep check (§6.2c): every outflow lands on hot/VASP-infra of ONE entity -> deposit address.
            sweep = None if node is start else sweep_of(moves, infra)
            if sweep:
                lab = infra[key(sweep["to"])]
                hit(node, "ATTRIBUTED", {**lab, "role": "deposit",
                                         "source": f"sweeps to {sweep['to']} in tx {sweep['hash']}; "
                                                   f"that address: {lab['role']} <- {lab['source']}"},
                    "sweep_proven", node["hop"])
                continue
            # Deterministic truncation (§9.1): value desc -> ts -> hash, top `fanout` per asset.
            by_asset: dict[str, list] = {}
            for m in sorted(moves, key=lambda m: (-m["value"], m["ts"], m["hash"])):
                by_asset.setdefault(m["asset"], []).append(m)
            for m in [m for ms in by_asset.values() for m in ms[:fanout]]:
                k = key(m["to"])
                child = {"addr": m["to"], "hop": hop, "since": m["block"], "parent": node, "via": m,
                         "asset": m["asset"] if chain != "btc" else None,
                         "utxos": [m["out"]] if m["out"] else None}
                if k in deposit:
                    hit(child, "ATTRIBUTED", deposit[k], deposit[k]["role_basis"], hop)
                elif k in infra:
                    hit(child, "ATTRIBUTED_INFRA", infra[k], "labeled_infra", hop)
                elif k in nodes:
                    if m["out"] and nodes[k]["hop"] == hop:  # same-level UTXO merge
                        nodes[k]["utxos"].append(m["out"])
                    continue
                else:
                    if k in sanctioned:
                        flags.append(f"ofac:{sanctioned[k]['entity']}@{m['to']}(hop {hop})")
                    nodes[k] = child
                    nxt.append(child)
        if hits:
            reason = "hit"
            break
        if ch.calls >= MAX_CALLS:
            reason = f"api-call budget ({MAX_CALLS}) exhausted"
            break
        if not nxt:
            reason = "no further outgoing value"
            break
        frontier = nxt
    else:
        reason = f"hop budget ({max_hops}) exhausted"

    wall = round(time.time() - t0, 1)
    if not hits:
        return {"wallet": wallet, "chain": chain, "result": "UNATTRIBUTED", "hops": None,
                "api_calls": ch.calls, "wall_clock_s": wall, "endpoint": None, "role_basis": None,
                "reason": reason, "addresses_seen": len(nodes), "flags": flags}
    best = sorted(hits, key=lambda h: (-RANK[h["result"]], h["hops"], h["endpoint"]))[0]
    path, n = [], best["node"]
    while n["via"]:
        v = n["via"]
        path.append({"from": v["from"], "to": v["to"], "tx": v["hash"], "value": v["value"],
                     "asset": v["asset"], "ts": v["ts"]})
        n = n["parent"]
    return {"wallet": wallet, "chain": chain, "result": best["result"], "hops": best["hops"],
            "api_calls": ch.calls, "wall_clock_s": wall, "endpoint": best["endpoint"],
            "entity": best["entity"], "role": best["role"], "role_basis": best["role_basis"],
            "label_source": best["source"], "path": path[::-1], "addresses_seen": len(nodes), "flags": flags,
            "other_hits": [{k: h[k] for k in ("result", "endpoint", "entity", "hops", "role_basis")}
                           for h in hits if h is not best][:10]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("wallet")
    ap.add_argument("chain", choices=["btc", "eth", "polygon"])
    ap.add_argument("--since-block", type=int, default=0)
    ap.add_argument("--max-hops", type=int, default=4)
    ap.add_argument("--fanout", type=int, default=5)
    ap.add_argument("--expect", choices=["ATTRIBUTED", "ATTRIBUTED_INFRA", "UNATTRIBUTED"])
    ap.add_argument("--record", choices=["controlled_case", "public_case"])
    ap.add_argument("--source-doc", default="")
    a = ap.parse_args()
    if a.max_hops > HARD_MAX_HOPS:
        sys.exit(f"--max-hops capped at {HARD_MAX_HOPS}")

    r = trace(a.wallet, a.chain, a.since_block, a.max_hops, a.fanout)
    print(json.dumps(r, indent=2, default=str))
    print(f"\n{r['result']}: " + (f"{r['entity']} via {r['endpoint']} ({r['role_basis']}) in {r['hops']} hop(s)"
                                 if r["endpoint"] else f"no labeled endpoint — {r['reason']}")
          + f" · {r['api_calls']} API calls · {r['wall_clock_s']}s")
    if a.record:
        out = REPO / "scripts" / "day1_results.json"
        allr = json.loads(out.read_text()) if out.exists() else {}
        allr[a.record] = {**r, **({"source_doc": a.source_doc} if a.source_doc else {})}
        out.write_text(json.dumps(allr, indent=2, default=str) + "\n")
        print(f"recorded -> {out} [{a.record}]")
    ok = a.expect is None or r["result"] == a.expect
    print("PASS" if ok else f"FAIL (expected {a.expect}, got {r['result']})")
    sys.exit(0 if ok else 1)


def _selfcheck():
    infra = {"hot": {"entity": "X"}, "hot2": {"entity": "Y"}}
    mv = lambda to, v: {"to": to, "value": v, "asset": "BTC"}  # noqa: E731
    assert sweep_of([mv("hot", 78.9), mv("other", 0.01)], infra)["to"] == "hot"   # consolidation + side output
    assert sweep_of([mv("hot", 1.6), mv("other", 0.35)], infra) is None            # customer paying X
    assert sweep_of([mv("hot", 5), mv("hot2", 5)], infra) is None                  # split across entities


if __name__ == "__main__":
    _selfcheck()
    main()
