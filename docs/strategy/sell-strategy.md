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
2. Query `/market/itemordershistogram?country=US&language=english&currency=1&item_nameid={item_nameid}&two_factor=0`.
3. Parse the following metrics:
   - `highest_buy_order`: Top buyer bid (integer cents).
   - `lowest_sell_order`: Cheapest active listing (integer cents).
   - `buy_order_graph`: Array of depth points `[price, cumulative_qty, description]`.
   - `buy_order_table`: HTML table of discrete price levels.

### 3.2 Top-Level vs. Cumulative Quantity Parsing
* **The Problem:** The user's screenshot revealed a market showing:
  - Header: `"211,731 requests to buy at $5.09 or lower"`
  - Table: Level `$5.09` had a quantity of only **`1`**.
* **Engine Parsing Rule:**
  - `cumulative_quantity` = 211,731 (total buy orders across all price points down to $0.03).
  - `top_level_quantity` = 1 (exact number of items requested at the peak bid).
* **Alert Warning:** If `top_level_quantity == 1`, the alert must carry a `Single-Item Fill Only` warning: if another seller beats you by 1 second, your item will not fill.

---

## 4. Anomaly Detection & Ghost Order Defense

### 4.1 What Constitutes an "Unusually High" Buy Order?
A high buy order is triggered when:
1. `highest_buy_order >= min_alert_threshold` (e.g. $\ge \$1.00 USD).
2. **Multiple Over Baseline:**
   $$\text{Bid Multiple} = \frac{\text{highest\_buy\_order}}{P_{\text{baseline}}} \ge \text{configurable\_multiplier (e.g. } 1.30\times \text{ to } 3.00\times)$$
   Where $P_{\text{baseline}}$ is the 7-day median sale price.

### 4.2 Defending Against Ghost / Unfunded Buy Orders
As established in our research, unfunded buy orders linger on the market until a sale is attempted:
1. **Pre-Alert Re-Check:**
   - Before firing an alert, the engine pauses 3 seconds and re-queries `itemordershistogram`.
   - If the bid vanished or shifted downward, the alert is cancelled.
2. **Crossed Book Check (`highest_buy_order >= lowest_sell_order`):**
   - If the highest bid is greater than or equal to the lowest ask, the book is **crossed**.
   - On SCM, a crossed book almost always signals:
     a) The high buy order is unfunded (ghost) and failed to match the cheap listing, OR
     b) Steam CDN cache desynchronization.
   - **Engine Action:** Mark alert status as `SUSPICIOUS: CROSSED BOOK`. Require explicit user skepticism.
3. **Spread Analysis:**
   - If `highest_buy_order` is 200% above the lowest sell listing, flag as `Probable Ghost / Unfunded Bid`.

---

## 5. Seller Proceeds Calculation & Economics

Given the top buyer bid $B_{\text{cents}}$:
1. Use the exact integer cents inversion function:
   $$R_{\text{cents}}, \, \text{steam\_fee}_{\text{cents}}, \, \text{pub\_fee}_{\text{cents}} = \text{calculate\_seller\_receives}(B_{\text{cents}})$$
2. Compare against normal sale proceeds:
   - $R_{\text{normal}} = \text{calculate\_seller\_receives}(P_{\text{baseline}})$
   - $\text{Incremental Net Gain} = R_{\text{cents}} - R_{\text{normal}}$
3. The alert displays both:
   - **Buyer Pays (Listing Price):** $B_{\text{cents}} / 100$
   - **Seller Receives (Net Wallet):** $R_{\text{cents}} / 100$
   - **Incremental Profit:** $+ \$X.XX$ vs normal sale.

---

## 6. Execution Verification & Feedback Loop

Because buy orders can disappear or fail funding checks:
1. **Manual Execution Instructions:**
   - The user opens their Steam Inventory page.
   - Selects the exact item.
   - Sets the "Buyer Pays" amount to the exact `highest_buy_order` price.
2. **Precision Tracking in Database:**
   - After an alert is issued, the app records an `alert_event` in SQLite.
   - User clicks one of three feedback buttons on the dashboard:
     - `Filled Successfully` (records realized gain, increments model precision).
     - `Ghost / Failed Execution` (records ghost event, penalizes item anomaly score).
     - `Expired / Too Slow` (bid was taken by someone else).
   - This allows the system to self-measure its real-world alert precision over time.

---

## 7. Actionable Alert Output

* `[SELL CANDIDATE]` status label.
* Exact Item Name, Wear & Inventory Asset ID.
* Live Highest Buy Order (Buyer Pays).
* Top-Level Quantity (e.g. `1 available`) vs Cumulative Orders.
* Lowest Active Sell Listing (Ask) & Spread.
* Calculated Seller Proceeds (Net into Steam Wallet).
* Incremental Gain vs. Normal Market Value.
* Warning Tags: `Ghost Risk: High`, `Crossed Book: No`, `Single Unit`.
* Direct Steam inventory listing link.
