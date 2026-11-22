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
| **Steam Community Market** | Internal Custodial | Semi-public undocumented HTTP | 5% Steam + 10% CS2 (min $0.01 ea) | 0% | None (Steam Wallet closed loop) | **VERIFIED SUPPORTED** (Steam Wallet only; non-withdrawable virtual balance) | Tier 1 (Highest global volume) |
| **CSFloat** | Peer-to-Peer (P2P) | Public REST API + Developer Keys | 2.0% (flat) | 0% (card deposit +2.5-3.5%) | Stripe Connect / Crypto (USDC) / Bank Wire | **RESTRICTED / UNKNOWN** (Stripe Connect local merchant onboarding is unsupported in TN; cross-border recipient wire eligibility unverified; crypto cash-out subject to strict Sumsub KYC) | Tier 1 (Dominant Western P2P) |
| **Skinport** | Bot-Custodial | Public API (Catalog/Prices only) | 12% standard (6% for >$1,000) | 0% | SEPA / Adyen Bank Wire | **VERIFIED UNSUPPORTED** (Adyen does not support Tunisian domestic bank accounts for payouts; VPN strictly prohibited by terms) | Tier 1 (High Western trust, bot-based) |
| **BUFF163** | P2P (Chinese Eco) | Private / Session-gated API | 2.5% | 0% | Alipay / WeChat Pay / Chinese Bank | **VERIFIED BLOCKED FOR FOREIGNERS** (Strict mainland Chinese national ID + domestic bank KYC enforced; reference/pricing benchmark only) | Tier 1 (Largest catalog, closed ecosystem) |
| **Youpin898** | P2P (Chinese Eco) | Private / App-only API | 2.5% - 3.5% | 0% | Alipay / Chinese Bank | **VERIFIED BLOCKED FOR FOREIGNERS** (Mainland Chinese phone & citizen ID required for seller onboarding) | Tier 1 (Major Chinese domestic volume) |
| **DMarket** | P2P & Bot Hybrid | Public REST API + Signature Auth | ~5% to 7% | Variable | Crypto (USDT, BTC, LTC), Credit Card, Payoneer | **CONDITIONALLY VERIFIED VIA CRYPTO** (Sumsub KYC accepts Tunisian passport/national ID; crypto cashout functional, but charges 3-5% payment/network fee) | Tier 2 (High Eastern Europe/Global volume) |
| **Waxpeer** | P2P | Public REST API + API Key | 6.0% (or 5% VIP) | 0% | Crypto (USDT TRC20, BTC, LTC, ETH) | **CONDITIONALLY VERIFIED VIA CRYPTO** (P2P automated trading, crypto payouts allow Tunisian access without local banking rail) | Tier 2 (Popular automated P2P) |
| **market.csgo** (CSGO.com) | P2P | Public REST API + Secret Key | 5.0% | 0% | Crypto (USDT TRC20, BTC), WebMoney | **CONDITIONALLY VERIFIED VIA CRYPTO** (Crypto withdrawal functional for international accounts; requires bot extension) | Tier 2 (Active P2P bot network) |
| **SkinBid** | P2P / Auction | Web endpoints (limited API) | 8.0% standard | 0% | Bank Wire (Trustly / Stripe) | **VERIFIED UNSUPPORTED** (Restricted to EU/EEA/UK banking; no Tunisian payout route) | Tier 3 (Niche high-tier / auction) |
| **C5Game** | P2P (Chinese Eco) | Private API | 2.0% - 3.0% | 0% | Alipay / UnionPay | **VERIFIED BLOCKED FOR FOREIGNERS** (Chinese domestic banking required) | Tier 3 (Secondary Chinese marketplace) |
| **LIS-SKINS** | Instant Cash-Out | Private / Partner API | Spread-based (15% - 35% cut) | N/A | Crypto (USDT, LTC), Payeer | **CONDITIONALLY VERIFIED VIA CRYPTO** (High spread penalty, but crypto payout works internationally) | Tier 2 (Instant buyer/liquidator) |
| **White.Market** | P2P | Public API | 2.0% - 4.0% | 0% | Crypto (USDT, ETH, BTC) | **CONDITIONALLY VERIFIED VIA CRYPTO** (Crypto payout supported without Western bank requirement) | Tier 3 (Moderate liquidity) |
| **Tradeit.gg** | Trade Bot / Cash | Limited Public API | High trade margin (~10-15%) | N/A | Crypto, Skin balance | **CONDITIONALLY VERIFIED VIA CRYPTO** (High spread/margin penalty) | Tier 2 (Trade-bot focused) |
| **SkinSwap** | Instant Cash-Out | Private API | Spread-based (20% - 30% cut) | N/A | Crypto (PayPal/Venmo western only) | **CONDITIONALLY VERIFIED VIA CRYPTO** (Steep instant-cash haircut) | Tier 3 (Instant cashout site) |
| **BitSkins** | Bot-Custodial / P2P | REST API (v2 / v3) | 5.0% - 10.0% | 0% | Crypto (LTC, BTC, USDT), Bank Wire | **CONDITIONALLY VERIFIED VIA CRYPTO** (Crypto payouts functional, but liquidity significantly declining) | Tier 3 (Declining liquidity) |

---

## 3. Deep-Dive on Tunisia Regional Feasibility

### 3.1 The Banking & Currency Dilemma in Tunisia
Traders operating from Tunisia face specific structural constraints:
1. **Exchange Control & Currency Restrictions:** The Tunisian Dinar (TND) is non-convertible. Standard local bank cards cannot receive or transfer international currency without an authorized CTI or approved corporate export account.
2. **Third-Party Payment Processor Coverage:**
   - **Adyen (Skinport):** Official documentation (<https://skinport.com/faq>) restricts payouts to specific bank accounts in supported regions (primarily EEA, UK, US, Canada, Australia). Tunisia is not supported for bank payouts. Skinport terms strictly ban VPN or false location data.
   - **Stripe Connect (CSFloat):** CSFloat historical country documentation listed Tunisia, but modern Stripe Connect merchant accounts are restricted in Tunisia. The exact payout eligibility for Tunisian residents via CSFloat wire or crypto is **UNKNOWN / UNVERIFIED** without official live confirmation.
   - **BUFF163 & Youpin:** Official terms require a mainland Chinese citizen identity card and domestic payment systems (Alipay/WeChat). International non-Chinese accounts cannot sell or withdraw.
   - **Crypto Cashout Marketplaces (DMarket, Waxpeer):** Marketplaces supporting cryptocurrency withdrawals (USDT, LTC, BTC) allow international users to withdraw without local Western bank rails. Sumsub KYC verification accepts Tunisian passports. However, withdrawal processing fees (3%–5%) apply.
3. **Strict Policy on Compliance:**
   - **Never recommend VPNs, proxy residential data, or false foreign identity documents.**
   - If a marketplace's payout route for Tunisia cannot be established via official documentation, it is marked as `UNKNOWN` or `RESTRICTED`.

### 3.2 Practical Execution Strategy for the App
1. **Primary Strategy: Steam Community Market Internal Operations:**
   - Both BUY and SELL engines execute on SCM.
   - SCM is verified 100% accessible in Tunisia (`steamCountry=TN`).
   - Wallet funds are closed-loop virtual stored value.
2. **External Marketplaces as Valuation Benchmarks:**
   - Use CSFloat, Skinport, and Buff public catalog prices strictly as **unbiased valuation anchors** to check whether an SCM price anomaly is real.
   - No external marketplace selling or automated withdrawal is required or attempted.

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
