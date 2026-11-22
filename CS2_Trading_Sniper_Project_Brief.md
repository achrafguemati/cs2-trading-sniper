# CS2 Trading Sniper — Project Brief & Technical Requirements

**Prepared:** 21 November 2026  
**Target platform:** Windows 11  
**Product goal:** A read-only CS2 market research, opportunity detection, inventory and alerting application with two core strategies: **BUY LOW → RESALE** and **SELL INTO UNUSUALLY HIGH BUY ORDERS**.

> This document is the product and engineering brief for Antigravity. It is not permission to start coding immediately. Follow the planning and approval gates in `ANTIGRAVITY_INITIAL_PROMPT.md`.

---

## 1. Product objective

Build one local application that helps a human trader identify, verify, rank and manually act on potentially profitable CS2 skin market opportunities.

The application has two independent market engines that share a data layer, item identity model, fee calculator, history store, opportunity evaluator, dashboard and notification service.

### Mode A — BUY SNIPER

Find underpriced **individual Steam Community Market sell listings** for skins the user wants to buy. Evaluate whether the item could plausibly be resold later at a profit after fees, price risk, liquidity, market movement and the CS2 re-market restriction.

The app must not buy automatically.

### Mode B — SELL SNIPER

Monitor marketable items in the user's CS2 inventory (and/or a manually entered list) for unusually high active Steam buy orders. Detect potentially actionable bids, verify item identity and book consistency, estimate seller proceeds after fees, and alert the user so they can manually list the item.

The app must not list or sell automatically.

### Shared goal

The app should not confuse a displayed market anomaly with executable profit. Every opportunity must show source, currency, data age, exact item variant, order quantity, fee assumptions, confidence and reasons for rejection or warning.

---

## 2. Non-negotiable product rules

1. **Read-only monitoring only.** No automatic buying, selling, listing, placing/cancelling buy orders, trading, accepting offers or modifying Steam inventory.
2. **No Steam password, Steam Guard code, maFile, `steamLoginSecure` cookie or browser session cookie.** Do not ask for or store any of these.
3. Prefer public, unauthenticated market data. Inventory import should work with a public Steam inventory and a manual watchlist. If an inventory is private, explain that limitation and offer manual import; do not seek session cookies as a workaround.
4. Do not bypass CAPTCHA, access controls, rate limits, IP blocks, regional restrictions or anti-bot protections. Do not rotate proxies to evade limits.
5. Respect rate limits. On HTTP 429, 403 challenge, unusual HTML, `success=false`, malformed/null payloads or unexpected schema, pause and back off globally. Never retry aggressively.
6. Do not claim that an alert is a guaranteed fill or guaranteed profit. Use labels such as `Candidate`, `Needs verification`, `High-confidence signal`, `Stale data`, `Book crossed`, `Unverified order` and `Manual confirmation required`.
7. Steam Wallet value is not cash. Report Steam Wallet profit separately from external cash proceeds. Do not imply that Steam Wallet funds can be withdrawn to a bank account.
8. Every price must carry a currency and a price semantic: buyer-pays/gross, seller-receives/net, order-book bid, listing ask, reference price, third-party cash price, or Steam Wallet price.
9. Store integer minor units (e.g. cents) internally. Avoid floating-point money arithmetic.
10. Store timestamps in UTC and show local time in the UI. Record source and retrieval time for every market observation.
11. Preserve exact CS2 item identity. Never merge different wears, StatTrak/Souvenir state, paint index, pattern/seed, float or listing assets just because names look similar.
12. The app must remain useful even when Steam's undocumented endpoints change: isolate each data source behind adapters, validate schemas, log failures and provide a manual fallback.

---

## 3. Current Steam Market context to research and support

The UI reference supplied by the user is the **redesigned Steam Community Market** with item pages, graph, order-book section, advanced filters and individual listing cards. The user supplied screenshots showing both the compact order-book panel and the newer item page with wear/float/sticker/pattern details.

Valve announced major Community Market page, listing and search/filter changes in beta on **12 May 2026**. Counter-Strike was a major use case for richer item metadata. The app must investigate and support the current UI/data model rather than relying on screenshots or old HTML selectors.

Official Steam sources to consult first:

- Community Market FAQ: <https://help.steampowered.com/en/faqs/view/61F0-72B7-9A18-C70B>
- Steam Community Market Updates (May 12, 2026): <https://store.steampowered.com/news/>
- CS2 seven-day re-trade/re-market restriction announcement (Oct 2024): <https://store.steampowered.com/news/app/730>
- Trade Protection information (separate from market re-list restrictions): <https://help.steampowered.com/en/faqs/view/>

**Important distinction:** the 7-day re-trade/re-market restriction introduced for CS2 items acquired through the Steam Community Market is not the same thing as the later Trade Protection/reversal feature for player-to-player trades. Treat these as separate rules. Never hard-code a marketable date solely from purchase time when Steam displays the authoritative item restriction/marketable date. Track the displayed/observed date where possible.

Valve's Community Market documentation describes the Steam transaction fee and game-specific fee. For CS2 calculations, verify the current fee schedule and rounding/minimum-fee behavior at implementation time. Do not assume a simple `price * 0.85` calculation. Build a cent-accurate fee module and test it against the actual Steam sell confirmation UI and current official fee documentation.

Steam Market endpoints used by community projects are generally undocumented. Treat routes such as `priceoverview`, `search/render`, `itemordershistogram`, `orderbook`, `/listings/.../render`, and any structured `POST /market/listings/...` route as **experimental/unofficial unless Valve provides official documentation**. Do not assume field names, request bodies, rate limits, currency parameters or availability based on a blog post or another AI report. Verify each route against live requests and current community source code with a dated evidence log.

### 3.1 User's URL compatibility requirement

Support standard Steam listing URLs and grouped URLs such as:

`https://steamcommunity.com/market/listings/730/G18820420D5083003?category_Exterior=WearCategory2&appid=730`

The parser must preserve:

- App ID (730)
- The exact path identifier (including grouped `G...` identifiers)
- Exterior/wear filter (`WearCategory0` through `WearCategory4` when applicable)
- Other valid item filters/query parameters
- The canonical market hash name if it can be resolved reliably

Never assume the path token is a `market_hash_name`. The grouped URL must not be fed blindly into endpoints that require a canonical market hash name. Build a resolver with explicit outcomes: `resolved`, `partially_resolved`, `unsupported`, or `ambiguous`.

### 3.2 Order-book semantics

The UI can show:

- Cheapest current sell listing / lowest ask
- Highest current buy order / highest bid
- Quantity at each individual price level
- Cumulative order counts or totals
- Historical median sale chart

Do not confuse cumulative totals with quantity at the top price level. In the user's screenshot, for example, Steam displayed `211,731 requests to buy at $5.09 or lower`, while the exact `$5.09` level showed quantity `1`. The UI must store/display both `top_level_quantity` and `cumulative_quantity` separately when those fields are available.

A buy order is a maximum amount a buyer is willing to pay, not proof that the buyer will pay that amount for a cheaper eligible listing. Steam's matching logic and order priority must be verified from official Steam documentation and live controlled observations. A persistent crossed book (`highest_bid >= lowest_ask`) is a data-quality or execution-risk warning: re-fetch, verify exact item identity/variant and do not call it a reliable opportunity until explained.

---

## 4. Strategy A — BUY LOW → HOLD → RESALE

### 4.1 User flow

1. User adds Steam Market URLs or searches/selects skins in the app.
2. User configures maximum buyer-paid purchase price per skin, optional minimum discount, target exit venue and minimum net profit/ROI.
3. The BUY engine retrieves current individual sell listings where technically supported.
4. Normalize all candidate prices to the same currency and buyer-paid/seller-received semantics.
5. Compare each candidate with a reliable reference: recent transaction history where available, robust median/volume-weighted median, current lowest ask, and optional independent marketplace references.
6. Inspect item variant and listing details: weapon/skin, wear, StatTrak/Souvenir, float, paint seed/pattern, stickers/charms and listing asset ID where available.
7. Reject or flag mismatches, missing metadata, suspicious outliers, stale listings, low-liquidity items, extreme discounts likely caused by bad data or incomparable attributes.
8. Estimate the complete acquisition cost and a conservative exit price after fees, holding period, price-risk buffer and liquidity discount.
9. Send a BUY alert with the exact listing link and reasons. Human buys manually.
10. Record the purchase and acquisition cost manually (or via a manual portfolio entry). Track the CS2 marketability/re-market eligibility date from Steam's item status. Re-evaluate while holding.
11. When eligible and the expected net exit remains profitable, send a RESALE alert. Human lists manually.
12. Record realized proceeds and actual profit after the user confirms the sale.

### 4.2 BUY opportunity math

Use explicit price semantics:

- `purchase_buyer_pays`: total amount the user must pay.
- `expected_exit_buyer_pays`: target price paid by the future buyer.
- `expected_exit_seller_receives`: exact seller proceeds after CS2/Steam fees.
- `net_profit_wallet`: expected seller proceeds minus the full purchase cost and any other Steam Wallet costs.
- `roi_percent`: net_profit divided by acquisition cost.

For external marketplaces, build a separate cash calculation including purchase cost, seller fee, withdrawal fee, deposit/payment fee, FX and cross-market transfer restrictions. Never silently combine Steam Wallet USD with withdrawable USD.

Do not mark a candidate profitable if the resale restriction, liquidity, trade lock, fee or exit venue is unknown. Use scenarios: conservative/base/optimistic rather than a single false-precision forecast.

### 4.3 Holding and seven-day handling

- Store `acquired_at`, acquisition source, item asset ID, cost basis, restriction source, expected/observed `marketable_at`, and last verification time.
- If Steam exposes the true marketability date, use it rather than blindly calculating `acquired_at + 7 days`.
- Continue tracking market value while held, but do not send a sell-now alert for an item Steam marks unmarketable.
- Provide price-drop and break-even alerts during the holding period.

---

## 5. Strategy B — SELL INTO AN UNUSUALLY HIGH BUY ORDER

### 5.1 User flow

1. Import marketable CS2 inventory from a public inventory when available, or allow a manual skin list / manual inventory CSV/JSON.
2. Track exact item identity, wear, StatTrak/Souvenir, paint variant and marketability status.
3. Retrieve current order-book data for each exact marketable variant.
4. Retrieve cheapest current sell listing, highest buy order, exact quantity at the top bid, cumulative quantity if available, and timestamp.
5. Compare the highest bid to a robust historical baseline and a configurable minimum threshold/multiplier (e.g. buyer-paid bid >= $1 and >= 10x normal baseline; these are examples, not hard-coded truths).
6. Check the lowest ask and all visible order-book levels. If the book is crossed, inconsistent, stale or the bid seems unfillable, re-query and mark as suspicious/unverified.
7. Re-check the top bid immediately before alerting; record the observed quantity and market timestamp.
8. Calculate expected seller proceeds for the buyer-paid bid using current CS2 fees and cent rounding.
9. Send SELL alert only as a candidate, never as a guaranteed fill. User opens Steam, verifies the item/price and confirms listing manually.
10. Record whether the order still existed and whether the sale completed, so the bot can measure real alert quality over time.

### 5.2 SELL alert fields

Every alert should show:

- Exact skin name, wear and any relevant variant attributes
- Highest buy order (buyer-pays amount)
- Quantity at that exact bid price
- Cumulative buy quantity only if separately labelled
- Lowest sell listing (buyer-pays amount)
- Bid/ask spread
- Historical baseline and multiplier
- Expected seller proceeds after fees
- Estimated incremental proceeds versus a normal sale
- Order-book retrieval time and freshness
- `crossed_book`, `unverified`, `stale`, `low_quantity`, `variant_mismatch` warnings
- Steam item page / inventory link
- Clear manual verification instructions

### 5.3 SELL opportunity logic

Do not define `highest_bid >= threshold` as sufficient by itself. At minimum require:

- Exact variant match.
- Correct currency.
- Fresh and valid API response.
- Positive top-level buy quantity if available.
- Bid materially above normal reference.
- A valid lowest ask / explainable no-ask state.
- Repeated confirmation close to alert time.
- Net seller proceeds and opportunity value calculated correctly.

If `highest_bid >= lowest_ask`, normally classify as `crossed_book` and investigate/re-fetch. Do not issue a normal sell opportunity alert solely from crossed values. Explain possible stale snapshots, variant/filter mismatch, endpoint inconsistency or matching execution.

If `lowest_ask > highest_bid`, this is a normal spread. The user may list manually at the buyer-paid bid only if the specific buy order still exists and the exact skin qualifies. This remains a candidate, not a guaranteed fill; a buyer order can disappear or fail funding checks before execution.

---

## 6. Market data sources and competitor research

Build a source registry with source type, URL, API access model, auth requirements, terms/rate limits, update cadence, fee schedule, currency, listing-level vs aggregate capabilities, available metadata, geographic/cash-out availability, and confidence.

### 6.1 Steam-first data

Steam Community Market is the primary execution market for both initial strategies. Research current redesigned Market pages and item data. Isolate all Steam calls in a `SteamMarketAdapter` and validate every response. Support both legacy and new/structured routes only where verified.

### 6.2 Secondary market references

Investigate and compare current capabilities, API terms, prices, fee schedules and cash-out restrictions for:

- CSFloat: <https://csfloat.com>
- Skinport: <https://skinport.com>
- BUFF163: <https://buff.163.com>
- Youpin: <https://www.youpin898.com>
- DMarket: <https://dmarket.com>
- Waxpeer: <https://waxpeer.com>
- market.csgo: <https://market.csgo.com>
- SkinBid: <https://skinbid.com>
- C5Game: <https://www.c5game.com>
- LIS-SKINS: <https://lis-skins.com>
- White.Market: <https://white.market>
- Tradeit: <https://tradeit.gg>
- SkinSwap: <https://skinswap.com>
- BitSkins: <https://bitskins.com>

These are research candidates, not endorsements. Verify whether a service is currently operating, has accessible data/API, allows users in Tunisia, requires KYC, and supports a cash withdrawal route. Do not scrape against terms or fabricate a supported API.

### 6.3 Competitor / reference websites

Investigate current features and data coverage for:

- Pricempire: <https://pricempire.com>
- CS2.sh: <https://cs2.sh>
- Skinstrack: <https://skinstrack.com>
- SteamAnalyst: <https://www.steamanalyst.com/terminal>
- CSFloat marketplace and extension: <https://csfloat.com>
- CS Armoury: <https://csarmoury.com/market>
- CSGAIN: <https://csgain.com/market>

Do not rely on competitor marketing claims alone. Record current plan pricing/limits only if directly verified and dated.

---

## 7. Open-source repositories to inspect

These repositories are research references, not instructions to copy wholesale. Inspect the actual default branch, commits, issues, tests, dependency age, license file, API calls, request intervals and known limitations. Do not claim that README-only features are proven working.

### Priority repositories

1. **CS2 Sniper** — <https://github.com/Soophere/cs2-sniper>  
   Python/FastAPI, CSFloat listing scanner, multi-market reference book, fee/risk/liquidity guards, dashboard, Discord. MIT license observed. Best reference for candidate scoring and false-profit protections; not Steam-first.
2. **UUfetch** — <https://github.com/juggperc/uupinfetch>  
   Python/FastAPI multi-market scraper, arbitrage, portfolio, backtesting, patterns, webhook and Windows EXE. Inspect license, active maintenance and each market adapter before reuse.
3. **CSFloat Tracker** — <https://github.com/MhmdMK277/CSFloatPriceChecker>  
   Python desktop/local dashboard, one-minute CSFloat/Skinport alerts, inventory, float/sticker metadata, price history. MIT license observed. Strong listing-alert and inventory UI reference; not Steam listing source.
4. **SkinTicker** — <https://github.com/skinticker/skinticker>  
   CSFloat/Steam comparisons, inventory cost basis, Telegram and history. AGPL-3.0; account for copyleft obligations before incorporating code.
5. **Steam Market Scraper** — <https://github.com/ErayBahar/steammarketscraper>  
   Python, Steam `itemordershistogram`, lowest sell/highest buy, Telegram. Check exact license and limitations; older implementation and not sufficient as-is.
6. **Steam Market Multi Watcher** — <https://github.com/blubtxt/Steam-Market-Multi-Watcher>  
   PHP multi-item dashboard and Discord threshold notifications. Uses aggregate `priceoverview`, so not adequate for listing-level sniping.
7. **CS2 Market Analytics Bot** — <https://github.com/ArseniHv/cs2-market-bot>  
   Telegram, market analytics, trends and predictions; default collection interval is not designed for sub-minute Steam listing alerts.
8. **Steam CS2 Market Scraper Analytics** — <https://github.com/Madejson1/steam-cs2-market-scraper-analytics>  
   Steam page parsing/Selenium, listing IDs, asset IDs, floats, patterns and sticker codes. Reference only until license and current compatibility are confirmed.
9. **Steam item name IDs** — <https://github.com/somespecialone/steam-item-name-ids>  
   Mapping data useful for legacy histogram lookups; verify freshness and coverage.
10. **CS2 Items Schema** — <https://github.com/somespecialone/cs2-items-schema>  
    Item names, paints, qualities and metadata. Verify current format and license.
11. **CS2 Historical Dataset** — <https://github.com/Asalio123/cs2-skins-daily-dataset>  
    Historical daily data for research/backtesting, not live prices. Verify latest commit and data provenance.
12. **CS Skins Prices Parser** — <https://github.com/MMaakkss/cs-skins-prices-parser>  
    Steam/DMarket/market.csgo asks and bids plus history. Study market normalization and persistence. Verify license and exact supported endpoints.
13. **CS2 Cross-Market Arbitrage Analyzer** — <https://github.com/i4cobus/cs2-cross-market-arbitrage-analyzer>  
    Data-quality, liquidity, risk and explainable scoring; primarily CSFloat/Youpin. Useful for analysis architecture.
14. **CS2 Trader Extension** — <https://github.com/gergelyszabo94/csgo-trader-extension>  
    Browser helper for manual market inspection. GPL-3.0; companion reference, not required dependency.
15. **CSFloat Extension** — <https://github.com/csfloat/extension>  
    Browser companion for float/pattern data. Verify current license and API compatibility.
16. **Steam Market Search (Node.js)** — <https://github.com/DrKain/steam-market-search>  
    Search/filter library reference, not an alert engine.

### Repository audit requirements

Create `docs/research/repository-audit.md` with a table containing:

- Repository URL and commit SHA/date inspected
- License (from actual LICENSE file; `Unknown` if missing)
- Last commit date and archived status
- Runtime/language/dependencies
- Supported marketplace(s)
- Exact endpoint(s) and payload shape used
- Listing-level or aggregate data
- Alert channels
- Inventory support
- Tests and test results
- Security concerns (cookies/session credentials/auto-trading)
- What can be reused, what must not be reused, and why

Never infer stars/forks/maintenance from search snippets. Check GitHub directly and date the observation.

---

## 8. Required market research deliverables before implementation

Before writing application code, research and produce:

1. `docs/research/market-landscape.md` — competitors, marketplaces, plans, API access, fees, cash-out and regional availability.
2. `docs/research/reddit-community-findings.md` — dated Reddit discussions, firsthand experiences, common failure modes, community recommendations. Separate first-hand reports from speculation/promotional posts.
3. `docs/research/repository-audit.md` — audit from Section 7.
4. `docs/research/steam-api-verification.md` — verified current endpoints, request parameters, headers, response examples with secrets removed, response-schema validation, currency semantics, known rate-limit observations, failure modes, Terms/risks and a safe test plan.
5. `docs/research/steam-market-rules.md` — official fee schedule, matching behavior, buy-order priority, marketability restrictions, Trade Protection distinction and Steam Wallet constraints. Cite official Valve sources first.
6. `docs/strategy/buy-strategy.md` — entry filters, normal-price model, exit assumptions, holding restriction, profit/risk and sizing.
7. `docs/strategy/sell-strategy.md` — inventory requirements, bid/ask validation, top-level quantity, ghost/stale/crossed-book warnings, fees and execution verification.
8. `docs/strategy/opportunity-scoring.md` — transparent scoring, rejection reasons and confidence labels.
9. `docs/architecture/architecture-proposal.md` — proposed design, alternatives, diagrams, data model and deployment.
10. `docs/decisions/decision-log.md` — decision records, assumptions and unresolved questions.

Every external claim needs a source URL and access/publication date. Prioritize official Valve sources for rules and current implementation source code for undocumented endpoints. If an endpoint is not independently verified, label it `unverified/experimental`.

---

## 9. Recommended architecture (proposal to evaluate, not pre-approved)

A local-first application for Windows 11:

- Backend: Python 3.12+ (confirm current stable compatibility), `httpx`, `asyncio`, Pydantic.
- API/UI backend: FastAPI.
- Database: SQLite for a single-user local app; use migrations (e.g. Alembic) if schema needs versioning.
- Frontend: React + TypeScript + Vite (or a smaller server-rendered interface if research justifies it). Use a modern responsive dashboard.
- Scheduler: asyncio-based scheduler or APScheduler with explicit per-source rate budgets.
- Notifications: Telegram Bot API, optional Discord webhook. Do not require a cloud account.
- Packaging: native Windows launch script first; PyInstaller/desktop packaging only after core is stable.
- Configuration: `.env` for secrets and validated `config.toml`/`config.yaml` or DB settings for non-secret configuration.
- Logging: structured rotating logs, request IDs, source status, data age and rate-limit events.

### Suggested service boundaries

- `SteamMarketAdapter` — listing search, listing details, order book, price history, marketability data.
- `InventoryService` — public inventory, manual watchlist, asset mapping, marketable status.
- `ItemIdentityService` — group IDs, market hash names, wear, StatTrak/Souvenir, float, paint seed, stickers and schema normalization.
- `PriceHistoryService` — snapshots, robust median, volume, outlier detection.
- `FeeCalculator` — cent-exact buyer-pays/seller-receives calculations by game and venue.
- `BuyOpportunityEngine` — listing-level BUY candidate detection and future exit evaluation.
- `SellOpportunityEngine` — high bid detection and order-book verification.
- `PortfolioService` — acquisition cost, holding status, marketable date, sale proceeds, realized/unrealized P&L.
- `OpportunityScorer` — explainable score and reject reasons.
- `AlertService` — Telegram/Discord, deduplication, cooldown and alert audit.
- `Scheduler/RateLimiter` — source-specific request scheduling, global backoff, caching and circuit breaker.
- `Dashboard/API` — watchlists, opportunities, inventory, portfolio, data-source health and settings.

### Data model (minimum)

- `items` / `item_variants`
- `watchlist_entries` (BUY and SELL settings)
- `inventory_assets` (asset ID, exact identity, quantity, tradable/marketable, observed marketable date)
- `market_observations` (source, item, currency, price semantics, bid, ask, quantities, timestamp, raw payload hash)
- `individual_listings` (listing ID, asset ID, exact attributes, buyer-pays price, source, seen time, current status)
- `price_history`
- `purchases` / `holdings` / `sales`
- `opportunities` (strategy, score, confidence, expected costs/proceeds, risk flags)
- `alerts` (dedupe key, sent time, channel, status)
- `source_health` / `rate_limit_events`

---

## 10. Dashboard requirements

### Main dashboard

- BUY signals, SELL signals, holdings, capital deployed, realized profit, unrealized estimate.
- Data freshness and source health.
- Number of active watchlist items, last scan and last successful request.
- Warnings when Steam source is degraded or rate-limited.

### BUY page

- Add Steam URL / search market hash name.
- Parse grouped `G...` IDs and exterior filters.
- Per-item max buyer-paid price, minimum discount, minimum net profit and target exit venue.
- Sort by lowest total acquisition cost or highest estimated risk-adjusted net profit.
- Listing ID, asset ID, float, seed, stickers and item link when available.
- Alert history and already-seen/expired listing state.

### SELL page

- Import public inventory or manual inventory file/list.
- Filter marketable items only; show locked items separately with their marketable date.
- Configure minimum high bid, multiplier vs baseline, minimum top-level quantity, cooldown.
- Show highest bid and exact quantity at that level, lowest ask and quantity, cumulative totals separately.
- Highlight suspicious/crossed/stale books and require a fresh recheck.
- Sort by expected incremental seller proceeds, not gross bid alone.

### Portfolio/Holding page

- Purchase date, acquisition source, total cost, item restriction date, days until eligible.
- Current reference value and conservative exit net.
- Realized/unrealized P&L.
- Sell reminders after item becomes marketable.

### Settings

- Currency (USD default), locale, request budgets, alerts, source toggles, thresholds, watchlist intervals.
- Defaults must be conservative and user-adjustable.
- No secrets rendered back to the UI after initial entry.

---

## 11. Alert design

### BUY alert

Include:

- `BUY CANDIDATE` label, not a promise
- Item + exact wear/variant
- Current individual listing buyer-pays price
- Lowest comparable listings and reference median
- Discount %
- Expected resale buyer-pays and seller-receives
- Expected net profit / ROI after fees and risk buffer
- Holding/marketability restriction warning
- Listing ID, age and exact Steam URL
- Data source, timestamp and confidence/rejection warnings

### SELL alert

Include:

- `SELL CANDIDATE` label
- Exact inventory item/variant
- Highest bid and quantity at exact bid level
- Lowest ask and quantity
- Bid/ask spread and baseline multiple
- Expected seller proceeds after fees
- Expected extra proceeds versus normal sale
- Freshness, recheck result and warning flags
- Exact Steam item page/inventory link
- Explicit instruction to verify the price on Steam before listing

### Notification controls

- Per-item thresholds and cooldowns.
- Deduplicate the same listing ID.
- For SELL, deduplicate by item + bid price + time window; re-alert only on a meaningful bid increase or after configured cooldown.
- Offer `/status`, `/buy`, `/sell`, `/inventory`, `/opportunities`, `/set`, `/pause`, `/resume` commands if supported safely.
- Do not expose bot tokens in logs.

---

## 12. Testing and acceptance criteria

### Unit tests

- Currency conversion and cents arithmetic
- Steam fee calculation and rounding (test against official docs/UI cases)
- Parsing standard and `G...` listing URLs
- Wear mapping and variant normalization
- Listing ID deduplication
- Orderbook parsing for all known observed response envelopes
- Currency mismatch rejection
- Empty/null/HTML/error payload rejection
- Crossed-book classification
- Sell quantity parsing
- Price baseline and outlier resistance
- ROI/profit calculations with seven-day holding risk
- Alert cooldown and re-alert logic
- Timezone and marketability-date handling

### Integration tests

- Recorded, redacted fixtures from real Steam responses.
- Read-only live smoke tests with a very small number of manual requests, no high-frequency loops.
- Telegram test message.
- Manual inventory import test.
- Public inventory test only when user supplies a SteamID64 and inventory is public.
- Verify the exact Steam listing URL opens the matching wear/variant.

### Acceptance criteria

1. User can add a standard Steam URL and a grouped `G...` URL with wear filter.
2. Parser either resolves the correct canonical item/variant or clearly explains ambiguity; no silent wrong match.
3. BUY engine can display real individual listing candidates with listing identity where the data source supports it.
4. BUY candidate net economics include acquisition price, seller proceeds, fees, holding restriction and configurable price-risk buffer.
5. SELL engine can import a public or manual inventory list and distinguish marketable from restricted items.
6. SELL candidate displays highest bid, top-level quantity, lowest ask and separate cumulative totals where available.
7. Crossed/stale/inconsistent book never becomes a high-confidence sale signal without revalidation.
8. Alerts are deduplicated and include timestamps, source and exact market links.
9. All requests respect configured budgets; 429 triggers backoff and visible status.
10. No buy/sell/trade endpoint or account credential path exists in the product.
11. Test suite passes; failures and unverified endpoint assumptions are documented.
12. User can run the application locally on Windows 11 using documented steps.

---

## 13. Research and engineering quality standards

- Browse current sources and date the evidence. The date of this brief is 21 November 2026; refresh every date-sensitive fact before implementation.
- Official Valve source first for market rules, fees, item restrictions and matching behavior.
- Inspect live GitHub code and commit hashes; never cite a repository README as proof that a feature works.
- Do not invent API fields or undocumented request bodies. Save redacted response examples and add schema tests.
- Distinguish `official`, `community-documented`, `observed`, and `unverified` facts.
- Use a small controlled read-only proof of concept before building the complete application.
- If Steam's endpoint is unavailable or incompatible with the new UI, explain the limitation and propose a compliant fallback. Do not circumvent protections.
- Do not promise guaranteed profits, guaranteed detection latency or guaranteed order execution.
- Use clear English in code and UI by default; keep user-facing labels simple and optionally support French/Arabic later.
- Add a `CHANGELOG.md`, `.env.example`, `.gitignore`, `README.md`, setup script and a security guide.

---

## 14. Antigravity skills, rules, MCP and plugins

The project does not require a special third-party plugin to work. Core integrations are HTTP requests to Steam and Telegram plus local storage. Do not install unrelated plugins or expose credentials to external services.

First inspect Antigravity's currently available skills, rules, MCP servers and plugins. Use only relevant, trusted tools that materially improve research or implementation.

Potential workspace skills to create only if useful:

1. `.agents/skills/steam-market-research/SKILL.md` — verify Steam endpoints, source provenance, currency semantics, rate limits and response schema.
2. `.agents/skills/cs2-trading-math/SKILL.md` — fee calculations, buyer-pays/seller-receives, ROI, holding restrictions, risk buffers and wallet-vs-cash distinctions.
3. `.agents/skills/market-data-quality/SKILL.md` — item identity, variant matching, stale data, crossed books, outliers, liquidity and dedupe.
4. `.agents/skills/python-quality/SKILL.md` — Python typing, async HTTP, tests, migrations, logging and Windows packaging.
5. `.agents/skills/security-review/SKILL.md` — no Steam passwords/cookies, secret handling, read-only operation, endpoint safety and dependency checks.

Each skill should have one focused job and valid YAML frontmatter (`name`, `description`). Do not create five large generic skills just for appearance; consolidate or omit if unnecessary.

Useful current Antigravity docs:

- Agent Skills: <https://antigravity.google/docs/skills>
- Rules and workflows: <https://antigravity.google/docs/rules-workflows/>
- Artifact review / Planning mode: <https://antigravity.google/docs/artifact-review/>
- Skills migration: <https://antigravity.google/docs/migration/workflows-to-skills/>

Prefer Antigravity **Planning mode** for the initial phase. The current Antigravity documentation describes Planning mode as producing structured plans/artifacts and researching the project before execution. Use `.agents/skills/` for workspace skills and `AGENTS.md` or `.agents/rules/*.md` for concise persistent project rules. Workflows are being migrated toward Agent Skills; do not build the project around deprecated workflow conventions without checking current docs.

Optional tools may include:

- Web research access for current Steam/market documentation and competitor research.
- GitHub read/search access for repository audits.
- Browser access for visually checking Steam Market pages and the resulting local UI.
- Terminal/test runner for local Windows commands.

Do not add a database/cloud plugin, payment plugin, or account connector unless a documented requirement arises and the user approves it.

---

## 15. Scope boundaries / future features

Not in the first version:

- Auto-buy or auto-sell
- Steam trade automation
- Market manipulation, fake buy orders, wash trading or price pumping
- Proxy rotation or anti-bot bypass
- Private inventory cookie scraping
- Automated cash withdrawals or payment handling
- Multi-user hosted SaaS
- AI price predictions used as the sole basis for trading

Future candidates after the local MVP works:

- Cross-market price adapters with terms-compliant official APIs
- Cash-out fee comparison and Tunisia payment availability research
- Advanced float/sticker/pattern valuation
- Backtesting and paper-trading mode
- More detailed market depth and volume analytics
- Optional multilingual UI (Arabic/French/English)

---

## 16. Official/current reference links

Use these as starting points, then verify the latest versions:

- Steam Community Market FAQ: <https://help.steampowered.com/en/faqs/view/61F0-72B7-9A18-C70B>
- Valve Steam Community Market update announcement (May 12, 2026): <https://store.steampowered.com/news/>
- Valve CS2 item marketability restriction update (Oct 2024): <https://store.steampowered.com/news/app/730>
- Valve Trade Protection information: <https://help.steampowered.com/en/faqs/view/>
- Antigravity Agent Skills: <https://antigravity.google/docs/skills>
- Antigravity Planning/artifact review: <https://antigravity.google/docs/artifact-review/>

**Remember:** this brief is a product specification. The first Antigravity task is research, fact-checking and strategy design only. No implementation until the user approves the plan.
