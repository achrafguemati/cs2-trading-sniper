# Changelog

All notable milestones completed in **CS2 Trading Sniper**.

## [1.0.0] - Milestones M1–M9 Complete

### M9 — Final Acceptance, Security Audit & Packaging
- Added automated static and runtime security audit suite (`tests/test_security_audit.py`) verifying zero Steam credentials, strictly read-only Steam transport, hard `<= 12 RPM` cap, zero proxy rotation, and zero notification secret leakage.
- Added full end-to-end offline lifecycle suite (`tests/test_e2e_lifecycle.py`) covering Watchlist addition -> BUY Sniper detection -> 7-day lock Portfolio tracking -> unlock `RESALE READY` notification -> Inventory import -> SELL Sniper anomaly detection -> realized Wallet P&L accounting.
- Added `SECURITY.md`, `CHANGELOG.md`, finalized `README.md`, and marked Milestone 9 complete across planning documentation.

### M8 — Dashboard, FastAPI Backend & Windows Launcher
- Implemented local-first FastAPI backend (`src/sniper/api/app.py`, `schemas.py`) serving overview, watchlists, manual history import, BUY/SELL scans, inventory sync, 7-day portfolio lifecycle, alerts, health/breaker controls, and secret-free settings.
- Implemented React 18 + TypeScript + Vite SPA (`frontend/`) with Overview, BUY Sniper, SELL Sniper, Portfolio & 7-Day Lock, Health & Alerts, and Settings tabs, plus Vitest component smoke suite (`frontend/src/App.test.tsx`).
- Added `sniper serve` CLI subcommand, packaged default `seed_market_ids.json` loader, single-click Windows 11 launcher (`launch.bat`), and CI `test-frontend` job.

### M7 — Portfolio 7-Day Tracker & Unified Scheduler
- Implemented `PortfolioService` with authoritative Steam `"until/after ... GMT/UTC"` lock timestamp parsing, M4 integer break-even tracking, `Under Water` flags, `HOLDING -> READY_TO_LIST` transitions, `[RESALE READY]` & `[UNDER WATER]` Discord/Telegram alerts, and exact integer Wallet P&L calculation.
- Implemented `SniperScheduler` sharing a single global `SequentialRateLimiter` (`<= 12 RPM`) and `ReadOnlyHttpClient` across BUY, SELL, and Portfolio loops, plus SQLite Migration 6 and CLI `portfolio` commands.

### M6 — BUY Engine, Listing Evaluator & Cookie-Free History
- Implemented individual `/market/listings/730/.../render/` card parser (`src/sniper/adapters/listing_parser.py`) extracting buyer-pays minor units, stickers/charms, optional float/paint metadata, inspect links, and cookie-free `var line1` HTML chart history while rejecting sold or mismatched variants.
- Implemented `BuySniperEngine` (`src/sniper/engine/buy_engine.py`) with Volume-Weighted Median baselines, 7d/30d spike guards, illiquidity & missing-baseline suppression, 5% 7-day volatility buffer, and 0–100 scoring backed by SQLite Migration 5.

### M5 / M5.1 / M5.2 — SELL Engine, Inventory Tracker & Free Notifications
- Implemented strict `(classid, instanceid)` public inventory importer and manual CSV/JSON importer (`src/sniper/adapters/inventory.py`).
- Implemented `SellSniperEngine` (`src/sniper/engine/sell_engine.py`) with crossed-book suppression, ghost buy-order heuristics, and un-fabricated depth verification.
- Implemented zero-cost Discord Incoming Webhook and Telegram Bot API alert channels (`src/sniper/notifications/`) with token redaction, per-listing/per-channel cooldowns, and persistent bounded retry backoff (Migration 4).

### M1–M4 — Foundation, Steam Adapters, Token-Bucket Breaker & Exact Fee Engine
- Built SSR hydration + legacy fallback identity resolver (`src/sniper/core/identity.py`), `ReadOnlyHttpClient` (`src/sniper/core/http_client.py`), monotonic 12 RPM token-bucket circuit breaker (`src/sniper/core/rate_limiter.py`), fail-closed `CurrencyConfig` (`src/sniper/core/currency.py`), exact dual-floor integer Valve fee engine (`src/sniper/core/fees.py`), SQLite Migrations 1–3, and CLI commands.
