"""Screen OFAC-sanctioned Tron addresses for golden-set discovery cases (§11.2): same method as
golden_discovery_btc.py, USDT (TRC-20) only.

Budget: TronGrid keyless is paced at 1.5 req/s. Screening costs 1 call per address (first page of
USDT history), tracing up to ~200. Repeat runs are served from the §12 raw store.

    docker compose run --rm -e PYTHONPATH=/app -v "$PWD/backend/app:/app/app" -v "$PWD/scripts:/repo/scripts" \
      api python /repo/scripts/golden_discovery_tron.py [--screen 80] [--limit 15]
"""
import argparse
import json
from pathlib import Path

from app.db import connect
from app.providers.tron import TronGridProvider
from app.trace.engine import trace

OUT = Path(__file__).resolve().parent / "golden_discovery_tron.json"
SNAPSHOT = 1790467200   # 2026-09-27 00:00 UTC — on Tron the snapshot is unix seconds (providers/tron.py)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--screen", type=int, default=80)
    ap.add_argument("--limit", type=int, default=15)
    ap.add_argument("--max-hops", type=int, default=5)
    ap.add_argument("--fanout", type=int, default=4)
    a = ap.parse_args()

    with connect() as c:
        ls = c.execute("SELECT version FROM label_set ORDER BY created_at DESC LIMIT 1").fetchone()[0]
        rows = c.execute("SELECT DISTINCT address, entity_id FROM address_label WHERE label_set_version=%s "
                         "AND chain='tron' AND role='sanctioned' ORDER BY address LIMIT %s",
                         (ls, a.screen)).fetchall()
    prov = TronGridProvider()
    screened = []
    for addr, ent in rows:
        rx = prov.rows(addr, 0, SNAPSHOT, max_pages=1)
        out = [r for r in rx if r["from"] == addr and int(r["value"]) > 0]
        if out and len(rx) < 200:   # has USDT outflows and is not itself a hub
            screened.append((len(out), addr, ent))
    print(f"label set {ls}: screened {len(rows)}, {len(screened)} with USDT outflows ({prov.upstream} upstream)")

    results = []
    for n_out, addr, ent in sorted(screened, reverse=True)[:a.limit]:
        r = trace(addr, "tron", until_block=SNAPSHOT, max_hops=a.max_hops, fanout=a.fanout, label_set=ls)
        row = {"suspect": addr, "ofac_entity": ent, "result": r["result"], "entity": r.get("entity"),
               "hops": r["hops"], "endpoint": r["endpoint"], "role_basis": r["role_basis"],
               "calls": r["api_calls"], "reason": r.get("reason"),
               "sweep": [e for e in r["sweep_evidence"] if e.get("sweep")][:3]}
        results.append(row)
        print(f"{row['result']:<14} {addr} ({ent}) -> {row['entity']} hops={row['hops']} "
              f"basis={row['role_basis']} calls={row['calls']}")
    OUT.write_text(json.dumps({"snapshot": SNAPSHOT, "label_set": ls, "results": results}, indent=2) + "\n")
    print(f"-> {OUT}")


if __name__ == "__main__":
    main()
