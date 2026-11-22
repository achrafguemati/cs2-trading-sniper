# Reddit Community Market Intelligence Log

This document tracks ground-truth trader observations, market anomaly reports, and policy discussions from `r/csgomarketforum`, `r/GlobalOffensiveTrade`, and `r/cs2`, evaluated according to the `reddit-community-intel` skill.

---

## Intelligence Entries

### Entry 1: Buy Order Priority, Over-Allocation, and Automatic Cancellation
* **Source:** Official Valve Steam Community Market FAQ (<https://help.steampowered.com/en/faqs/view/61F0-72B7-9A18-C70B>) cross-referenced against recurrent `r/csgomarketforum` discussions.
* **Date Checked (UTC):** 2026-11-22T17:30:00Z
* **Evidence Summary:** Traders frequently debate whether a seller who lists below the highest active buy order receives the buyer's maximum bid or their own lower listing price. Experienced market participants confirmed Valve's official rules: buyers pay the price of the listing that fulfills their order; they do not overpay if an item is listed for less. Furthermore, buy orders placed against an over-allocated Steam Wallet are automatically cancelled by Valve's backend when the wallet balance is insufficient at execution time.
* **Confidence Classification:** **CONFIRMED BY PRIMARY SOURCE** (Matches official Valve Community Market FAQ).
* **Project Relevance:** Directly validates Mode B (SELL Sniper) specifications. A seller cannot expect to receive an above-market bid simply by undercutting it; orders execute at the lower price.
* **Recommended Action:** Enforce conservative valuation: Mode B alerts must evaluate net proceeds against the verified highest buy order or lowest ask, with clear warnings that crossed books (`highest_bid >= lowest_ask`) represent inconsistent or transient states.
* **Changes Project Requirements:** No (Reaffirms existing M1.2 specifications).

---

### Entry 2: Speculation Regarding Regional Currency Minimum Price Adjustments
* **Source:** `r/csgomarketforum` (Community discussion lead, thread title variants around regional listing errors)
* **Date Checked (UTC):** 2026-11-22T17:30:00Z
* **Thread Reference:** Community sentiment / Unverified research lead (exact thread URL pending archival confirmation)
* **Evidence Summary:** Several users reported inability to list low-tier items below certain currency thresholds in non-USD currencies, speculating that Valve adjusted minimum transaction thresholds or price steps to reduce microtransaction load. However, other users in the same thread argued this was due to local currency conversion rounding or temporary database sync delays.
* **Confidence Classification:** **UNVERIFIED RESEARCH LEAD / COMMUNITY SPECULATION** (Unresolved speculation regarding exact numbers).
* **Project Relevance:** Highlights that Steam does NOT enforce a flat universal 3-minor-unit minimum across all global currencies. Non-USD currencies (like JPY, BRL, or regional pricing) have distinct minimums and allowed price steps.
* **Recommended Action:** Do not assume 3 minor units is a market listing minimum. The official Valve Community Market FAQ says some currencies may have a minimum sale price but does not specify USD as $0.03. Mark USD, EUR, JPY, and regional listing thresholds as `UNKNOWN` until verified by official Valve documentation or reproducible Community Market/client captures. Synthetic fixtures are not empirical evidence of live listing minimums.
* **Changes Project Requirements:** Yes — Successfully addressed in M1.1/M1.2 currency updates (separated fee floors from currency-specific market listing minimums; financial calculations fail closed on unverified currencies).

---

### Entry 3: Ghost Buy Orders and Unfunded High Bids
* **Source:** `r/csgomarketforum` (Community discussions on high bids on illiquid items)
* **Date Checked (UTC):** 2026-11-22T17:30:00Z
* **Thread Reference:** Community sentiment / Unverified research lead
* **Evidence Summary:** Users noted that on illiquid items (e.g. Souvenirs or rare pattern seeds), high buy orders sometimes fail to execute when a seller lists an item matching the exact hash name. The community hypothesizes two causes: (1) transient database sync latency where an order was already cancelled or filled, or (2) the buyer's wallet funds were spent on another concurrent order, leaving the remaining order pending cancellation upon next matching cycle.
* **Confidence Classification:** **UNVERIFIED RESEARCH LEAD (Anecdotal Community Observations)**
* **Project Relevance:** Critical for Mode B (SELL Sniper). Alerts based solely on a single high buy order could mislead users into listing an item that will not fill.
* **Recommended Action:** Require mandatory depth verification (`top_level_buy_quantity`, `cumulative_buy_quantity`), compare against lowest ask, and label high-bid signals with `Needs verification` status tags.
* **Changes Project Requirements:** No (Already incorporated into Mode B strategy specification).

---

### Entry 4: Payout and KYC Shifts Across Secondary Marketplaces
* **Source:** `r/GlobalOffensiveTrade` (Community discussions on non-EU/US cashout friction)
* **Date Checked (UTC):** 2026-11-22T17:30:00Z
* **Thread Reference:** Community sentiment / Unverified research lead
* **Evidence Summary:** Traders outside the EU and US report increasing friction with automated payout methods (Stripe, SEPA, Payoneer) on platforms like Skinport and DMarket due to enhanced KYC and regional banking bans. Platforms like CSFloat remain the most accessible peer-to-peer option via crypto or select virtual bank accounts, while local marketplace options in North Africa remain severely restricted.
* **Confidence Classification:** **UNVERIFIED RESEARCH LEAD (Macro Trader Sentiment)**
* **Project Relevance:** Reaffirms our regional feasibility assessment for Tunisia: Steam Wallet funds cannot be assumed to equal withdrawable cash, and secondary cashout estimates must remain strictly segregated from Steam Wallet accounting.
* **Recommended Action:** Maintain strict separation between Steam Wallet valuation and cashout estimates; do not advise automated cashout routes that require VPN or false residency.
* **Changes Project Requirements:** No (Reaffirms project safety rules).
