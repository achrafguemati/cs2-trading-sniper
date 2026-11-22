# CS2 Trading Sniper (Market Research & Opportunity Detection)

[![Platform: Windows 11](https://img.shields.io/badge/Platform-Windows%2011-blue.svg)](https://microsoft.com/windows)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python: 3.12+](https://img.shields.io/badge/Python-3.12%2B-blue.svg)](https://python.org)
[![Operation: Read--Only](https://img.shields.io/badge/Operation-Read--Only-red.svg)](#non-negotiable-safety-principles)

A local-first market research, opportunity detection, inventory tracking, and alerting application for Counter-Strike 2 on the Steam Community Market (SCM).

The application features two independent, quantitative market engines sharing a common data layer:
1. **BUY SNIPER (Mode A):** Detects genuinely underpriced individual sell listings on SCM, evaluates wear, float, stickers, and liquidity, accounts for the 7-day market re-list restriction and exact Valve fees, and alerts the user for manual purchase.
2. **SELL SNIPER (Mode B):** Monitors marketable inventory items, detects unusually high live Steam buy orders, verifies top-level depth vs. cumulative orders, guards against "ghost" / unfunded buy orders, computes net seller proceeds, and alerts the user for manual listing.

---

## Non-Negotiable Safety Principles

1. **Strictly Read-Only:** Zero automated buying, selling, listing, or trade execution. All market actions are manually executed by the human user.
2. **Zero Credential Storage:** The application **never** asks for, stores, or transmits Steam passwords, Steam Guard codes, `maFile` secrets, or browser session cookies.
3. **No Rate-Limit Evasion or Proxy Rotation:** Operates within conservative sequential request pacing (target ~10 requests/min with strict sequential serialization and randomized jitter) with automated exponential backoff on HTTP 429 and immediate circuit breaker halt on HTTP 403.
4. **Integer Cents Financial Precision:** All pricing and fee calculations operate strictly in integer minor currency units (cents), implementing Valve's exact dual-floor fee schedule without floating-point math.
5. **Steam Wallet $\ne$ Cash:** Virtual Steam Wallet balances are strictly segregated from external cashout estimates.

See the complete rules in [AGENTS.md](AGENTS.md).

---

## Current Status: Milestone 1.2.2 Complete (Final Edge-Case Hardening)

- **Milestone 1 / 1.1 / 1.2 / 1.2.1 / 1.2.2 (Complete):**
  - Read-only asynchronous HTTP client (`ReadOnlyHttpClient`) with strict request-level serialization (`asyncio.Lock`), preventing concurrent execution overlap.
  - Transparent, non-spoofed application identification (`CS2TradingSniper/0.1.0`) with minimal standard headers (zero fabricated `Sec-Fetch-*` headers).
  - Strict production URL allowlist: HTTPS only, `steamcommunity.com` only, exact path matching for `/market/itemordershistogram`, boundary validation for `/market/listings/730/` and `/inventory/`, userinfo and non-default port rejection. Mock routes injected only through test configuration.
  - Redirect pacing: every redirect hop acquires the shared rate limiter separately, maintaining sequential pacing across redirect lifecycles while rejecting cross-host and HTTPS-to-HTTP downgrades before following.
  - Unified, fail-closed JSON processing path (`get_json(schema=...)`) with exact Decimal token parsing (`parse_float=Decimal`) and schema validation inside the serialized request lock.
  - Fail-closed circuit breaker: unexpected HTML challenges, malformed JSON, and schema failures immediately halt subsequent polling.
  - Sequential rate limiter (`SequentialRateLimiter`) with monotonic timers (`time.monotonic()`), exponential backoff, and full RFC 7231 `Retry-After` support (delta-seconds and HTTP-date).
  - Fail-closed currency semantics: unverified listing minimums and unverified price steps strictly fail closed on market listing validation (`validate_market_listing=True`), while separating pure mathematical fee inversion (`validate_market_listing=False`). JPY, EUR listing minimums, and regional Tunisia listing rules remain strictly marked UNKNOWN.
  - Exact dual-floor integer-cents fee engine (`calculate_buyer_pays`, `calculate_seller_receives`, `calculate_exact_breakeven_buyer_pays`) with step-jump gap handling ($R=99 \rightarrow 112, R=100 \rightarrow 115$).
  - Strict minor-unit currency model (`CurrencyConfig`) with hardened grammar and trailing zero exactness (accepts `Decimal("22.500") -> 2250`, continues rejecting `22.501`, `22.505`, `22.50001`).
  - Strict orderbook graph validation: strictly positive orderbook prices, non-negative integer quantities without silent fractional truncation.
  - Research skills (`.agents/skills/`) and intelligence registers (`docs/research/`).
- **Milestones 2–9 (Planned):**
  - Item identity resolver, SQLite storage, detection algorithms, and dashboard remain deferred pending subsequent milestone approvals.

---

## Installation & Verification

```bash
# 1. Install package in editable mode with dependencies
pip install -e .

# 2. Run comprehensive unit test suite (68 deterministic tests)
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

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.
