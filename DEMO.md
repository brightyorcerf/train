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

1. **The question.** "Which exchange can identify the account holder behind this wallet?" — an
   investigative lead, not identity and not evidence.
2. **Case A — BTC discovery.** Wu Huihui (OFAC) → Binance. Crowned **70/100**, role basis
   **sweep_proven**, 2 hops. Point at `deposit tx` and the amount caveat: value at the endpoint is
   what landed there, not the suspect's own share.
   **Do not say "separation HIGH".** This case reaches ONE candidate, so there is no gap between a
   top two; the panel now reads "single candidate — no separation". Separation is a real claim only
   when two entities are ranked, and saying it here invites the judge to ask what the second
   candidate was.
3. **GraphView.** Hit `replay hop by hop`. Rectangular `:Tx` hypernodes alternate with circular
   addresses — that is the UTXO model rendered literally, and EVM renders address → address with no
   tx nodes at all. Red octagon = sanctioned/mixer STOP. Gold halo = the crowned target.
   Default view is the **spine (6 of 246 nodes)**; `show all 246` is one click away and the caption
   states the 240 collapsed are fan-out addresses nothing was concluded from.
4. **Case B — the honest non-answer.** Hydra market → `UNATTRIBUTED` at a CoinJoin boundary. This is
   the beat most teams do not have. Abstention is a result.
5. **Calibration — say less than the old script did.** ScoreBreakdown sliders move each weight
   ±20% and the backend re-scores (same function the harness calls, zero provider calls). What is
   honest to claim:
   > **Weights were frozen before evaluation (`w-75bf07eaee79`), and the score is an index out of
   > 100 — not a probability and not ownership. Here is the arithmetic behind the number, live.**

   **Do NOT claim rank stability as evidence on these cases.** Every golden case reaches at most
   one candidate, and a one-row ranking cannot change under any weights — so "12/12" and
   "160/160 case-profiles" are arithmetic, not calibration. The harness now prints the CONTESTED
   denominator (cases with ≥2 candidates: currently **0**) and says this in its own output. If a
   judge presses: *"rank stability is the metric we would report, and we will not report it until
   the golden set contains a case with competing candidates — that is open work, not a result."*
   Never "accuracy %" — there is no trained model and no validation set.
6. **Convergence.** Seven Lazarus complaints → **Tornado Cash, 7/7, 120,200 ETH** (re-verified
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
| 2 | "Is your confidence calibrated?" | It is an **index out of 100**, not a probability and not ownership, and every factor is inspectable on screen. Weights were frozen before evaluation (`w-75bf07eaee79`). Rank stability under ±20% is the metric we would defend, and we do not claim it yet: our golden cases reach one candidate each, so the ranking cannot move — the harness prints that denominator itself. | ______ |
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

The audit (`reportscratchpad.md`) cut these; do not let them back into the script.

| Do not say | Say instead |
|---|---|
| "separation HIGH (70 pts)" on Case A | "one candidate — no separation to report" |
| "12/12 / 160/160 rank-stable, so it's calibrated" | "weights frozen before evaluation; stability is open work until a case has two candidates" |
| "WazirX fires the SAHYOG portal" | "this is the portal branch for an onboarded VASP; our case takes MLAT" |
| "instantly connects illicit wallets to exchanges" | "bounded trace, seconds to minutes, and it abstains when the evidence is not there" |
| "zero upstream calls, Wi-Fi is irrelevant" | "the pinned cases replay from the store; a new wallet needs the network" |
