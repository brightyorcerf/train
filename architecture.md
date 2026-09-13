# VASP Attribution Engine — Architecture (SIH26182)

**Single source of truth.** This consolidates and supersedes every earlier draft (v1 build
doc, v2 core, v3 review-response). Where earlier versions conflict, this document wins.
Ministry of Home Affairs · Indian Cyber Crime Coordination Centre (I4C) · Blockchain &
Cybersecurity.

**Two things are still OPEN and must be resolved by the team — they are not answerable by
more research** (see §1): (1) whether GraphSense TagPacks contain *deposit-address-level*
labels and any Indian-VASP coverage — inspect the repo directly; (2) the SIH 2026 grand-finale
format (pre-built + present vs. on-site build) — confirm with your nodal SPOC. Both change how
you spend the 14 days.

---

## Contents

1. External facts & limits (verified) · OPEN risks
2. Core principle, the two layers, and the pipeline
3. The two claims kept separate — *nearest* vs *confidence*
4. Repo layout
5. Docker Compose runtime
6. **The Label & Entity subsystem** (the center of the project)
7. Data model
8. Providers & rate limiting
9. Trace engine — capacity math, Celery chords, states, service-node policy
10. Attribution engine — candidate ranking + recommendation
11. Scoring & evaluation harness
12. Reliability invariants — determinism, idempotency, reproducibility
13. Explainability, provenance & frontend
14. API surface
15. Security / PII posture
16. Observability
17. India-first & SAHYOG integration
18. Language discipline — claims that survive scrutiny
19. Q&A defense
20. The demo
21. Scope ledger (B/S/F)
22. 14-day build plan — with cuts and tripwires
23. Pitch + scope boundary
24. Honest score trajectory + day-1 gates

---

## 1. EXTERNAL FACTS & LIMITS (verified Sep 2026 — re-check before building; these drift)

| Fact | Status | Note |
|---|---|---|
| Etherscan V2: one key, `chainid` selects chain, 60+ EVM chains | ✅ | Base URL `https://api.etherscan.io/v2/api` |
| Etherscan **free tier rate** | ⚠️ **3 req/s** per Etherscan's own docs; some secondaries still say 5 (that's now the paid **Lite** plan, $49/mo) | **Key-test it in 60 s** on day 1; doesn't change any decision — 3 vs 5 is ~5 min vs ~3 min per trace |
| Etherscan free daily quota | ✅ 100,000 calls/day | cumulative across your keys — the ceiling that actually bites (§9) |
| Etherscan free **record cap** | ✅ **1,000 records/request** (from 10k, July 2026) | forces pagination |
| Etherscan free **chain coverage** | ✅ ~90% of chains; **ETH (1) free**, **Polygon (137) free** (key-test), **BNB (56) NOT free** | BNB dropped from free June 2026 |
| BNB free alternative | ✅ **BSCTrace / MegaNode** — but **JSON-RPC 2.0, not REST** | different adapter shape; budget for it |
| `txlist`, `txlistinternal` (by-address), `tokentx` | ✅ free-tier | internal-txs-*by-block-range* was removed from free; by-address stays |
| Etherscan nametag/metadata endpoint | ❌ **Pro Plus only**, 2 req/s | optional paid enrichment; never a free-tier dependency |
| BTC live path: **mempool.space + Blockstream Esplora** | ✅ free, indexed (address/tx/UTXO), ~100–500 req/min per IP | use with automatic failover (`esplora-client`) |
| Blockchair BTC | ⚠️ ~**1,000 calls/day** free | too thin for live BFS; use for curation/dataset dumps only |
| Bitquery | ⚠️ free points **first-month-only**; self-service tiers **real-time only** (history is a paid per-chain add-on) | keep behind the abstraction; never on the historical demo path |
| OFAC SDN crypto list | ✅ free, US Treasury (`sdn.csv` / `sdn_advanced.xml`), `Digital Currency Address - XBT/ETH/…` | authoritative sanctioned layer |
| **SAHYOG**: 45+ VASPs onboarded (WazirX, KuCoin, Bybit, Bitget), legal basis **BNSS §94 + IT Act §79(3)(b)**, **no public API schema** | ✅ | reframes the whole India story (§17) |
| Label mutability | ✅ e.g. Tornado Cash sanctioned 2022, **delisted 2025** | forces label-set versioning (§12) |

**OPEN — resolve by hand, not by searching:**
- **GraphSense TagPacks granularity/coverage/license.** Do the packs carry *address-level* tags
  with a `deposit` role, or only entity/service tags? Any Indian/Asian VASP coverage? License?
  → clone `graphsense/graphsense-tagpacks`, open packs, grep entity names, read LICENSE (~20 min).
  **If it's entity-tags-only and Western-skewed (likely), §6.2c sweep detection is your PRIMARY
  deposit-detection mechanism, not a nice-to-have — build it first.**
- **SIH 2026 grand-finale format & duration.** The 14-day plan (§22) assumes pre-build +
  present. If substantial on-site build is required, the schedule shifts. Confirm with the SPOC.
- **Case A existence** (see §20, §24) — the single binding project risk.

---

## 2. CORE PRINCIPLE, THE TWO LAYERS, AND THE PIPELINE

Do not build a blockchain explorer. Build an **evidence-driven VASP attribution engine** that
happens to use blockchain graphs underneath. Attribution requires two layers:

```
   ON-CHAIN EVIDENCE                 ENTITY INTELLIGENCE
   (transaction graph,        ⊕     (VASP registry, address roles,
    values, timing, clusters)        source-graded labels — §6)
                       │
                       ▼
              ATTRIBUTION ENGINE
                       │
                       ▼
   Ranked VASP candidates + confidence + auditable explanation
```

**The pipeline** (this is *not* shortest-path-to-a-labeled-node — that was the trap):

```
POST wallet → BFS walks tx edges outward hop-by-hop (bounded), writing each edge to the graph
→ enumerate ALL candidate endpoints (labeled or sweep-proven) → per-path evidence vector
→ aggregate by resolved ENTITY → rank VASP candidates → confidence + separation
→ recommend one primary target → render report → route to SAHYOG mock
```

One-sentence pitch:

> *"We turn an unknown wallet into an explainable investigation graph and return the nearest
> deposit-accepting VASPs, ranked by multi-signal evidence, each with an auditable reason and an
> honest confidence — and the system abstains rather than guessing when the evidence isn't
> there."*

**Stated assumption (put it on a slide):** "real-time investigative intelligence" = fast,
on-demand turnaround for a single wallet trace (seconds–minutes), **not** continuous chain-wide
streaming. Chain-wide monitoring is explicitly out of scope.

---

## 3. THE TWO CLAIMS, KEPT SEPARATE — *nearest* vs *confidence*

The PS asks for the *nearest direct-deposit-accepting exchange*. That is a **topology** claim.
Evidence strength is a **confidence** claim. They are orthogonal; the UI keeps them apart and
the confidence score does **not** contain a proximity term (that would double-count against the
Nearest panel).

- **Proximity / Nearest** — a fact about the graph: hops from suspect to the candidate's deposit
  address, plus the connecting tx (hash, amount, timestamp).
- **Attribution confidence** — a `0–100` **index (not a probability)**, an explicit weighted sum
  of inspectable evidence factors, always shown with its factor breakdown.
- **Separation** — the gap between the top two candidates. Below a threshold → present as
  *ambiguous* ("candidates X and Y are comparable"), don't crown one. With `UNATTRIBUTED`, this
  is the engine's honest non-answer.

A 2-hop weak-label Binance path and a 3-hop strong-deposit-evidence Coinbase path must be
comparable on *evidence*, not decided by hop count. The **recommendation layer** (§10) combines
the two axes transparently to name one target — without fusing them into a fake scalar.

---

## 4. REPO LAYOUT (monorepo)

```
vasp-attribution/
├── docker-compose.yml            # one-command judge-laptop startup
├── .env.example                  # all secrets via env; none in repo
├── scripts/
│   ├── day1_verify.py            # prove Case A traces on free HISTORICAL data + endpoint documented
│   └── rebuild_graph.py          # rebuild Neo4j from Postgres (Neo4j is a derived index — §7.6)
├── labels/                       # curated label data, checked into git
│   ├── ofac_sdn_crypto.csv       # authoritative sanctioned addresses (US Treasury)
│   ├── sahyog_vasps.yaml         # curated deposit/hot/withdrawal addrs for SAHYOG-onboarded VASPs
│   ├── mixers_bridges_dex.yaml   # known mixer + bridge + DEX-router addresses
│   └── golden_set.yaml           # eval fixtures: unlabeled suspects 3+ hops from documented VASP
├── backend/
│   ├── Dockerfile                # pin WeasyPrint sys-libs (Pango, Cairo, GDK-PixBuf) — see §5
│   ├── pyproject.toml
│   └── app/
│       ├── main.py               # FastAPI app factory + router mount
│       ├── core/{config.py,deps.py,ratelimit.py}   # ratelimit.py = Redis token bucket per provider
│       ├── api/{cases,trace,wallets,reports,sahyog}.py
│       ├── schemas/              # pydantic request/response models (§14)
│       ├── providers/            # provider abstraction (§8) — NOT one-file-per-chain-scan
│       │   ├── base.py           # BlockchainProvider ABC
│       │   ├── etherscan_v2.py   # EVM: ETH, Polygon (chainid); REST
│       │   ├── bsctrace.py       # BNB via MegaNode — JSON-RPC 2.0 (different shape)
│       │   ├── esplora.py        # BTC: mempool.space primary → blockstream.info failover
│       │   ├── blockchair.py     # BTC curation / dataset dumps only
│       │   └── tron.py           # STUB — documents the interface; deferred (§21)
│       ├── labels/               # THE LABEL & ENTITY SUBSYSTEM (§6)
│       │   ├── registry.py       # VASP registry + entity resolution
│       │   ├── ingest.py         # load labels/* → Postgres + Neo4j
│       │   ├── propagate.py      # cluster→entity label propagation over SAME_OWNER
│       │   └── sweep.py          # sweep-to-hot deposit-behaviour detection (§6.2c)
│       ├── trace/{engine,bfs,tasks}.py             # state machine, frontier logic, Celery chords
│       ├── graph/{client,queries}.py               # Neo4j driver + Cypher (MERGE, candidate enum)
│       ├── attribution/{engine,aggregate,recommend}.py   # candidate → evidence → rank → recommend
│       ├── scoring/{engine,rules,weights.py}       # frozen expert-prior weights (§11)
│       ├── boundary/{mixer,bridge,dex,change}.py   # service-node policy (§9) + BTC change detection
│       ├── typology/peeling.py                     # peel-chain detector — time-boxed, first to cut
│       ├── eval/harness.py                         # offline fixture eval + telemetry (§11)
│       ├── reports/{generator.py,templates/report.html}   # WeasyPrint HTML→PDF
│       └── db/{models,session}.py                  # SQLAlchemy
│   └── worker/celery_app.py      # Celery app, Redis broker + backend
└── frontend/                     # React + Vite + Cytoscape.js
    └── src/
        ├── api/client.ts         # typed fetch + status polling
        └── components/{TraceForm,CaseList,GraphView,Leaderboard,RecommendedTarget,
                        NearestPanel,ScoreBreakdown,ProvenanceCard,ReportButton}.tsx
```

`providers/tron.py` and the ABC are the physical proof of the "pluggable multi-chain" claim:
adding a chain is a new provider file, not a new engine.

---

## 5. DOCKER COMPOSE — the runtime

```yaml
services:
  postgres:   # system of record: cases, jobs, reports, audit log, labels, evidence
    image: postgres:16
    environment: [POSTGRES_DB=vasp, POSTGRES_PASSWORD=...]
    volumes: [pgdata:/var/lib/postgresql/data]
  redis:      # Celery broker + result backend + response cache + per-provider rate-limit buckets
    image: redis:7
  neo4j:      # DERIVED graph index — rebuildable from Postgres
    image: neo4j:5-community
    environment: [NEO4J_AUTH=neo4j/password]
    ports: ["7474:7474","7687:7687"]
    volumes: [neo4jdata:/data]
  api:
    build: ./backend
    command: uvicorn app.main:app --host 0.0.0.0 --port 8000
    depends_on: [postgres, redis, neo4j]
    ports: ["8000:8000"]
  worker:
    build: ./backend
    command: celery -A worker.celery_app worker --concurrency=8 -Q trace,fetch,score
    depends_on: [redis, neo4j, postgres]
  frontend:
    build: ./frontend
    ports: ["5173:5173"]
volumes: { pgdata: {}, neo4jdata: {} }
```

**Day-1 ops note (not day 11):** WeasyPrint needs Pango, Cairo, GDK-PixBuf. Pin them in the
backend Dockerfile now, or PDF generation surprises you the night before the demo.

---

## 6. THE LABEL & ENTITY SUBSYSTEM (the center of the project)

### 6.1 Why this is the whole game

Output quality is capped by **label coverage**. A flawless trace that lands on an unlabeled
address or a hot wallet collapses "nearest deposit-accepting VASP" into "nearest thing we had a
tag for." Labels are a **first-class engineered subsystem**, not a data-loading step. This is
the single highest-leverage part of the build.

### 6.2 Three coverage mechanisms

**(a) Curate around the SAHYOG-onboarded set.** The 45+ VASPs already on SAHYOG (WazirX,
KuCoin, Bybit, Bitget, …) are *simultaneously* the routing target and the India answer. Seed
`sahyog_vasps.yaml` with their known deposit / hot / withdrawal addresses across BTC, ETH, and
TRC-20 where obtainable. Small, high-value, demo-relevant.

**(b) Cluster→entity label propagation.** A label on one address propagates to co-owned
addresses with decayed confidence, over explicit `SAME_OWNER` edges (§7.4): BTC via
common-input-ownership; ETH via the deposit→hot-wallet sweep relationship. This multiplies
sparse labels into coverage — it is *why* clustering matters.

**(c) Sweep-to-hot as first-class deposit evidence** *(likely your PRIMARY mechanism — see §1
OPEN)*. An endpoint that (i) receives from many distinct senders and (ii) sweeps its balance to
a *labeled hot wallet of entity X* within k transactions **behaves like a deposit address of X**
— earning "deposit-accepting" with no per-address label. This closes the gap between "labeled
address" and "deposit-accepting VASP," and it is demo-visible.

### 6.3 Honest endpoint-role taxonomy (what the claim downgrades to)

```
deposit  (labeled OR sweep-proven)  → "nearest deposit-accepting VASP: X"          [full claim]
hot / withdrawal (labeled infra)    → "attributable to VASP X via reachable        [downgraded]
                                        labeled infrastructure"
cluster-propagated (decayed conf)   → same, lower confidence
unlabeled                           → UNATTRIBUTED
```

The engine **never** silently promotes a hot-wallet hit to "deposit-accepting." The downgrade is
the honesty that survives Q&A.

### 6.4 The deposit-event artifact

Every leaderboard row carries: the actual deposit tx (hash, amount, ts), label provenance, and
the **role basis** (labeled vs sweep-proven). This artifact is the concrete answer to "this is
just Etherscan plus a lookup table."

---

## 7. DATA MODEL

### 7.1 Nodes mean what the data means
`:Address` is the atom (an address/account). `:Cluster` is the *inferred* entity from clustering.
`:VASP` is the registered entity. `:Tx` is a UTXO transaction hypernode. (We do **not** call the
atom `:Wallet` — a "wallet" is a conclusion, not raw data.)

```
(:Address { address, chain, is_labeled, cluster_id, first_seen, last_seen })
(:Tx      { hash, chain, ts, block, total_in, total_out })      # UTXO only
(:Label   { role, entity_id→VASP, source, confidence })
(:VASP    { id, name, type, jurisdiction, aliases[] })

Constraints: (address, chain) unique on :Address ; (hash, chain) unique on :Tx
Index: :Address(is_labeled)
```

### 7.2 Account model (Ethereum / EVM) — directed edges, three tx kinds

```
(:Address)-[:SENT { tx_hash, value, asset, ts, block, kind, log_index|trace_address }]->(:Address)
                                                      # kind: native | erc20 | internal
```

- Pull **native** (`txlist`), **internal native value** (`txlistinternal`, by-address, free),
  and **ERC-20** (`tokentx`). Contract-routed token moves *do* surface in `tokentx` event logs.
- **Edge-identity (bug fix — do not skip):** MERGE key =
  `(from, to, tx_hash, kind, log_index | trace_address)`. One `tx_hash` emits many movements;
  collapsing them understates flows and corrupts `value_relevance`.
- **ERC-20 `from/to/value` come from the Transfer *event log*** (`tokentx`), not the
  transaction's `from/to` (which is only the submitting EOA). Normalize token decimals.
- Deep DeFi state reconstruction is out of scope — say so.

### 7.3 UTXO model (Bitcoin) — a transaction is a hypernode

A BTC tx with n inputs and m outputs asserts *the tx paid these outputs from those inputs* — not
that input A paid output B. Pairwise `Address→Address` edges fabricate n×m relationships and
manufacture phantom paths. Model the transaction:

```
(:Address)-[:FUNDS { vin }]->(:Tx)-[:CREDITS { value, vout }]->(:Address)
```

Tracing walks `Address → FUNDS → Tx → CREDITS → Address`. This dual-schema (account = edges,
UTXO = hypernode) is one of the strongest technical-depth talking points; render the difference
visibly in the UI (§13).

### 7.4 Clustering as traversable edges — `SAME_OWNER`

Common-input-ownership is not just a note; it is an edge. Post-ingestion, materialize
`(:Address)-[:SAME_OWNER { confidence }]->(:Address)` between the co-inputs of a `:Tx`. BFS
traverses `SAME_OWNER` with a hop-penalty. This is also the carrier for label propagation
(§6.2b). Present clustering as a **heuristic with a confidence**, never "same owner" — CoinJoin/
PayJoin violate it (GraphSense ships a Wasabi/CoinJoin false-positive suppressor — reuse, don't
reinvent).

### 7.5 BTC change-address detection (soundness fix)

Forward tracing that follows every output as a "payment" follows **change** back toward the
sender; a peel chain *is* a chain of change outputs. Add a naive change heuristic (newly-created
/ non-round output returning toward the input cluster). This is **prior to** the peel typology
and is **kept even if peel is cut** (§21).

### 7.6 Postgres (system of record) — Neo4j is a derived index

```sql
cases       (id uuid pk, wallet, chain, source,           -- source: 'sahyog'|'manual'
             snapshot_block bigint, snapshot_ts timestamptz,   -- determinism pin (§12)
             label_set_version text, status, created_at)
trace_jobs  (id uuid pk, case_id fk, state,               -- see §9 states
             current_hop int, progress float, result jsonb, error, started_at, finished_at)
reports     (id uuid pk, case_id fk, pdf_path, content_hash, adapter_version, weight_hash, created_at)
audit_log   (id bigserial pk, trace_id, ts, input_params jsonb, upstream_hash, actor)   -- append-only
vasp        (id pk, name, type, jurisdiction, aliases jsonb)
address_label (id pk, address, chain, role, entity_id fk, source, confidence, last_verified,
             unique(address, chain, source))
evidence    (id pk, trace_id, candidate_entity, factor, value, provenance jsonb)
golden_set  (id pk, suspect_addr, chain, expected_entity, min_hops, source_doc)
```

`rebuild_graph.py` rebuilds Neo4j entirely from Postgres normalized edges — "we can blow away
and rebuild the graph from the system of record" is both a reliability property and a Q&A line.
All graph writes use `MERGE`, never `CREATE` (§12).

---

## 8. PROVIDERS & RATE LIMITING

```python
class BlockchainProvider(ABC):
    chain: str
    async def get_outgoing(self, address, until_block) -> list[Edge]: ...  # EVM: native+internal+erc20
    async def get_tx(self, tx_hash) -> TxRecord: ...                       # UTXO: full inputs/outputs
    async def get_neighbors(self, address, until_block) -> list[str]: ...
```

| Chain | Provider | Free reality | Notes |
|---|---|---|---|
| ETH (1) | Etherscan V2 (REST) | 3 req/s, 100k/day, 1k rec/req, per-addr internal free | MVP workhorse |
| Polygon (137) | Etherscan V2 (REST) | free (confirmed; key-test) | **default live second chain** |
| BNB (56) | **BSCTrace/MegaNode (JSON-RPC 2.0)** or Etherscan Lite (paid) | no Etherscan free tier | different adapter shape — config swap via the abstraction *demonstrates* it |
| BTC (live walk) | **mempool.space → Blockstream Esplora** (failover) | free, indexed, ~100–500 req/min/IP | `esplora-client` fails over automatically |
| BTC (curation) | Blockchair | ~1k calls/day | dataset dumps / curation only, not live BFS |
| Alternate (Tron/Solana future) | Bitquery | first-month points; real-time only | history is a paid add-on; never on the demo path |
| Labels | OFAC (Treasury) · curated SAHYOG set · GraphSense TagPacks (see §1 OPEN) · Etherscan nametags (Pro Plus, optional) | | see §6 |

**Global rate limiter (load-bearing):** a Redis token-bucket **per provider key**, bounded
worker concurrency, jittered exponential backoff. **On HTTP 429 → serve from cache and mark the
result partial** — never a spinning wheel or a stack trace on stage. A Celery chord fan-out
against a 3 req/s provider is a self-inflicted ban generator without this. **Never run your own
node** — multi-chain node sync is a two-week-project killer; the abstraction is why you don't.

---

## 9. TRACE ENGINE

### 9.1 Why async — the arithmetic (corrected)

Expansions to depth `d`, branching `b` = `(bᵈ−1)/(b−1)`. For `b=20, d=3` → **421 expansions**
(you *discover* ~8,000 depth-3 leaves but never expand them — labels are a local lookup, so you
don't fetch a node to check its label). With **conditional fetch** (call `txlistinternal` only
for contracts, `tokentx` only on token activity) → ~1.2–2 calls/expansion → **~500–850 calls**.
At **3 req/s** + 1k-record pagination → **single-digit minutes** for a normal case.

**The real explosion risk is not uniform `b=20` — it's one hub node** (an exchange hot wallet
with 10⁵–10⁶ counterparties) at a shallow hop; one hub blows the whole budget. Defenses:
`max_neighbors` cap, `min_value_threshold` (also defeats dust/address-poisoning), dedupe/
visited-set, and a **deterministic truncation rule** (value desc → ts → tx-hash tiebreak). Async
+ cache + caps are justified by the hub case — a framing a judge with a calculator can't nibble.

### 9.2 BFS frontier as Celery chords (idempotent)

```python
@app.task(queue="fetch")
def fetch_neighbors(address, chain, snapshot):
    edges = run(provider(chain).get_outgoing(address, until_block=snapshot))  # bounded to snapshot
    merge_edges_to_neo4j(edges)                  # MERGE — idempotent (correct edge key, §7.2)
    persist_and_hash_upstream(address, edges)    # audit_log (provenance, §12)
    record_candidates_if_labeled_or_sweep(edges) # candidate endpoints collected inline (§10)
    return [e.to for e in edges]
# each hop = one chord: fan out the frontier in parallel → join → decide next frontier → recurse
```

*"We parallelize each BFS frontier as a Celery chord rather than walking serially"* — now also
deterministic and retry-safe.

### 9.3 Trace states + service-node policy

| State | Meaning | Report line |
|---|---|---|
| `ATTRIBUTED` | ≥1 labeled/sweep-proven endpoint reached | ranked VASP candidates + evidence |
| `BROKEN_AT_MIXER` | branch hit a known mixer | "prototype trace boundary; downstream not followed" |
| `BROKEN_AT_BRIDGE` | hit a known cross-chain bridge | emits bridge contract + destination chain — "subpoena the bridge operator; cross-chain follow = future" |
| `BROKEN_AT_DEX` | hit a known DEX router/pool | "asset swapped via <DEX>; 1:1 value linkage broken" — see policy below |
| `UNATTRIBUTED` | budget exhausted, no label | "reached N hops, no known VASP; here is how far we got" |
| `ERROR` | upstream/data failure | logged, surfaced honestly |

**Service-node policy** (know the difference — judges reward it):

| Node class | Policy |
|---|---|
| Mixer (known) | **STOP** (trace boundary) |
| Bridge (known) | **STOP**, emit bridge contract + destination chain |
| **DEX router/pool** | **RECORD swap, continue at reduced confidence**, note broken 1:1 linkage |
| Labeled hot/deposit | **STOP expansion**, run one-step sweep check (§6.2c) |
| Unlabeled high-degree hub | cap + threshold; likely `UNATTRIBUTED` |

Preload the top ~50 DEX routers into `mixers_bridges_dex.yaml`. `UNATTRIBUTED` is the state naive
teams forget; for an LEA tool it's the most honest output — build it first-class.

---

## 10. ATTRIBUTION ENGINE — candidate ranking + recommendation

```
BFS (bounded)             → candidate endpoints (all labeled/sweep-proven nodes reached)
per candidate             → representative path(s) + evidence vector
aggregate by ENTITY       → per-VASP evidence   (max + saturating top-k, NOT sum)
score each VASP           → confidence 0–100 (+ breakdown)   [proximity NOT a factor]
rank + separation         → recommend ONE primary target (transparent policy)
                          → ATTRIBUTED | ambiguous(abstain) | UNATTRIBUTED
```

Candidates are recorded inline during BFS as labeled/sweep-proven addresses are discovered.
Representative path per candidate (over the small BFS-materialized subgraph; `SAME_OWNER`
traversed with penalty):

```cypher
MATCH (start:Address {address:$addr, chain:$chain}), (dest:Address {address:$endpoint, chain:$chain})
MATCH p = shortestPath( (start)-[:SENT|FUNDS|CREDITS|SAME_OWNER*..12]->(dest) )
RETURN p, length(p) AS hops ORDER BY hops LIMIT 1;
```

`shortestPath` here retrieves a *representative path per candidate* — it is **not** the winner
selector. Winner selection is the ranking below. (Fallback if Neo4j fights you by day 5:
NetworkX + Postgres, same BFS output, hand-rolled path.)

**Aggregation guards scatter:** per-factor `max` + a saturating path count, so 50 dusty paths to
50 Binance deposit addresses cannot outrank one strong documented path.

**Recommendation layer (crown one target — reject the fused scalar):** rank by a *documented,
transparent policy* — evidence tier first, proximity as tiebreak — and name one *"Recommended
primary target for SAHYOG request"* with a one-line rationale ("closest endpoint with a labeled
deposit address of an onboarded VASP"). We deliberately do **not** compute a single
`(1/hops)×confidence` scalar: it manufactures false precision and re-buries the two axes. The
operator gets a decision; the tool never pretends it's calibrated.

**Separation / abstain:** if `top1 − top2 < τ` → *ambiguous*, don't crown. `UNATTRIBUTED` = none
reached. Two honest non-answers.

---

## 11. SCORING & EVALUATION HARNESS

### 11.1 Confidence scoring — explainable, frozen priors, proximity excluded

```python
def score(v) -> tuple[int, dict]:
    # Confidence = evidence ABOUT THE ENTITY. Proximity is NOT here (it is the Nearest axis §3
    # + a ranking tiebreak §10) — keeping it out avoids double-counting.
    f = {
      "source_tier":     source_weight(v.best_label),          # ofac>curated>tagpacks>heuristic ∈[0,1]
      "deposit_basis":   deposit_basis(v.endpoint),            # labeled=1.0, sweep-proven=0.7, none=0.3
      "value_relevance": min(1.0, log10(v.total_usd)/6),       # dust≈0, ≥$1M≈1  (log-normalized)
      "temporal":        time_coherence(v.paths),              # ∈[0,1], tight window → high
    }
    penalty = mixer_penalty(v) + bridge_penalty(v) + dex_penalty(v)   # each ∈[0,~0.3]
    idx = round(100 * clamp01(sum(W[k]*f[k] for k in f) - penalty))
    return idx, f          # breakdown returned verbatim to the UI
```

Discipline: every factor ∈ [0,1] and explainable in one sentence; cap ~5 factors (more knobs
with no ground truth = overfitting theater); **weights `W` are hand-set expert priors, FROZEN
before the golden set runs** (§11.2). Present the number as **"87 / 100 attribution confidence,"
never "87%."** The function is the clean drop-in point for a trained model *once labeled
operational data exists* — the concrete future-work item.

### 11.2 Evaluation harness — the honest metric (train-on-test fix)

- **Golden set** = ~15 **unlabeled suspect addresses 3+ hops from a documented VASP endpoint**
  (from sanctions listings, DOJ/court filings, forensic writeups). This tests *discovery*, not a
  0-hop OFAC lookup (which only proves the DB loaded). Include **confusers**: paths ending at
  non-VASP labels, and paths passing *through* an exchange (must not crown the pass-through).
- **Weights are frozen before the run.** Report the 15 cases as a **held-out smoke test**,
  verbatim: *"on 15 publicly documented cases, the true VASP ranked #1 in M."* Never "M/15
  accuracy," never a probability. n=15 → wide CIs; say so.
- **The defensible metric is rank stability under ±20% weight perturbation** — do the rankings
  survive? That, not the raw count, answers "is this calibrated?"
- **Offline fixture mode** (`eval run --offline`, from stored raw responses) = the reproducibility
  demo. **Per-case telemetry** (API calls, wall-clock, cost) = the direct evidence for the PS's
  "reduce investigation time" goal.

Keep this proportionate: freeze + smoke test + perturbation. Do **not** build a cross-validation
apparatus for a 14-day project.

---

## 12. RELIABILITY INVARIANTS

- **Determinism (with versioning).** Pin per case: **block snapshot + label-set version/date +
  adapter version + weight-profile hash** — print all four on the PDF. Score = pure function of
  (stored raw responses, pinned label set). *Why the label version matters:* Tornado Cash was
  sanctioned 2022 and delisted 2025 — the same address flips sanctioned→clean by OFAC date,
  changing the penalty and the score. Pinning only the block snapshot leaves it non-deterministic.
- **Storage & hashing.** Store raw responses content-addressed (budget the storage);
  **canonicalize before hashing** (stable key order, strip volatile headers); apply the
  deterministic truncation rule (§9.1). Fetch-all → **discard `block > snapshot`** client-side
  (pagination race).
- **Idempotency.** `MERGE` writes with the correct edge key (§7.2); Celery tasks safe to retry.
- **Job state machine.** `QUEUED → FETCHING(hop k) → SCORING → DONE | FAILED`, resumable/
  restartable.
- **Reproduce-and-verify.** Regenerate a report from `audit_log` inputs, recompute the response
  hash, compare — equal ⇒ verified. Scope the claim: reproducible **from the store**; re-*fetch*
  depends on provider stability. It's **provenance & reproducibility, never legal chain of
  custody** (§18).

---

## 13. EXPLAINABILITY, PROVENANCE & FRONTEND

The graph gets attention; the evidence panel wins the argument. Components:

- **TraceForm** → posts a wallet, gets a `trace_id`, polls `/trace/{id}/status`.
- **GraphView** (Cytoscape.js) — the centerpiece: hop-by-hop animated reveal, amounts on edges.
  **Render the data-model difference visibly** — BTC transactions as *rectangular `:Tx` nodes*
  between circular addresses; EVM as address→address arrows. It *proves* the distributed backend
  is working (not a screenshot) and makes the hypernode work visible.
- **Leaderboard** — ranked VASP candidates with scores + separation indicator.
  ```
  🥇 Binance   87 / 100
  🥈 Coinbase  63 / 100     separation: HIGH
  🥉 Kraken    41 / 100
  ```
- **RecommendedTarget** — the one crowned primary target + one-line rationale (§10).
- **NearestPanel** — hops, deposit tx, amount, timestamp — *separate* from confidence (§3).
- **ScoreBreakdown** — factor contributions behind the number.
- **ProvenanceCard** — `source · retrieved-at · tx hash · response hash · role basis ·
  confidence` — the I4C-friendly reproducibility surface + the deposit-event artifact (§6.4).
- **ReportButton** — opens the PDF, the artifact an investigator files.

Budget real time on GraphView (days 11); it dies *after* leaderboard/evidence cards, never before.

---

## 14. API SURFACE (FastAPI)

```
POST /cases                {wallet, chain}     → {case_id}                      201
POST /trace                {case_id}           → {trace_id}                     202 (async)
GET  /trace/{id}/status                         → {state, current_hop, progress}
GET  /trace/{id}                                → TraceResult
GET  /trace/{id}/timeline                       → per-phase timing (§16)
GET  /wallets/{addr}/score ?chain=              → {score, breakdown}
GET  /report/{id}                               → application/pdf
POST /sahyog/cases         {wallet, chain}      → {case_id}        # MOCK, OpenAPI-documented
POST /sahyog/disclosure    {disclosure_payload} → {request_id}     # MOCK — the §17 payload
```

```python
class TraceResult(BaseModel):
    trace_id: str
    state: Literal["ATTRIBUTED","BROKEN_AT_MIXER","BROKEN_AT_BRIDGE","BROKEN_AT_DEX",
                   "UNATTRIBUTED","ERROR"]
    candidates: list[Candidate]      # ranked; each: entity, confidence, breakdown, nearest{hops,tx,...},
                                     #                deposit_event, role_basis, provenance[]
    recommended: Optional[str]       # crowned entity id, or None if abstaining
    separation: Optional[str]        # HIGH | LOW | None
    flags: list[str]                 # e.g. ["mixer:tornado", "dex:uniswap_v3", "bridge:multichain"]
    pins: dict                       # snapshot_block, label_set_version, adapter_version, weight_hash
```

FastAPI auto-generates the OpenAPI contract; the `/sahyog/*` stubs are real and callable but
**never a live integration** — the documented contract *is* the SAHYOG deliverable.

---

## 15. SECURITY / PII POSTURE

- Secrets via environment only; none in the repo; `.env.example` documents them.
- `audit_log` append-only; per-case access token on the API (prototype-grade authz).
- **A wallet is not a person.** Attribution is to a *VASP*; identifying the human behind a
  deposit address is the VASP's KYC under a lawful SAHYOG request — not something the tool claims.
- Production needs real authn/authz, encryption at rest, a data-retention/DPDP policy — named as
  future work, not built.

---

## 16. OBSERVABILITY

Structured logs per trace; per-hop timing; `GET /trace/{id}/timeline` exposes the phase
breakdown (fetch / normalize / graph / score). Doubles as the demo's proof-of-real-work and
feeds the "reduce investigation time" telemetry (§11.2).

---

## 17. INDIA-FIRST & SAHYOG INTEGRATION

- **Curate labels for the 45+ SAHYOG-onboarded VASPs** — that set is both the routing target and
  the India answer, and it's a tractable curation job.
- **State the tool's job plainly:** it tells the LEA *which onboarded VASP* to send a lawful
  disclosure request to, under **BNSS §94 + IT Act §79(3)(b)**. Identifying the human is the
  VASP's KYC under that request — the tool provides the **investigative lead, not the identity,
  and not evidence.**
- **Seed the entity layer with FIU-IND-registered VASPs**; add one India-relevant typology (e.g.
  pig-butchering USDT-on-Tron cash-out) even if narrated rather than fully built.
- **SAHYOG payload mapping** — the report emits, and the demo routes through the mock on stage:
  ```json
  { "target_vasp": "...", "suspect_addresses": ["..."], "transaction_hashes": ["..."],
    "deposit_events": [{"tx":"...","amount":"...","ts":"..."}], "provenance": [ ... ] }
  ```
  Label the schema **illustrative** — the real SAHYOG schema is non-public; conformable under an
  MoU. Framing: the engine is the missing **routing layer** on top of SAHYOG's existing VASP
  connections.

---

## 18. LANGUAGE DISCIPLINE — claims that survive scrutiny

- **Provenance & reproducibility ≠ legal chain of custody.** State what production custody needs.
- **Attribution confidence ≠ probability ≠ ownership.**
- **Common-input-ownership heuristic (with a confidence) ≠ "same owner."** Note CoinJoin.
- **Prototype trace boundary ≠ untraceable.**
- **Risk = value-weighted exposure to sanctioned/mixer/bridge nodes + typology flags** — a
  *different* computation, **not** "same engine, different weights." Or cut it (PS: "may
  additionally support").
- **Never claim:** full six-chain coverage · chain-wide real-time monitoring · mixer
  de-anonymization · a trained-model accuracy figure without a validation set · live SAHYOG
  integration · load-tested blockchain-scale throughput.

---

## 19. Q&A DEFENSE

1. *"This is Etherscan plus a lookup table."* → ranking + entity resolution + **sweep evidence**
   + provenance; show ≥2 entities with real deposit addresses and the deposit-event artifact.
2. *"How do you know the exchange owns that address?"* → label provenance + **deposit-role basis**
   (labeled vs sweep-proven); conditional-on-label phrasing. **FREEZE RISK #1.**
3. *"Is your confidence calibrated?"* → index, not probability; frozen priors; **perturbation
   stability**; abstain behavior. **FREEZE RISK #2.**
4. *"Why does an Indian tool run on the US OFAC list?"* → OFAC is only the *sanctioned* layer;
   deposit labels are curated for the **SAHYOG-onboarded** VASPs; the tool routes to
   Indian-accessible exchanges under BNSS §94.
5. *"Why no Tron, when USDT-on-Tron is the fraud rail?"* → two data models (UTXO/account) first;
   Tron labels seeded; adapter interface shown; **prioritized next chain**; acknowledged honestly.
6. *"What happens at a DEX / mixer / bridge?"* → the service-node policy (§9); we distinguish them.
7. *"Can you trace through a mixer?"* → no, and no one reliably can; we detect + flag + drop
   downstream confidence. Never claim to de-mix.
8. *"Have you integrated with SAHYOG? Is this evidence?"* → documented mock contract, never live;
   output is an **investigative lead** under BNSS §94 / IT §79(3)(b), not evidence; the VASP's KYC
   identifies the human.
9. *"How does it scale?"* → stateless API + queue workers, bounded depth, normalized stored data;
   **architected for horizontal scale, not load-tested at blockchain volume** — explicit future work.
10. *"429 on stage?"* → graceful degradation to cache; partial result, not a crash.
11. Monero/privacy coins → out of scope, untraceable by design. Backward/source tracing → future
    (we trace forward: suspect → deposits). Dust/address-poisoning → `min_value_threshold`; named.
    Post-snapshot funds → re-run with a new snapshot.

Rehearse one owner per freeze-risk question, defending without notes.

---

## 20. THE DEMO

- **Case A (flagship):** unknown wallet → documented (or sweep-proven) **deposit address** of a
  **SAHYOG-onboarded** VASP; ends by routing the disclosure payload through the SAHYOG mock.
- **Case B (the mature moment):** `BROKEN_AT_MIXER` or `UNATTRIBUTED` — show the candidate you
  *refused* to crown and why. Abstention reads as discipline.
- **Divergence case** where nearest ≠ top-confidence — or drop the split panel (don't show a
  split that always coincides; it reads as rhetoric).
- **60-second live second-chain vignette on Polygon** (free) — turns "multi-chain" from claim to
  fact; else narrate the config-swap to BSCTrace as the abstraction paying off.
- **Colonial Pipeline = internal typology/mixer test fixture only.** Its endpoint is an FBI
  seizure address, not a VASP; it does not serve the headline. One sentence if asked ("we
  reproduce the public trace from free data as a validation fixture"); **zero demo minutes.**

**Staging:** open on a bare wallet (what an investigator has today) → enter it live, real
progress indicator (not a spinner) → graph builds hop-by-hop → arrive at the leaderboard +
recommended target + evidence breakdown → generate the PDF → route to the SAHYOG mock. Pre-cache
Case A and the eval run (thin Blockchair/Esplora + 3 req/s quotas). Keep one short fully-live
example and a full screen-recording backup. Rehearse broken-Wi-Fi mode.

---

## 21. SCOPE LEDGER (B = build · S = scope-down · F = frame/roadmap)

| Component | Status | Note |
|---|---|---|
| Trace-to-VASP graph engine (data-model-aware) | **B** | core |
| **Label & entity subsystem + sweep detection** | **B** | the center (§6); sweep likely primary deposit signal |
| Candidate ranking + leaderboard + recommendation | **B** | replaces shortest-path-only |
| VASP/entity registry + resolution | **B** | |
| EVM adapter (native+internal+erc20, correct edge identity) | **B** | ETH + Polygon |
| BTC adapter (Esplora) + tx-hypernode + change detection + clustering | **B** | |
| Confidence scoring (frozen priors, proximity excluded) | **B** | 0–100 index + breakdown |
| Determinism (versioned) + idempotency + provenance | **B** | reliability layer |
| Evaluation harness + golden set + perturbation + offline fixture | **B (small)** | the honest metric |
| Reports (HTML→PDF) + SAHYOG payload mapping | **B** | WeasyPrint |
| Service-node policy: mixer/bridge/DEX/hot + 6 states | **S** | flag/boundary; no de-mix; no cross-chain follow |
| Global rate limiter + 429→cache | **B** | demo survival |
| BNB via BSCTrace (JSON-RPC) | **S** | or paid Lite; config swap |
| Risk score (exposure-based) | **S/optional** | last; PS says "may additionally support" |
| Peeling-chain typology | **S** | time-boxed; **first to cut** (keep change detection) |
| SAHYOG intake + disclosure | **F** | documented mock contract |
| Tron / Solana adapters | **F** | interface shown; **Tron = first stretch (fraud rail)**; labels seeded |
| Security/authz, scale, streaming, learned scoring | **F** | named future work |
| Colonial Pipeline | fixture | test only, zero demo minutes |

---

## 22. 14-DAY BUILD PLAN — with explicit cuts & tripwires

**Cuts to keep scope flat** (reallocated to load-bearing): Colonial → fixture; peel → first to
cut (**keep change detection**); risk score → optional/last; full Tron → deferred (labels-first).

| Day | Focus |
|---|---|
| 1 | Compose + keys (**key-test Etherscan rate + Polygon-free**). `day1_verify`: Case A traces on **free historical data** (Esplora/ETH) **AND** endpoint is a **documented deposit address or sweep-provable**. Lock Case A + Case B. Call-counter + timer + written per-case budget. **Inspect GraphSense TagPacks (§1 OPEN). Confirm SIH finale format.** |
| 2 | BTC adapter (mempool.space→Blockstream failover) + `:Tx` hypernode + **change detection** + common-input → `SAME_OWNER`. |
| 3 | **Label & Entity subsystem** (first-class day): VASP registry, entity resolution, OFAC (Treasury) + SAHYOG-set curation, propagation over `SAME_OWNER`. |
| 4 | EVM adapter (ETH+Polygon; correct event-log edge identity; conditional fetch) + **sweep-to-hot deposit evidence**. |
| 5 | Neo4j both models + `MERGE` (correct key) + candidate enumeration + `rebuild_graph.py`. **Fallback checkpoint → NetworkX+Postgres.** |
| 6 | Celery-chord BFS + **global rate limiter + 429→cache** + snapshot/label-version pinning + idempotency. |
| 7 | Attribution engine: candidate → evidence vector → entity aggregation (max/top-k) → confidence (**no proximity**) + separation + recommendation layer. |
| 8 | Scoring normalization + **frozen weights**; golden set (discovery + confusers) + eval CLI + offline fixture + perturbation. Service-node policy (mixer/bridge/DEX/hot). Peel *only if ahead*. |
| 9 | FastAPI endpoints + SAHYOG mock + **payload mapping** + provenance surface + `/trace/{id}/timeline`. |
| 10 | React: case list, form, wired. |
| 11 | GraphView animated reveal **with visible hypernode** + leaderboard + recommended target + **nearest-vs-confidence split** + evidence/provenance cards. |
| 12 | Reports (WeasyPrint) + reproduce-and-verify + **route-to-SAHYOG on stage** + polish. |
| 13 | Full rehearsal both cases + **provider-down/429 drill** + pre-cache demo **and** eval + clean `docker compose up`. |
| 14 | Q&A rehearsal (owner per freeze-risk question) + backup video + freeze. |

**Tripwires:** D5 Neo4j fallback decision; D8 peel dies if behind; animated reveal dies *before*
leaderboard / evidence cards / eval / provenance ever do; **eval, determinism, and provenance are
never cut.** If the team is junior on async Python/Neo4j, move every tripwire a week earlier —
the plan assumes the plumbing already runs.

---

## 23. THE PITCH + THE BOUNDARY

> **"Unlike a blockchain explorer that shows you transactions, this performs evidence-weighted
> entity attribution over transaction graphs: it returns the nearest deposit-accepting VASPs,
> ranked, each with an auditable reason and an honest confidence — and it abstains rather than
> guess. It tells an Indian investigator which SAHYOG-onboarded exchange to serve under BNSS §94."**

```
┌──────────── 2-WEEK MVP ─────────────┐        ┌──────────── PRODUCTION ─────────────┐
│ BTC (UTXO) + ETH/Polygon (account)   │        │ Tron (fraud rail) / Solana / more    │
│ Label & entity subsystem + sweep     │   →    │ Learned scoring on real labels       │
│ Candidate ranking + recommendation   │        │ Cross-chain correlation              │
│ Evidence scoring + eval harness      │        │ Streaming / chain-wide intelligence  │
│ Determinism + provenance             │        │ Real SAHYOG integration, at scale    │
│ Reports + SAHYOG mock + payload map  │        │ Enterprise authz / retention / DPDP  │
└──────────────────────────────────────┘        └──────────────────────────────────────┘
```

Build a small, real, honest attribution engine — not a large, fake, Chainalysis cosplay.

---

## 24. HONEST SCORE TRAJECTORY + DAY-1 GATES

Independent review put the pre-consolidation plan at ~7–7.5/10. With the label subsystem + sweep
evidence (which actually earns "deposit-accepting"), the frozen-weights eval, and the
India/SAHYOG reframing, the ceiling is **~9 — but only if the three day-1 gates pass:**

1. **Case A locks:** an unknown wallet that, on free historical data, reaches a documented
   deposit address (or sweep-provable one) of a SAHYOG-onboarded VASP. If not by end of day 2,
   fall back to the "nearest attributable VASP" headline. *This is the single binding risk.*
2. **TagPacks inspected:** if entity-tags-only/Western-skewed, sweep detection (§6.2c) is the
   primary deposit mechanism — build it first.
3. **SIH format confirmed:** pre-build-and-present vs on-site build changes the whole schedule.

Honest current state: **a strong 8.5 once those gates pass and the P0s close; a graph explorer if
Case A never locks.** The label subsystem (§6) is the difference between those two outcomes.

### Gate status — day 5 is formally unrun, by decision (2026-09-13)

`scripts/day5_graph_check.py` is **deliberately not run**, and this is the record of why rather than
a loose end.

The gate's purpose is to prove the graph write layer — including the `merge_edges` / `merge_txs`
batching. It asserts `n_pg == btc_n + evm_n`, which only holds on an empty table, so it opens with
`TRUNCATE trace_edge, edge, evidence, utxo_tx`. Running it therefore destroys the multi-victim
convergence data (§8) that the Lazarus set demonstrates, and nothing re-populates those 8 ETH
subgraphs except spending Etherscan quota to re-trace them.

**The substitute proof is stronger than the gate.** After the day-13 hardening fixes, a full
`scripts/rebuild_graph.py` pushed **5,200 edges (1,749 BTC + 3,451 ETH) with 0 Neo4j errors**
through the new 500-row batching — and that 3,451 includes the exact **2,935-edge wallet**
(`0x098B716B8Aaf…`) whose single oversized write caused the original `Response write failure` and
killed a chord. The gate would have proven batching on synthetic fixtures; the rebuild proved it on
the real failure case, at larger volume.

So: batching is verified, the formal gate stays unrun, and re-running it would trade real demo data
for a re-proof of something already established. Do not run day 5 to make a checklist green.
