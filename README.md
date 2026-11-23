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

## Current Status: M2.3 Identity Trust & SQLite Integration

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
- **Milestone 2.1 & 2.2 (Previously completed):**
  - M2.1: Canonical and grouped Steam listing URL parsing, SSR hydration bucket resolution, and pinned offline mapping database (`somespecialone/steam-market-ids` at `caac34bd8a28578e800501da5cc9f16c7cb3fa67`).
  - M2.2: Persistent SQLite storage layer (schema version 3) with integer minor-unit financial arithmetic, typed repositories (`ItemRepository`, `CurrencyRepository`, `ListingRepository`, `OrderbookRepository`, `PortfolioRepository`, `InventoryRepository`), triggers protecting immutable listing/orderbook history and parent-item integrity, atomic migrations, and concurrency serialization with full rollback guards.
- **M2.3 (Identity Trust & SQLite Integration):**
  - Application service layer (`ItemIdentityService`, `ingest_market_item` in `src/sniper/storage/service.py`) integrating URL parsing, grouped SSR hydration, and trusted offline mapping lookup directly into the SQLite catalog.
  - Strict mapping trust policy: `SYNTHETIC_TEST_DATA` and `legacy_unverified` are strictly rejected from production identity resolution; only verified dataset provenance matching pinned repository and revision is accepted.
  - Fail-closed synthetic classid protection: synthetic or unverified `classid` values never leak into identity results.
  - Provenance protection in `ItemRepository.save()`: prevents lower-trust, unverified, or missing incoming data from overwriting verified mapping provenance.
  - Persistence validation: grouped URLs remaining in `PARSED_URL_ONLY` status and items with blank `market_hash_name` are blocked from creating database records.
  - 100% offline deterministic test coverage across all identity-to-storage paths.

---

## Installation & Verification

```bash
# 1. Install package in editable mode with dependencies
pip install -e .

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
