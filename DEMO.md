# Demo runbook — SIH26182 (VASP Attribution Engine)

Everything below was measured on this machine, not estimated. Where a number appears, it came from
a run recorded in the build log; where something is unproven, this file says so.

---

## 1. The stage decision: run OFF THE PRE-CACHED / OFFLINE PATH

**Decided and proven.** The demo runs from the content-addressed raw store, not from live provider
calls.

Why: this host dropped a Neo4j write under a single 2,935-edge batch on 2026-09-13, and it has hit
OOM twice. The offline path removes both the network and the graph database from the critical path.

**The console now always pins a snapshot** (`DEMO_SNAPSHOT` in `src/api/client.ts`). It used to
submit none, so the API pinned the live chain tip, every read missed the store keyed to the old
snapshot, and the "offline" demo went to the network: the last pre-audit Hydra run made 15 upstream
calls. Pinning is what makes the claim below true from the UI as well as from a script.

What was actually verified (Neo4j **stopped**, `offline=True`, zero upstream calls):

```
state DONE · ATTRIBUTED · recommended binance · hops 2 · upstream 0 · partial False · 1.64s
```

And every surface answered 200 with Neo4j stopped:

| surface | result with Neo4j down |
|---|---|
| `/cases`, `/trace/{id}`, `/timeline`, `/provenance` | PASS |
| `/trace/{id}/graph` (BTC) | PASS — 246 nodes / 315 edges |
| `/trace/{id}/graph` (UNATTRIBUTED) | PASS — 830 nodes |
| `/report/{id}` (PDF) | PASS — 24,937 bytes |
| `/sahyog/onboarding`, `/sahyog/disclosure` | PASS |
| `/trace/{id}/rescore` ×12 profiles | PASS — 12/12 unchanged |
| `/convergence` | PASS — n_shared 5 |
| `/wallets/{addr}/score` | PASS |

This works because Postgres is the system of record and Neo4j is a derived index (§7.6):
`converge()` and `/trace/{id}/graph` both read `trace_edge` in Postgres by design.

**If Neo4j dies on stage, say so and keep going.** Nothing on screen depends on it.

One honest caveat to have ready: a wallet that has **never** been traced on this machine has no
stored responses, so tracing something new on stage does hit the providers. The pinned golden cases
replay from the store; a wallet a judge invents does not.

---

## 2. Pre-flight (run in this order)

```bash
docker compose up -d                 # clean boot measured at 78s to all-healthy
docker compose ps                    # expect 6/6 healthy
# only if the graph index looks empty (it is rebuildable, never authoritative):
docker compose run --rm -e PYTHONPATH=/app -v "$PWD/scripts:/repo/scripts:ro" \
  api python /repo/scripts/rebuild_graph.py     # ~13.5s, 5,200 edges
```

Then open the console and click one ATTRIBUTED case to warm it.

**Do NOT run `scripts/day5_graph_check.py`.** It `TRUNCATE`s `trace_edge, edge, evidence, utxo_tx`
and will wipe the Lazarus convergence. See architecture.md §24 for why it is formally unrun.

---

## 3. The beats

1. **The question + the number.** Landing page. "Which exchange can identify the account holder
   behind this wallet?" Point at the scorecard — it is `GET /benchmark`, the frozen-weight harness run
   live over 8 OFAC/DOJ-documented cases from the evidence store: **7 of 8 decided as the record
   says, 0 wrong, 0 network calls.** Say "7 of 8", never a percentage.
2. **Case A — BTC discovery.** Click the *Wu Huihui* card. The report scrolls to the top and the
   verdict flashes: **Funds reached Binance in 2 hops**, 70/100, sweep_proven. The replay starts on
   its own: particles run only along the attributed path, the camera follows each hop, and the
   storyboard on the right names each technique with its basis (observed / heuristic / label) —
   *Consolidation* (the coin is one of 10 inputs), *Rapid layering* (moved on after 1h20m),
   *Deposit-address sweep* (100% forwarded to a labeled Binance hot wallet, 27+ senders).
   Amount caveat: value at the endpoint is what landed there, not the suspect's share.
   **Do not say "separation HIGH"** — one candidate, so the card reads "single candidate".
3. **The data model.** Hit *Skip to end*, then *Show all*. Rectangular `:Tx` hypernodes between
   circular addresses is the UTXO model rendered literally; EVM (the Potekhin card) has none.
4. **Case B — the honest non-answers.** Hydra Market → no target, the trail ends at a CoinJoin.
   Then **Li Jiadong**: the engine reached TWO sweep-proven exchanges — Binance 70, Bitfinex 64 — and
   abstains because the gap (6) is under τ=10. The replay ends on both endpoints. This is the beat:
   *the government record says Binance; our engine will not name one exchange on a 6-point margin,
   and it did not tune its weights to make that go away.*
5. **Calibration.** ScoreBreakdown sliders re-score on the backend (the harness's own function,
   zero provider calls). Weights frozen (`w-75bf07eaee79`). Rank stability: the harness now has
   **one contested case** (Li Jiadong) and its abstention holds in 20 of 20 ±20% profiles. Claim
   exactly that; the other seven reach one candidate, where stability is arithmetic.
   Never "accuracy %".
5b. **The engine, live.** On any golden report hit **Re-trace live ↻** (forced re-run at the same
   snapshot — replays from the store, no Wi-Fi needed). The *live* panel streams `/trace/{id}/stream` (SSE): hop
   lanes fill as the Celery chord for each hop commits. Flex line: *the distributed driver decides
   all 8 golden cases call-for-call identically to the sequential reference*
   (`scripts/parity_check.py`, 8 of 8).
6. **Convergence** (*Recent traces* drawer → tick the Lazarus wallets → find shared nodes). Seven Lazarus complaints → **Tornado Cash, 7/7, 120,200 ETH** (re-verified
   2026-09-18; the eighth wallet's trace is FAILED and is not selectable, which is why it is seven
   and not eight). The honest ending: this converges on a **mixer boundary**, not a VASP — so one
   SAHYOG request cannot cover them. Saying that is the difference between an insight and a false
   lead. Those traces now report `BROKEN_AT_MIXER` rather than a bare `UNATTRIBUTED`.
7. **The filed artifact.** ReportButton → PDF with all four determinism pins on the face, and BOTH
   routing branches. Word this precisely:
   > **This case crowns Binance, which is not on the SAHYOG portal — so it takes the honest MLAT
   > branch. Alongside it we print what the portal branch looks like for a VASP that IS onboarded,
   > WazirX, under BNSS §94.**

   WazirX is a **registry lookup shown for contrast, not a case that reached it** — we hold no
   deposit-role address for any onboarded VASP (0 ground-truth labels; WazirX has one hot address
   on ETH/Polygon and none on BTC). Claiming "the portal route fires" implies a case we do not
   have. The controlled-deposit case that would make it fire for real is open work (§7) and needs
   our own deposit to WazirX/KuCoin.

---

## 4. Failure playbook

| If this happens | Do this |
|---|---|
| Neo4j dies | Nothing. Every surface is Postgres-backed. Mention it as a design property. |
| A trace errors | It lands **FAILED** with the real cause in the row, and the UI prints it. |
| The worker dies mid-trace | Now lands FAILED too: Redis redelivery is 90s (was a 1-hour default) and `/status` sweeps a job with no progress for 600s into FAILED with "worker lost". Before the audit this spun forever — verified stuck for 8 minutes with a healthy worker. If it happens, re-submit the case; it is idempotent at the same snapshot. |
| Provider 429 / offline | Result completes and is flagged **partial**; the HUD shows a partial badge. If nothing was reached it now lands **INCOMPLETE**, not UNATTRIBUTED — degraded data must not be presented as a considered abstention. |
| Wi-Fi dies | The pinned golden cases replay from the store: zero upstream calls. A wallet never traced on this machine WILL need the network — do not take one from the audience with the Wi-Fi down. |
| Everything dies | Play the backup recording. |

---

## 5. Q&A owners (§19, §22)

Assign a name to each slot before the presentation — one owner per question, so nobody freezes.

| # | Question | Answer in one breath | Owner |
|---|---|---|---|
| 1 | "How do you know the exchange owns that address?" | Sweep proof (§6.2c): the deposit address swept into a labeled hot wallet; `role_basis: sweep_proven`, with the sweep tx, distinct-sender count and share on the Provenance card. | ______ |
| 2 | "Is your confidence calibrated?" | It is an **index out of 100**, not a probability and not ownership, and every factor is inspectable on screen. Weights were frozen before evaluation (`w-75bf07eaee79`). Rank stability under ±20%: one golden case is contested (Li Jiadong, Binance 70 vs Bitfinex 64) and its abstention holds in 20/20 profiles; the other seven reach one candidate, where stability is arithmetic — the harness prints that denominator itself. | ______ |
| 3 | "Why OFAC data for an Indian tool?" | OFAC is only the sanctioned layer. The India answer is the SAHYOG routing: onboarded VASP → portal under BNSS §94; otherwise the honest MLAT boundary. | ______ |
| 4 | "What if it errors on stage?" | FAILED is a real state with the cause recorded; convergence and the graph are Postgres-only; and the whole demo replays offline. | ______ |
| 5 | "Is this chain-wide / real-time?" | No, and we never claim it. Bounded BFS, pinned snapshot, capped call budget. Claiming chain-scale monitoring is the claim that would not survive scrutiny (§18). | ______ |
| 6 | "Can you de-anonymise the mixer?" | No. A mixer is a **prototype trace boundary**, not "untraceable" — we stop and say where we stopped. | ______ |
| 7 | "Is this evidence?" | No. It is an investigative lead. Identifying the holder is the VASP's KYC under a lawful request. Reproducibility ≠ legal chain of custody. | ______ |

---

## 6. Freeze checklist

- [ ] `git status` clean, everything pushed to `main`
- [ ] `.env` untracked and in `.gitignore` (holds the Etherscan key)
- [ ] `docker compose down && docker compose up -d` → 6/6 healthy
- [ ] **`curl 127.0.0.1:5173/api/health` returns `{"status":"ok"}`** — the frontend healthcheck now
      goes through the proxy, because "healthy" used to be true while every API call failed
- [ ] One ATTRIBUTED case clicked and warmed
- [ ] `docker compose exec api python -m app.eval.harness` → 8/8, 0 upstream
- [ ] Backup recording on the presenting machine, playable offline
- [ ] Q&A owners filled in above (they are still blank)
- [ ] Do not run day 5

## 7. Language that is now wrong on stage

The pre-launch audit cut these; do not let them back into the script.

| Do not say | Say instead |
|---|---|
| "separation HIGH (70 pts)" on Case A | "one candidate — no separation to report" |
| "12/12 / 160/160 rank-stable, so it's calibrated" | "weights frozen before evaluation; stability is open work until a case has two candidates" |
| "WazirX fires the SAHYOG portal" | "this is the portal branch for an onboarded VASP; our case takes MLAT" |
| "instantly connects illicit wallets to exchanges" | "bounded trace, seconds to minutes, and it abstains when the evidence is not there" |
| "zero upstream calls, Wi-Fi is irrelevant" | "the pinned cases replay from the store; a new wallet needs the network" |
