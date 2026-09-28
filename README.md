# train — VASP Attribution Engine (SIH26182)

Turns an unknown crypto wallet into an explainable investigation graph and returns the nearest
deposit-accepting VASPs, ranked by evidence, each with an auditable reason and an honest
confidence index. It abstains rather than guessing when the evidence is not there.

Chains: Bitcoin, Ethereum, Polygon and Tron (USDT, TRC-20). Input is a wallet, or free complaint
text: every BTC, ETH and Tron address in it is extracted, checksum-validated, traced, and checked for
convergence across complaints.

It tells an Indian investigator which exchange to serve under BNSS §94 + IT Act §79(3)(b). The
output is an investigative lead, not identity and not evidence: identifying the account holder
behind a deposit address is the VASP's KYC under a lawful request.

## Demo

[media/train-demo.mp4](media/train-demo.mp4): a full walkthrough, landing page to filed report,
including an abstained case and a CoinJoin trap the engine refuses to follow.

- `architecture.md`: the spec and single source of truth (data model, scoring, invariants).
- `DEMO.md`: the stage runbook.
- `backend/app/eval/results/canonical.json`: every number the project quotes, with the weights and
  label set it was computed under.

## Results

From `canonical.json` (weights `w-75bf07eaee79`, frozen before any run; label set `ls-dd778ec3dbfa`):

- **Golden set, 9 OFAC/DOJ-documented cases:** 8 of 9 decided as the record says, 0 wrong. The right
  exchange is ranked #1 in 5 of 6 discovery cases (the sixth abstains, Binance 70 vs Bitfinex 64), and
  3 of 3 traps are refused. Replayed offline: 0 network calls. One case is an OFAC-listed USDT wallet
  on Tron that reaches a sweep-proven KuCoin deposit, so the SAHYOG portal route fires (BNSS §94).
- **Recovery test, 40 ETH wallets 1 to 3 hops before a known deposit:** named an exchange 16 times,
  all 16 real deposits the money reached (0 false leads); abstained on 24; 10 of 10 mixer traps
  refused. Strong at 1 hop, weak at 3. The truth is picked by sweep behaviour, so this measures
  traversal and ranking, not sweep-proof itself.

These are small-n smoke tests, quoted as counts, never as percentages.

## What it is not

No chain-wide or real-time monitoring. No mixer de-anonymisation. No trained-model accuracy figure.
No live SAHYOG integration (the mock is a documented contract). Confidence is a 0-100 index, not a
probability. Reproducibility is not legal chain of custody.

## Quickstart

Requires Docker (about 4 GB for the VM) and a free [Etherscan API key](https://etherscan.io/myapikey).
BTC needs no key. Tron works keyless (paced at 1.5 req/s); an optional `TRONGRID_API_KEY` raises that.

```bash
cp .env.example .env            # then paste your ETHERSCAN_API_KEY
./scripts/fetch_vendor.sh       # OFAC SDN + GraphSense TagPacks (~165 MB, not vendored in git)
docker compose up -d            # 6 services; ~80s to all-healthy on a laptop
docker compose ps               # expect 6/6 healthy

# one-time: build the label set (§6) from labels/ + vendor/ into Postgres
docker compose exec api python -m app.labels.ingest

open http://127.0.0.1:5173      # the console
```

The API is published on `127.0.0.1:8000` only, it has no authentication yet (§15), so it is not
exposed to the network. `http://127.0.0.1:8000/docs` is the generated OpenAPI contract.

### Without the demo database

A clone starts with an empty Postgres. The schema is created automatically on API boot, but the
stored provider responses that make the offline demo possible are not in git: they live in the
`pgdata` volume of the machine that fetched them. On a fresh machine a trace fetches from the
providers (free tier, rate-limited: Etherscan 3 req/s, Blockstream 700 req/h per IP, TronGrid keyless
~0.4 req/s sustained), so budget a
few minutes for the first run of a case and expect the call counters in the HUD to be non-zero.

## Trace a wallet

```bash
# a golden BTC case, pinned to the snapshot the golden set uses
curl -s -X POST 127.0.0.1:8000/cases -H 'content-type: application/json' \
  -d '{"wallets":["12w6v1qAaBc4W8h8C2Cu5SKFaKDSv3erUW"],"chain":"btc","snapshot_block":966946}'
curl -s 127.0.0.1:8000/trace/<trace_id>/status
curl -s 127.0.0.1:8000/trace/<trace_id> | jq '{state, recommended, separation}'
curl -s 127.0.0.1:8000/report/<trace_id> -o report.pdf

# the golden Tron case (snapshot is unix seconds on Tron): reaches KuCoin, a SAHYOG exchange
curl -s -X POST 127.0.0.1:8000/cases -H 'content-type: application/json' \
  -d '{"wallets":["THJUJFyTnrBWb1ijWav7qfRBrjUuKgP49Z"],"chain":"tron","snapshot_block":1790467200}'

# complaint text in, one case per chain out, plus the /convergence query for each chain
curl -s -X POST 127.0.0.1:8000/intake -H 'content-type: application/json' \
  -d '{"text":"paid 0x08723392Ed15743cc38513C4925f5e6be5c17243 then 0x3e37627dEAA754090fBFbb8bd226c1CE66D255e9","snapshots":{"eth":25906777}}'
```

Every case pins four things and prints them on the report (§12): snapshot block, label-set
version, adapter version, weight-profile hash. Re-running with the same four reproduces the result
from the stored responses.

## Checks

```bash
# offline golden-set evaluation from the stored responses, zero provider calls
# (--canonical backend/app/eval/results/canonical.json rewrites the quotable numbers)
docker compose exec api python -m app.eval.harness

# ETH recovery test: live tracing, hours on the free tier; resumable, results into canonical.json
docker compose run --rm -e PYTHONPATH=/app -v "$PWD/scripts:/repo/scripts" \
  api python /repo/scripts/recovery_bench.py --n 40 --decoys 10

# pure self-checks: complaint-text extraction + checksums
docker compose exec api python -m app.api.intake

# §12 reproduce-and-verify: re-render a filed report and compare its content hash
docker compose exec api python -m app.eval.verify <trace_id>

# module self-checks (pure, no DB)
docker compose exec api python -m app.scoring.rules
docker compose exec api python -m app.attribution.aggregate

# rebuild the derived Neo4j index from Postgres (§7.6); --keep is non-destructive
docker compose run --rm -e PYTHONPATH=/app -v "$PWD/scripts:/repo/scripts:ro" \
  api python /repo/scripts/rebuild_graph.py --keep
```

Do not run `scripts/day5_graph_check.py`. It `TRUNCATE`s `trace_edge, edge, evidence, utxo_tx` and
wipes the multi-victim convergence data. See `architecture.md` §24 for why it is formally unrun.

## Layout

```
backend/app/
  providers/     chain adapters (esplora = BTC/UTXO, etherscan_v2 = ETH+Polygon, tron = TronGrid USDT)
  labels/        the label & entity subsystem (§6): registry, ingest, sweep proof, propagation
  trace/         bounded forward BFS; one engine, two drivers (in-process CLI + Celery chords)
  attribution/   candidates -> per-entity evidence -> ranking -> one crowned target or abstention
  scoring/       frozen expert-prior weights, four explainable factors, 0-100 index
  boundary/      mixer / bridge / DEX policy and BTC change + CoinJoin detection
  api/           report (WeasyPrint), SAHYOG mock, timeline, complaint-text intake
  eval/          golden-set harness, report verification, results/canonical.json
frontend/src/    React + Vite + Cytoscape console
labels/          curated label data (OFAC crypto list, SAHYOG VASPs, bridges, golden set)
scripts/         gate checks, golden-case discovery (BTC, Tron), the recovery test, the Neo4j rebuild
```

Postgres is the system of record, Neo4j is a derived index and can be rebuilt from it at any time.
The graph and convergence surfaces read Postgres directly, so they keep working with Neo4j down.
