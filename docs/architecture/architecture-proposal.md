# System Architecture Proposal: Local-First CS2 Sniper

**Document Version:** 1.0  
**Date:** 22 November 2026  
**Target Platform:** Windows 11  

---

## 1. Architectural Comparison & Evaluation

Two primary architectures were evaluated for the local-first Windows 11 application:

| Architectural Dimension | Option 1: FastAPI + SQLite + React/Vite SPA (Recommended) | Option 2: Monolithic Desktop GUI (PyQt6 / CustomTkinter) |
| :--- | :--- | :--- |
| **User Interface & UX** | Modern, responsive grid/bento layouts, high-density order-book depth charts, interactive tables, toast notifications. | Native OS widgets; complex charts and custom skin inspect cards are difficult and rigid to style. |
| **Separation of Concerns** | Strict decoupling: Backend headless API service handles scheduling, rate limiting, and Telegram alerts independently of UI. | UI thread tightly coupled to Python runtime; background polling loops risk freezing the GUI event loop. |
| **Headless / Background Operation** | Can run continuously in the background or system tray while the user closes the browser tab. | Closing the window terminates all background polling and Telegram notifications unless complex tray logic is added. |
| **Maintainability & Tech Debt** | Highly modular; tested standard patterns proven in audited repos (`MhmdMK277/CSFloatPriceChecker`). | Heavy Qt dependencies (PyQt6 adds 150MB+ bundle size); fragile cross-thread signal/slot plumbing. |
| **Multi-Device / Mobile Access** | Dashboard can be viewed locally on Windows or opened from a phone/tablet on the local LAN. | Strictly restricted to the local Windows monitor. |

### Decision: Option 1 (FastAPI + SQLite + React/TypeScript + Vite)
We recommend **Option 1**. It delivers superior visualization for complex order books and skin metadata, provides complete decoupling between background market polling and the UI, and aligns directly with the best-performing audited open-source project (`MhmdMK277/CSFloatPriceChecker`).

---

## 2. High-Level System Architecture

```
+-----------------------------------------------------------------------------------+
|                                 USER INTERFACES                                   |
|  +-------------------------------------+   +------------------------------------+ |
|  |     React 18 + Vite Web Dashboard   |   |        Telegram Bot Alerts         | |
|  |  (BUY Page, SELL Page, Portfolio)   |   |   (Actionable Snipes & High Bids)  | |
|  +-------------------------------------+   +------------------------------------+ |
+-----------------------------------------------------------------------------------+
                                          |
                                    HTTP / WebSocket
                                          |
+-----------------------------------------------------------------------------------+
|                             FASTAPI BACKEND SERVICE                               |
|                                                                                   |
|  +-------------------+  +--------------------+  +-------------------------------+ |
|  | BUY Engine        |  | SELL Engine        |  | Portfolio & Holding Service   | |
|  | (Listing Scans)   |  | (High Bid Tracker) |  | (7-day Marketable Tracker)    | |
|  +-------------------+  +--------------------+  +-------------------------------+ |
|            |                      |                            |                  |
|  +-------------------+  +--------------------+  +-------------------------------+ |
|  | OpportunityScorer |  | FeeCalculator      |  | ItemIdentityService           | |
|  | (0-100 Score)     |  | (Integer Cents)    |  | (Group IDs, Wears, Schema)    | |
|  +-------------------+  +--------------------+  +-------------------------------+ |
|                                                                                   |
|  +-----------------------------------------------------------------------------+  |
|  |                      RateLimiter & Async Scheduler                          |  |
|  |           (Token Bucket, 10-15 RPM Pacing, 300s Backoff on 429)             |  |
|  +-----------------------------------------------------------------------------+  |
|                                          |                                        |
|  +-----------------------------------------------------------------------------+  |
|  |                              SteamMarketAdapter                             |  |
|  |  - SSR Hydration Parser (/market/listings/730/G...)                         |  |
|  |  - Listing Render Client (/market/listings/730/.../render/)                 |  |
|  |  - Order Book Client (/market/itemordershistogram)                          |  |
|  |  - Public Inventory Client (/inventory/{steamid64}/730/2)                   |  |
|  +-----------------------------------------------------------------------------+  |
+-----------------------------------------------------------------------------------+
                                          |
                        +-----------------------------------+
                        |      SQLite Local Database        |
                        | (Items, Watchlists, Observations, |
                        |  Holdings, Alerts, Health Logs)   |
                        +-----------------------------------+
```

---

## 3. Database Schema (SQLite)

All monetary columns are strictly **INTEGER** (minor currency units / cents).

### Core Tables
1. **`items`**:
   - `id` (INTEGER PRIMARY KEY), `market_hash_name` (TEXT UNIQUE), `base_name` (TEXT), `exterior` (TEXT), `is_stattrak` (BOOLEAN), `is_souvenir` (BOOLEAN), `item_nameid` (INTEGER), `group_id` (TEXT).
2. **`watchlist_entries`**:
   - `id` (INTEGER PRIMARY KEY), `item_id` (FK), `strategy` (TEXT: 'BUY' or 'SELL'), `max_buy_price_cents` (INTEGER), `min_sell_multiplier` (REAL), `is_active` (BOOLEAN).
3. **`inventory_assets`**:
   - `id` (INTEGER PRIMARY KEY), `asset_id` (TEXT UNIQUE), `item_id` (FK), `marketable` (BOOLEAN), `tradable` (BOOLEAN), `marketable_at` (TIMESTAMP), `cost_basis_cents` (INTEGER).
4. **`market_observations`**:
   - `id` (INTEGER PRIMARY KEY), `item_id` (FK), `observed_at` (TIMESTAMP), `lowest_ask_cents` (INTEGER), `highest_bid_cents` (INTEGER), `top_level_buy_qty` (INTEGER), `cumulative_buy_qty` (INTEGER), `is_crossed` (BOOLEAN).
5. **`individual_listings`**:
   - `id` (INTEGER PRIMARY KEY), `listing_id` (TEXT UNIQUE), `item_id` (FK), `price_cents` (INTEGER), `fee_cents` (INTEGER), `inspect_link` (TEXT), `stickers_json` (TEXT), `seen_at` (TIMESTAMP), `status` (TEXT).
6. **`holdings`**:
   - `id` (INTEGER PRIMARY KEY), `item_id` (FK), `acquired_at` (TIMESTAMP), `cost_cents` (INTEGER), `marketable_at` (TIMESTAMP), `resale_proceeds_cents` (INTEGER), `status` (TEXT: 'HOLDING', 'LISTED', 'SOLD').
7. **`alerts`**:
   - `id` (INTEGER PRIMARY KEY), `item_id` (FK), `strategy` (TEXT), `score` (INTEGER), `confidence` (INTEGER), `gross_price_cents` (INTEGER), `net_proceeds_cents` (INTEGER), `sent_at` (TIMESTAMP), `user_outcome` (TEXT).
8. **`source_health`**:
   - `id` (INTEGER PRIMARY KEY), `timestamp` (TIMESTAMP), `endpoint` (TEXT), `status_code` (INTEGER), `response_time_ms` (INTEGER), `rate_limited` (BOOLEAN).

---

## 4. Rate Limiting, Pacing & Circuit Breaker Architecture

```
               [Request Initiated by Engine]
                             |
                             v
               +---------------------------+
               | Token Bucket Rate Limiter | <--- Max 12 requests / minute
               +---------------------------+
                             |
                             v
               +---------------------------+
               |   Random Jitter Injector  | <--- Delay: 4.0s - 6.0s + rand(0, 1.5s)
               +---------------------------+
                             |
                             v
                  [HTTP Request to Steam]
                             |
                +------------+------------+
                |                         |
            (HTTP 200)                (HTTP 429 / 403)
                |                         |
                v                         v
        [Process JSON]          [TRIGGER CIRCUIT BREAKER]
                                          |
                                          v
                                 1. Freeze All Polling
                                 2. Pause 300 Seconds
                                 3. Log Event to source_health
                                 4. Surface UI Status Warning
                                 5. Send 1 Canary Request at 300s
```

---

## 5. Packaging & Windows 11 Deployment

1. **Phase 1 (Development & MVP):**
   - Native Windows batch launcher: `launch.bat`
   - Automatically activates virtual environment, checks dependencies, launches FastAPI server on `http://127.0.0.1:8000`, and opens the default browser.
2. **Phase 2 (Production Packaging):**
   - Single-click desktop executable using PyInstaller + embedded frontend assets.
