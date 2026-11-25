# Security Policy & Non-Negotiable Architecture Guarantees

**CS2 Trading Sniper** is designed from first principles as a **strictly read-only, local-first** Steam Community Market research and opportunity detection utility.

## Core Security Guarantees

1. **Strictly Read-Only Steam Integration**
   - Every HTTP call to `steamcommunity.com` goes through `ReadOnlyHttpClient`, which supports read-only `GET` requests exclusively against a strict HTTPS allowlist:
     - `/market/listings/730/...`
     - `/market/itemordershistogram`
     - `/inventory/{steamid64}/730/2`
   - Zero automated purchasing, listing creation, order placement, trade offers, or inventory mutations exist anywhere in the codebase or API.

2. **Zero Steam Credential Storage**
   - The application never requests, reads, logs, stores, or transmits Steam passwords, Steam Guard codes, `maFile` / `shared_secret` / `identity_secret`, or browser cookies (`steamLoginSecure`, `sessionid`).
   - Public inventory sync operates strictly against unauthenticated public inventory endpoints for user-supplied SteamID64 identifiers, alongside offline manual CSV/JSON imports.
   - Steam endpoints that require authenticated cookies (such as `/market/pricehistory/`) are never queried.

3. **Zero Secret Echo-Back for Notification Channels**
   - Outbound alert secrets (`DISCORD_WEBHOOK_URL`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`) are loaded exclusively from `.env` / environment variables into runtime memory.
   - Secrets are never persisted to SQLite (`app_settings` stores only non-secret tuning preferences) and are never returned by FastAPI GET/PUT responses or displayed in the dashboard UI.
   - HTTP error logs and persisted `alert_deliveries` / `strategy_alert_deliveries` errors redact all Telegram bot tokens and Discord webhook URLs before persistence or console output.

4. **Conservative Sequential Rate Limiting & Fail-Closed Circuit Breaker**
   - One shared `SequentialRateLimiter` gates every Steam request across BUY, SELL, and Portfolio cycles (`asyncio.Lock`, token capacity = `1`, monotonic refill ceiling hard-clamped at `<= 12.0` requests/minute).
   - No proxy rotation, IP spoofing, header forgery (`Sec-Fetch-*`), or CAPTCHA/WAF bypass mechanisms exist.
   - HTTP `429` triggers immediate 300-second global exponential cooldown (`OPEN -> HALF_OPEN` single-canary probe); HTTP `403`, HTML anti-bot challenges, or malformed/schema-mismatched payloads immediately transition the breaker to `HALTED` pending explicit human inspection and reset.

5. **Integer Minor-Unit Financial Integrity**
   - All monetary calculations operate exclusively on integer minor currency units (`int` cents/minor units) without floating-point rounding drift.
   - Steam Wallet balances remain strictly isolated from cashout/off-platform estimates across backend payloads and dashboard indicators.

## Reporting a Security or Safety Regression

If any change introduces credential collection, proxy rotation, Steam write actions, unredacted webhook/token leaks, or rate-limiter bypasses, automated verification via `tests/test_security_audit.py` will fail CI immediately.
