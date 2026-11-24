# CS2 Trading Sniper (Market Research & Opportunity Detection)

[![Platform: Windows 11](https://img.shields.io/badge/Platform-Windows%2011-blue.svg)](https://microsoft.com/windows)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python: 3.12+](https://img.shields.io/badge/Python-3.12%2B-blue.svg)](https://python.org)
[![Operation: Read--Only](https://img.shields.io/badge/Operation-Read--Only-red.svg)](#non-negotiable-safety-principles)

A local-first market research, opportunity detection, inventory tracking, and alerting application for Counter-Strike 2 on the Steam Community Market (SCM).

The application features two independent, quantitative market engines sharing a common data layer:
1. **BUY SNIPER (Mode A):** Detects potentially underpriced individual sell listings on SCM, evaluates wear, float, stickers, and liquidity, accounts for the 7-day market re-list restriction and modeled fees, and alerts the user for manual inspection.
2. **SELL SNIPER (Mode B):** Monitors marketable inventory items, detects unusually high live Steam buy orders, verifies top-level depth vs. cumulative orders, guards against "ghost" / unfunded buy orders, computes modeled net seller proceeds, and alerts the user for manual inspection.

---

## Non-Negotiable Safety Principles

1. **Strictly Read-Only:** Zero automated buying, selling, listing, or trade execution. All market actions are manually executed by the human user.
2. **Zero Credential Storage:** The application **never** asks for, stores, or transmits Steam passwords, Steam Guard codes, `maFile` secrets, or browser session cookies.
3. **No Rate-Limit Evasion or Proxy Rotation:** Operates within conservative sequential request pacing (target ~10 requests/min with strict sequential serialization and randomized jitter) with automated exponential backoff on HTTP 429 and immediate circuit breaker halt on HTTP 403.
4. **Integer Minor-Unit Financial Precision:** Price and fee calculations avoid floating-point arithmetic. The application uses an integer fee model with exact arithmetic under the project's modeled formula; Valve's exact rounding implementation remains UNKNOWN.
5. **Steam Wallet $\ne$ Cash:** Virtual Steam Wallet balances are strictly segregated from external cashout estimates.

See the complete rules in [AGENTS.md](AGENTS.md).

---

## Current Status: M3 Steam Market Adapters & Token-Bucket Circuit Breaker

- **Milestone 1 / 1.1 / 1.2 / 1.2.1 / 1.2.2 / 1.2.3 (Previously completed):**
  - Read-only asynchronous HTTP client (`ReadOnlyHttpClient`) with strict request-level serialization (`asyncio.Lock`), preventing concurrent execution overlap.
  - Transparent, non-spoofed application identification (`CS2TradingSniper/0.1.0`) with minimal standard headers (zero fabricated `Sec-Fetch-*` headers).
  - Strict production URL allowlist: HTTPS only, `steamcommunity.com` only, exact path matching for `/market/itemordershistogram`, boundary validation for `/market/listings/730/` and `/inventory/`, userinfo and non-default port rejection. Mock routes injected only through test configuration.
  - Redirect pacing: every redirect hop acquires the shared rate limiter separately, maintaining sequential pacing across redirect lifecycles while rejecting cross-host and HTTPS-to-HTTP downgrades before following.
  - Unified, fail-closed JSON processing path (`get_json(schema=...)`) with exact Decimal token parsing (`parse_float=Decimal`) and schema validation inside the serialized request lock.
  - Fail-closed circuit breaker: unexpected HTML challenges, malformed JSON, and schema failures immediately halt subsequent polling.
  - Sequential rate limiter (`SequentialRateLimiter`) with monotonic timers (`time.monotonic()`), exponential backoff, and full RFC 7231 `Retry-After` support (delta-seconds and HTTP-date).
  - Fail-closed currency semantics: currency metadata, fee rules, and market listing rules are verified independently. USD, EUR, JPY, and Tunisia listing minimums/price steps remain UNKNOWN absent direct Community Market evidence; pure mathematical inversion requires `validate_market_listing=False`.
  - `calculate_seller_receives` requires explicit currency context for market listing validation; pure mathematical inversion requires the caller to explicitly disable listing validation.
  - Orderbook normalization requires verified currency metadata and minor-unit precision, independently of market listing rules; fee calculations with currency require both verified metadata and fee rules.
  - Integer fee model (`calculate_buyer_pays`, `calculate_seller_receives`, `calculate_exact_breakeven_buyer_pays`) with step-jump gap handling ($R=99 \rightarrow 112, R=100 \rightarrow 115$) using exact arithmetic under the project's modeled formula. Valve's exact rounding implementation remains UNKNOWN (`calculate_exact_breakeven_buyer_pays` is exact relative to the project's model, not proof of Valve's live implementation).
  - Strict minor-unit currency model (`CurrencyConfig`) with hardened grammar and trailing zero exactness (accepts `Decimal("22.500") -> 2250`, continues rejecting `22.501`, `22.505`, `22.50001`).
  - Strict orderbook graph validation: strictly positive orderbook prices, non-negative integer quantities without silent fractional truncation.
  - Research skills (`.agents/skills/`) and intelligence registers (`docs/research/`).
- **Milestone 2.1 & 2.2 & 2.3 (Previously completed):**
  - M2.1: Canonical and grouped Steam listing URL parsing, SSR hydration bucket resolution, and pinned offline mapping database (`somespecialone/steam-market-ids` at `caac34bd8a28578e800501da5cc9f16c7cb3fa67`).
  - M2.2: Persistent SQLite storage layer (schema version 3) with integer minor-unit financial arithmetic, typed repositories (`ItemRepository`, `CurrencyRepository`, `ListingRepository`, `OrderbookRepository`, `PortfolioRepository`, `InventoryRepository`), triggers protecting immutable listing/orderbook history and parent-item integrity, atomic migrations, and concurrency serialization with full rollback guards.
  - M2.3: Application service layer (`ItemIdentityService`, `ingest_market_item` in `src/sniper/storage/service.py`) integrating URL parsing, grouped SSR hydration, and trusted offline mapping lookup directly into the SQLite catalog.
  - Strict mapping trust policy: `SYNTHETIC_TEST_DATA` and `legacy_unverified` are strictly rejected from production identity resolution; only verified dataset provenance matching pinned repository and revision is accepted.
  - Fail-closed synthetic classid protection: synthetic or unverified `classid` values never leak into identity results.
  - Provenance protection in `ItemRepository.save()`: prevents lower-trust, unverified, or missing incoming data from overwriting verified mapping provenance on both insert and upsert paths.
  - Strict integer validation on `ItemRecord.item_nameid` preventing boolean-to-integer coercion.
  - 100% offline deterministic test coverage across all identity-to-storage paths.
- **Milestone 3 (Steam Market Adapters & Token-Bucket Circuit Breaker):**
  - Typed Steam Market adapters (`SteamMarketAdapter` in `src/sniper/adapters/steam_market.py`) for deep order-book histogram (`/market/itemordershistogram`), active listing render (`/market/listings/730/{market_hash_name}/render/`), and SSR hydration data extraction (`valve-ssr-data` JSON script parsing).
  - Strict provenance and input validation: requires verified `ItemIdentityResult`, strictly positive integer IDs, validated currency IDs, pagination bounds, and safe URL percent-encoding for market hash names.
  - Genuine token-bucket rate limiter (`SequentialRateLimiter`): budget of 12 requests per minute (internal conservative safety policy, not an official Valve quota or guarantee), capacity of 1 token (burst prevention), monotonic refill, and randomized jitter that only slows requests down.
  - Global circuit breaker with 4 states (`CLOSED`, `OPEN`, `HALF_OPEN`, `HALTED`), exponential backoff (300s initial safety minimum up to 3600s), single-canary recovery in `HALF_OPEN` blocking waiting requests, and permanent fail-closed halt on HTTP 403 or protocol/schema violations.
  - `ReadOnlyHttpClient.get_parsed()` execution path ensuring response parsing and validation occurs within the serialized request lock, guaranteeing invalid HTML/SSR payloads trip the circuit breaker and cannot pass as canaries.
  - 251 offline deterministic unit tests passing across all test suites without issuing live Steam network calls.
- **Milestone 4 (Exact Fee Calculator & Math Engine):**
  - Pure integer minor-unit fee functions in `src/sniper/core/fees.py` implementing the project's modeled CS2 fee formula with independent floor components (5% Steam, 10% CS2 publisher; Valve's exact live rounding implementation remains UNKNOWN).
  - Mathematically safe integer binary search algorithm for backward fee inversion, finding the maximal integer seller proceeds whose forward buyer price does not exceed the target, with exact reachability tracking (`is_exact_match`).
  - Strict input validation rejecting booleans, floats, strings, negative amounts, zero amounts, and invalid publisher percentages without silent type coercion.
  - Fail-closed currency configuration hardening in `src/sniper/core/currency.py` preventing invalid or negative fee floors and price steps.
  - Clear separation between pure mathematical fee calculations (`validate_market_listing=False`) and market listing validation (`validate_market_listing=True`). Currency listing minimums and price step increments remain UNKNOWN for USD, EUR, JPY, and Tunisia pending primary empirical evidence.
  - Expanded deterministic unit test coverage in `tests/test_fees.py` covering boundary cases ($0.03, $1.00, $10.00, $1,000.00), unreachable step-jump gaps (113, 114 cents), high-tier unreachable price ($500.00 -> $434.79 net / $499.99 actual), break-even math, and exhaustive invariant verification across 3 to 5,000 cents.
- **Milestone 5 (SELL Engine + Inventory Tracker + Free Multi-Channel Notifications):**
  - Public Steam inventory ingestion client (`PublicInventoryClient` in `src/sniper/adapters/inventory.py`) querying public endpoints with pagination bounds and exact `(classid, instanceid)` asset matching.
  - Manual inventory CSV and JSON importers (`ManualInventoryImporter`) with row-level validation for private profiles.
  - SELL Sniper Engine (`SellSniperEngine` in `src/sniper/engine/sell_engine.py`) with strict identity provenance gating, historical buy-order median baselines, crossed-book detection, top-level quantity anomaly filters (`Single-Item Top Bid`), exact M4 modeled seller proceeds, and quantitative opportunity/confidence scoring.
  - Free multi-channel notification infrastructure (`src/sniper/notifications/`): Discord Webhooks (`DiscordNotifier`) with rich embeds and Telegram Bot API (`TelegramNotifier`) with safely escaped HTML formatting.
  - Zero-cost architecture: 100% free operation without paid subscriptions, paid APIs, or cloud servers.
  - Complete credential redaction (`redact_secrets()`) preventing secret leakage into database records, logs, or exception traces.
  - Database schema Migration 4 adding `alert_events` and `notification_deliveries` tables with fingerprint deduplication and cooldown controls.
  - CLI command-line interface (`src/sniper/cli.py`) supporting `import-inventory`, `import-csv`, `import-json`, `scan-sell`, `test-notification`, and `channel-status`.
- **Milestone 5.1 (Critical Hardening & Reliability Fixes):**
  - Strict Steam inventory description matching: matched exclusively by `(classid, instanceid)` tuple, removing all ambiguous classid-only fallbacks, and marking unmatched assets as unknown, non-marketable, and non-tradable.
  - Fail-closed public inventory pagination: raises `PublicInventoryError` and rolls back transactions on page failures, private inventories, missing cursors, or repeated cursors; validates pagination limits (`count` 1..5000, `max_pages` 1..50, `max_items` 1..50000); legitimately empty inventories produce empty snapshots cleanly.
  - Hardened manual CSV/JSON marketability validation: strict boolean parsing with fail-closed `False` defaults on missing values, rejecting ambiguous strings (`'maybe'`, `'unknown'`, `'2'`) and invalid restriction days with row-level errors.
  - Un-fabricated buy depth: removed artificial fallback defaults (`or 1`); missing or zero orderbook depth tags `BUY DEPTH UNAVAILABLE` and suppresses actionable notifications; opportunity scoring assigns 0.0 to depth when missing.
  - Per-channel delivery-aware cooldown: cooldown references actual successful deliveries (`status = 'delivered'`) per channel in `notification_deliveries`; intervening non-actionable or suppressed observations do not alter the cooldown reference timestamp or reference bid; supports short failure retry backoff (default 60s, max 3 retries) with channel independence.
  - Actionable status alignment: `High-confidence signal` and `Candidate` are notification-eligible; `Needs verification` is suppressed by default unless explicitly configured.
  - Local environment loading: integrates `python-dotenv` with `override=False` to preserve existing process environment variables while loading `.env` on Windows and Linux; ensures clean HTTP client lifecycle management via `ReadOnlyHttpClient.aclose()`.
- **Milestone 5.2 (Final Inventory Integrity & Notification Retry Hardening):**
  - Public inventory truncation elimination: verified total accumulated asset count against `max_items` across completed inventories (including when `more_items=False`); raises `PublicInventoryError` on limit breach without creating snapshots, and tracks cursor histories (`seen_cursors`) to reject cycles.
  - Cumulative notification retry accounting: persistent retry accounting independently tracked per `(item_id, currency_id, alert_type, channel, alert_fingerprint)`; counts failed delivery cycles cumulatively, enforcing the maximum of 3 retries after the initial failed send; skipped/cooldown evaluations never consume or reset failure counts; fresh bids receive a fresh retry budget; successful delivery clears retry state.
  - Fail-closed historical timestamp validation: `OrderbookRepository.get_historical_buy_orders()` excludes malformed, missing, or far-future timestamps from age-filtered baselines under an explicit clock-skew tolerance policy (`MAX_CLOCK_SKEW_SECONDS = 5.0s`), preserving historical database records without deletion.
  - Strict manual JSON/CSV numeric validation: `ManualInventoryImporter` rejects booleans, floats (`1.0`, `1.9`, `0.0`, `7.0`), non-positive amounts, and negative restriction days without silent truncation; enforces an explicit manual-only namespace (`manual_`) for all manual asset IDs.

---

## Installation & Verification

```bash
# 1. Install package in editable mode with dependencies
pip install -e .

# 2. Run comprehensive offline unit test suite
python -m unittest discover -s tests -p "test_*.py" -v

# 3. CLI status and notification checks
sniper status
sniper test-notifications

# 4. Import inventory (public SteamID64 or manual CSV/JSON)
sniper import-inventory 76561198000000000
sniper import-csv my_inventory.csv

# 5. Run SELL Sniper opportunity scan
sniper scan-sell
```

# 2. Run comprehensive offline unit test suite
python -m unittest discover -s tests -p "test_*.py" -v

# 3. Run single-request read-only connectivity smoke test
python scripts/smoke_test.py
```

---

## Project Documentation & Research Deliverables

All technical specifications, empirical research, and architectural blueprints are documented under [`docs/`](docs/):

### 1. Empirical Research
* [Market Landscape & Secondary Marketplaces](docs/research/market-landscape.md) — 14 marketplaces audited, fee schedules, liquidity tiers, and regional analysis for Tunisia.
* [Steam API Verification & Redesign Architecture](docs/research/steam-api-verification.md) — May 12, 2026 SSR React architecture, `valve-ssr-data` hydration, `G...` grouped URLs, and live rate-limit tests.
* [Steam Market Rules & Exact Fee Math](docs/research/steam-market-rules.md) — Dual-floor fee formulas, integer cents arithmetic, and 7-day restriction vs. Trade Protection.
* [Reddit & Trader Community Findings](docs/research/reddit-community-findings.md) — Ghost buy order mechanics, bot latency realities, and sticker/float alpha.
* [Open-Source Repository Audit](docs/research/repository-audit.md) — Deep audit of 16 GitHub projects with commit SHAs, licenses, and code reuse assessments.

### 2. Strategy Specifications
* [Mode A: BUY Strategy Specification](docs/strategy/buy-strategy.md) — URL resolution, listing-level data, volume-weighted median, and conservative 7-day exit scenarios.
* [Mode B: SELL Strategy Specification](docs/strategy/sell-strategy.md) — Inventory ingestion, top-level vs. cumulative depth, crossed-book defense, and ghost order filters.
* [Opportunity Scoring Framework](docs/strategy/opportunity-scoring.md) — Explainable 0–100 opportunity and confidence scoring algorithms.

### 3. Architecture & Planning
* [System Architecture Proposal](docs/architecture/architecture-proposal.md) — FastAPI + SQLite + React/Vite local-first architecture and rate-limiter circuit breaker.
* [Architecture Decision Records (ADRs)](docs/decisions/decision-log.md) — Log of core technical and economic decisions.
* [Implementation Roadmap](docs/plan/implementation-roadmap.md) — 9 phased milestones with strict acceptance gates.
* [Test & Acceptance Plan](docs/plan/test-and-acceptance-plan.md) — Unit test matrix, recorded fixtures, and criteria validation.

---

## Planned Architecture

* **Backend:** Python 3.12+ / FastAPI / `asyncio` / `httpx` / `pydantic`
* **Storage:** Local SQLite database with integer cents financial schemas
* **Frontend:** React 18 / TypeScript / Vite / modern responsive dashboard
* **Alerting:** Telegram Bot API notifications with deduplication and cooldowns
* **Deployment:** Windows 11 native launch script (`launch.bat`)

---

## License

Project code is licensed under MIT. The vendored `src/sniper/data/seed_market_ids.json` mapping data is separately licensed under upstream WTFPL 2.0 at the pinned repository revision documented in [`research-source-register.md`](docs/research/research-source-register.md).
