# SIH26182 VASP Attribution Engine — pre-launch audit

Audited 2026-09-17/18 against `architecture.md` (spec) and `DEMO.md` (stage plan), at commit
`cf05f72`. Reviewer stance: skeptical, nothing assumed working until observed.

**Upstream API calls spent: Etherscan 0 · Blockstream 0 · mempool.space 0.** Every trace run
during the audit used `offline=True`; every `POST /cases` passed an explicit `snapshot_block`
with `dispatch:false`. `scripts/day5_graph_check.py` was NOT run.

Phase 4 (browser) was NOT run — the Claude-in-Chrome extension was not connected. Frontend
findings below are from code, API responses and the build, never from a rendered page.

---

## 0. Status after remediation (2026-09-18)

Everything below the line was found in the audit pass. This section records what has since been
fixed in the code and what is still open. Verification after the fixes, on the rebuilt stack:

| Check | Result |
|---|---|
| hostile-input sweep (`p2_api.py`, 52 assertions) | **52 PASS / 0 FAIL** (was 35 PASS / 17 FAIL) |
| invariant suite (`p3_logic.py`) | **19 / 19 PASS** (was 11 / 19) |
| offline golden-set eval | 5 of 5 discovery correct, 6 of 6 scored, **2 not scored** (see F28), 0 upstream — **superseded 2026-09-26, see §0.1** |
| §12 reproduce-and-verify | **VERIFIED**, exit 0 — `python -m app.eval.verify <trace_id>` |
| report determinism | same hash on repeated renders (`419c6f04…`) |
| `audit_log` UPDATE / DELETE | both rejected by trigger |
| backend lint (ruff F,E9,B,I) + import smoke + 12 self-checks | clean / all pass |
| `npm run lint` · `tsc -b` · `npm run build` | all pass (build was broken) |
| stack | 6/6 healthy; frontend healthcheck now goes through `/api/health` |
| Lazarus convergence | unchanged: 7/7 Tornado Cash, 120,200 ETH |
| DB after cleanup | baseline exactly: cases 77, edge 5,200, trace_edge 35,510, utxo_tx 102 |

### 0.1 · Update 2026-09-26 — store completed, drivers unified

- **F28 closed.** The two unscored confusers' reads were fetched once (`harness --online`, 210
  upstream) and now replay offline: both CORRECT.
- **F12 closed.** `collect_all` is the API default. Per-task cost was the Neo4j driver built per
  node and one upsert round trip per edge; one driver per worker + a batched `unnest` upsert took a
  warm full walk to 2.2–6.4s per golden case (was 1901s on Case B).
- **New finding — the chord driver's budget accounting diverged from the sequential reference.**
  Cold per-task memos re-counted reads and hits were stamped with the level-END total, so Li Jiadong
  crowned Binance live while the harness abstained. `level_done` now walks results in address order,
  counts only reads the trace-wide memo has not seen, and stamps each hit with the running count.
  `scripts/parity_check.py`: **8 of 8 cases, live == harness** on state, target and call count.
- **Headline moved: 7 of 8 decided as documented (4 of 5 discovery, 3 of 3 confusers), 0 wrong.**
  With the store complete, Li Jiadong reaches a second sweep-proven candidate (Bitfinex 64 at 4 hops
  vs Binance 70 at 3) and abstains on separation 6 < τ 10. Weights were NOT touched. This is also the
  first contested golden case, so rank stability now has a denominator that can move (F23).
- `/benchmark`, `/trace/{id}/techniques`, `/trace/{id}/stream` (SSE) added; `/graph` now puts the
  candidates' path edges ahead of its 1200-edge render cap (Li Jiadong's path was being cut).

**Fixed:** F01, F02, F03, F04, F06 (partly — see below), F07, F08, F09, F10, F11, F13, F14, F15
(partly), F16, F17, F18, F19, F21, F22, F25, F26, F27, F29.

**Open, deliberately:**

| ID | Why it is still open |
|---|---|
| F05 | Needs a real deposit from you to WazirX/KuCoin (~68 Etherscan calls). DEMO.md beat 7 now states the WazirX branch is a contrast lookup, not a case that fired. |
| F15 (half) | Per-case access token NOT built. Mitigation shipped: api and neo4j bind to `127.0.0.1`, `audit_log` is trigger-enforced. Anyone who reaches the port still reads every case. |
| F20 | The 2,935-edge wallet exists only as a FAILED trace. Re-tracing costs Etherscan quota. |
| F24 | Needs a new golden fixture (a dated Tornado sanctioned→delisted pair). F23 is met by Li Jiadong since 2026-09-26. |
| F28, F30 | New findings from this pass — below. |
| Phase 4 | The browser pass never ran (extension not connected). All frontend claims remain code-level only. |

### F28 · P1 · Eval — two golden confusers were passing on data that was never stored
Exposed by the F13 fix. In offline mode a store miss raises, which now surfaces as `INCOMPLETE`
instead of being flattened into `UNATTRIBUTED`:
- `bitfinex-2016-unnamed-services` misses the store on its **first read at hop 0** (`calls=1`) — it
  traced nothing at all, yet scored CORRECT because `recommended is None` matched `expect:
  UNATTRIBUTED`. A pass on zero evidence.
- `hydramarket-coinjoin-confuser` does reach its two CoinJoin boundaries but a hop-3 branch is
  unstored at the golden snapshot (966553).

The harness now refuses to score an `INCOMPLETE` case, so the honest headline is **6 of 6 scored,
2 not scored**, not 8 of 8. Fixing it means storing those reads at the golden snapshot (provider
quota) or re-pinning those cases. Demo Case B at the *demo* snapshot (966946) is unaffected:
re-verified `UNATTRIBUTED`, 0 upstream.

### F29 · P2 · Performance — `PgRegistry` re-queried ~4k entity rows per Celery task
Measured 1.27s per construction, paid once per frontier node (and again per chord callback), which
is what made a full-frontier trace take half an hour. Entity rows for a pinned label-set version
are immutable by construction, so they are now cached per process; each Registry still builds its
own objects and its own overlay, so no trace can see another's derived labels.
**0.78s → 0.07s** per construction, isolation verified.

### F30 · P2 · Data — `trace_edge` has no foreign key, and 40% of it is orphaned
`trace_edge.trace_id` references nothing, so rows survive the deletion of their job — and a chord
that is redelivered after its job row is gone writes fresh orphans (reproduced during the F04 kill
test: 189 rows under a deleted trace). The table currently holds **14,360 orphan rows across 40
dead trace_ids**, ~40% of 35,510. They are inert (every query filters by an explicit `trace_id`)
but they inflate the table and would silently corrupt any future "all edges this trace saw" query.
Fix: add `trace_id uuid REFERENCES trace_jobs(id) ON DELETE CASCADE` after a one-off cleanup — the
cleanup deletes real rows, so it needs your go-ahead.

## 1. Verdict — 6 / 10 (as found, before the fixes in §0)

The engine core is real and reproducible. The offline eval reproduces `day8_eval_results.json`
exactly (8/8 cases, 0 field diffs excluding `wall_s`, 0 upstream calls, 24.0s). An offline Celery
replay of Case A at its pinned snapshot reproduces its stored result field for field. Snapshot
discipline held on every trace sampled. Weights are genuinely frozen.

Three things hold it back:

1. **The demo path is not the path that was proven.** A clean `docker compose up --build` yields a
   UI whose every API call 500s (F01) — it works today only because a stale 2026-09-11 frontend
   image is still running. The UI pins the *live chain tip* and hard-codes `chain: 'btc'`, so a
   stage run goes live against providers (F02; the last Hydra run made 15 upstream calls). If the
   worker dies mid-trace the job spins forever (F04).
2. **The calibration and separation story rests on numbers that cannot fail.** Every golden case
   has ≤ 1 candidate, so "160/160 rank-stable", the on-stage "12/12", and "separation HIGH
   (70 pts)" are true by construction, not by evidence (F03).
3. **The SAHYOG headline does not exist** (F05: 0 ground-truth labels; the WazirX "portal fires"
   branch is a registry lookup for an entity no trace reached), and several logic defects would
   lose a hostile Q&A (F07 weak hot-wallet hit crowned as ATTRIBUTED, F08 cross-path factor
   mixing, F10 ERC-20 edge collapse).

With §0 applied, the code defects in (1) and (2) are closed and the claims are reworded to match
what the evidence supports. What still caps the score is data, not code: no controlled-deposit case
(F05), no golden case with competing candidates (F23), two confusers that cannot be served offline
(F28), and a frontend that has never been driven in a browser during this audit (Phase 4).

---

## 2. Findings

Severity: **P0** demo-breaking · **P1** wrong result · **P2** degraded · **P3** polish.

### F01 · P0 · Ops — compose never sets `API_URL`; the rebuilt frontend cannot reach the API
- **Where:** `docker-compose.yml` (`frontend:` service, no `environment:`), `frontend/vite.config.ts:10`
  (`const target = process.env.API_URL || 'http://127.0.0.1:8000'`), whose own comment claims
  "API_URL is set to http://api:8000 by compose" — it is not.
- **Repro:** `docker compose up -d --build frontend` then `curl -s -w '%{http_code}' localhost:5173/api/health`
- **Evidence:** `HTTP 500`; frontend log: `[vite] http proxy error: /health  Error: connect ECONNREFUSED 127.0.0.1:8000`.
  The container that has been serving for 3 days was built 2026-09-11 and has no proxy at all
  (`vite.config.ts` inside it is the bare template); all 15 `frontend/src` files differ from HEAD.
  `/api/health` returned 200 on the stale image only because of Vite's SPA fallback (content-type
  `text/html`). The healthcheck only fetches `/`, so the service reports **healthy** while every
  API call fails.
- **Fix:** add `environment: {API_URL: http://api:8000}` to the frontend service; change the
  healthcheck to `wget -qO- http://127.0.0.1:5173/api/health`.
- **Effort:** S

### F02 · P0 · FE/Demo — the UI pins the live tip and hard-codes btc, so the stage run is live
- **Where:** `frontend/src/components/TraceForm.tsx:26` and `:37` (`createCase({ wallets, chain: 'btc' })`,
  no `snapshot_block`); `backend/app/main.py:110` (`snapshot = req.snapshot_block or Tracer(req.chain, 10**9).until`).
- **Repro:** press "golden cases →" in the UI.
- **Evidence:** the two demo traces are pinned at snapshot **966946**, while the golden set and raw
  store are keyed to **966553** (`labels/golden_set.yaml`, `eval/harness.py:37`). Store scope is
  `snapshot:<block>`, so a new tip means new keys and store misses. Stored telemetry: Hydra trace
  `746b1d0a` — `api_calls 57, upstream_calls 15`; several Wu Huihui runs — `upstream 12`. DEMO.md
  §1 claims "zero upstream calls" and §4 "Wi-Fi dies → Irrelevant".
  Also: an ETH address typed into the form is traced as btc, and ETH/Polygon are unreachable from
  the UI even though both adapters exist.
- **Fix:** golden buttons should open the stored trace (or POST with the golden `snapshot_block`);
  add a chain selector; consider an `offline` flag on `POST /cases` for stage use.
- **Effort:** S

### F03 · P0 · Claims/Logic — separation and perturbation are vacuous on every real case
- **Where:** `backend/app/attribution/recommend.py:28` (`gap = top["score"] - ranked[1]["score"] if len(ranked) > 1 else top["score"]`),
  `:29` (`ambiguous = len(ranked) > 1 and gap < tau`); `DEMO.md:63,73-76,105`; `backend/app/api/report.py`
  ("Separation … pts between top two").
- **Repro:** `scratchpad/p3_logic.py`; `scripts/day8_eval_results.json`; the stored demo trace.
- **Evidence:**
  - Every one of the 8 golden cases has `ranked` of length 0 or 1: discovery cases are
    `[['binance', 69]]`, `[['binance', 70]]`, `[['bitfinex', 59]]`…, confusers are `[]`.
  - A 1-candidate trace keeps top-1 across 200 profiles at ±90% perturbation (0 flips). So
    "rank stability … 160 of 160 case-profiles" and the stage line "12/12" cannot fail.
  - For comparison, a hand-built 2-candidate near-tie flips top-1 in **4/20** ±20% profiles — the
    metric does discriminate when there is anything to discriminate.
  - Single-candidate demo A reports `separation HIGH (70 pts)`, i.e. the gap is its own score. The
    PDF prints "Separation HIGH (70 pts between top two)" with one row in the table.
  - `AMBIGUOUS` and the abstain path have never fired on real data.
- **Fix:** `separation`/`separation_pts` = `null` when `len(ranked) < 2`, and have the UI/PDF say
  "single candidate — no separation to report"; rewrite DEMO beats 2 and 5 and Q&A #2 to state
  what perturbation actually shows; add ≥ 2-candidate golden cases (see F28/path item 10).
- **Effort:** S (honesty) / M (cases)

### F04 · P0 · Ops — a worker killed mid-trace leaves the job FETCHING forever
- **Where:** `backend/worker/celery_app.py:8-13` — `task_acks_late=True` with no
  `broker_transport_options`, so Redis' default `visibility_timeout` of 3600s applies; the
  `trace_failed` errback only fires on a task *exception*, never on a lost worker.
- **Repro:** dispatch offline; wait for `current_hop:2`; `docker compose kill worker`;
  `docker compose up -d worker`; poll `/trace/{id}/status`.
- **Evidence:** trace `82feca6d-517c-4767-bb50-e96706a6a5ff` stayed
  `{"state":"FETCHING","current_hop":2,"progress":0.25,"error":null}` for 3 min with no worker and
  a further 8 min with a healthy worker (`worker=Up 8 minutes (healthy)`). No resume, no FAILED.
  (First run of this test was invalid — `docker compose start worker` had failed silently with its
  output redirected; re-run with `up -d` gave the result above.)
  `frontend/src/api/client.ts:320` gives up only after 600 s, so the stage shows a 10-minute spinner.
  DEMO.md §4 claims "It lands FAILED with the real cause in the row … Never a spinner."
- **Fix:** `broker_transport_options={'visibility_timeout': 120}`, `task_reject_on_worker_lost=True`,
  and a sweeper that marks jobs FAILED when `started_at` is old and no task is in flight.
- **Effort:** M

### F05 · P0 · Claims — the controlled-deposit / portal-route case does not exist
- **Where:** `backend/app/api/report.py::_routes` (contrast branch), `frontend/src/components/ReportButton.tsx:13`
  (`const CONTRAST = 'wazirx'`), `DEMO.md:80-82` (beat 7: "WazirX (IN, onboarded) fires the SAHYOG
  portal under BNSS §94"); `backend/app/labels/ingest.py:8` references
  `labels/ground_truth_deposits.yaml`, which is not in the repo.
- **Repro:** SQL against the pinned label set `ls-56616bc26d3d`.
- **Evidence:** `basis='ground_truth'` → **0 rows** (only `exchange_published_deposit`, 336,104).
  Onboarded entities carry infrastructure only: `wazirx eth hot 1`, `wazirx polygon hot 1`,
  `kucoin eth hot 27`, `bybit btc hot 4`, `bitget eth token 1` — no deposit-role address, nothing
  on BTC for WazirX. Rendered PDF: `WazirX (contrast branch, sahyog: confirmed, jurisdiction: IN)
  ROUTABLE — SAHYOG portal (BNSS S.94 …)` while the case itself reads
  `Binance (this case, sahyog: unknown) NOT ROUTABLE`. The UI does the same via `onboarding('wazirx')`.
  Per project state this is a demo blocker, and the UI cannot trace ETH at all (F02), which is the
  only chain where WazirX has any label.
- **Fix:** either build the controlled deposit (your own deposit to WazirX/KuCoin, ~68 Etherscan
  calls, plus a `ground_truth` label), or reword beat 7 to "this is the route an onboarded VASP
  takes — no case in this demo reached one".
- **Effort:** L (build) / S (reword)

### F06 · P0 · Ops — a judge who clones the repo cannot run it
- **Where:** repo root (no README), `backend/app/main.py` (no startup hook calling `init_schema()`),
  `backend/app/db/schema.sql:2` ("Idempotent … so `init_schema()` runs on every start" — nothing
  calls it), `.gitignore:8` (`vendor/`), `docker-compose.yml` (`env_file: [.env]`).
- **Evidence:** `grep -rn init_schema` finds callers only in `app/labels/ingest.py:233` and the day
  scripts. Labels need `vendor/graphsense-tagpacks` (44 MB) + `vendor/sdn_advanced.xml` (121 MB),
  both gitignored, and the label-set version hash is derived from them. All demo data (raw store,
  traces, label sets) exists only in the local `pgdata` volume — there is no seed dump. `.env` is
  required by compose, so `docker compose up` fails outright without it, and
  `EtherscanV2Provider.__init__` raises if the key is empty. `frontend/README.md` is the untouched
  Vite template. `app/trace/tasks.py:37` and `app/main.py:318` index `fetchone()[0]` unguarded, so
  an empty `label_set` table 500s.
- **Fix:** root README with a quickstart; call `init_schema()` on API startup; commit a seed
  `pg_dump` (or a `make bootstrap` that ingests); a `scripts/fetch_vendor.sh`; ship `.env.example`
  → `.env` guidance and a clear error when the label set is missing.
- **Effort:** M

### F07 · P1 · Logic — a weak hot-wallet-only hit is crowned, and the §6.3 downgrade is erased
- **Where:** `backend/app/attribution/engine.py:33-34`
  (`state = "ATTRIBUTED" if rec["recommended"] else "AMBIGUOUS" if … else trace.get("result", …)`),
  `backend/app/attribution/recommend.py:29-30` (no minimum score to crown).
- **Repro:** `scratchpad/p3_logic.py`, "separation" block: one candidate, `role='hot'`,
  `basis='labeled'`, `tier='heuristic'`, dust value, 300-day span.
- **Evidence:** result — `score 23/100, separation HIGH (23 pts), crowned='solo', state=ATTRIBUTED`.
  The trace-level hit was `ATTRIBUTED_INFRA` (engine.py:298), i.e. §6.3's explicit downgrade, and
  the attribution layer overwrites it with `ATTRIBUTED`. §6.3: "The engine **never** silently
  promotes a hot-wallet hit to 'deposit-accepting'."
- **Fix:** preserve `ATTRIBUTED_INFRA` as the state when `best_claim != "ATTRIBUTED"`; add a crown
  floor (e.g. abstain below ~50) so a lone weak endpoint is reported but not crowned.
- **Effort:** S

### F08 · P1 · Logic — the entity score mixes factors from different endpoints
- **Where:** `backend/app/attribution/aggregate.py:33-37` (per-factor `max` across the entity's
  candidates, then one `score()` over the mixed vector).
- **Repro:** `scratchpad/p3_logic.py`, "§10 aggregation": endpoint A = dust with same-day timing,
  endpoint B = 50 BTC with a 300-day span, same entity.
- **Evidence:** entity score **70** vs the best single real path **61**;
  `factor_from={'deposit_basis':'A','source_tier':'A','temporal':'A','dust_floor':'B'}`. The number
  on the leaderboard describes no path that exists. (The headline §10 property does hold: 50 dust
  paths score 59 vs 69 for one strong path, and corroboration saturates.)
- **Fix:** entity score = max over per-path scores (already computed as `per[i]["score"]`), keeping
  the per-factor maxima in the breakdown as diagnostics only.
- **Effort:** S

### F09 · P1 · Logic — the breakdown's penalty cannot be reconciled with its explanation
- **Where:** `backend/app/attribution/aggregate.py:36` (`penalty = max(p["penalty"] for p in per)`)
  vs `:43-44` (`penalties_applied` taken from the representative path).
- **Repro:** same fixture with a clean path and a mixer-tainted path for one entity.
- **Evidence:** `penalty=0.3, penalties_applied=[]` — the PDF and ScoreBreakdown show "−0.3,
  penalties applied: none".
- **Fix:** source both from the same path.
- **Effort:** S

### F10 · P1 · Data — the tokentx pagination dedupe collapses distinct ERC-20 edges
- **Where:** `backend/app/providers/etherscan_v2.py:164`
  (`k = (t["hash"], t.get("logIndex"), t.get("traceId"), t["from"], t["to"], t["value"])`) —
  runs in `rows()`, i.e. *before* `_erc20()` assigns its `contract:value:ordinal` identity. Since
  V2 `tokentx` no longer returns `logIndex`, that component is always `None`, and
  `contractAddress` is absent from the key entirely.
- **Repro:** `scratchpad/p3_logic.py`, "EVM edge identity" — stub `_get` returning two identical
  Transfer rows in one tx, then the same with two different tokens of equal value.
- **Evidence:** `_erc20` alone → 2 edges (`…:1000000:0`, `…:1000000:1`) **PASS**; through `rows()`
  → **1 edge**; USDT + USDC of equal value in one tx → `['USDT']` only. §7.2: "collapsing them
  understates flows and corrupts `value_relevance`."
- **Fix:** include `contractAddress` and an occurrence counter in the `rows()` key, or skip
  content-based dedupe for `tokentx` and dedupe only on the true page boundary.
- **Effort:** S

### F11 · P1 · Logic/Spec — `BROKEN_AT_MIXER/BRIDGE/DEX` are never emitted
- **Where:** `backend/app/attribution/engine.py:33-34` — the only states produced are
  `ATTRIBUTED`, `AMBIGUOUS`, and whatever the trace returned (`ATTRIBUTED` /
  `ATTRIBUTED_INFRA` / `UNATTRIBUTED`). `grep -rn BROKEN_AT backend/app` matches only a docstring
  in `boundary/bridge.py:1`. `ERROR` (§9.3) is likewise unused; the job state is `FAILED`.
- **Evidence:** Lazarus trace `e7f8b9df` — `state UNATTRIBUTED`, `reason "no further outgoing value"`,
  with flag `mixer:Tornado Cash@0xd90e2f925da726b50c4ed8d0fb90ad053324f31b(hop 2, tx 0x5f62…)`.
  All 15 ETH traces are `UNATTRIBUTED`. `frontend/src/components/CaseList.tsx:8` even styles
  `s.startsWith('BROKEN')`, so the UI was written expecting a state the backend never sends.
- **Fix:** when there are no candidates and a boundary flag exists, derive
  `BROKEN_AT_MIXER|BRIDGE|DEX` from the flag kind (nearest hop wins).
- **Effort:** S

### F12 · P1 · Logic — the shipped chord path is a different algorithm from the evaluated one
- **Where:** `backend/app/trace/tasks.py:176` (`done = bool(state["hits"]) or …` — stops at the
  first hop level that produced any hit) vs `backend/app/eval/harness.py:53-54`
  (`collect_all=True`) and `backend/app/attribution/engine.py:4-7` ("The difference from a plain
  trace: the BFS does NOT stop at the first labeled endpoint"). Also `tasks.py:221-223`
  (`_tracer(ctx)` builds a **fresh** `PgRegistry` in every task and in every `level_done`), so the
  sweep-proven and cluster-propagated labels added to `reg` inside `expand_task` are discarded
  when that task returns.
- **Evidence:**
  - `SELECT count(*) FROM evidence WHERE trace_id='3649ade8-…'` → **0**, although that trace's
    crowned endpoint is `sweep_proven`. `derived` in `level_done:187` therefore persists nothing,
    and `rebuild_graph.py` cannot re-attach those labels.
  - `propagate()` at `tasks.py:173` runs against a registry with no overlay, so trace-derived
    labels never propagate over `SAME_OWNER` in the shipping path, and `SAME_OWNER` is never
    *traversed* by BFS at all (§7.4 requires traversal with a hop penalty).
  - Consequence for §11.2: the eval measures `collect_all=True` + persistent registry; the product
    ships neither. On the two cases where both paths exist the outcome happens to agree (offline
    chord replay of Case A == stored == eval), which is why this is invisible.
- **Fix:** carry the overlay labels in the chord `state` dict (they are JSON-able) and rehydrate
  them in `_tracer`; make the chord honour `collect_all` (or run the eval through the chord
  driver) so one algorithm is both measured and shipped.
- **Effort:** M

### F13 · P1 · BE/FE — a degraded provider produces an "honest abstention" that is really data loss
- **Where:** `backend/app/trace/engine.py:58-61` (ProviderError → `partial:provider_unavailable`
  flag, `stop=True`), `:278-280` (no hits → `result: "UNATTRIBUTED"`);
  `frontend/src/components/RecommendedTarget.tsx:10-27` renders "No target crowned — UNATTRIBUTED …
  The trace stopped at a boundary rather than guessing past it", and `Leaderboard.tsx:15-18` adds
  "An empty leaderboard is a result, not a failure". Neither reads `r.partial`.
- **Evidence:** code path (not executed live — it needs provider failure or quota). The only signal
  is the HUD badge at `HUD.tsx:32-36`. §8 requires "serve from cache and mark the result partial";
  the marking exists but the verdict wording contradicts it.
- **Fix:** distinct state (e.g. `INCOMPLETE`) when `partial and not hits`; the panels must say
  "incomplete — provider unavailable", never "abstention".
- **Effort:** S

### F14 · P1 · Reproducibility (§12) — reproduce-and-verify does not exist and the report is not pinned
- **Where:** `backend/app/main.py:421` + `api/report.py::build_pdf` (WeasyPrint embeds a creation
  timestamp; the hash is taken over the PDF bytes); `api/report.py:79` (`reg = reg or PgRegistry()`
  → **latest** label set, not `pins.label_set_version`), same for
  `api/sahyog.py:34` (`r = reg or PgRegistry()`); `trace/tasks.py:137`
  (`upstream_hash = sha256("\n".join(t.prov.requests))` — a hash of request *strings*, not of
  upstream response bodies); `trace/tasks.py:39` (`"adapter_version": "day6"` hard-coded).
- **Repro:** `curl -sD - localhost:8000/report/3649ade8-… -o /dev/null | grep -i x-content-hash` twice.
- **Evidence:** `7dd3454dd0d0c236…` then `27536f22d4068670…` for two identical renders. No endpoint
  or script regenerates a report from `audit_log` and compares (`grep -rn verify` finds nothing).
  So §12's "Regenerate a report from `audit_log` inputs, recompute the response hash, compare" is
  unimplemented and, as built, impossible. Also: a trace pinned to `ls-56616bc26d3d` renders its
  routing/entity names from whatever label set is newest.
- **Fix:** deterministic PDF metadata; hash the canonical HTML (or the result JSON) instead of PDF
  bytes; pass `PgRegistry(pins["label_set_version"])` in the report and SAHYOG paths; store the
  content hash of each upstream *body* in `audit_log`; derive `adapter_version` from git.
- **Effort:** M

### F15 · P1 · Security (§15) — no per-case token, mutable audit log, exposed Neo4j
- **Where:** `backend/app/main.py` (no auth dependency anywhere), `backend/app/db/schema.sql:96`
  (`audit_log … -- append-only`, comment only), `docker-compose.yml` neo4j
  `ports: ["7474:7474","7687:7687"]` with `NEO4J_AUTH: neo4j/password123`, api
  `ports: ["8000:8000"]`.
- **Repro / evidence:**
  - `GET /report/{any trace_id}` with no credentials → `200 %PDF-1.7`. Any trace id from any case
    is readable; there is no case scoping to violate because none exists.
  - `BEGIN; UPDATE audit_log SET actor='tampered' WHERE id=(SELECT min(id)…); DELETE FROM audit_log
    WHERE id=(SELECT max(id)…); ROLLBACK;` → `UPDATE 1`, `DELETE 1`, tampered count 1.
    `pg_trigger` (non-internal) = 0, `pg_rules` = 0, and the app role `vasp` is `rolsuper = t`.
  - CORS: no middleware, no `Access-Control-Allow-Origin` header, `OPTIONS /cases` → 405. That is
    fine (the Vite proxy is same-origin) — scoped by absence, worth stating rather than claiming.
  - Postgres and Redis are bound to loopback (good); the API and Neo4j are on all interfaces, so
    anyone on the venue Wi-Fi can read every case, POST traces that burn provider quota, or
    `MATCH (n) DETACH DELETE n` the graph index with the documented password.
  - Injection: all SQL is parameterised and `_CANDIDATES.replace("$$max", str(int(max_hops)))`
    casts to int — `'; DROP TABLE vasp;--` as an `entity_id` returns a clean 200 echo. One residual:
    `main.py:95` builds `ILIKE ANY(%s)` patterns from the wallet string, so a wallet containing
    `%` or `_` broadens the provenance query (wallets are unvalidated — see F16).
- **Fix:** bind api/neo4j to `127.0.0.1`; `REVOKE UPDATE, DELETE ON audit_log` from the app role
  plus a `BEFORE UPDATE OR DELETE` trigger that raises; a per-case token header checked on
  `/trace/*` and `/report/*`; escape `%`/`_` in the ILIKE patterns.
- **Effort:** S–M

### F16 · P1 · BE — hostile inputs 500 or are silently accepted
- **Where:** `backend/app/main.py:70,108,128,164,171,183,194,285,372,402`; `CaseRequest` at `:42-49`.
- **Repro:** `scratchpad/p2_api.py` (zero upstream by construction).
- **Evidence:**
  - **500s (11 total, no stack trace leaked to the client but "Internal Server Error"):**
    8 × `psycopg.errors.InvalidTextRepresentation` from non-UUID path params
    (`/trace/not-a-uuid`, `/trace/not-a-uuid/status`, `/trace/'%20OR%201=1--`, `/report/xyz`,
    `/trace/xyz/graph`, `/trace/xyz/provenance`, `/sahyog/disclosure?trace_id=xyz`,
    `/convergence?trace_ids=x,y`); 2 × `InvalidRowCountInLimitClause` (`/cases?limit=-1`,
    `/trace/{id}/graph?limit=-1`); 1 × `ValueError: Out of range float values are not JSON
    compliant: nan` (rescore with `NaN` weights).
  - **Accepted with 201 that should be 4xx:** `not-an-address`; an EVM address on `chain=btc`; a
    BTC address on `chain=eth`; a 100 KB wallet string; `max_hops=10**9`; `fanout=-5`;
    `snapshot_block=-1`; extra unknown body fields. Leading/trailing whitespace is stored verbatim
    (`"  12w6v1…\n"`), and `/wallets/%20%20…/score` then reports "no label in the pinned set",
    i.e. a wrong answer rather than an error.
  - **Overflow, not rejected:** rescore with `1e308` weights → `200` with `weights` all `0.0`.
  - **Idempotency:** two identical `POST /cases` → two different case ids (`8d227b47` vs `54b8c5c4`).
  - Correct behaviours worth recording: unsupported chain → `400` with a useful message; empty body
    → `422`; 11 wallets → `422`; unknown UUID → `404`; `GET /trace/{id}` before DONE → `409 "trace
    is FETCHING/FAILED"`; `rescore profiles=51` → `422`; missing weight key → `400` naming the
    required keys; `/wallets/{addr}/score` deterministic across 3 calls (48, binance); `rescore`
    deterministic across 2 calls.
- **Fix:** type path params as `uuid.UUID`; `limit: int = Query(50, ge=1, le=500)`; a per-chain
  address validator + `.strip()` on ingest; bound `max_hops`/`fanout`/`snapshot_block`;
  `model_config = ConfigDict(extra='forbid')`; reject non-finite weights; make `POST /cases`
  idempotent on `(wallet, chain, snapshot_block)`.
- **Effort:** S

### F17 · P2 · FE — the production build fails, and the graph's glow/halo styles are not real Cytoscape
- **Where:** `frontend/src/components/GraphView.tsx:80` and `:95` (`'shadow-blur'`, `'shadow-color'`,
  `'shadow-opacity'`).
- **Repro:** `npm run build`
- **Evidence:** `error TS2353: Object literal may only specify known properties, and ''shadow-blur''
  does not exist in type 'Node | Edge | Core'` (×2) — `tsc -b` fails, so `npm run build` fails.
  Cytoscape 3 has no node `shadow-*` properties (`underlay-*` / `outline-*` are the supported
  ones), so the "confidence as glow" and the crowned "gold halo" (DEMO beat 3) most likely do not
  render; the crowned node still gets its gold border/width. NOT browser-verified.
  With the typecheck skipped, `vite build` succeeds: **710.84 kB JS (225.71 kB gzip)**, one chunk,
  over Vite's 500 kB warning; CSS 5.31 kB.
- **Fix:** switch to `underlay-color`/`underlay-padding`/`underlay-opacity`, or `outline-*`; then
  confirm in a browser.
- **Effort:** S

### F18 · P2 · Telemetry — `store_hits` is always 0 on chord-driven traces
- **Where:** `backend/app/trace/tasks.py:186` restores `calls`, `upstream`, `stale` onto the
  tracer but not `store_hits`; `trace/engine.py:275` then reads `prov.store_hits` from a
  freshly-built provider.
- **Evidence:** every stored trace shows `upstream_calls 0, store_hits 0` while `api_calls 38` —
  arithmetically impossible unless the counter is lost. The eval harness (same field, CLI path)
  reports it correctly.
- **Fix:** sum and restore `store_hits` like `upstream`.
- **Effort:** S

### F19 · P2 · Spec drift — §14 vs the shipped API
See the contract table in section 4. Summary of drift that should be resolved in the spec or the
code: `POST /cases` body shape and `202`/`201`; `POST /sahyog/disclosure` takes a query param, not
the §14 body (the documented body shape returns `422`); `candidates` renamed `vasp_candidates`;
the state enum gained `AMBIGUOUS` and lost `BROKEN_AT_*`/`ERROR`; four endpoints
(`/trace/{id}/graph`, `/convergence`, `/trace/{id}/rescore`, `/trace/{id}/provenance`) plus
`GET /cases` and `/sahyog/onboarding/{id}` are undocumented in §14; §11.1's `value_relevance`
(`log10(total_usd)/6`) is implemented as `dust_floor` (per-asset native units, never rewards size);
§8's `async` provider ABC is sync. The last two are deliberate and documented in code docstrings —
architecture.md should be updated so code and spec agree.
- **Effort:** S

### F20 · P2 · Data/Claims — the 2,935-edge wallet cannot be rendered
- **Where:** `architecture.md:824-828` and the GraphView performance claim.
- **Evidence:** the only trace of `0x098B716B8Aaf…` holding 2,935 `trace_edge` rows is
  `44dfa65d-b053-4a89-ab88-7523e906db0d`, whose state is **FAILED**
  (`ChordError: … ServiceUnavailable("Failed to read from defunct connection … neo4j:7687")`), so
  `GET /trace/44dfa65d…/graph` → `409 {"detail":"trace is FAILED"}`. The later DONE trace of the
  same wallet (`5e48edf4`) has **0** trace_edge rows. The UI's convergence picker lists only DONE
  traces, so those 2,935 edges are reachable by no surface.
- **Fix:** re-trace that wallet to DONE (costs Etherscan quota) or stop claiming it renders.
- **Effort:** S (+ quota)

### F21 · P2 · BE — every report view writes a `reports` row
- **Where:** `backend/app/main.py:423-428`.
- **Evidence:** 3 report GETs during this audit added 3 rows (baseline 8). Rows are never deduped
  on `content_hash`, and the hash differs per render anyway (F14).
- **Fix:** insert once per `(case_id, content_hash)`, or only on an explicit "file this report" action.
- **Effort:** S

### F22 · P2 · FE — hard-coded BTC units and the wrong sahyog field
- **Where:** `frontend/src/components/RecommendedTarget.tsx:45` (`{dep.amount_btc ?? dep.amount} BTC`),
  `:36-38` (`r.entity_sahyog` is the trace-level best hit, not the crowned entity),
  `NearestPanel.tsx:31` (same `BTC` literal).
- **Evidence:** code. An ETH deposit event would print "… BTC"; when the crowned entity differs
  from the best hit, the sahyog tag describes the wrong entity.
- **Fix:** use `dep.asset` / `top.sahyog`.
- **Effort:** S

### F23 · P2 · Eval — two discovery cases are shallower than §11.2 requires, and one case sits at the call cap
- **Where:** `labels/golden_set.yaml:17` (`min_hops: 1`, zhdanova — the file itself says "shallow —
  validates known-OFAC flow, not discovery") and `:59` (`min_hops: 2`, wuhuihui).
- **Evidence:** harness output `zhdanova hops=1`, `wuhuihui hops=2`; §11.2 defines the golden set as
  "unlabeled suspect addresses **3+ hops** from a documented VASP endpoint". Wuhuihui is the
  flagship Case A. Separately, `polyanin-revil-binance` consumed `calls=200` — exactly `MAX_CALLS`
  (`trace/engine.py:21`), so its hit survives only because `calls_at_hit <= max_calls`; any small
  change in fan-out pushes it over and the case becomes UNATTRIBUTED.
- **Fix:** label the two shallow cases as validation rather than discovery when presenting "5 of 5";
  raise the budget or reduce fan-out for polyanin so it is not at the cliff edge.
- **Effort:** S

### F24 · P2 · Logic (§12) — the Tornado Cash sanctioned→delisted flip is not modelled
- **Evidence:** for `0xd90e2f925da726b50c4ed8d0fb90ad053324f31b`, both label sets contain exactly
  the same two rows (`mixer/heuristic/tornadocashrouter/labeled`, `mixer/tagpacks/tornado/labeled`)
  and no `sanctioned` row at all. There is no date-validity column in `address_label`, so the
  spec's motivating example for label-set pinning cannot be demonstrated. Pinning itself does work
  for traces (the case's pinned version is used by `PgRegistry(pins[...])`) — but see F14 for the
  report path.
- **Fix:** add a dated OFAC fixture (sanctioned at 2022 label set, absent at 2025) and show the
  score change, or drop the claim from the Q&A.
- **Effort:** M

### F25 · P3 · Ops/hygiene
- `frontend/.vite/` is untracked and not ignored — it is Vite's dependency-optimizer cache, so
  **yes, add it to `.gitignore`** (`frontend/.gitignore` already covers `dist`).
- `.env.example` omits `API_URL` (the F01 root cause) and `REPO_ROOT`, and documents
  `BLOCKCHAIR_API_KEY`/`TRONGRID_API_KEY`, which no code reads (only `etherscan_api_key`,
  `mempool_base_url`, `esplora_base_url`, postgres/neo4j/redis settings are read).
- No secrets are committed: `.env` was never tracked (`git log --all -- .env` empty), and the
  live Etherscan key (34 chars) appears in no tracked file or any commit.
- Stale comments: `HUD.tsx:3` and `api/client.ts:3` claim `wall_clock_s` is 0 for chord traces
  (fixed since — `tasks.py:252` sets `wall_start`, and the stored traces show 1.8–13.4 s);
  `schema.sql:2` claims `init_schema()` runs on every start (F06);
  `vite.config.ts:8` claims compose sets `API_URL` (F01).
- `frontend/Dockerfile` copies only `package.json` (no lockfile) and runs `npm install` → the image
  is not reproducible; it also serves the dev server as the demo runtime.
- ruff (`--select F,E9,B`) on `app`+`worker`: 7 trivia — unused imports `WEIGHTS`
  (`eval/harness.py:32`) and `start_trace` (`main.py:31`), `F541` f-string without placeholders
  (`api/sahyog.py:45`), 3 × `zip()` without `strict=`, one unused loop variable. Default-config
  ruff over the repo reports 104, almost all `RUF100`/`E402` noise in `scripts/`.
- mypy: 59 errors in 10 files, mostly Optional/annotation noise in untyped dicts. The two that
  matter are the unguarded `fetchone()[0]` calls in F06.
- Import smoke test: all 44 modules import cleanly; all 12 module `_selfcheck()`s pass.

### F26 · P3 · Claims — copy that overstates
- `frontend/src/App.tsx:82` and `frontend/index.html:7`: "An automated blockchain tracing engine
  that **instantly** connects **illicit**, unknown crypto wallets to known exchanges" — traces take
  1.8–13.4 s replayed and up to 80 s live (stored `wall_clock_s`), §2 commits to
  "seconds–minutes"; "illicit" prejudges the input; "connects … to known exchanges" omits
  abstention, which is the project's whole discipline.
- `ScoreBreakdown.tsx:138`: "run the harness's 12 seeded ±20% profiles" — this is
  `POST /rescore {profiles:12}` on one trace; the harness runs 20 profiles × 8 cases. Same label
  in `DEMO.md:74,105` ("12/12").
- Nothing in the UI or PDF attaches "%" to confidence: the sole "%" in the rendered report is the
  sweep share ("99.99% of sweep tx"), which is correct usage. No "accuracy", "calibrated",
  "real-time monitoring" or "identifies the person" claims outside explicit disclaimers.
  `GraphView.tsx:77` encodes confidence as glow specifically to avoid printing a probability-like
  number (and that style is broken anyway — F17).

### F27 · P3 · FE/Data — case list noise
32 traces exist for the Wu Huihui wallet and 19 for Zhdanova (`GET /cases` default limit 50), so the
stage list is mostly duplicates of one address; 2 legacy BTC jobs have `result->>'state'` NULL and
would render an empty state tag / break panels expecting `vasp_candidates`.
- **Fix:** dedupe by (wallet, snapshot) keeping the newest, or filter the list to the demo set.

---

## 3. Spec matrix

| § | Topic | Status | Note |
|---|---|---|---|
| 3 | proximity excluded from confidence | **PASS** | `factors()` identical at hops 1 and 5; `rules.py`/`engine.py` reference no hop count (the `hops` name in `path_penalty` is a set of addresses) |
| 5 | compose runtime, WeasyPrint libs | **FAIL** | F01; PDF does render in-container (Dockerfile build-time assert PASS) |
| 6.2c | sweep-to-hot deposit evidence | **PASS** | sweep_proven endpoint, 27 distinct senders, 99.99% share on demo A |
| 6.3 | honest role taxonomy / no promotion | **FAIL** | F07 |
| 7.2 | EVM edge identity | **FAIL** | F10 (Postgres UNIQUE key itself is correct) |
| 7.3 | BTC tx hypernode | **PASS** | `/graph` emits `tx:` nodes with funds/credits edges |
| 7.4 | SAME_OWNER as traversable edges | **DRIFT** | built and persisted, never traversed; overlay lost in chord path (F12) |
| 7.5 | change detection | **NOT VERIFIED** | detector + selfcheck pass; change outputs are annotated but still followed as moves (see QUESTIONS) |
| 7.6 | Neo4j is derived | **PASS (by design)** | `/graph` + `/convergence` read Postgres; verified earlier with Neo4j stopped (DEMO §1). `rebuild_graph.py` NOT run: it wipes Neo4j unless `--keep` |
| 8 | rate limiter, 429→cache | **NOT VERIFIED / FAIL** | needs quota to exercise; F13 for the silent-degradation half |
| 9.1 | deterministic truncation | **PASS** | value desc → ts → hash in `absorb` |
| 9.3 | six states, service-node policy | **FAIL** | F11 |
| 10 | aggregation max + saturating count | **PARTIAL** | 50-dust property PASS (59 vs 69), corroboration saturates; F08, F09 |
| 10 | separation / abstain | **PASS (n≥2) / FAIL (n=1)** | F03, F07 |
| 11.1 | factors ∈[0,1], penalties ≤0.3, clamp 0–100 | **PASS** | brute-forced over NaN/inf/None/negative values and 6 timestamp shapes: no violation; contributions sum to the index |
| 11.1 | index not percentage | **PASS** | no "%" beside confidence in UI or PDF |
| 11.2 | golden set reproduces offline | **PASS** | 8/8, 0 field diffs, 0 upstream, 24.0s |
| 11.2 | weights frozen | **PASS** | `backend/app/scoring/*` unchanged since `f29e8cf`, before the eval commit `f08aa94`; hash `w-75bf07eaee79` |
| 11.2 | perturbation = the defensible metric | **FAIL** | F03 |
| 11.2 | 3+ hop discovery cases | **PARTIAL** | F23 |
| 12 | snapshot discipline | **PASS** | 0 edges with `block > snapshot_block` on 4 traces (BTC max 705511/624678 vs pin 966946; ETH 14.8M/16.9M vs 25906777) |
| 12 | label-set pinning | **PARTIAL** | traces pinned; report/SAHYOG use latest (F14); Tornado flip unmodelled (F24) |
| 12 | idempotency | **PARTIAL** | edge/trace_edge writes idempotent (UNIQUE + ON CONFLICT); `POST /cases` is not (F16) |
| 12 | state machine QUEUED→FETCHING→SCORING→DONE/FAILED | **PARTIAL** | transitions correct and progress ≤1.0 (`FETCHING 1/0.0 → 2/0.25 → 3/0.5 → SCORING 3/1.0 → DONE`); not resumable (F04) |
| 12 | reproduce-and-verify | **FAIL** | F14 |
| 13 | frontend surfaces | **NOT VERIFIED in browser** | F02, F17, F22, F27 from code/API |
| 14 | API surface | **DRIFT** | section 4 |
| 15 | security / PII posture | **FAIL** | F15 |
| 16 | observability / timeline | **PASS** | `/timeline` reports nulls for uninstrumented phases rather than faking zeros |
| 17 | SAHYOG routing | **PARTIAL** | non-onboarded → MLAT correctly (`binance routable_via_sahyog:false`), abstain → no target named; controlled deposit absent (F05) |
| 18 | language discipline | **FAIL** | F03, F05, F26 |
| 19 | Q&A defense | **PARTIAL** | owners unassigned; answers #2 and #3 rest on F03 |

---

## 4. API contract vs §14

Flow confirmed: `TraceForm` → `POST /cases` (creates case+job, dispatches hop 1) → chord
`expand_task`×frontier → `level_done` (merge → next frontier or `attribute_result`) → `DONE` →
`GET /trace/{id}` → panels (`Leaderboard`/`RecommendedTarget`/`NearestPanel`/`ScoreBreakdown` from
that payload; `ProvenanceCard` → `/provenance`; `GraphView` → `/graph`; `ConvergencePanel` →
`/convergence`; `ReportButton` → `/report` + `/sahyog/disclosure` + `/sahyog/onboarding/wazirx`).

| Endpoint | §14 | Actual | Mismatch |
|---|---|---|---|
| `POST /cases` | `{wallet, chain}` → `{case_id}` **201** | `{wallets[≤10], chain, snapshot_block?, max_hops, fanout, graph, dispatch}` → `{snapshot_block, chain, cases[], trace_ids[], case_ids[]}`, **202** (201 only when `dispatch:false`) | ⚠ body, status, response shape |
| `POST /trace` | `{case_id}` → `{trace_id}` 202 | as spec; re-POST returns existing id + note | ok |
| `GET /trace/{id}/status` | `{state,current_hop,progress}` | + `error` | ok (⚠ non-UUID → 500) |
| `GET /trace/{id}` | `TraceResult` | `409` before DONE; `vasp_candidates` not `candidates`; `state` ∈ {ATTRIBUTED, ATTRIBUTED_INFRA, AMBIGUOUS, UNATTRIBUTED} | ⚠ field name, enum |
| `GET /trace/{id}/timeline` | per-phase timing | as spec (`normalize: null` by design) | ok |
| `GET /wallets/{addr}/score?chain=` | `{score,breakdown}` | + entity/role/label_set_version/note; `score:null` when unlabeled | ok |
| `GET /report/{id}` | `application/pdf` | pdf + `X-Content-Hash`; inserts a `reports` row per GET | ⚠ F21 |
| `POST /sahyog/cases` | `{wallet,chain}` → `{case_id}` | mirrors `/cases`, always **201** even when dispatched | ⚠ minor |
| `POST /sahyog/disclosure` | `{disclosure_payload}` → `{request_id}` | **query param** `trace_id` (+ optional `case_reference`); §14's body → `422` | ⚠ |
| `GET /cases` | — | present | ⚠ undocumented |
| `GET /trace/{id}/graph` | — | present | ⚠ undocumented |
| `GET /trace/{id}/provenance` | — | present | ⚠ undocumented |
| `POST /trace/{id}/rescore` | — | present | ⚠ undocumented |
| `GET /convergence` | — | present | ⚠ undocumented |
| `GET /sahyog/onboarding/{id}` | — | present | ⚠ undocumented |
| `GET /health` | — | present | ⚠ undocumented (compose depends on it) |

Disclosure payload (§17) validated against a real trace — keys present: `target_vasp`,
`target_vasp_name`, `target_vasp_sahyog`, `jurisdiction`, `routable_via_sahyog`, `route`,
`suspect_addresses`, `endpoint_address`, `transaction_hashes`, `deposit_events`, `attribution`
(`confidence_index`, `of:100`, `separation`, `hops`, `role_basis`, `rationale`), `provenance`,
`reproduce` (4 pins), `legal_basis`, `schema_note`, `case_reference`, `disclaimer`. Binance →
`routable_via_sahyog:false` + MLAT text; abstaining trace → `target_vasp:null`. §17 conformance
**PASS**, except that the illustrative payload is reachable only via a query param (F19).

---

## 5. DEMO.md freeze checklist

| Item | Status | Note |
|---|---|---|
| `git status` clean, pushed to main | **FAIL** | `frontend/.vite/` untracked (F25) |
| `.env` untracked and ignored | **PASS** | never committed; key absent from all history |
| `docker compose down && up -d` → 6/6 healthy | **FAIL** | all 6 report healthy, but at HEAD the UI cannot reach the API (F01); "healthy" is itself misleading |
| One ATTRIBUTED case clicked and warmed | **NOT VERIFIED** | browser unavailable |
| Backup recording playable offline | **NOT VERIFIED** | not found in repo |
| Q&A owners filled in | **FAIL** | all 7 rows blank in DEMO.md |
| Do not run day 5 | **PASS** | not run; convergence intact |

Failure-playbook rows, re-tested: "Neo4j dies → nothing depends on it" **PASS by design** (both
surfaces read Postgres; previously verified with Neo4j stopped). "A trace errors → lands FAILED,
never a spinner" **FAIL** for worker death (F04); it does hold for task exceptions (the
`trace_failed` errback works — trace `44dfa65d` carries its real ChordError cause).
"Provider 429/offline → partial, no crash" **PARTIAL** (F13). "Wi-Fi dies → irrelevant, zero
upstream" **FAIL** as the demo is currently driven (F02).

---

## 6. Path to 10/10 — ranked by score gained per hour

1. **F01** — add `API_URL` to compose, point the healthcheck at `/api/health`. ~5 min; without it
   there is no demo on a clean machine.
2. **F03 + F26 + DEMO copy** — separation `null` at n=1; stop citing 160/160 and 12/12 as evidence;
   reword beats 2, 5, 7 and Q&A #2; drop "instantly"/"illicit"; rename the rescore button. Pure
   honesty, and it closes the two freeze-risk questions.
3. **F02** — stage buttons open stored traces or pass the golden snapshot; add a chain selector.
   Makes the offline claim true.
4. **F16 + F17** — UUID path params, bounded `limit`, address validation/strip, finite weights; fix
   `shadow-*` so `npm run build` passes. Removes 11 live 500s and unbreaks the build.
5. **F07 + F08 + F09 + F11** — crown floor, keep `ATTRIBUTED_INFRA`, per-path aggregation,
   `BROKEN_AT_*` states. Four small diffs in scoring/attribution that each remove a Q&A attack.
6. **F04** — `visibility_timeout`, `task_reject_on_worker_lost`, stale-job sweeper. Kills the
   10-minute stage spinner.
7. **F10 + F12 + F18** — ERC-20 dedupe key; carry the overlay through the chord (and make eval and
   product run one algorithm); restore `store_hits`.
8. **F15 + F14** — loopback binds, append-only trigger, pinned registry in the report, deterministic
   report hash + a `verify` path.
9. **F06 + F25** — README quickstart, `init_schema()` on startup, seed dump, vendor fetch script,
   `.gitignore`/`.env.example` fixes. This is what a judge cloning the repo actually experiences.
10. **F05 + ≥2-candidate golden cases (F23, F24, F20)** — the controlled deposit and a golden case
    with real competing candidates. Highest ceiling, highest cost: needs your own deposit, Etherscan
    quota, and a re-trace of the 2,935-edge wallet.

---

## 7. QUESTIONS (no evidence yet — do not treat as findings)

- **BTC change outputs are followed as payments.** `trace/engine.py:110-115` emits change outputs
  as moves (annotated with `change`), and `absorb` does not filter them; only *reuse* change (an
  output paying an input address) is excluded. Forward-tracing the suspect's own remaining funds
  may be intended (a peel chain is a chain of change outputs), and I never observed change being
  attributed as a counterparty. Which behaviour does §7.5 want?
- **Redis token bucket under concurrent chords** — needs real provider traffic to exercise.
- **Frontend runtime behaviour** — polling teardown, duplicate intervals, heap growth over 10
  traces, FPS on the big wallet, keyboard access, contrast, dark/light, mobile layout: all
  unverified (no browser). The code reads clean (single `waitAll` loop, interval cleared in
  `finally`, `useEffect` keyed on `trace_id`), but that is not a measurement.
- **`rebuild_graph.py` parity** — not run: it calls `g.wipe()` unless `--keep` is passed. With
  `--keep` it is non-destructive (MERGE) and would prove idempotency, not from-empty parity. Want
  me to run `--keep` and compare `Graph.counts()` before/after?

---

## 8. State changed during the audit

- **The frontend container was rebuilt at HEAD** and currently runs only because of a scratchpad
  compose override supplying `API_URL`. A plain `docker compose up` will exhibit F01. The API was
  restarted as a side effect (it matches HEAD; the frontend image did not).
- The worker was killed and restarted for the F04 test; it is healthy. The orphaned chord from that
  test may still sit in Redis; if redelivered after `visibility_timeout` it could write
  `trace_edge` rows under a deleted trace id (no FK on `trace_id`).
- 14 audit-created rows were deleted with the user's approval (12 undispatched cases from the
  hostile-input sweep + 2 offline replay traces), together with their `trace_edge`/`audit_log`
  rows. Counts returned to baseline exactly: cases 77, edge 5,200, trace_edge 35,510,
  audit_log 645, utxo_tx 102, raw_response 1,782.
- 3 rows were added to `reports` by report GETs (left in place).
- Convergence re-verified unchanged after all of the above: 7 DONE Lazarus traces → Tornado Cash
  7/7, 120,200 ETH, `n_shared 5`; including the FAILED 8th → 8/8, 217,900 ETH, `n_shared 30`.
- Audit scripts kept in the session scratchpad: `p2_api.py` (hostile inputs), `p3_logic.py`
  (invariants), `eval_rerun.json` + `eval_rerun.log`, `audit-override.yml`.
