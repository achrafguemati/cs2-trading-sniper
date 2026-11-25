# Open-Source Project Watchlist & Audit Log

This watchlist tracks open-source repositories audited for potential code reuse, architectural reference, and security evaluation, in accordance with the `github-open-source-audit` skill.

---

## Audit Matrix Summary

| Repository | License | Activity / Last Push | Safety & Read-Only | Classification | Primary Use / Note |
| :--- | :--- | :--- | :--- | :--- | :--- |
| [`somespecialone/steam-market-ids`](https://github.com/somespecialone/steam-market-ids) | WTFPL 2.0 | Pinned revision used for M2.1 seed | ✅ Safe (Static Data) | **Reuse Candidate (data only)** | Partial upstream mapping of exact `market_hash_name` strings to `item_name_id`; no `classid`. |
| [`somespecialone/cs2-items-schema`](https://github.com/somespecialone/cs2-items-schema) | MIT | Active (`main` branch) | ✅ Safe (Static Data) | **Reuse Candidate** | Maps game item definitions, paint index, rarity. |
| [`i4cobus/cs2-cross-market-arbitrage-analyzer`](https://github.com/i4cobus/cs2-cross-market-arbitrage-analyzer) | MIT | Maintained | ✅ Safe (Read-Only) | **Reference Only** | Multi-market fee logic (uses floating point). |
| [`Luca-Ospel/cs2-market-scraper`](https://github.com/Luca-Ospel/cs2-market-scraper) | MIT | Inactive | ⚠️ Unbuffered Scraper | **Reference Only** | Legacy scraper; superseded by our async client. |
| [`MhmdMK277/CSFloatPriceChecker`](https://github.com/MhmdMK277/CSFloatPriceChecker) | MIT | Active (`2026-08`) | ✅ Safe (Public API) | **Reuse Candidate (concepts)** | Unauthenticated Skinport `/v1/items` feed, tiered fees (`markets.py`), wear-bracket float percentiles & tier proximity (`stats.py`). |
| [`maciejL03/price-oracle`](https://github.com/maciejL03/price-oracle) | MIT | Active (`2026-11`) | ✅ Safe (Read-Only) | **Reuse Candidate (concepts)** | Skinport + Steam snapshot normalization, quote freshness checks (`max_quote_age`), clean read-only market adapter contracts. |
| [`spikeshowglhf-max/cs2-skin-pricer`](https://github.com/spikeshowglhf-max/cs2-skin-pricer) | MIT | Active (`2026-11`) | ✅ Safe (Read-Only) | **Reuse Candidate (concepts)** | Public CS.Market `/api/v2/prices/USD.json` feed & multi-venue comparison with Discord webhook alerts. |
| [`woctezuma/steam-market`](https://github.com/woctezuma/steam-market) | MIT | Maintained | ✅ Safe (Read-Only core) | **Reference Only** | Two-stage funnel: bulk low-cost screen before selective live orderbook confirmation (`apply_workflow` also has optional crafting flags excluded here). |
| [`raitnigol/Steam-Market-Arbitrage-Calculator`](https://github.com/raitnigol/Steam-Market-Arbitrage-Calculator) | Unlicense | Active (`2026-05`) | ✅ Safe (Calculator) | **Reference Only** | Client-side arbitrage percentage helpers (excluded float fee math in favor of our exact integer model). |
| [`ByMykel/CSGO-API`](https://github.com/ByMykel/CSGO-API) | MIT | Active (`2026-11`) | ✅ Safe (Static JSON) | **Reuse Candidate (data reference)** | Comprehensive open CS2 skin, collection, crate, sticker & charm catalog JSONs. |
| [`ernsahin/Csfloat-Trades`](https://github.com/ernsahin/Csfloat-Trades) | No License | Active (`2026-11`) | ✅ Safe (Read-Only) | **Reference Only (zero code copy)** | Strategy taxonomy only: float tier upgrade proximity, dynamic profit tiers, conservative vs aggressive sticker retention %. |
| [`ErayBahar/steammarketscraper`](https://github.com/ErayBahar/steammarketscraper) | MIT | Active (`2026-07`) | ❌ Proxy Rotation | **License or Security Concern (Reject)** | Contains `core/proxy_manager.py` and rotating user-agent evasion; strictly incompatible with `AGENTS.md` Rule 3. |
| [`Soophere/cs2-sniper`](https://github.com/Soophere/cs2-sniper) | N/A | Inaccessible / 404 | ❌ Unverified | **Reject** | Repository deleted, private, or non-existent. |

---

## Detailed Repository Audits

### 1. `somespecialone/steam-market-ids`
* **Source URL:** `https://github.com/somespecialone/steam-market-ids`
* **Target Ref / Branch:** Immutable commit `caac34bd8a28578e800501da5cc9f16c7cb3fa67` (do not silently follow `main` for verified imports).
* **Date Checked (UTC):** 2026-11-22T22:11:33Z (license) and 2026-11-22T22:13:43Z (dataset).
* **SPDX License & Evidence:** WTFPL 2.0. Verified the pinned root `LICENSE` file; its opening identifies “DO WHAT THE FUCK YOU WANT TO PUBLIC LICENSE, Version 2, December 2004.” The prior MIT label was incorrect.
* **Language & Tech:** Python, JSON mapping tables.
* **Project Relevance:** Provides `data/CS2/item_names.json`, a JSON object mapping exact canonical names to positive integer `item_nameid` values. Pinned file SHA-256: `9f34cee0a6f5479017d227568e1c8132c8b3c4f82b2d87123a80951e5adb3212`; 1,491,818 bytes; 26,935 mappings. Example Redline values: FT `7178002`, MW `7179644`, WW `7181849`, BS `7179463`; Factory New is absent and remains unresolved. StatTrak is separately keyed (FT `7180207`).
* **Safety Evaluation:** Strictly read-only data assets. No network mutations, no credential requests, no proxy rotation.
* **Reusable Assets:** Mapping data only; upstream JSON is vendored byte-for-byte at `src/sniper/data/seed_market_ids.json`. Its dataset scope is explicitly partial relative to all/current Steam market items. The file contains no `classid`; any class ID in an identity result must carry its own SSR provenance.
* **Classification:** **Reuse Candidate (data only; WTFPL 2.0)**
* **Action:** Verified import is pinned to this revision, exact file digest, source path, timestamp, license, and record count. Missing names stay `None`; never infer IDs.

### 2. `somespecialone/cs2-items-schema`
* **Source URL:** `https://github.com/somespecialone/cs2-items-schema`
* **Target Ref / Branch:** `main` (automated daily extraction workflow)
* **Date Checked (UTC):** 2026-11-22T17:25:00Z
* **SPDX License & Evidence:** MIT License. Permissive open-source license file verified in repository root.
* **Language & Tech:** Python, GitHub Actions automated CI extraction from CS2 VPK files.
* **Project Relevance:** Provides static item schema extracted from CS2 VPK/game files, detailing weapon names, paint wear boundaries, and skin categories.
* **Safety Evaluation:** 100% static data generated via automated CI. Zero security risks.
* **Reusable Assets:** Schema validation fixtures and skin classification rules.
* **Classification:** **Reuse Candidate**
* **Action:** Use as a secondary validation source for item metadata and wear tier confirmation in Milestone 2.

### 3. `i4cobus/cs2-cross-market-arbitrage-analyzer`
* **Source URL:** `https://github.com/i4cobus/cs2-cross-market-arbitrage-analyzer`
* **Date Checked (UTC):** 2026-11-22T17:25:00Z
* **SPDX License:** MIT.
* **Language & Tech:** Python (asyncio, requests).
* **Project Relevance:** Analyzes price discrepancies across CSFloat, Skinport, Buff, and Steam Community Market.
* **Safety Evaluation:** Read-only market observer. Does not store account credentials.
* **Issues / Non-Compliance:** Uses floating-point arithmetic for pricing and fees; approximates Steam fees with a naive 0.85 multiplier rather than the exact dual-floor integer schedule.
* **Classification:** **Reference Only**
* **Action:** Consult for secondary market fee comparisons in documentation; do not reuse pricing calculation code.

### 4. `Luca-Ospel/cs2-market-scraper`
* **Source URL:** `https://github.com/Luca-Ospel/cs2-market-scraper`
* **Date Checked (UTC):** 2026-11-22T17:25:00Z
* **SPDX License:** MIT.
* **Language & Tech:** Python (BeautifulSoup, aiohttp).
* **Project Relevance:** Scrapes listings and histogram orderbooks from Steam.
* **Safety Evaluation:** Read-only scraper. However, it lacks robust exponential backoff, monotonic request serialization, and Pydantic schema validation. Rapid polling causes immediate HTTP 429 blocks.
* **Classification:** **Reference Only**
* **Action:** Superseded by our hardened `ReadOnlyHttpClient` and `SequentialRateLimiter`. Retain as reference for legacy scraping edge cases only.

### 5. `MhmdMK277/CSFloatPriceChecker` (Re-Audited November 2026)
* **Source URL:** `https://github.com/MhmdMK277/CSFloatPriceChecker`
* **Date Checked (UTC):** 2026-11-25T10:23:00Z
* **SPDX License & Evidence:** MIT License (verified `LICENSE` in repository root).
* **Language & Tech:** Python (`httpx`, `FastAPI`, `React`), SQLite cache.
* **Project Relevance:** Implements unauthenticated `GET https://api.skinport.com/v1/items?app_id=730&currency=USD` (`Accept-Encoding: br`) caching ~37k CS2 items, static multi-venue seller fee schedules (`markets.py`: Skinport 12%/9%/6%, CSFloat 2%, Buff163 2.5%), wear-bracket percentile calculations (`stats.py:deal_reason`), and next-better-wear comparison.
* **Safety Evaluation:** Read-only public API endpoints; zero credential harvesting or proxy evasion. Note `core/skinport.py` uses `round(float(...) * 100)` — our re-implementation strictly parses via `Decimal` -> integer cents instead.
* **Classification:** **Reuse Candidate (MIT concepts; integer-rewritten)**

### 6. `maciejL03/price-oracle`
* **Source URL:** `https://github.com/maciejL03/price-oracle`
* **Date Checked (UTC):** 2026-11-25T10:24:20Z
* **SPDX License & Evidence:** MIT License (`Copyright (c) 2026 Maciej Dorian Lapczynski`).
* **Language & Tech:** Python (`httpx`, `Pydantic`, `Decimal`).
* **Project Relevance:** Clean separation between quote normalization (`PricingService`, `QuoteExclusionReason.STALE` freshness window) and cached Skinport index loading (`SkinportMarketplace`).
* **Safety Evaluation:** Strictly read-only public observers; zero credential inputs.
* **Classification:** **Reuse Candidate (concepts)**

### 7. `spikeshowglhf-max/cs2-skin-pricer`
* **Source URL:** `https://github.com/spikeshowglhf-max/cs2-skin-pricer`
* **Date Checked (UTC):** 2026-11-25T10:24:20Z
* **SPDX License & Evidence:** MIT License (`Copyright (c) 2026 spikeshowglhf-max`).
* **Language & Tech:** Python stdlib + HTML/JS.
* **Project Relevance:** Documents public `https://market.csgo.com/api/v2/prices/USD.json` CS2 market feed and Discord alert formatting.
* **Safety Evaluation:** Read-only public price feeds only. Superseded rate limiter / float math by our strict `SequentialRateLimiter` and integer arithmetic.
* **Classification:** **Reuse Candidate (feed reference)**

### 8. `woctezuma/steam-market`
* **Source URL:** `https://github.com/woctezuma/steam-market`
* **Date Checked (UTC):** 2026-11-25T10:23:08Z
* **SPDX License & Evidence:** MIT License (`Copyright (c) 2019 Wok`).
* **Language & Tech:** Python.
* **Project Relevance:** Demonstrates clean two-stage funnel optimization: run bulk low-cost filtering before spending strict Steam orderbook requests on top shortlisted candidates. Note automated execution flags (`create_then_sell_booster_packs_for_batch`) are strictly ignored per `AGENTS.md`.
* **Classification:** **Reference Only**

### 9. `ernsahin/Csfloat-Trades`
* **Source URL:** `https://github.com/ernsahin/Csfloat-Trades`
* **Date Checked (UTC):** 2026-11-25T10:23:07Z
* **SPDX License & Evidence:** No license file present in repository root.
* **Language & Tech:** Python (`requests`, Flask).
* **Project Relevance:** Clear taxonomy of profit opportunity strategies: Price Anomaly (`>= 8%` dip), Float Tier Upgrade Proximity (`< 0.008` from next tier), Low Float Top-10% Percentile, Conservative Sticker Retention Tiers (`1% / 4% / 6%`), and Dynamic ROI Targets by skin price bracket.
* **Classification:** **Reference Only (conceptual strategies only; zero code copied due to missing license)**

### 10. `ErayBahar/steammarketscraper`
* **Source URL:** `https://github.com/ErayBahar/steammarketscraper`
* **Date Checked (UTC):** 2026-11-25T10:23:07Z
* **SPDX License & Evidence:** MIT License.
* **Safety Evaluation:** Includes `core/proxy_manager.py` for IP/proxy rotation and user-agent spoofing to evade Steam rate limits.
* **Classification:** **License or Security Concern (Reject — violates `AGENTS.md` Rule 3)**

### 11. `Soophere/cs2-sniper`
* **Source URL:** `https://github.com/Soophere/cs2-sniper`
* **Date Checked (UTC):** 2026-11-22T17:25:00Z
* **SPDX License:** Unknown / Unavailable.
* **Project Relevance:** Named in initial research leads.
* **Safety Evaluation:** Inaccessible repository (HTTP 404 / deleted / private). Cannot be audited.
* **Classification:** **Reject**
* **Action:** Discard from active watchlist unless a valid mirror or updated URL is provided.
