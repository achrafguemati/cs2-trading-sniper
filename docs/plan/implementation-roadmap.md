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

### Milestone 3: Order-Book & Listing Adapters + Safe Rate Limiter (COMPLETED)
* **Status:** Complete & CI Verified.
* **Deliverables:**
  - `src/sniper/adapters/steam_market.py`: Production-ready typed `SteamMarketAdapter` supporting:
    - Order-book histogram adapter (`GET /market/itemordershistogram`) with strict provenance verification, top-level vs cumulative quantity preservation, and explicit currency validation.
    - Listing-render adapter (`GET /market/listings/730/{market_hash_name}/render/`) with pagination bounds, integer validation, safe URL percent-encoding, and listing/asset identifier preservation.
    - SSR hydration adapter extracting `<script id="valve-ssr-data" type="application/json">` via standard-library `HTMLParser`, validating outer and nested `loaderData[3]` schemas, and pairing wear buckets to source URLs.
  - `src/sniper/core/rate_limiter.py`: Genuine token-bucket rate limiter enforcing conservative maximum budget of 12 requests/minute (internal project policy, not a Valve guarantee), capacity of 1 token (burst prevention), monotonic refill, and randomized jitter that can only slow requests down.
  - Global circuit breaker with 4 states (`CLOSED`, `OPEN`, `HALF_OPEN`, `HALTED`), exponential backoff schedule (300s initial safety minimum up to 3600s), single-canary recovery in HALF_OPEN blocking waiting requests, and permanent fail-closed halt on HTTP 403 or protocol/schema violations.
  - `src/sniper/core/http_client.py`: Enhanced with `get_parsed()` callback executing inside the serialized request lock, guaranteeing invalid HTML/SSR payloads trip the circuit breaker and cannot pass as canaries.
  - 100% deterministic offline test suite (`tests/test_steam_market_adapter.py`, `tests/test_rate_limiter.py`) using fake clocks and mock transports.
* **Acceptance Criteria:** Verified: request rate cannot exceed 12 RPM; single canary probe recovers cleanly on success and reopens backoff on 429; all adapter endpoints share a single global rate limiter and circuit breaker state.

### Milestone 4: Exact Fee Calculator & Math Engine
* **Deliverables:**
  - `src/sniper/core/fees.py`: Integer minor-unit fee functions implementing the project's modeled CS2 fee formula with independent floor components:
    - Steam fee: $\max(m_s, \lfloor (R \times 5) / 100 \rfloor)$
    - CS2 publisher fee: $\max(m_p, \lfloor (R \times 10) / 100 \rfloor)$
    - Buyer pays: $R + \text{steam\_fee} + \text{publisher\_fee}$
    *(Note: Valve's documented fee rates and minimum components do not establish its exact internal rounding implementation; Valve's exact rounding implementation remains UNKNOWN).*
  - Backward inversion function using a mathematically safe integer binary search over $[1, B_{\text{req}}]$, finding the maximal integer seller proceeds $R$ such that forward $B(R) \le B_{\text{req}}$, reporting exact reachability via `is_exact_match`.
  - Strict input validation rejecting booleans, floats, strings, negative amounts, and invalid percentages.
  - Strict separation between pure mathematical fee calculations (`validate_market_listing=False`) and market listing validation (`validate_market_listing=True`). Currency listing minimums and price step increments remain UNKNOWN for USD, EUR, JPY, and Tunisia pending primary empirical evidence.
  - Comprehensive deterministic unit test suite (`tests/test_fees.py`) covering monetary boundaries ($0.03, $1.00, $10.00, $1,000.00), unreachable step-jump gaps (e.g. 113, 114 cents yielding $R=99$, actual buyer pays 112 cents), high-tier unreachable price ($500.00 yielding $R=434.79$, actual buyer pays $499.99), and monotonic range invariant tests across $B_{\text{req}} \in [3, 5000]$.
* **Acceptance Criteria:** Verified: 100% deterministic test pass for exact arithmetic under the project's modeled formula; mathematically safe integer boundary search; fail-closed rejection on unverified currencies or market listing rules; strict non-coercing input validation.

### Milestone 5: SELL Engine + Inventory Tracker + Free Multi-Channel Notifications (COMPLETED)
* **Status:** Complete & Verified.
* **Deliverables:**
  - `src/sniper/adapters/inventory.py`: Production-ready `PublicInventoryClient` with SteamID64 validation, pagination bounds, and exact `(classid, instanceid)` asset matching; `ManualInventoryImporter` supporting CSV and JSON manual imports with row-level validation for private profiles.
  - `src/sniper/engine/sell_engine.py`: `SellSniperEngine` featuring:
    - Strict identity provenance gating preventing network requests for items lacking trusted `item_nameid`.
    - Historical buy-order median baseline modeling (`historical_buy_bid_median_minor`), suppressing alerts when sample counts are insufficient (`DATA INSUFFICIENT`).
    - Crossed-book detection (`highest_bid >= lowest_ask`) tagging `INCONSISTENT SNAPSHOT / CROSSED BOOK` and suppressing execution recommendations.
    - Top-level quantity anomaly filters distinguishing `top_level_buy_quantity` vs `cumulative_buy_quantity` and tagging `Single-Item Top Bid` and `Unverified High Bid`.
    - Modeled seller proceeds calculated via exact M4 fee functions (`calculate_seller_receives(bid, currency, validate_market_listing=False)`).
    - Quantitative Opportunity Scoring (0–100) and Confidence Scoring (0–100) based on explainable sub-score formulas.
  - `src/sniper/notifications/`: Zero-cost notification infrastructure (Discord + Telegram ONLY):
    - `DiscordNotifier` / `DiscordWebhookChannel`: Incoming webhooks with rich embeds, color coding, and character limit protection.
    - `TelegramNotifier` / `TelegramBotChannel`: Official Bot API `sendMessage` with safely escaped HTML formatting and retry-after handling.
    - Central secret redaction engine (`redact_secrets()`) preventing credential leakage across logs and databases.
    - `NotificationDispatcher`: Isolated delivery pipeline with per-channel failure tolerance and SQLite audit logging.
  - SQLite Migration 4 (`src/sniper/storage/migrations.py`): Adding `alert_events` and `notification_deliveries` tables with SHA256 fingerprint deduplication and configurable cooldown (default 30 min) with meaningful bid increase overrides.
  - CLI Entry Point (`src/sniper/cli.py`): Subcommands `import-inventory`, `import-csv`, `import-json`, `scan-sell`, `test-notification`, and `channel-status`.
* **Acceptance Criteria:** Verified: High-bid alert generation with exact net proceeds, ghost order warnings, crossed-book suppression, and cooldown deduplication across 100% free notification transports.

### Milestone 5.1: Critical Hardening & Reliability Fixes (COMPLETED)
* **Status:** Complete & Verified.
* **Deliverables:**
  - `src/sniper/adapters/inventory.py`:
    - Strict `(classid, instanceid)` matching for public Steam inventory descriptions, eliminating ambiguous `classid`-only fallbacks.
    - Fail-closed error handling: raises `PublicInventoryError` and rolls back database transactions on partial pagination failures, missing/repeated cursors, or non-success Steam responses.
    - Strict manual CSV/JSON validation: fail-closed boolean parsing (`False` on missing), rejection of ambiguous strings (`'maybe'`, `'unknown'`, `'2'`) and negative/non-integer restriction days.
  - `src/sniper/engine/sell_engine.py`:
    - Removal of fabricated depth fallbacks (`or 1` removed); tagging `BUY DEPTH UNAVAILABLE` and suppressing actionable alerts when depth is missing or $\le 0$.
    - Actionable status alignment: `High-confidence signal` and `Candidate` are notification-eligible; `Needs verification` is non-actionable by default unless configured.
  - `src/sniper/storage/repositories.py` & `src/sniper/notifications/dispatcher.py`:
    - Per-channel delivery-aware cooldown anchored to `notification_deliveries` (`status = 'delivered'`).
    - Reference bid and timestamp derived exclusively from successful deliveries, preventing intervening non-actionable evaluations from resetting cooldown.
    - Independent failure retry backoff (default 60s, max 3 retries) and credential redaction across error logs.
  - `src/sniper/cli.py` & `src/sniper/core/http_client.py`:
    - Local `.env` loading via `python-dotenv` with `override=False`.
    - Explicit asynchronous resource closing via `ReadOnlyHttpClient.aclose()`.
* **Acceptance Criteria:** Verified: All edge cases covered by deterministic offline regression tests; 100% test pass without live network dependencies.

### Milestone 5.2: Final Inventory Integrity & Notification Retry Hardening (COMPLETED)
* **Status:** Complete & Verified.
* **Deliverables:**
  - `src/sniper/adapters/inventory.py`:
    - Elimination of silent inventory truncation: checks total accumulated assets against `max_items` across completed pages, raising `PublicInventoryError` without modifying database snapshots.
    - Cursor history set (`seen_cursors`) tracking all pagination cursors to reject cursor cycles promptly.
    - Strict manual JSON/CSV numeric parsing: `_parse_amount` rejects booleans, floats (1.0, 1.9), and negative/zero values; `_parse_restriction_days` rejects booleans, floats, and negative numbers; `_parse_strict_bool` rejects floats.
    - Explicit manual asset ID namespace (`manual_`) ensuring manual imports never fabricate or mimic real Steam asset IDs.
  - `src/sniper/storage/repositories.py`:
    - `OrderbookRepository.get_historical_buy_orders()`: fail-closed timestamp validation rejecting missing, malformed, or far-future timestamps under documented clock skew tolerance policy (`MAX_CLOCK_SKEW_SECONDS = 5.0s`), preserving historical rows in storage.
    - `AlertRepository.is_channel_in_retry_backoff()`: cumulative retry accounting per `(item_id, currency_id, alert_type, channel, alert_fingerprint)`, enforcing a maximum of 3 retries (4 total failures) after the initial failure; cleared upon successful delivery or new bid fingerprint.
* **Acceptance Criteria:** Verified: Cumulative retries cap at 3; new bids receive fresh budget; malformed timestamps excluded from baseline; manual JSON rejects floats and booleans; 342 offline unit tests pass cleanly.

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
