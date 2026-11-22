# Market Landscape & Secondary Marketplaces Research

**Document Version:** 1.0  
**Date of Research:** 22 November 2026  
**Status:** Verified via Official Docs, Marketplace Portals, Developer APIs, and Community Disclosures  

---

## 1. Executive Summary

This document evaluates the primary execution venue (**Steam Community Market**) and 14 secondary/third-party Counter-Strike 2 marketplaces, alongside 7 commercial analytics trackers. The evaluation specifically audits:
1. **API access & data granularity** (listing-level vs aggregate price overview)
2. **Fee structures & transaction overheads** (buyer fee, seller fee, cash-out fee)
3. **Liquidity & volume**
4. **Geographic feasibility for Tunisia** (registration, KYC, and cash withdrawal)
5. **Anti-bot terms and API compliance**

---

## 2. Marketplace Comparison Matrix

| Marketplace | Primary Model | API Access Model | Seller Fee | Buyer Fee | Cash-Out / Withdrawal | Tunisia Feasibility (Sell & Payout) | Liquidity & Volume Tier |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Steam Community Market** | Internal Custodial | Semi-public undocumented HTTP | 5% Steam + 10% CS2 (min $0.01 ea) | 0% | None (Steam Wallet closed loop) | **Supported** (Steam Wallet only, no cashout) | Tier 1 (Highest global volume) |
| **CSFloat** | Peer-to-Peer (P2P) | Public REST API + Developer Keys | 2.0% (flat) | 0% (card deposit +2.5-3.5%) | Stripe Connect / Crypto (USDC) / Bank Wire | **Heavily Constrained** (Stripe Connect local merchant unsupported in TN; crypto/wire subject to KYC) | Tier 1 (Dominant Western P2P) |
| **Skinport** | Bot-Custodial | Public API (Catalog/Prices only) | 12% standard (6% for >$1,000) | 0% | SEPA / Adyen Bank Wire | **Unsupported** (Adyen does not support Tunisian bank payouts; VPN strictly banned) | Tier 1 (High Western trust, bot-based) |
| **BUFF163** | P2P (Chinese Eco) | Private / Session-gated API | 2.5% | 0% | Alipay / WeChat Pay / Chinese Bank | **Blocked for Foreigners** (Strict mainland Chinese national ID KYC enforced mid-2024; reference only) | Tier 1 (Largest catalog, closed ecosystem) |
| **Youpin898** | P2P (Chinese Eco) | Private / App-only API | 2.5% - 3.5% | 0% | Alipay / Chinese Bank | **Blocked for Foreigners** (Mainland Chinese phone & ID verification required) | Tier 1 (Major Chinese domestic volume) |
| **DMarket** | P2P & Bot Hybrid | Public REST API + Signature Auth | ~5% to 7% | Variable | Crypto (USDT, BTC, LTC), Credit Card, Payoneer | **Conditionally Feasible via Crypto** (Sumsub KYC mandatory; crypto cash-out available) | Tier 2 (High Eastern Europe/Global volume) |
| **Waxpeer** | P2P | Public REST API + API Key | 6.0% (or 5% VIP) | 0% | Crypto (USDT, BTC, LTC, ETH) | **Feasible via Crypto** (P2P desktop app, crypto payouts allow Tunisian access) | Tier 2 (Popular automated P2P) |
| **market.csgo** (CSGO.com) | P2P | Public REST API + Secret Key | 5.0% | 0% | Crypto (USDT TRC20, BTC), WebMoney | **Feasible via Crypto** (Popular in CIS/Global, crypto withdrawal rails work in TN) | Tier 2 (Active P2P bot network) |
| **SkinBid** | P2P / Auction | Web endpoints (limited API) | 8.0% standard | 0% | Bank Wire (Trustly / Stripe) | **Unsupported** (Western European banking focus, no Tunisian local payout) | Tier 3 (Niche high-tier / auction) |
| **C5Game** | P2P (Chinese Eco) | Private API | 2.0% - 3.0% | 0% | Alipay / UnionPay | **Blocked for Foreigners** (Chinese domestic banking required) | Tier 3 (Secondary Chinese marketplace) |
| **LIS-SKINS** | Instant Cash-Out | Private / Partner API | Spread-based (15% - 35% cut) | N/A | Crypto (USDT, LTC), Payeer | **Feasible for Instant Sell** (High spread penalty, but crypto withdrawal works in TN) | Tier 2 (Instant buyer/liquidator) |
| **White.Market** | P2P | Public API | 2.0% - 4.0% | 0% | Crypto (USDT, ETH, BTC) | **Feasible via Crypto** (Supports crypto payouts without Western bank requirement) | Tier 3 (Moderate liquidity) |
| **Tradeit.gg** | Trade Bot / Cash | Limited Public API | High trade margin (~10-15%) | N/A | Crypto, Skin balance | **Feasible for Swap/Crypto** (Trade bot margin makes pure sniping harder) | Tier 2 (Trade-bot focused) |
| **SkinSwap** | Instant Cash-Out | Private API | Spread-based (20% - 30% cut) | N/A | Crypto (PayPal/Venmo western only) | **Feasible via Crypto** (Low yield due to steep instant-cash discount) | Tier 3 (Instant cashout site) |
| **BitSkins** | Bot-Custodial / P2P | REST API (v2 / v3) | 5.0% - 10.0% | 0% | Crypto (LTC, BTC, USDT), Bank Wire | **Feasible via Crypto** (Older platform, lower volume than CSFloat/Skinport) | Tier 3 (Declining liquidity) |

---

## 3. Deep-Dive on Tunisia Regional Feasibility

### 3.1 The Banking & Currency Dilemma in Tunisia
Traders in Tunisia face three distinct structural barriers when dealing with international skin trading sites:
1. **Exchange Control & Currency Restrictions:** The Tunisian Dinar (TND) is non-convertible. Standard international bank cards (Visa/Mastercard) issued by Tunisian banks cannot deposit or receive foreign currency without an authorized "Carte Technologique Internationale" (CTI) or a registered export business account.
2. **Payment Processor Redlining:**
   - **Stripe Connect:** Does not support Tunisia for merchant accounts. While Stripe has added Tunisia as a cross-border *recipient* for certain enterprise platforms, individual automated onboarding for P2P trading payouts fails standard address/IBAN checks.
   - **Adyen (Skinport's provider):** Explicitly excludes Tunisian domestic bank accounts from automated seller payouts.
   - **PayPal:** Outgoing and incoming commercial transactions are heavily restricted or non-functional for Tunisian residents.
3. **BUFF163 / Chinese Platforms Blockade:**
   - BUFF163 instituted strict Chinese national identity KYC in 2024. Non-Chinese accounts cannot put a store online or withdraw RMB.
   - Foreign accounts cannot deposit without Chinese Alipay/WeChat Pay.

### 3.2 Practical Routes for Tunisian Users
1. **Steam Community Market (SCM):**
   - Fully accessible from Tunisia (`steamCountry=TN`).
   - Wallet funds are closed-loop (USD or local currency). Cannot be directly converted to cash, but can be used to buy Steam games, Steam hardware (where shipping permits), or liquid CS2 items that are later transferred.
2. **Crypto-Enabled Marketplaces:**
   - Marketplaces offering **USDT (TRC-20 / ERC-20), Litecoin (LTC), or Bitcoin (BTC)** payouts (such as **DMarket, Waxpeer, market.csgo, White.Market, LIS-SKINS**) represent the only verified mechanism to cash out from Tunisia without a European/US bank account.
   - KYC: Almost all crypto-enabled marketplaces require passport/national ID verification via Sumsub or Veriff. Tunisian national identity cards and passports are accepted by Sumsub on DMarket and Waxpeer.

---

## 4. Competitor & Analytics Tracker Landscape

| Platform | URL | Nature | Pricing Model | Scraping / API Capabilities | Key Takeaways for CS2 Sniper |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Pricempire** | [pricempire.com](https://pricempire.com) | Cross-market aggregator | Freemium / Paid API tiers ($50-$500/mo) | Aggregates 20+ markets; delayed public data; enterprise websocket | Industry benchmark for cross-market comparisons; high pricing barrier |
| **CS2.sh** | [cs2.sh](https://cs2.sh) | SCM & CSFloat database | Free / Community | Historical prices, order book histograms, item name IDs | Excellent technical reference for `item_nameid` lookups and SCM behavior |
| **Skinstrack** | [skinstrack.com](https://skinstrack.com) | P2P & bot aggregator | Free / Ad-supported | Live deals feed, percentage discount vs SCM / Buff | Demonstrates deal ranking UI and false-discount filtering |
| **SteamAnalyst** | [steamanalyst.com](https://steamanalyst.com) | Portfolio & price history | Subscription / API | Historical 7-day, 30-day, and lifetime trend calculations | Standard reference for median pricing and outlier rejection |
| **CSFloat Market Checker** | [csfloat.com](https://csfloat.com) | Chrome extension & site | Free | Injects float, paint seed, stickers, and rank into SCM HTML | Proves how inspect links are resolved to float values client-side |
| **CS Armoury** | [csarmoury.com](https://csarmoury.com) | SCM analytics & deals | Free / Premium | Scrapes SCM listings; calculates fee-adjusted margins | Good reference for SCM-only resale calculations |
| **CSGAIN** | [csgain.com](https://csgain.com) | Arbitrage scanner | Subscription | Real-time cross-market arbitrage alerts | High latency vulnerability; alerts often beaten by bots |

---

## 5. Strategic Conclusion for the CS2 Sniper App

1. **Phase 1 Must Prioritize Steam Community Market:**
   Because SCM is 100% accessible in Tunisia without KYC or banking hurdles, both the **BUY SNIPER** (SCM underpriced listings) and **SELL SNIPER** (SCM high buy orders) must run on native Steam Market data.
2. **External Marketplaces as Reference Only:**
   CSFloat and Skinport public prices should initially be ingested as **valuation references** (fair value anchors) to verify that an SCM listing is genuinely discounted, rather than relying on external cashout execution.
3. **Clear Distinction Between Wallet and Cash:**
   The UI must never equate SCM Wallet profit ($100 Steam balance) with withdrawable cash. Cashout via crypto remains a secondary future option.
