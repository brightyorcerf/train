"""Ground-truth manufacture: record a REAL deposit the team made to an exchange, verify it on-chain,
and find where the exchange swept it (its hot wallet). Appends to labels/ground_truth_deposits.yaml.

    backend/.venv/bin/python scripts/day1_groundtruth.py            # guided prompts
    backend/.venv/bin/python scripts/day1_groundtruth.py --dry-run  # verify + show, write nothing

Before running — make the deposit:
  1. On the exchange (WazirX / CoinDCX / ZebPay / …) open Deposit, pick the coin + network
     (BTC, ETH on Ethereum, or USDT/POL on Polygon) and copy YOUR deposit address.
  2. Send ~₹100–200 worth FROM A SELF-CUSTODY WALLET YOU CONTROL (MetaMask, Electrum, …), not
     from another exchange — an exchange withdrawal comes from a hot wallet with millions of
     counterparties and makes the controlled trace meaningless.
  3. Wait for the exchange to credit it. Copy the tx hash from your wallet.
  4. Run this script. Exchanges sweep deposits in batches — minutes to hours. If no sweep is
     found yet, record anyway and re-run later: the entry is updated in place.
"""
import argparse
import datetime as dt
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from day1_chain import REPO, Chain  # noqa: E402

GT = REPO / "labels" / "ground_truth_deposits.yaml"
TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


def ask(prompt, default=None, choices=None):
    while True:
        v = input(f"{prompt}{f' [{default}]' if default else ''}: ").strip() or default
        if v and (not choices or v in choices):
            return v
        print(f"  -> required{f', one of {choices}' if choices else ''}")


def fail(msg):
    print(f"\nFAIL: {msg}")
    sys.exit(1)


def verify_evm(ch, chain, dep, sender, txh):
    tx = ch.es(chain, module="proxy", action="eth_getTransactionByHash", txhash=txh)
    if not tx:
        fail(f"tx {txh} not found on {chain} — wrong chain/network?")
    rc = ch.es(chain, module="proxy", action="eth_getTransactionReceipt", txhash=txh)
    if not rc or rc.get("status") != "0x1":
        fail(f"tx not mined or reverted (receipt status {rc and rc.get('status')})")
    block = int(tx["blockNumber"], 16)
    head = int(ch.es(chain, module="proxy", action="eth_blockNumber"), 16)
    ts = int(ch.es(chain, module="proxy", action="eth_getBlockByNumber", tag=hex(block), boolean="false")["timestamp"], 16)
    d = dep.lower()
    if (tx.get("to") or "").lower() == d and int(tx["value"], 16) > 0:
        credit = {"asset": "native", "token": None, "amount": int(tx["value"], 16) / 1e18}
    else:
        logs = [lg for lg in rc["logs"] if lg["topics"][0] == TRANSFER and len(lg["topics"]) == 3
                and lg["topics"][2][-40:] == d[2:]]
        if not logs:
            fail(f"tx {txh} does not credit {dep} (neither native value nor an ERC-20 Transfer to it)")
        token = logs[0]["address"].lower()
        rows = [t for t in ch.es(chain, module="account", action="tokentx", address=dep, startblock=block,
                                 endblock=block) if t["hash"].lower() == txh.lower()]
        sym, dec = (rows[0]["tokenSymbol"], int(rows[0]["tokenDecimal"])) if rows else ("?", 0)
        credit = {"asset": sym, "token": token, "amount": int(logs[0]["data"], 16) / 10 ** dec}
    if tx["from"].lower() != sender.lower():
        print(f"  ! WARNING: tx sender is {tx['from']}, not the wallet you entered ({sender})")
    print(f"  PASS deposit tx confirmed: block {block} ({head - block} confirmations), "
          f"{credit['amount']} {credit['asset']} -> {dep}")
    moves = ch.evm_outgoing(chain, dep, block, extra_tokens=[credit["token"]] if credit["token"] else ())
    same = "native" if credit["asset"] == "native" else credit["asset"]
    for m in moves:
        m["same_asset"] = (m["kind"] in ("native", "internal")) if same == "native" else m["asset"] == same
    return block, ts, credit, moves


def verify_btc(ch, dep, sender, txh):
    t = ch.btc(f"/tx/{txh}")
    if not t["status"]["confirmed"]:
        fail("deposit tx is not confirmed yet — wait for a block and re-run")
    outs = [(i, o) for i, o in enumerate(t["vout"]) if o.get("scriptpubkey_address") == dep]
    if not outs:
        fail(f"tx {txh} has no output paying {dep}")
    ins = {(v.get("prevout") or {}).get("scriptpubkey_address") for v in t["vin"]}
    if sender not in ins:
        print(f"  ! WARNING: {sender} is not among the tx inputs {sorted(a for a in ins if a)}")
    amount = sum(o["value"] for _, o in outs) / 1e8
    print(f"  PASS deposit tx confirmed: block {t['status']['block_height']}, {amount} BTC -> {dep}")
    moves = []
    for i, _ in outs:  # follow the exact UTXO we created: who spent it?
        osp = ch.btc(f"/tx/{txh}/outspend/{i}")
        if not osp.get("spent"):
            continue
        s = ch.btc(f"/tx/{osp['txid']}")
        s_ins = {(v.get("prevout") or {}).get("scriptpubkey_address") for v in s["vin"]}
        for o in s["vout"]:
            a = o.get("scriptpubkey_address")
            if a and a not in s_ins:
                moves.append({"kind": f"utxo ({len(s['vin'])}-input tx)", "to": a, "value": o["value"] / 1e8,
                              "asset": "BTC", "hash": s["txid"], "ts": s["status"].get("block_time"),
                              "block": s["status"].get("block_height"), "same_asset": True})
    moves.sort(key=lambda m: -m["value"])  # sweep destination = where the bulk went
    return t["status"]["block_height"], t["status"]["block_time"], {"asset": "BTC", "amount": amount}, moves


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="verify and print; do not write the YAML")
    ap.add_argument("--exchange"), ap.add_argument("--chain", choices=["btc", "eth", "polygon"])
    ap.add_argument("--deposit-address"), ap.add_argument("--sender"), ap.add_argument("--tx")
    ap.add_argument("--usd", help="approx USD value of the deposit")
    ap.add_argument("-n", type=int, default=5, help="outgoing movements to show")
    a = ap.parse_args()

    print("Before running" + __doc__.split("Before running")[1])
    exchange = a.exchange or ask("Exchange name (e.g. WazirX)")
    chain = a.chain or ask("Chain", "eth", ["btc", "eth", "polygon"])
    dep = a.deposit_address or ask("Deposit address the exchange gave you")
    sender = a.sender or ask("Your sending wallet address")
    txh = a.tx or ask("Tx hash of your deposit")
    usd = a.usd or ask("Approx USD value of the deposit", "5")

    ch = Chain()
    print(f"\nVerifying on {chain} …")
    if chain == "btc":
        block, ts, credit, moves = verify_btc(ch, dep, sender, txh)
    else:
        block, ts, credit, moves = verify_evm(ch, chain, dep, sender, txh)

    print(f"\nNext {a.n} outgoing movement(s) from the deposit address after our deposit:")
    moves = moves[:a.n]
    for i, m in enumerate(moves, 1):
        dt_s = dt.datetime.fromtimestamp(m["ts"], dt.UTC).isoformat() if m["ts"] else "unconfirmed"
        print(f"  [{i}] {m['kind']:<22} {m['value']:.8g} {m['asset']:<6} -> {m['to']}  {dt_s}  tx {m['hash']}")
    suggested = next((i for i, m in enumerate(moves, 1) if m["same_asset"] and m["ts"]), None)
    sweep = None
    if not moves:
        print("  (none yet — the exchange hasn't swept. Recording as ground_truth; re-run later to add the sweep.)")
    else:
        pick = "s" if (a.dry_run and not suggested) else (str(suggested) if a.dry_run else
               ask("Which one is the sweep to the exchange's hot wallet? number, or 's' if none yet",
                   str(suggested) if suggested else "s"))
        if pick != "s":
            sweep = moves[int(pick) - 1]

    entry = {
        "exchange": exchange, "chain": chain, "deposit_address": dep,
        "our_sending_wallet": sender, "our_deposit_tx": txh, "our_deposit_block": block,
        "our_deposit_amount": f"{credit['amount']} {credit['asset']}", "our_deposit_amount_usd": f"~{usd}",
        "sweep_tx": sweep["hash"] if sweep else None,
        "sweep_destination": sweep["to"] if sweep else None,  # the hot wallet
        "sweep_observed_within_seconds": (sweep["ts"] - ts) if sweep else None,
        "role_basis": "sweep_proven" if sweep else "ground_truth",
        "confidence": 1.0, "verified_by": "ground_truth_manufacture",
        "api_calls": ch.calls, "date": dt.date.today().isoformat(),
    }
    print("\n" + yaml.safe_dump([entry], sort_keys=False))
    if a.dry_run:
        print("DRY RUN — nothing written.\nPASS")
        return
    entries = (yaml.safe_load(GT.read_text()) or []) if GT.exists() else []
    entries = [e for e in entries if (e["deposit_address"], e["our_deposit_tx"]) != (dep, txh)] + [entry]
    GT.write_text(yaml.safe_dump(entries, sort_keys=False))
    print(f"PASS — written to {GT} ({len(entries)} entr{'y' if len(entries) == 1 else 'ies'})")
    print(f"Next: backend/.venv/bin/python scripts/day1_verify.py {sender} {chain} "
          f"--since-block {block} --expect ATTRIBUTED --record controlled_case")


if __name__ == "__main__":
    main()
