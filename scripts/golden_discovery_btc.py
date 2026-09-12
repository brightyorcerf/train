"""Screen OFAC-sanctioned BTC addresses for golden-set discovery cases (§11.2): an unlabeled suspect
that reaches a documented or sweep-provable VASP endpoint several hops out.

BTC is where our label coverage is strongest (336k TagPacks deposit labels + 435 exchange hot
wallets), so multi-hop discovery is likelier here than on EVM.

PROVIDER BUDGET — read before raising --screen. Blockstream caps unauthenticated use at 700
requests/hour/IP (found on day 3) and mempool.space throttles bursts. Screening costs ONE call per
address and tracing costs up to ~200, so the defaults stay deliberately small; repeat runs are
served from the §12 raw store. Raise the limits only if you are willing to wait out a 429.

    backend/.venv/bin/python scripts/golden_discovery_btc.py [--snapshot N] [--screen 60] [--limit 12]
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.db import connect  # noqa: E402
from app.providers.esplora import EsploraProvider  # noqa: E402
from app.trace.engine import trace  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "scripts" / "golden_discovery_btc.json"
HUB = 1000   # §9.3: a suspect with this many receipts is a service, not a person


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", type=int, default=966553, help="BTC block snapshot (day-2 pin)")
    ap.add_argument("--screen", type=int, default=60, help="addresses to screen (1 call each)")
    ap.add_argument("--limit", type=int, default=12, help="addresses to actually trace")
    ap.add_argument("--max-hops", type=int, default=5)
    ap.add_argument("--fanout", type=int, default=4)
    ap.add_argument("--min-hops", type=int, default=2, help="only report cases at least this deep")
    a = ap.parse_args()

    with connect() as c:
        rows = c.execute(
            "SELECT DISTINCT address, entity_id FROM address_label WHERE chain = 'btc' AND role = 'sanctioned' "
            "AND label_set_version = (SELECT version FROM label_set ORDER BY created_at DESC LIMIT 1) "
            "ORDER BY address").fetchall()
    print(f"{len(rows)} OFAC BTC addresses in the label set; screening {min(a.screen, len(rows))} "
          f"at snapshot {a.snapshot}")

    p = EsploraProvider(snapshot=a.snapshot)
    cands = []
    for addr, ent in rows[:a.screen]:
        try:
            st = p.address_stats(addr)
        except Exception as e:                                   # noqa: BLE001
            print(f"   ! {addr}: {str(e)[:90]}")
            continue
        funded, spent = st.get("funded_txo_count", 0), st.get("spent_txo_count", 0)
        if spent and funded < HUB:      # it sent value, and it is not itself a service hub
            cands.append((addr, ent, funded, spent))
    cands.sort(key=lambda r: r[2])      # quietest first: cheap traces, clearer provenance
    print(f"{len(cands)} sent value and are not hubs; tracing {min(a.limit, len(cands))}\n")

    out = []
    for addr, ent, funded, spent in cands[:a.limit]:
        t0 = time.time()
        try:
            r = trace(addr, "btc", until_block=a.snapshot, max_hops=a.max_hops, fanout=a.fanout)
        except Exception as e:                                   # noqa: BLE001
            print(f"{ent[:26]:<28} {addr:<36} ERROR {type(e).__name__}: {str(e)[:70]}")
            continue
        # `suspect_entity` must not collide with the trace's own `entity` key (the attributed VASP);
        # spreading `**r` last used to silently overwrite it and made every row look exchange-owned.
        out.append({"suspect_entity": ent, "funded": funded, "spent": spent, **r})
        print(f"{ent[:26]:<28} {addr:<36} {r['result']:<16} hops={r['hops']} calls={r['api_calls']} "
              f"({r.get('upstream_calls')} up) {time.time() - t0:.0f}s {r.get('entity') or ''} "
              f"{r.get('role_basis') or r.get('reason', '')}")
    OUT.write_text(json.dumps(out, indent=2, default=str) + "\n")

    keep = [r for r in out if r["result"] != "UNATTRIBUTED" and (r["hops"] or 0) >= a.min_hops]
    print(f"\n{len(keep)} candidate(s) at >= {a.min_hops} hops — hand-verify each before it enters "
          f"the golden set (never add on behaviour alone):")
    for r in keep:
        print(f"   {r['wallet']} -> {r['entity']} @ {r['endpoint']} ({r['role_basis']}, {r['hops']} hops)")
    print(f"-> {OUT}")


if __name__ == "__main__":
    main()
