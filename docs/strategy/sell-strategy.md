# Strategy Design: Mode B — SELL SNIPER

**Document Version:** 1.0  
**Date:** 22 November 2026  
**Status:** Strategy Specification (No Auto-Execution)  

---

## 1. Strategy Objective

The **SELL SNIPER** engine monitors items currently owned in the user's inventory (or a manual watchlist) and compares them against live Steam Community Market buy orders. It detects **unusually high buy orders** (anomalous bids placed by collectors, misinformed traders, or aggressive buyers) that offer significantly higher seller proceeds than normal market value.

When an actionable high bid is verified, the engine alerts the user with exact proceeds after Valve fees, enabling the user to manually list their item on Steam at that price.

**Critical Constraint:** The engine is strictly read-only and will **never** list, sell, or modify items in the user's inventory.

---

## 2. Inventory Ingestion Without Credentials

### 2.1 Supported Ingestion Channels
To strictly respect Rule #2 (Zero Credential Storage):
1. **Public Steam Inventory Endpoint:**
   - URL: `https://steamcommunity.com/inventory/{steamid64}/730/2?l=english&count=5000`
   - Input: User provides their public `steamid64` (or custom vanity URL resolved to 64-bit ID).
   - Validation: Filters exclusively for items where `marketable == 1`.
   - Items with `marketable == 0` are placed in the "Held / Cooldown" tab with their `market_marketable_restriction` date.
2. **Manual Inventory Import (Private Profile Fallback):**
   - User pastes a list, CSV, or JSON file containing: `market_hash_name`, `wear`, `asset_id` (optional), and `cost_basis` (optional).
   - Allows users with private inventories to use the SELL SNIPER without changing profile privacy or sharing cookies.

---

## 3. Order-Book Ingestion & Quantity Analysis

### 3.1 Resolving the Deep Order Book
For each marketable item:
1. Lookup numeric `item_nameid` from the local database (`somespecialone/steam-item-name-ids`).
2. Query `/market/itemordershistogram?country={config.country}&language={config.language}&currency={config.currency_id}&item_nameid={item_nameid}&two_factor=0`.
   *(Note: `country`, `language`, and `currency_id` are explicit user configuration options stored with every observation; never hard-coded to US/USD).*
3. Parse the following metrics:
   - `highest_buy_order`: Top buyer bid (integer minor units).
   - `lowest_sell_order`: Cheapest active listing (integer minor units).
   - `buy_order_graph`: Array of depth points `[price, cumulative_qty, description]`.
   - `buy_order_table`: HTML table of discrete price levels.

### 3.2 Top-Level vs. Cumulative Quantity Parsing
* **The Problem:** The user's screenshot revealed a market showing:
  - Header: `"211,731 requests to buy at $5.09 or lower"`
  - Table: Level `$5.09` had a quantity of only **`1`**.
* **Engine Parsing Rule:**
  - `cumulative_quantity` = 211,731 (total buy orders across all price points down to $0.03).
  - `top_level_quantity` = 1 (exact number of items requested at the peak bid).
* **Alert Warning:** If `top_level_quantity == 1`, the alert must carry a `Single-Item Top Bid` warning: only a single item can execute at this exact price level.

---

## 4. Anomaly Detection & Order-Book Validation

### 4.1 What Constitutes an "Unusually High" Buy Order?
A high buy order is triggered when:
1. `highest_buy_order >= min_alert_threshold` (e.g. $\ge 100$ minor units).
2. **Multiple Over Baseline:**
   $$\text{Bid Multiple} = \frac{\text{highest\_buy\_order}}{P_{\text{baseline}}} \ge \text{configurable\_multiplier (e.g. } 1.30\times \text{ to } 3.00\times)$$
   Where $P_{\text{baseline}}$ is a verified historical baseline (e.g., median from local historical snapshots or verified Steam history). If no baseline is available, the alert is suppressed.

### 4.2 Buy Order Semantics & Execution Realities (Valve Rules)
* **Maximum Bid Commitment:** A buy order is the maximum amount the buyer is willing to spend.
* **Execution Price Rule:** If an item is listed for less than the buy order, the buyer pays the lower price. Listing below the highest buy order does NOT automatically yield the highest buy order price to the seller.
* **Order Priority:** When multiple buy orders exist that can fulfill a listing, Valve's matching engine selects the **oldest matching buy order** (FIFO by timestamp among eligible bids).
* **Wallet Balance & Order Cancellation:** Valve's official FAQ states that buy orders are automatically cancelled when an account's wallet balance can no longer fulfill them. We do not assume visible orders are definitively unfunded.
* **Persistence Check:** The engine pauses 3 seconds and re-queries `itemordershistogram`. This step confirms that the displayed market data persisted and was not an ephemeral snapshot, but does **not** prove that funds are reserved or that fill is guaranteed.
* **Inconsistent / Crossed Book Snapshots (`highest_buy_order >= lowest_sell_order`):**
  - If the highest bid is $\ge$ lowest ask, the book is in an **inconsistent snapshot state** (frequently caused by Steam CDN caching lag or variant tag mismatches).
  - **Engine Action:** Mark alert status as `INCONSISTENT SNAPSHOT / CROSSED BOOK`. Re-query after cooldown; never treat crossed book as an instant fill signal.
* **Status Classification:** Label anomalous bids as `Unverified High Bid` (never definitively "Ghost Order" without execution evidence).

---

## 5. Seller Proceeds Calculation & Economics

Given the top buyer bid $B$ (integer minor units):
1. Use the exact integer inversion function:
   $$\text{calc} = \text{calculate\_seller\_receives}(B)$$
   $$R = \text{calc}['\text{seller\_receives}'], \, B_{\text{actual}} = \text{calc}['\text{actual\_buyer\_pays}']$$
2. If `calc['is_exact_match']` is False, warn the user that $B$ is in a fee step-jump and listing at $B$ may adjust to $B_{\text{actual}}$.
3. Compare against normal sale proceeds:
   - $R_{\text{normal}} = \text{calculate\_seller\_receives}(P_{\text{baseline}})['\text{seller\_receives}']$
   - $\text{Incremental Net Gain} = R - R_{\text{normal}}$
4. The alert displays:
   - **Buyer Pays (Listing Target):** $B / 100$ (or currency minor unit divisor)
   - **Seller Receives (Net Wallet):** $R / 100$
   - **Incremental Profit:** $+ \$X.XX$ vs normal sale.

---

## 6. Execution Verification & Feedback Loop

Because market conditions fluctuate and high bids may be fulfilled by other users before the user lists:
1. **Manual Listing Instructions:**
   - The user opens their Steam Inventory page.
   - Selects the exact matching item and wear.
   - Sets the "Buyer Pays" amount to the exact `highest_buy_order` price.
2. **Precision Tracking in Database:**
   - After an alert is issued, the app records an `alert_event` in SQLite.
   - User logs the outcome via the UI:
     - `Filled Successfully` (records realized gain, increments model precision).
     - `Unfilled / Cancelled` (bid vanished or failed to match).
     - `Expired / Beaten by Another Seller`.
   - Allows tracking real-world alert precision over time.

---

## 7. Actionable Alert Output

* `[SELL CANDIDATE: UNVERIFIED HIGH BID]` status label.
* Exact Item Name, Wear & Inventory Asset ID.
* Live Highest Buy Order (Buyer Pays).
* Top-Level Quantity (e.g. `1 available`) vs Cumulative Orders.
* Lowest Active Sell Listing (Ask) & Spread.
* Calculated Seller Proceeds (Net into Steam Wallet).
* Incremental Gain vs. Normal Market Value.
* Warning Tags: `Snapshot Status: Consistent`, `Single Unit at Top Bid`.
* Direct Steam inventory listing link.
