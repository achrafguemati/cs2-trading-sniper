# Open-Source Project Watchlist & Audit Log

This watchlist tracks open-source repositories audited for potential code reuse, architectural reference, and security evaluation, in accordance with the `github-open-source-audit` skill.

---

## Audit Matrix Summary

| Repository | License | Activity / Last Push | Safety & Read-Only | Classification | Primary Use / Note |
| :--- | :--- | :--- | :--- | :--- | :--- |
| [`somespecialone/steam-market-ids`](https://github.com/somespecialone/steam-market-ids) | MIT | Active (2025–2026) | ✅ Safe (Static Data) | **Reuse Candidate** | Maps `market_hash_name` to `item_name_id`. |
| [`somespecialone/cs2-items-schema`](https://github.com/somespecialone/cs2-items-schema) | MIT | Active (Automated CI) | ✅ Safe (Static Data) | **Reuse Candidate** | Maps game item definitions, paint index, rarity. |
| [`i4cobus/cs2-cross-market-arbitrage-analyzer`](https://github.com/i4cobus/cs2-cross-market-arbitrage-analyzer) | MIT | Maintained | ✅ Safe (Read-Only) | **Reference Only** | Multi-market fee logic (uses floating point). |
| [`Luca-Ospel/cs2-market-scraper`](https://github.com/Luca-Ospel/cs2-market-scraper) | MIT | Inactive | ⚠️ Unbuffered Scraper | **Reference Only** | Legacy scraper; superseded by our async client. |
| [`MhmdMK277/CSFloatPriceChecker`](https://github.com/MhmdMK277/CSFloatPriceChecker) | MIT | Maintained | ✅ Safe (Public API) | **Reference Only** | CSFloat public API queries; lacks backoff. |
| [`Soophere/cs2-sniper`](https://github.com/Soophere/cs2-sniper) | N/A | Inaccessible / 404 | ❌ Unverified | **Reject** | Repository deleted, private, or non-existent. |

---

## Detailed Repository Audits

### 1. `somespecialone/steam-market-ids`
* **Source URL:** `https://github.com/somespecialone/steam-market-ids`
* **Date Checked (UTC):** 2026-11-22T17:25:00Z
* **SPDX License:** MIT (Permissive, commercial reuse permitted with attribution).
* **Language & Tech:** Python, JSON datasets.
* **Project Relevance:** Essential for Milestone 2 (Item Identity Resolver). It provides pre-mapped JSON dictionaries connecting canonical `market_hash_name` strings to Steam's internal integer `item_name_id` values.
* **Safety Evaluation:** Strictly read-only data assets. No network mutations, no credential requests, no proxy rotation.
* **Reusable Assets:** `item_names.json` mapping tables.
* **Classification:** **Reuse Candidate**
* **Action:** Review data freshness during Milestone 2; integrate offline mapping dictionary to minimize live Steam resolution requests.

### 2. `somespecialone/cs2-items-schema`
* **Source URL:** `https://github.com/somespecialone/cs2-items-schema`
* **Date Checked (UTC):** 2026-11-22T17:25:00Z
* **SPDX License:** MIT (Permissive).
* **Language & Tech:** Python, GitHub Actions automation.
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

### 5. `MhmdMK277/CSFloatPriceChecker`
* **Source URL:** `https://github.com/MhmdMK277/CSFloatPriceChecker`
* **Date Checked (UTC):** 2026-11-22T17:25:00Z
* **SPDX License:** MIT.
* **Language & Tech:** Python.
* **Project Relevance:** Queries CSFloat public APIs to inspect item prices and wear values.
* **Safety Evaluation:** Read-only queries against public endpoints.
* **Issues / Non-Compliance:** Lacks error handling, backoff, and integer minor-unit conversion.
* **Classification:** **Reference Only**
* **Action:** Useful reference for CSFloat JSON response shapes if secondary market cashout monitoring is integrated in later phases.

### 6. `Soophere/cs2-sniper`
* **Source URL:** `https://github.com/Soophere/cs2-sniper`
* **Date Checked (UTC):** 2026-11-22T17:25:00Z
* **SPDX License:** Unknown / Unavailable.
* **Project Relevance:** Named in initial research leads.
* **Safety Evaluation:** Inaccessible repository (HTTP 404 / deleted / private). Cannot be audited.
* **Classification:** **Reject**
* **Action:** Discard from active watchlist unless a valid mirror or updated URL is provided.
