# Test & Acceptance Plan

**Document Version:** 1.0  
**Date:** 22 November 2026  
**Testing Framework:** Python Standard Library `unittest` (`unittest.IsolatedAsyncioTestCase` for async suites) + recorded real-world fixtures  

---

## 1. Unit Test Matrix

Every module must achieve high test coverage before milestone completion:

| Test Module | Coverage Scope | Target Test Cases |
| :--- | :--- | :--- |
| **`test_fees.py`** | Integer fee model: exact arithmetic under the project's modeled formula (Valve's exact rounding implementation remains UNKNOWN) | Mathematical fee-floor example (not a market minimum), $1.00 $\rightarrow$ $0.88 net, high-tier ($500 $\rightarrow$ $434.79 net), rounding gaps, currency-required market validation, and explicit pure inversion. |
| **`test_identity.py`** | URL parsing and variant normalization | Canonical URLs, grouped `G...` URLs with `category_Exterior=WearCategory0-4`, StatTrak/Souvenir preservation, invalid URL rejection, ambiguous token handling. |
| **`test_orderbook.py`** | Order-book depth & quantity parsing | Top-level quantity extraction, cumulative quantity extraction, empty order book, single-bid book, crossed-book detection (`highest_bid >= lowest_ask`). |
| **`test_listings.py`** | Listing-level JSON & HTML parsing | Extraction of `listingid`, `assetid`, integer cents price, inspect link parsing, sticker extraction from description tags, currency validation. |
| **`test_economics.py`** | Profit & ROI scenarios | Conservative scenario with 5% risk buffer, baseline scenario, optimistic scenario, ROI division by acquisition cost, zero-profit rejection. |
| **`test_ratelimit.py`** | Token bucket & circuit breaker | Max requests per minute pacing, jitter delay distribution, 300s sleep trigger on HTTP 429/403, canary recovery test. |
| **`test_inventory.py`** | Public & manual inventory (M5, M5.1, M5.2) | Exact `(classid, instanceid)` matching, fail-closed pagination and cursor cycles (`seen_cursors`), truncation elimination on completed inventories, strict manual JSON/CSV numeric amount/days/bool validation, and manual asset ID namespaces. |
| **`test_sell_engine.py`** | SELL opportunity scoring & retry limits (M5, M5.1, M5.2) | Strict identity provenance gate, historical buy-order median baseline, crossed-book suppression, un-fabricated buy depth (`BUY DEPTH UNAVAILABLE`), per-channel delivery-aware cooldown, cumulative retry exhaustion capping, fresh retry budget on new bids. |
| **`test_notifications.py`** | Free notifications (Discord & Telegram) | Discord rich embed formatting & limits, Telegram safe HTML escaping, channel failure isolation, 429 rate limit backoff, secret redaction, independent channel enable/disable. |
| **`test_storage_m5.py`** | Migration 4, Retries & Timestamps (M5, M5.1, M5.2) | Migration 4 upgrade/rollback, persistent cumulative retry backoff accounting per fingerprint and channel, fail-closed timestamp validation (missing, malformed, future, clock skew). |
| **`test_listing_parser.py`** | Individual listing parsing & metadata (M6) | Extraction of `steam_listing_id`, `asset_id`, `converted_price + converted_fee`, inspect links, sticker/charm extraction, float/seed extraction, sold listing rejection, currency & variant mismatch rejection, cookie-free `var line1` HTML extraction. |
| **`test_buy_engine.py`** | BUY Sniper engine, scenarios & Migration 5 (M6) | Volume-weighted median baselines, Conservative 7-day 5% risk buffer + exact M4 fee inversion, `DATA INSUFFICIENT` & illiquidity suppression, 7d/30d spike guard, Discord/Telegram BUY notifications, multi-listing cooldown independence, cumulative retry cap, Migration 5 upgrade/rollback. |
| **`test_portfolio_and_scheduler.py`** | Portfolio 7-day tracker & scheduler (M7) | Steam authoritative `"After ... GMT"` lock parsing, zero `RESALE READY` alerts while locked, `Under Water` break-even detection, automatic unlock transitions & Discord/Telegram dispatch, exact manual sale P&L, `SniperScheduler` `<= 12 RPM` cap & breaker deferral, Migration 6 rollback guard, CLI `portfolio` commands. |
| **`test_cli.py`** | CLI interface & lifecycle | CLI commands parsing (`import-inventory`, `import-csv`, `import-json`, `watchlist`, `import-history`, `scan-buy`, `scan-sell`, `portfolio`, `status`), `python-dotenv` `.env` loading, explicit `ReadOnlyHttpClient.aclose()`. |

---

## 2. Integration & Fixture Testing

1. **Recorded Fixtures (Zero Live Dependency in CI):**
   - Save anonymized, recorded HTTP responses for:
     - SSR Hydration HTML (`fixtures/steam_ssr_grouped.html`)
     - Listings Render JSON (`fixtures/steam_listings_render.json`)
     - Orderbook Histogram JSON (`fixtures/steam_histogram.json`)
     - Public Inventory JSON (`fixtures/steam_inventory.json`)
   - All regression tests run against recorded fixtures to prevent hitting Steam rate limits during development.
2. **Controlled Live Smoke Tests:**
   - Single manual canary request to verify live Steam endpoint structure.
   - Strictly throttled; never executed in automated continuous CI loops.

---

## 3. Product Acceptance Criteria Verification

| # | Acceptance Criterion | Verification Method |
| :--- | :--- | :--- |
| 1 | User can add standard Steam URL and grouped `G...` URL with wear filter. | URL parser unit test with both input types. |
| 2 | Parser resolves canonical identity or clearly explains ambiguity. | Unit test verifying zero silent mismatches. |
| 3 | BUY engine displays real individual listings with listing IDs. | Listing parser test asserting presence of `listingid` and `assetid`. |
| 4 | BUY candidate net economics include acquisition cost, fees, and 7-day risk buffer. | Economic math test confirming conservative exit scenario calculation. |
| 5 | SELL engine imports public/manual inventory and distinguishes marketable items. | Inventory test asserting locked items are filtered into holding tab. |
| 6 | SELL candidate displays highest bid, top-level qty, lowest ask, and cumulative qty. | Histogram parser test asserting separate top vs cumulative quantities. |
| 7 | Crossed/stale book never becomes a high-confidence signal without revalidation. | Scorer test asserting 50-point deduction and `Crossed Book` warning tag. |
| 8 | Alerts are deduplicated and contain exact Steam market links. | Alert manager test verifying cooldown window and URL formatting. |
| 9 | All requests respect rate budget; 429 triggers 300s backoff and visible status. | Rate limiter test asserting 429 halts queue and updates health status. |
| 10 | Zero credential storage; no auto-execution code paths exist in the product. | Static code security audit verifying absence of password/cookie/trade endpoints. |
| 11 | Complete test suite passes cleanly. | `python -m unittest` run report with 100% green tests across CI environments. |
| 12 | User can launch locally on Windows 11 with single command. | Execution test of `launch.bat`. |
