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

## Current Status: Production Complete (Milestones M1–M9)

All 9 implementation milestones are complete and verified via automated offline tests, static & runtime security audits, and cross-platform CI (Windows & Linux Python 3.12/3.13 + React/TypeScript Vite SPA):

- **Milestones 1–4 (Core & Financial Engine):**
  - Strictly read-only `ReadOnlyHttpClient` with single `asyncio.Lock`, non-spoofed `CS2TradingSniper/0.1.0` headers, HTTPS `steamcommunity.com` path allowlist, redirect validation, fail-closed JSON/Decimal parsing, monotonic `SequentialRateLimiter` (capacity `1`, hard-capped `<= 12 RPM`, zero proxy/override backdoors), and 4-state global circuit breaker (`CLOSED`, `OPEN` 300s+ backoff, `HALF_OPEN` single-canary, `HALTED` fail-closed).
  - Exact `(app_id, market_hash_name, item_nameid)` SSR hydration & canonical/grouped `G...` URL resolution against pinned verified mapping provenance, backed by integer minor-unit dual-floor Valve fee arithmetic and `calculate_exact_breakeven_buyer_pays`.
- **Milestones 5 / 5.1 / 5.2 (SELL Sniper, Inventory & Free Notifications):**
  - Exact `(classid, instanceid)` public inventory client (`PublicInventoryClient`) with pagination cycle/limit enforcement and strict manual CSV/JSON importers (`manual_` asset IDs, strict integer validation).
  - `SellSniperEngine` with crossed-book suppression, un-fabricated `BUY DEPTH UNAVAILABLE` gating, top-level bid anomaly scoring, and zero-cost `DiscordNotifier` + `TelegramNotifier` with token redaction, per-channel cooldowns, and cumulative retry caps.
- **Milestone 6 (BUY Sniper & Cookie-Free History):**
  - Individual `/render/` card parser (`listing_parser.py`) extracting buyer-pays minor units, stickers/charms, optional float/seed metadata, inspect links, and cookie-free `var line1` HTML series while excluding sold listings and variant mismatches.
  - `BuySniperEngine` using Volume-Weighted Median baselines, 7d/30d spike guard, 5% 7-day volatility risk buffer, Conservative/Base/Optimistic exit scenarios, and Migration 5 persistence.
- **Milestone 7 (Portfolio 7-Day Lock Tracker & Unified Scheduler):**
  - `PortfolioService` parsing Steam `"until/after ... GMT/UTC"` lock timestamps cleanly in UTC, suppressing premature `RESALE READY` alerts while locked, flagging `Under Water` holdings, dispatching `[RESALE READY]` & `[UNDER WATER]` notifications, and computing exact integer Wallet P&L.
  - `SniperScheduler` sharing ONE global rate limiter and HTTP client (`<= 12 RPM`) across BUY, SELL, and Portfolio loops alongside SQLite Migration 6.
- **Milestone 8 (FastAPI Local Dashboard, React/Vite SPA & Windows Launcher):**
  - FastAPI backend (`src/sniper/api/app.py`) exposing local REST APIs with zero secret echo-back and SPA serving (`frontend/`), React 18 + TypeScript + Vite multi-tab workspace (Overview, BUY Sniper, SELL Sniper, Portfolio, Health & Alerts, Settings), `sniper serve` CLI, and one-click Windows 11 launcher (`launch.bat`).
- **Milestone 9 (Acceptance, Security Audit & Packaging):**
  - Automated static + runtime security audit (`tests/test_security_audit.py`), full offline E2E lifecycle verification (`tests/test_e2e_lifecycle.py`), [Security Policy](SECURITY.md), and [Changelog](CHANGELOG.md).

---

## Quick Start (Windows 11 One-Click Launch)

Double-click **`launch.bat`** (or run `.\launch.bat` in PowerShell / CMD):
- Creates `.env` from `.env.example` if missing (configure `DISCORD_WEBHOOK_URL` / `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` optionally for alerts — **never** add Steam passwords or cookies).
- Installs Python dependencies (`pip install -e .`) and builds `frontend/dist` (`npm install && npm run build`) automatically if needed.
- Starts the local dashboard at `http://127.0.0.1:8000` (`sniper serve`) and opens your browser.

---

## CLI Installation & Manual Commands

```bash
# 1. Install package in editable mode with dependencies
pip install -e .

# 2. Run comprehensive offline unit & security test suite
python -m unittest discover -s tests -p "test_*.py" -v

# 3. Frontend build & Vitest suite
cd frontend && npm ci && npm run build && npm test && cd ..

# 4. Launch dashboard manually
sniper serve --host 127.0.0.1 --port 8000

# 5. CLI status, watchlists, scans & portfolio management
sniper status
sniper test-notifications
sniper watchlist add "https://steamcommunity.com/market/listings/730/AK-47%20%7C%20Redline%20%28Field-Tested%29" --strategy BUY
sniper import-inventory 76561198000000000
sniper import-csv my_inventory.csv
sniper scan-buy
sniper scan-sell
sniper portfolio list

# 6. Controlled single-request live smoke check (manual only)
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
