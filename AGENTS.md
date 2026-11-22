# CS2 Trading Sniper — Project Rules & Engineering Standards

These rules are non-negotiable across all development phases of this project. Every agent, tool, and engineer must adhere strictly to these constraints.

---

## 1. Safety, Credentials & Read-Only Operation

1. **Strictly Read-Only:** The application is a market research, opportunity detection, and inventory alerting tool only.
   - **NO** automatic purchasing, listing, selling, trade offer creation/acceptance, or buy-order placement.
   - **NO** execution endpoints or code paths capable of mutating market state or initiating transactions.
2. **Zero Credential Storage:**
   - **NEVER** ask for, store, log, or accept Steam account passwords, Steam Guard codes, two-factor shared secrets (`maFile`), `steamLoginSecure` cookies, or browser session cookies.
   - Inventory tracking operates exclusively via public Steam inventory endpoints (`/inventory/{steamid64}/730/2`) or manual user import (CSV/JSON/watchlist).

---

## 2. Network Etiquette, Anti-Bot & Rate-Limiting

3. **No Rate-Limit Evasion or Proxy Rotation:**
   - **NEVER** rotate proxies, spoof IP addresses, bypass CAPTCHAs, or attempt to defeat Cloudflare/Akamai protections.
   - Respect Steam's rate limits. Local pacing target: **conservative sequential pacing of ~10–15 requests per minute with randomized jitter**. This is an internal project safety policy, not an official Steam rate limit or guaranteed safe quota. Any HTTP 429 triggers immediate global backoff.
4. **Global Backoff Mandate:**
   - On `HTTP 429 Too Many Requests`, `HTTP 403 Forbidden`, unexpected HTML challenge, or malformed schema, pause all polling immediately.
   - Implement exponential backoff starting at 300 seconds (5 minutes) before canary re-probing.

---

## 3. Data Integrity & Financial Precision

5. **Integer Cents Financial Arithmetic:**
   - All internal monetary values must be stored and computed as integer minor currency units (e.g., integer cents for USD/EUR).
   - Never use floating-point math for pricing calculations or fee deductions.
6. **Exact Valve Fee Implementation:**
   - Do not use a naive `price * 0.85` approximation.
   - Implement the exact dual-floor fee schedule: $\max(1, \lfloor R \times 0.05 \rfloor)$ for Steam and $\max(1, \lfloor R \times 0.10 \rfloor)$ for CS2, tested against official documentation.
7. **Explicit Price Semantics & Currency Segregation:**
   - Every price must explicitly state its currency and semantic: `buyer_pays_gross`, `seller_receives_net`, `orderbook_bid`, `listing_ask`, or `reference_price`.
   - Never conflate **Steam Wallet funds** with **withdrawable cash**. Wallet profits must be displayed separately from external cashout estimates.
8. **Exact CS2 Item Identity Preservation:**
   - Never merge, conflate, or fuzzy-match items across different wear categories (FN, MW, FT, WW, BS), StatTrak vs. Normal, Souvenir status, paint index, or pattern seed.

---

## 4. Signal Integrity & Alert Skepticism

9. **No Guarantees / High Skepticism:**
   - Alerts are candidates requiring manual human verification, never promises of fill or guaranteed profit.
   - Label every alert with clear status tags: `Candidate`, `Needs verification`, `High-confidence signal`, `Stale data`, `Crossed book`, `Unverified order`.
10. **Ghost Buy Order & Crossed Book Defense:**
    - High buy orders must be treated as maximum bids, not executable cash. The application must verify top-level quantity vs. cumulative quantity and check lowest ask before alerting.
    - Crossed books (`highest_bid >= lowest_ask`) must trigger re-querying and cautionary flags, never an instant buy/sell recommendation.
