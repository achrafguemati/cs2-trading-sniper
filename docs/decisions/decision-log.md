# Architecture Decision Records (ADR) & Decision Log

**Document Version:** 1.0  
**Date:** 22 November 2026  
**Status:** Approved Architectural Principles  

---

## ADR 1: Local-First FastAPI + SQLite + React Architecture
* **Context:** The application needs to run on Windows 11 for a single user, providing responsive UI, deep market visualizations, and continuous background polling with Telegram alerts.
* **Decision:** Build a local web-service architecture: FastAPI backend + SQLite database + React 18 / TypeScript frontend.
* **Alternatives Rejected:**
  - *PyQt6 / Tkinter Monolith:* Rigid UI, difficult to render custom skin cards and deep order-book graphs, risks UI freezing during polling loops.
  - *Cloud-Hosted SaaS:* Unnecessary ongoing server costs, violates local-first privacy, requires complex multi-tenant infrastructure.
* **Consequences:** Clean separation between background polling and UI; easily packageable via Windows `launch.bat`.

---

## ADR 2: Integer Minor Currency Units (Cents) Arithmetic
* **Context:** Financial pricing and Valve fee deductions are prone to catastrophic floating-point rounding errors (e.g. `0.1 + 0.2 = 0.30000000000000004`).
* **Decision:** All prices, fees, historical averages, and wallet balances must be stored and computed exclusively as **integer minor currency units** (cents for USD/EUR).
* **Consequences:** Complete mathematical determinism; matches Valve's internal integer-based fee accounting.

---

## ADR 3: Strictly Read-Only Operation & Zero Credential Storage
* **Context:** Managing Steam account passwords, Steam Guard shared secrets (`maFile`), or session cookies creates catastrophic security liabilities (account compromise, API scams, Valve Terms of Service bans).
* **Decision:** The application will **never** store passwords or cookies, and will **never** execute market transactions (no auto-buy, no auto-sell, no auto-trade). All actions are manual confirmations by the human user.
* **Consequences:** Zero risk of credential theft; completely terms-compliant; safe open-source distribution.

---

## ADR 4: Single-IP Rate Limiting & Global Backoff (No Proxy Rotation)
* **Context:** Steam enforces strict rate limits (HTTP 429). Naive scrapers use residential proxy rotation or CAPTCHA solvers, which violate Valve terms and trigger Cloudflare/Akamai IP blocks.
* **Decision:** Operate within a respectful single-IP rate budget: 10 to 15 requests per minute with randomized jitter. Trigger an immediate 300-second global pause upon receiving HTTP 429 or 403.
* **Alternatives Rejected:** Proxy rotation (violates project brief Rule #4).
* **Consequences:** Bulletproof stability, zero risk of residential proxy costs or bans, predictable network footprint.

---

## ADR 5: SCM-First Execution with External Markets as Reference Only
* **Context:** The user operates from Tunisia, where Stripe Connect and Adyen bank payouts are blocked, and BUFF163 enforces mainland Chinese national ID verification.
* **Decision:** Both BUY and SELL sniper strategies execute primarily on the Steam Community Market. External marketplaces (CSFloat, Skinport, Buff) are ingested purely as **pricing benchmarks** to validate whether an SCM deal is genuine.
* **Consequences:** Guaranteed operational viability for the user without international banking or KYC bottlenecks.

---

## ADR 6: Explicit Segregation of Steam Wallet Funds vs. Withdrawable Cash
* **Context:** Steam Wallet funds are closed-loop stored value under Valve's Subscriber Agreement and cannot be transferred to a bank account. Conflating wallet funds with cash misleads the user on profitability.
* **Decision:** The UI and data model must separate:
  - `net_profit_wallet` (Steam Wallet USD)
  - `net_cash_estimate` (Discounted external cash proceeds after external fees and liquidity haircut)
* **Consequences:** Transparent, realistic economic reporting.

---

## ADR 7: Top-Level Quantity vs. Cumulative Quantity Segregation
* **Context:** Steam order-book histograms aggregate all buy orders at or below a price tier into a single cumulative count (e.g. 211,731 orders), while the top-level bid may only have 1 item requested.
* **Decision:** The data model and UI must distinctly store and display `top_level_quantity` alongside `cumulative_quantity`.
* **Consequences:** Prevents false confidence in illiquid or single-unit spikes.

---

## ADR 8: Mandatory 7-Day Market Volatility Risk Buffer
* **Context:** Items purchased on SCM cannot be re-sold or traded for 7 days. During this period, the skin's market price can drop.
* **Decision:** The BUY engine's primary gate uses a **Conservative Exit Scenario** that applies a mandatory 5% price depreciation buffer on top of the 15% Valve fee schedule. An alert is only issued if the trade remains profitable under this conservative scenario.
* **Consequences:** Eliminates trades where thin profit margins are wiped out by ordinary weekly market noise.
