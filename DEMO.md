# Demo runbook — SIH26182 (VASP Attribution Engine)

Everything below was measured on this machine, not estimated. Where a number appears, it came from
a run recorded in the build log; where something is unproven, this file says so.

---

## 1. The stage decision: run OFF THE PRE-CACHED / OFFLINE PATH

**Decided and proven.** The demo runs from the content-addressed raw store, not from live provider
calls.

Why: this host dropped a Neo4j write under a single 2,935-edge batch on 2026-09-13, and it has hit
OOM twice. The offline path removes both the network and the graph database from the critical path.

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
2. **Case A — BTC discovery.** Wu Huihui (OFAC) → Binance. Crowned **70/100**, separation **HIGH
   (70 pts)**, role basis **sweep_proven**, 2 hops. Point at `deposit tx` and the amount caveat:
   value at the endpoint is what landed there, not the suspect's own share.
3. **GraphView.** Hit `replay hop by hop`. Rectangular `:Tx` hypernodes alternate with circular
   addresses — that is the UTXO model rendered literally, and EVM renders address → address with no
   tx nodes at all. Red octagon = sanctioned/mixer STOP. Gold halo = the crowned target.
   Default view is the **spine (6 of 246 nodes)**; `show all 246` is one click away and the caption
   states the 240 collapsed are fan-out addresses nothing was concluded from.
4. **Case B — the honest non-answer.** Hydra market → `UNATTRIBUTED` at a CoinJoin boundary. This is
   the beat most teams do not have. Abstention is a result.
5. **Calibration (the best 15 seconds).** ScoreBreakdown sliders: move weights ±20%, rankings hold.
   Then the harness button: **12/12 seeded profiles, ranking unchanged**. Say the line verbatim:
   > **8 documented cases, all rank-stable under ±20% perturbation.**
   Never "accuracy %" — there is no trained model and no validation set.
6. **Convergence.** Seven Lazarus complaints → **Tornado Cash, 7/7, 120,200 ETH**. The honest
   ending: this converges on a **mixer boundary**, not a VASP — so one SAHYOG request cannot cover
   them. Saying that is the difference between an insight and a false lead.
7. **The filed artifact.** ReportButton → PDF with all four determinism pins on the face, and BOTH
   routing branches: WazirX (IN, onboarded) fires the SAHYOG portal under BNSS §94; Binance is not
   on the portal and gets the honest MLAT boundary.

---

## 4. Failure playbook

| If this happens | Do this |
|---|---|
| Neo4j dies | Nothing. Every surface is Postgres-backed. Mention it as a design property. |
| A trace errors | It lands **FAILED** with the real cause in the row, and the UI prints it. Never a spinner. |
| Provider 429 / offline | Result completes and is flagged **partial**; the HUD shows a partial badge. Verified: `partial:provider_unavailable` on a miss, no crash. |
| Wi-Fi dies | Irrelevant — the demo path makes zero upstream calls. |
| Everything dies | Play the backup recording. |

---

## 5. Q&A owners (§19, §22)

Assign a name to each slot before the presentation — one owner per question, so nobody freezes.

| # | Question | Answer in one breath | Owner |
|---|---|---|---|
| 1 | "How do you know the exchange owns that address?" | Sweep proof (§6.2c): the deposit address swept into a labeled hot wallet; `role_basis: sweep_proven`, with the sweep tx, distinct-sender count and share on the Provenance card. | ______ |
| 2 | "Is your confidence calibrated?" | It is an **index out of 100**, not a probability and not ownership. Weights were frozen before evaluation (`w-75bf07eaee79`). Move them ±20% live — rankings hold, 12/12. | ______ |
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
- [ ] One ATTRIBUTED case clicked and warmed
- [ ] Backup recording on the presenting machine, playable offline
- [ ] Q&A owners filled in above
- [ ] Do not run day 5
