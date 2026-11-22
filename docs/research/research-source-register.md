# Research Source Register & Primary Evidence Log

This register tracks official primary sources, API endpoints, protocol structures, and external data feeds used by the CS2 Trading Sniper, maintained according to the `steam-market-research` skill.

---

## Primary Sources & API Endpoints

### 1. Official Steam Community Market FAQ
* **Source URL:** `https://support.steampowered.com/kb_article.php?ref=6088-UDXM-7214` / `https://steamcommunity.com/market/`
* **Date Checked (UTC):** 2026-11-22T17:35:00Z
* **Authority Level:** Official Valve Primary Source
* **Evidence Summary:**
  - **Steam Transaction Fee:** 5% charged to buyer, collected by Steam (minimum 1 minor unit).
  - **CS2 Publisher Fee:** 10% charged to buyer, collected by Valve for CS2 (minimum 1 minor unit).
  - **Rounding Logic:** Both fees are independently floored ($\lfloor \dots \rfloor$).
  - **Buy Order Matching:** "If multiple buy orders can fulfill a new listing, the oldest buy order is selected. The buyer pays the price of the listing that fulfills their order."
  - **Over-Allocated Wallets:** "When a buy order is created, funds are not removed from your wallet... when an order is matched, if you don't have enough funds, the order will be cancelled."
* **Confidence Classification:** **CONFIRMED BY PRIMARY SOURCE**
* **Project Relevance:** Establishes non-negotiable foundations for fee calculation, inverse price solving, and buy-order execution modeling.
* **Recommended Action:** Preserve exact integer dual-floor implementation in `src/sniper/core/fees.py`.
* **Changes Project Requirements:** No.

---

### 2. Steam Community Market SSR React Architecture (Post-May 12, 2026)
* **Target URLs:** `https://steamcommunity.com/market/listings/730/G...` and standard market pages.
* **Date Checked (UTC):** 2026-11-22T17:35:00Z
* **Authority Level:** Empirical Protocol Capture
* **Evidence Summary:**
  - Modern market item pages render via a Server-Side Rendered (SSR) React application.
  - Page hydration state is embedded in a `<script id="valve-ssr-data" type="application/json">` block.
  - The hydration dictionary contains a `loaderData` array where index `[3]` (`loaderData[3]`) contains bucket groupings across wear categories (Factory New, Minimal Wear, Field-Tested, Well-Worn, Battle-Scarred), minimum prices (`min_price`), and class IDs.
  - Items share a canonical grouped identifier (e.g. `G...`), grouping all wear variations under one URL root.
* **Confidence Classification:** **CONFIRMED BY PRIMARY SOURCE** (Empirically verified in Phase 1 and recorded in `tests/fixtures/grouped_ssr_data.json`).
* **Project Relevance:** Essential for Mode A (BUY Sniper) item resolution and wear bucket traversal.
* **Recommended Action:** Use `SsrListingLoaderData` Pydantic model in Milestone 2 identity resolver.
* **Changes Project Requirements:** No.

---

### 3. Steam Market Orderbook Histogram Endpoint
* **Endpoint URL:** `https://steamcommunity.com/market/itemordershistogram`
* **Query Parameters:** `country`, `language`, `currency`, `item_nameid`, `two_factor`
* **Date Checked (UTC):** 2026-11-22T17:35:00Z
* **Authority Level:** Unofficial / Public Steam Community Web API
* **Evidence Summary:**
  - Returns JSON payload containing `highest_buy_order` (string minor units), `lowest_sell_order` (string minor units), `buy_order_graph` (price, cumulative quantity, description), `sell_order_graph`, and HTML summary tables.
  - Does NOT require authentication or session cookies.
  - Subject to aggressive rate-limiting (HTTP 429) if requested without pacing; triggers HTTP 403 / HTML challenge if probed via unauthorized automated patterns.
* **Confidence Classification:** **CONFIRMED BY PRIMARY SOURCE** (Empirically verified via smoke test and unit fixtures).
* **Project Relevance:** Primary data feed for Mode B (SELL Sniper) buy order monitoring and Mode A orderbook reference.
* **Recommended Action:** Gate all histogram requests strictly through `ReadOnlyHttpClient` and `SequentialRateLimiter` with randomized sequential delay (4–6s).
* **Changes Project Requirements:** No.

---

### 4. Steam Public Community Inventory Endpoint
* **Endpoint URL:** `https://steamcommunity.com/inventory/{steamid64}/730/2`
* **Query Parameters:** `l=english`, `count=5000`
* **Date Checked (UTC):** 2026-11-22T17:35:00Z
* **Authority Level:** Official Public Steam Community API
* **Evidence Summary:**
  - Ingests CS2 inventory for public profiles without requiring user credentials, API keys, or cookies.
  - Returns `assets` list (assetid, classid, instanceid) and `descriptions` list (market_hash_name, marketable, tradable, `market_marketable_restriction`).
  - `market_marketable_restriction` explicitly provides the integer number of days remaining on the post-purchase 7-day re-list hold.
* **Confidence Classification:** **CONFIRMED BY PRIMARY SOURCE**
* **Project Relevance:** Ingestion source for Mode B inventory tracking and 7-day holding tracker.
* **Recommended Action:** Maintain Pydantic model `PublicInventoryResponse` and ingest market hold restrictions directly from description tags.
* **Changes Project Requirements:** No.

---

### 5. Steamworks Currency & Regional Pricing Documentation
* **Source:** Steamworks Partner Documentation / Public Steam Currency Enum Table
* **Date Checked (UTC):** 2026-11-22T17:35:00Z
* **Authority Level:** Official Valve Partner Documentation
* **Evidence Summary:**
  - Currencies are mapped to numeric IDs (1 = USD, 3 = EUR, 8 = JPY, etc.).
  - Currencies vary by minor unit precision (USD/EUR = 2 decimals, JPY = 0 decimals).
  - Certain regional currencies have specific minimum transaction amounts and allowed price increments.
  - Tunisia is not assigned a native currency (TND); accounts registered in Tunisia are billed in USD.
* **Confidence Classification:** **CONFIRMED BY PRIMARY SOURCE**
* **Project Relevance:** Validates currency configuration and eliminates incorrect assumptions regarding universal 3-minor-unit minimums.
* **Recommended Action:** Maintain `CurrencyConfig` with explicit precision, verified listing minimums, and explicit UNKNOWN flags for unverified currencies.
* **Changes Project Requirements:** No.
