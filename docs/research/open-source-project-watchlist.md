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
| [`MhmdMK277/CSFloatPriceChecker`](https://github.com/MhmdMK277/CSFloatPriceChecker) | MIT | Maintained | ✅ Safe (Public API) | **Reference Only** | CSFloat public API queries; lacks backoff. |
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
