# Implementation Roadmap & Milestone Acceptance Criteria

**Document Version:** 1.0  
**Date:** 22 November 2026  
**Execution Strategy:** Phased, gated development with automated testing at every milestone.

---

## Roadmap Overview

```
+-----------------------------------------------------------------------------------+
|  M1: Proof-of-Concept & Recorded Fixtures                                         |
|  - Raw HTTP client with safe pacing                                               |
|  - Recorded real-world fixtures (redacted) & Pydantic response schemas            |
+-----------------------------------------------------------------------------------+
                                          |
+-----------------------------------------------------------------------------------+
|  M2: Item Identity Resolver & URL Parser                                          |
|  - Grouped G... URL and standard URL parser                                       |
|  - WearCategory0-4 normalization & offline item_nameid database integration       |
+-----------------------------------------------------------------------------------+
                                          |
+-----------------------------------------------------------------------------------+
|  M3: Market Adapters & Rate Limiter Circuit Breaker                               |
|  - Token Bucket rate limiter (10-15 RPM) & 300s backoff                           |
|  - itemordershistogram, listing render, and SSR hydration clients                 |
+-----------------------------------------------------------------------------------+
                                          |
+-----------------------------------------------------------------------------------+
|  M4: Exact Fee Calculator & Math Engine                                           |
|  - Dual-floor integer cents fee calculation & backward inversion                  |
|  - Unit tests verifying exact Steam UI cents matching                             |
+-----------------------------------------------------------------------------------+
                                          |
+-----------------------------------------------------------------------------------+
|  M5: SELL Engine & Telegram Notifications                                         |
|  - Public inventory ingestion & manual CSV/JSON import                            |
|  - Anomaly detection, top-level quantity parsing, ghost order defense             |
|  - Telegram alert dispatcher                                                      |
+-----------------------------------------------------------------------------------+
                                          |
+-----------------------------------------------------------------------------------+
|  M6: BUY Engine & Listing Evaluator                                               |
|  - Listing-level ingestion & sticker/float inspection                             |
|  - Robust baseline (VWM), conservative exit scenario, net wallet ROI              |
+-----------------------------------------------------------------------------------+
                                          |
+-----------------------------------------------------------------------------------+
|  M7: Portfolio & 7-Day Marketability Tracker                                      |
|  - Acquisition cost basis & holding tracker                                       |
|  - Seven-day cooldown countdown & resale readiness alerts                         |
+-----------------------------------------------------------------------------------+
                                          |
+-----------------------------------------------------------------------------------+
|  M8: React/TypeScript Dashboard & Windows Launcher                                |
|  - Vite + React 18 SPA (BUY page, SELL page, Inventory, Settings)                 |
|  - Windows launch.bat one-click launcher                                          |
+-----------------------------------------------------------------------------------+
                                          |
+-----------------------------------------------------------------------------------+
|  M9: End-to-End Verification, Security Audit & Final Packaging                    |
|  - Full integration test pass & zero-credential security audit                    |
|  - Documentation & user guide                                                     |
+-----------------------------------------------------------------------------------+
```

---

## Milestone Breakdown & Acceptance Criteria

### Milestone 1: Read-Only Steam Endpoint PoC & Response-Schema Tests
* **Deliverables:**
  - `src/sniper/core/http.py`: Resilient async HTTP client with strict user-agent headers and backoff.
  - `tests/fixtures/`: Recorded, anonymized JSON/HTML fixtures for SSR pages, listings render, and histograms.
  - Pydantic models validating all endpoint payloads.
* **Acceptance Criteria:** Tests pass against recorded fixtures; malformed schemas raise validation errors without crashing.

### Milestone 2: Item Identity Resolver & URL Parser
* **Deliverables:**
  - `src/sniper/core/identity.py`: Parser handling both canonical URLs and grouped `G...` URLs with `category_Exterior` query parameters.
  - Integration of `somespecialone/steam-item-name-ids` database in local SQLite with pinned upstream dataset.
* **Acceptance Criteria:** Given any valid Steam URL (standard or grouped), outputs exact canonical item name, wear condition, and `item_nameid`.

### Milestone 2.2: Persistent SQLite Storage & Repository Layer
* **Deliverables:**
  - `src/sniper/storage/`: SQLite storage layer with schema version 3, atomic immediate migrations, and triggers protecting historical observation immutability.
  - Repositories: `ItemRepository`, `CurrencyRepository`, `ListingRepository`, `OrderbookRepository`, `PortfolioRepository`, `InventoryRepository`.
* **Acceptance Criteria:** Integer minor-unit currency storage, parent-delete protection, transaction isolation, concurrent migration serialization, and full rollback guards.

### Milestone 2.3: Identity Trust & SQLite Integration
* **Deliverables:**
  - `src/sniper/storage/service.py`: `ItemIdentityService` and `ingest_market_item` application service layer connecting URL parsing, SSR bucket resolution, and offline mapping database with the SQLite catalog.
  - Strict mapping trust policy: `SYNTHETIC_TEST_DATA` and `legacy_unverified` are rejected from production identity resolution; only verified dataset provenance matching pinned repository and revision is accepted.
  - Provenance protection in `ItemRepository.save()`: prevents lower-trust or unverified updates from downgrading verified mapping provenance.
* **Acceptance Criteria:**
  - Verified pinned mappings produce `ITEM_NAMEID_RESOLVED`; synthetic or unverified mappings produce `CANONICAL_RESOLVED` with `item_nameid=None`.
  - Synthetic `classid` values never leak into identity results.
  - Incomplete grouped URLs (`PARSED_URL_ONLY`) are blocked from persistence.
  - Idempotent repeated persistence preserves `first_seen_at` and original primary keys across close/reopen.
  - 100% offline deterministic test coverage across all identity-to-storage paths.

### Milestone 3: Order-Book & Listing Adapters + Safe Rate Limiter
* **Deliverables:**
  - Token-bucket rate limiter enforcing max 12 requests/minute with random jitter.
  - Circuit breaker that automatically freezes polling for 300s on HTTP 429 or 403.
* **Acceptance Criteria:** Unit tests verify request rate cannot exceed budget; canary probe recovers cleanly after simulated 429.

### Milestone 4: Exact Fee Calculator & Math Engine
* **Deliverables:**
  - `src/sniper/core/fees.py`: Integer-cents fee functions implementing Valve's dual-floor schedule.
  - Full test suite testing boundary conditions ($0.03, $1.00, $10.00, $1000.00).
* **Acceptance Criteria:** 100% test pass matching Steam sell confirmation modal cents.

### Milestone 5: SELL Engine + Inventory Tracker + Telegram Alerts
* **Deliverables:**
  - Public inventory fetcher (`/inventory/{steamid64}/730/2`) and manual CSV/JSON parser.
  - Top-level vs cumulative quantity extraction.
  - Telegram Bot alert dispatcher with deduplication and cooldown.
* **Acceptance Criteria:** Generates high-bid alert with exact net proceeds and ghost order warnings; deduplicates alerts within cooldown window.

### Milestone 6: BUY Engine + Listing Evaluator
* **Deliverables:**
  - Listing-level parser extracting listing ID, asset ID, price, and stickers.
  - Volume-weighted median baseline calculator.
  - Conservative exit scenario model with 7-day risk buffer.
* **Acceptance Criteria:** Detects underpriced listing; correctly excludes illiquid items; computes net wallet ROI after fees.

### Milestone 7: Portfolio & 7-Day Marketability Tracker
* **Deliverables:**
  - Holding database tracking acquired skins, cost basis, and Steam `marketable_at` date.
  - Background alert when an item passes its 7-day restriction and is ready for resale.
* **Acceptance Criteria:** Displays accurate countdown for locked items; transitions to "Ready to List" upon restriction expiry.

### Milestone 8: Dashboard, Settings & Windows Launcher
* **Deliverables:**
  - React/TypeScript dashboard with BUY view, SELL view, Portfolio view, and Source Health indicator.
  - Windows `launch.bat` script.
* **Acceptance Criteria:** Single click launches backend and frontend, opening browser automatically on Windows 11.

### Milestone 9: Final Acceptance, Security Audit & Packaging
* **Deliverables:**
  - Comprehensive end-to-end test suite.
  - Security audit confirming zero credential storage and strictly read-only execution.
* **Acceptance Criteria:** All acceptance criteria in Section 12 of the Project Brief are satisfied.
