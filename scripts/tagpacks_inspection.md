# GraphSense TagPacks inspection (Day 1, §1 OPEN item)

Repo: `graphsense/graphsense-tagpacks` @ `7f9a5d1f` (2026-09-11), cloned to `vendor/`.
78 packs, ~524k address tags, 3,814 actors.

## Summary

- **deposit_address_level:** **no**
- **indian_vasp_coverage:** **none found** at address level. WazirX, CoinDCX, ZebPay, BitBNS,
  Giottus, BuyUcoin, Unocoin and Mudrex exist **only as actor (entity) definitions** in
  `actors/graphsense.actorpack.yaml` (id / label / uri / jurisdiction), with **zero address tags**
  pointing at them (`actor: wazirx|coindcx|…` → 0 hits across all packs). The one "Coinswitch" hit
  is a single ETH address (`0xd0808da0…fc59`) from an Etherscan word-cloud crawl
  (`confidence: web_crawl`, no actor, no role). It is probably CoinSwitch.co, the 2017 global swap
  aggregator, not CoinSwitch Kuber. Treat it as unverified.
- **license:** **MIT** (© 2022 Iknaio Cryptoasset Analytics GmbH, © 2018–2022 AIT). Free to vendor
  and redistribute with attribution.
- **implication:** **yes — sweep detection (§6.2c) is our PRIMARY deposit-detection mechanism.**
  Build it first, on day 3, together with the label subsystem.

## Evidence

**Schema.** These are the keys used across all packs, by frequency: `address, label,
is_cluster_definer, actor, currency, source, abuse, lastmod, category, context, concepts, title,
tags, creator, confidence, description`. **There is no `role` field.** The word "deposit" appears
only inside free-text labels, in 4 files: `'Binance: Eth2 Depositor'`, `'Wintermute: Binance Deposit'`,
DeFi contract names like `fei_EthLidoPCVDeposit`, and scam descriptions. None of these is a
structured deposit-role label.

**Packs opened (7):**

| Pack | What it actually contains |
|---|---|
| `exchange-wallets-binance.yaml` | Binance **reserve** (proof-of-reserves) BTC/ETH addresses; `is_cluster_definer: true`. These are hot/cold infra, not deposit addresses |
| `binance.yaml` | 2 BTC addresses from the 2019 Binance hack ("sender addresses"). Infra |
| `exchange-wallets-kucoin.yaml` | KuCoin reserve wallets, BTC/ETH/TRX. Infra |
| `walletexplorer.yaml` | 2021 BTC crawl of WalletExplorer: one representative address per *cluster* (Huobi, Bittrex, Kraken…). Entity-level |
| `ofac.yaml` | OFAC scrape by INTERPOL CNTL, dated 2024-02-26. **Stale**; our own `labels/ofac_sdn_crypto.csv` (issued 2026-09-10) supersedes it |
| `etherscan-wordcloud-exchange.yaml` | 2023 Etherscan label crawl, `confidence: web_crawl`. Names like "Binance 14". No role |
| `hydra.yaml` | Hydra market addresses sourced from the Treasury 2022-04-05 action (`authority_data`) |

**Coverage of SAHYOG-set VASPs (address tags with `actor:`):** binance 69, huobi 103, kucoin 22,
cryptocom 3, bybit 1, bitget 1, okx 0. All of these are reserve or hot infrastructure, and
almost all are Western or global exchanges.

## What we take from it

1. **Hot-wallet seeds for sweep detection.** The Binance, KuCoin and Huobi reserve and hot
   addresses are exactly the "labeled hot wallet of entity X" that §6.2c needs. An address that
   sweeps into one of them behaves like a deposit address of X. Ingest them as
   `role: hot, source: tagpacks` at a lower source tier than curated labels.
2. **Actor registry.** `graphsense.actorpack.yaml` gives us a ready VASP/entity table (id, label,
   uri, jurisdiction) that includes the Indian exchanges. Use it to seed the `vasp` table and do
   entity resolution. The addresses behind those actors have to come from our own ground-truth
   deposits (§6.2a).
3. **Never promote a TagPacks hit to "deposit-accepting".** Per §6.3, a TagPacks hit is labeled
   infrastructure and gets the downgraded claim, unless sweep behaviour proves deposit.

## Addendum: what the live Case A trace showed (day1_verify.py)

- **`binance.yaml` matters more than the reserve pack.** It tags `1NDyJtNTjmwk5x…`, Binance's
  long-lived BTC hot wallet. The public trace (Zhdanova, OFAC) reached it at hop 2. That made
  the hop-1 address `1KNvwHu…` **sweep-proven as a Binance deposit address**: it is 1 of 34
  co-inputs in a consolidation paying 99.99% of the value to that hot wallet. Without this pack
  the trace would have passed *through* Binance and credited the next exchange downstream
  (BitMEX). That is exactly the pass-through confuser §11.2 warns about.
- **One de-facto deposit-level exception: BitMEX.** `exchange-wallets-bitmex_*.yaml` holds 336k
  addresses from BitMEX's own proof-of-reserves (Nov 2022). BitMEX documents that its customer
  deposit addresses are unique per user and start with `3BMEX` / `bc1qmex`
  (<https://blog.bitmex.com/reissuing-btc-wallet-addresses/>). So those addresses are
  exchange-published **deposit** addresses, even though the pack has no role field. They're
  legacy: reissued in Oct 2023, no longer credited from 31 Mar 2025. They aren't Indian and
  aren't on SAHYOG. Useful as historical fixtures only.
- **Sweep rule lesson.** "Any outflow to a hot wallet" is too weak: it mislabeled a *customer
  paying into* a BitMEX deposit address as a deposit address. "All outflows" is too strict:
  Binance consolidations piggy-back a tiny withdrawal output. Day-1 rule: ≥90% of the node's
  outflow value (per asset) goes to one entity's hot wallets. It's pinned by `_selfcheck()` in
  `day1_verify.py`. Day 3 adds the §6.2c "many distinct senders" condition.
