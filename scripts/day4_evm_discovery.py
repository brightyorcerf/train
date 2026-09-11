"""Screen OFAC-sanctioned ETH addresses for an EVM discovery case (§11.2: an unlabeled suspect
that reaches a documented / sweep-proven VASP endpoint several hops out).

Screening costs one txlist call per address; only addresses that actually sent value are traced.

    backend/.venv/bin/python scripts/day4_evm_discovery.py [--snapshot N] [--limit 12] [--max-hops 4]
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from day1_verify import trace  # noqa: E402

from app.db import connect  # noqa: E402
from app.providers.etherscan_v2 import EtherscanV2Provider  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "scripts" / "day4_evm_discovery.json"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", type=int, default=0, help="ETH block snapshot (default: chain tip)")
    ap.add_argument("--limit", type=int, default=12)
    ap.add_argument("--max-hops", type=int, default=4)
    ap.add_argument("--fanout", type=int, default=4)
    a = ap.parse_args()

    p = EtherscanV2Provider("eth")
    snapshot = a.snapshot or p.tip()
    with connect() as c:
        rows = c.execute(
            "SELECT DISTINCT address, entity_id FROM address_label WHERE chain = 'eth' AND role = 'sanctioned' "
            "AND address LIKE '0x%' AND label_set_version = (SELECT version FROM label_set "
            "ORDER BY created_at DESC LIMIT 1) ORDER BY address").fetchall()
    print(f"screening {len(rows)} OFAC ETH addresses at snapshot {snapshot}")

    cands = []
    for addr, ent in rows:
        try:
            txs = p.rows("txlist", addr, 0, snapshot, max_pages=1)
        except Exception as e:  # noqa: BLE001
            print(f"   ! {addr}: {e}")
            continue
        sent = [t for t in txs if t["from"].lower() == addr.lower() and int(t["value"]) > 0]
        if sent:
            cands.append((addr, ent, len(txs), len(sent)))
    cands.sort(key=lambda r: r[2])   # quietest first: cheap traces, and a hub start is not a suspect
    print(f"{len(cands)} have outgoing value; tracing {min(a.limit, len(cands))}\n")

    out = []
    for addr, ent, n_tx, n_sent in cands[:a.limit]:
        t = time.time()
        try:
            r = trace(addr, "eth", until_block=snapshot, max_hops=a.max_hops, fanout=a.fanout)
        except Exception as e:  # noqa: BLE001
            print(f"{ent[:28]:<30} {addr} ERROR {type(e).__name__}: {str(e)[:80]}")
            continue
        out.append({"entity": ent, **r})
        print(f"{ent[:28]:<30} {addr} {r['result']:<16} hops={r['hops']} calls={r['api_calls']} "
              f"({r.get('upstream_calls')} upstream) {time.time() - t:.0f}s {r.get('entity', '')} "
              f"{r.get('role_basis') or r.get('reason', '')} flags={len(r['flags'])}")
    OUT.write_text(json.dumps(out, indent=2, default=str) + "\n")
    print(f"\n-> {OUT}")


if __name__ == "__main__":
    main()
