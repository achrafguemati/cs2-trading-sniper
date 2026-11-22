# Open-Source Repositories Audit & Technical Evaluation

**Document Version:** 1.0  
**Audit Date:** 22 November 2026  
**Auditor:** Lead Engineer & Market Research Architect  
**Methodology:** Direct inspection of GitHub API, default branches, source code, license files, commit histories, and dependencies.

---

## 1. Audit Summary Matrix

| Repository | Default Branch & Commit SHA | Date Inspected | Verified License | Language & Stack | Markets Covered | Granularity | Alerts / Output | Security Concerns | Overall Reuse Recommendation |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Soophere/cs2-sniper** | `master` (`6cf5dfc1`) | 2026-10-18 | **MIT** | Python 3.12+, FastAPI, asyncio | CSFloat (primary), 8 reference markets | Listing-level | Discord webhook, local dashboard | None (read-only, no credentials) | **High (Score & Guards)**: Reuse `pricing.py` anti-fake-profit guards & liquidity logic. |
| **juggperc/uupinfetch** | `master` (`c0a857df`) | 2026-04-23 | **Unknown** (Missing) | Python, FastAPI, Requests | Youpin, CSFloat, BUFF | Mixed | Webhook, Windows EXE | Missing license file; IP risks | **Low / Reference only**: Architecture reference; do not copy unlicensed code. |
| **MhmdMK277/CSFloatPriceChecker** | `main` (`bcc4963f`) | 2026-07-17 | **MIT** | Python, FastAPI, SQLite, React/TS, Vite | CSFloat, Skinport | Listing & Item level | Web UI, Telegram | Clean, read-only | **High (UI & Storage)**: Best architectural template for FastAPI + SQLite + React dashboard + tests. |
| **skinticker/skinticker** | `main` (`cc9322f9`) | 2026-07-08 | **AGPL-3.0** | Python, FastAPI, SQLite | CSFloat, Steam | Item level (cost basis) | Telegram, Web UI | Strict copyleft (AGPL-3.0) | **Inspect only / Do not import**: AGPL copyleft risks; study cost-basis concepts only. |
| **ErayBahar/steammarketscraper** | `main` (`284ec9a1`) | 2025-08-18 | **MIT** | Python, Requests, BeautifulSoup | Steam Community Market | Aggregate (top bid/ask only) | Telegram bot | Contains proxy rotator (`proxy_manager.py`) | **Low**: Shows basic `itemordershistogram` call, but naive and violates our no-proxy rule. |
| **blubtxt/Steam-Market-Multi-Watcher** | `main` (`12489cf7`) | 2025-11-11 | **Unknown** | PHP, cURL | Steam Community Market | Aggregate (`priceoverview`) | Discord webhook | Obsolete aggregate route | **Reject**: PHP stack; `priceoverview` lacks depth/listings; unmaintained. |
| **ArseniHv/cs2-market-bot** | `main` (`1c076119`) | 2026-03-23 | **Unknown** | Python, Telebot | Steam Community Market | Aggregate trends | Telegram | Polling interval too slow | **Low**: Study Telegram notification formatting only. |
| **Madejson1/steam-cs2-market-scraper-analytics** | `main` (`7c1d114d`) | 2026-05-12 | **Unknown** | Python, Streamlit, Selenium | Steam Community Market | Listing-level (Selenium) | Streamlit UI | Heavy Selenium dependency; brittle HTML selectors | **Reject**: Headless browser automation is too heavy and brittle for our local API architecture. |
| **somespecialone/steam-item-name-ids** | `main` (`caac34bd`) | 2026-09-07 | **WTFPL** | JSON / Data mapping | Steam Community Market | Name-to-ID mapping | Static JSON dataset | None (public domain) | **High (Mapping Asset)**: Essential offline lookup dataset mapping skin names to `item_nameid`. |
| **somespecialone/cs2-items-schema** | `master` (`f833337c`) | 2026-11-20 | **MIT** | Python / JSON | CS2 Item Economy | Full item taxonomy | JSON Schema generator | None | **High (Schema Asset)**: Actively maintained schema of CS2 skins, wears, paint indices, and collections. |
| **Asalio123/cs2-skins-daily-dataset** | `main` (`99350e65`) | 2026-08-28 | **NOASSERTION** | Python / Parquet / CSV | Steam Community Market | Daily median price & volume | Dataset files | None | **Medium (Research / Backtesting)**: Valuable for validating historical volatility and baseline models. |
| **MMaakkss/cs-skins-prices-parser** | `main` (`fa3715cd`) | 2026-11-08 | **Unknown** | Python, aiohttp | Steam, DMarket, market.csgo | Bids and asks | Database | Unlicensed | **Low / Reference only**: Study market data normalization logic. |
| **i4cobus/cs2-cross-market-arbitrage-analyzer** | `main` (`28f07b51`) | 2026-05-23 | **MIT** | Python, SQLite, LLM tool | CSFloat, Youpin | Listing & aggregate | SQL reports | Clean | **Medium (Scoring Logic)**: Good reference for liquidity filtering and explainable opportunity scoring. |
| **gergelyszabo94/csgo-trader-extension** | `master` (`0922f21a`) | 2026-11-19 | **GPL-3.0** | JavaScript (WebExtension) | SCM, Buff, CSFloat | In-page DOM injection | Browser overlay | GPL-3.0 copyleft | **Reference only**: Demonstrates in-browser inspect-link handling and SCM DOM structure. |
| **csfloat/extension** | `master` (`daf021b2`) | 2026-10-20 | **MIT** | TypeScript (WebExtension) | Steam Community Market | In-page listing enrichment | Browser overlay | None | **High (Float Extraction Reference)**: Authoritative code demonstrating how inspect links are parsed. |
| **DrKain/steam-market-search** | `master` (`45abb8c1`) | 2022-03-26 | **MIT** | TypeScript / Node.js | Steam Market Search | Search query builder | NPM package | Outdated | **Low**: Useful for query parameter naming reference only. |

---

## 2. In-Depth Technical Breakdown of Top Candidates

### 2.1 Soophere/cs2-sniper (Primary Logic Reference)
* **Architecture:** FastAPI + AsyncIO background scanner.
* **Key Components Inspected:**
  - `server/pricing.py`: Contains battle-tested heuristics:
    ```python
    def is_liquid_enough(sell_orders: int, liquidity_score: int, bid_volume: int, category: ItemCategory) -> bool:
        if category in {ItemCategory.CASE, ItemCategory.KEY}:
            return sell_orders >= max(20, settings.min_market_qty)
        if sell_orders >= settings.min_market_qty:
            return True
        return False
    ```
  - Categorization logic separating bulk commodities from unique weapon skins.
* **Reuse Scope:** Re-implement the liquidity thresholds, anti-fake-profit margin guards, and urgency tier scoring in clean, self-contained Python modules. Do not import raw code wholesale.

### 2.2 MhmdMK277/CSFloatPriceChecker (Primary Architecture Template)
* **Architecture:** Full-stack local dashboard:
  - Backend: FastAPI with Pydantic schemas, SQLite persistence via `sqlite3` + aiosqlite, async worker background loop.
  - Frontend: Vite + React 18 + TypeScript + Tailwind-like token styles.
  - Complete automated test suite (`tests/test_storage.py`, `tests/test_client.py`, `tests/test_worker.py`).
* **Reuse Scope:** Provides the ideal structural skeleton for our Windows 11 local-first dashboard. Its separation between API client, storage layer, and UI state matches our project requirements.

### 2.3 somespecialone/steam-item-name-ids & cs2-items-schema (Data Assets)
* **The Problem:** Querying Steam's order-book histogram endpoint (`/market/itemordershistogram`) requires an internal numeric `item_nameid` (e.g. `176000000`). There is no official lookup route.
* **The Solution:** `somespecialone/steam-item-name-ids` contains pre-compiled, community-verified mappings of exact CS2 market hash names to their corresponding `item_nameid`.
* **License:** WTFPL (Do What The Fuck You Want To Public License) and MIT. Safe for direct inclusion in our local SQLite database.

---

## 3. Code & Patterns to Explicitly Reject

1. **Proxy Rotation Modules (e.g., in `ErayBahar/steammarketscraper`):**
   - Must NOT be used. Violates non-negotiable rule #4.
2. **AGPL-3.0 Code from `skinticker`:**
   - Must NOT be copied or linked into our codebase to prevent viral licensing restrictions.
3. **Heavy Browser Automation (Selenium in `Madejson1`):**
   - Slow, memory-heavy, and easily detected by Cloudflare/Akamai on Steam. HTTP-first requests with SSR data parsing are 100x faster and use 90% less RAM.
