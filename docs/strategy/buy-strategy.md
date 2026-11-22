# Strategy Design: Mode A — BUY SNIPER

**Document Version:** 1.0  
**Date:** 22 November 2026  
**Status:** Strategy Specification (No Auto-Execution)  

---

## 1. Strategy Objective

The **BUY SNIPER** engine continuously scans monitored Counter-Strike 2 skin watchlists on the Steam Community Market (SCM) to identify genuinely underpriced individual sell listings. It evaluates the exact skin variant, condition, float, applied stickers, historical liquidity, and seven-day holding volatility risk.

When a verified profitable candidate is detected, the engine generates an actionable alert (via Web UI and Telegram) so the user can manually inspect and purchase the skin on Steam.

**Critical Constraint:** The engine is strictly read-only and will **never** attempt automated purchasing or wallet deductions.

---

## 2. Ingestion & Identity Resolution

### 2.1 Supported URL Ingestion
The engine accepts two input formats from the user:
1. **Canonical URLs:** `https://steamcommunity.com/market/listings/730/AK-47%20%7C%20Redline%20%28Field-Tested%29`
2. **Grouped URLs (May 2026 Redesign):** `https://steamcommunity.com/market/listings/730/G1807209A023004?category_Exterior=WearCategory2&appid=730`

### 2.2 Canonical Identity Resolution Pipeline
1. Parse URL parameters: extract `appid` (must be `730`), group/hash name token, and query filters (`category_Exterior`, `Quality`).
2. Map `category_Exterior` to wear categories:
   - `WearCategory0` $\rightarrow$ Factory New (FN)
   - `WearCategory1` $\rightarrow$ Minimal Wear (MW)
   - `WearCategory2` $\rightarrow$ Field-Tested (FT)
   - `WearCategory3` $\rightarrow$ Well-Worn (WW)
   - `WearCategory4` $\rightarrow$ Battle-Scarred (BS)
3. If grouped `G...` token is provided, query SCM listing SSR hydration payload or local SQLite `item_name_mapping` database to resolve the exact canonical `market_hash_name` and internal `item_nameid`.
4. Output a verified `CanonicalItemIdentity`:
   - `base_name`: e.g., "AK-47 | Redline"
   - `exterior`: e.g., "Field-Tested"
   - `is_stattrak`: False
   - `is_souvenir`: False
   - `market_hash_name`: "AK-47 | Redline (Field-Tested)"
   - `item_nameid`: e.g., 176000000

---

## 3. Data Retrieval & Granularity

1. **Avoid Naive Price Overview:** The `/market/priceoverview/` route returns only the lowest price across all listings without listing IDs, asset IDs, or inspect links. The BUY engine rejects aggregate-only data for sniping.
2. **Retrieve Individual Listings:**
   - Query `/market/listings/730/{market_hash_name}/render/?query=&start=0&count=20&currency=1&format=json`.
   - Parse `listinginfo` dictionary: extract `listingid`, `price` (cents), `fee` (cents), and asset link.
   - Parse `assets` dictionary: extract `assetid`, inspect action preview link (`market_actions`), and description tags (applied stickers/patches).

---

## 4. Valuation, Baselines & Premium Evaluation

### 4.1 Establishing a Robust Price Baseline
To avoid fake discounts caused by sudden market crashes, illiquidity, or manipulated spikes:
1. **Defined Data Sources for Historical Baselines:**
   - **Primary Source:** Steam's embedded listing page price history array (historical daily median sale price and volume tuples: `[timestamp, median_price, volume]`).
   - **Secondary / Local Source:** Locally accumulated observations from previous polling cycles stored in the local SQLite `market_observations` table.
   - **Important Constraint:** Steam does **not** expose individual completed transactions or trade logs through an unauthenticated endpoint. Baselines are computed from aggregated daily median price points and volume.
2. **Minimum Data Requirements & Missing-Data Suppression:**
   - A valid baseline requires at least **7 daily median observations** within the last 14 days and cumulative volume $\ge 10$ units.
   - **Suppression Rule:** If historical data is missing, stale, or below the minimum volume threshold, **profit alerts are strictly suppressed**. The UI labels the item as `[BASELINE DATA INSUFFICIENT]` rather than inventing a baseline.
3. **Reference Price Calculation:**
   - Baseline $P_{\text{baseline}}$: Volume-Weighted Median (VWM) of verified daily median points.
   - Current Floor $A_{\text{floor}}$: Median of the 3 lowest active eligible listings.
   - Reference Price: $P_{\text{ref}} = \min(P_{\text{baseline}}, A_{\text{floor}})$.

### 4.2 Handling Sticker and Float Premiums
* **Conservative Rule:** Never treat sticker or float premiums as guaranteed cash exit value.
* **Low Floats:**
  - A 0.150x Field-Tested Redline has higher desirability than a 0.35x FT, but base economics must assume standard FT exit. Low float adds a **quality score bonus**, not an inflated exit price guarantee.
* **Applied Stickers:**
  - Standard stickers (<$10 value) add **0%** to exit valuation.
  - High-tier stickers (Katowice 2014, Crown Foil) are flagged for human review with a `Sticker Craft Detected` tag, while financial formulas default to conservative baseline exit.

---

## 5. Economic & Net Profit Calculations

All financial calculations operate strictly in **integer cents** ($USD \times 100$).

### 5.1 Financial Definitions
* $C_{\text{acq}}$: Total Buyer Acquisition Cost in cents (`listing_price + listing_fee`).
* $E_{\text{gross}}$: Target Future Sale Price paid by next buyer in cents.
* $E_{\text{net}}$: Net Seller Proceeds received in Steam Wallet after Steam (5%) and CS2 (10%) fees.
  $$E_{\text{net}} = \text{calculate\_seller\_receives}(E_{\text{gross}})$$
* $P_{\text{wallet}}$: Net Steam Wallet Profit in cents:
  $$P_{\text{wallet}} = E_{\text{net}} - C_{\text{acq}}$$
* $\text{ROI}_{\text{net}}$: Net Return on Investment:
  $$\text{ROI}_{\text{net}} = \frac{P_{\text{wallet}}}{C_{\text{acq}}} \times 100\%$$

### 5.2 Scenario Modeling
To account for 7-day holding risk and market volatility:
1. **Conservative Exit (Primary Gate):**
   - Assumes market drops 5% over 7 days.
   - $E_{\text{gross, cons}} = \lfloor P_{\text{ref}} \times 0.95 \rfloor$.
   - **Candidate Threshold:** $P_{\text{wallet, cons}} > 0$ and $\text{ROI}_{\text{net, cons}} \ge 5\%$.
2. **Base Exit:**
   - Assumes market holds steady at $P_{\text{ref}}$.
   - $E_{\text{gross, base}} = P_{\text{ref}}$.
3. **Optimistic Exit:**
   - Assumes premium exit (e.g. selling at 2nd or 3rd lowest listing).

---

## 6. Protection Against Common Traps

| Trap | Failure Mode | Engine Defense Mechanism |
| :--- | :--- | :--- |
| **Thin Liquidity** | Skin has high nominal price but sells once a month; funds get locked. | Require $\ge 15$ sales in last 7 days; active buy order volume $\ge 50$. |
| **Manipulated Spike** | Low-tier skin pumped by wash sales; appears to have 70% discount. | Cap $P_{\text{ref}}$ against 30-day moving average. Reject if 7-day median > $2 \times$ 30-day median. |
| **Stale Listing** | Listing already bought by a bot seconds ago. | Check listing age; discard if seen > 180s ago without revalidation. |
| **Mismatched Variant** | User adds FT link, but scraper matches BS listing. | Strict equality check on `market_hash_name` and `classid`. |
| **7-Day Price Depreciation** | Skin bought at 15% discount loses 15% to fees and 5% to price drop. | Strict Conservative Exit scenario mandate; alerts require positive net margin after full 15% Valve fees + 5% volatility buffer. |

---

## 7. Actionable Alert Output

When a listing passes all gates, the alert contains:
* `[BUY CANDIDATE]` status label (not a guarantee).
* Canonical Item Name & Wear.
* Listing Price ($C_{\text{acq}}$) & Reference Price ($P_{\text{ref}}$).
* Discount % vs. Current Lowest Ask & Historical Median.
* Conservative Net Wallet Profit ($) and Net ROI (%).
* Applied Stickers / Float details if parsed.
* Direct Steam Market purchase link (`https://steamcommunity.com/market/listings/730/...`).
* Clear prompt: "Open on Steam, manually verify sticker/float, and confirm purchase."

---

## 8. Seven-Day Holding Portfolio & Price Tracking Engine

### 8.1 Ingesting Authoritative Steam Restriction Metadata
When the user confirms a purchase and adds it to their portfolio:
1. The app queries the public inventory endpoint (`/inventory/{steamid64}/730/2`).
2. Extracts the exact asset matching the purchased skin.
3. Reads the authoritative Steam item attributes:
   - `marketable`: `0` (restricted) or `1` (tradable/marketable).
   - `market_marketable_restriction`: Integer days (e.g., `7`).
   - `descriptions`: Scrapes the red restriction text for the exact unlock timestamp (e.g., `After Nov 29, 2026 (7:00:00) GMT`).
4. **Never hardcodes `acquired_at + 7 days` blindly**: Uses the authoritative Steam unlock date stored in UTC.

### 8.2 Daily Holding Valuation & Volatility Monitoring
While the asset is locked in the 7-day holding period:
* **Cost Basis:** $C_{\text{acq}}$ (integer cents paid).
* **Break-Even Sale Price:** The gross buyer-pays price required to recover cost basis:
  $$B_{\text{breakeven}} = C_{\text{acq}} + \max(1, \lfloor C_{\text{acq}} \times 0.05 \rfloor) + \max(1, \lfloor C_{\text{acq}} \times 0.10 \rfloor)$$
* **Current Estimated Net Exit:** Real-time net proceeds if sold at current market floor $A_{\text{floor}}$:
  $$E_{\text{net, current}} = \text{calculate\_seller\_receives}(A_{\text{floor}})$$
* **Unrealized P&L:**
  $$\Delta P = E_{\text{net, current}} - C_{\text{acq}}$$
* **Price Drop Alert:** If $A_{\text{floor}} < B_{\text{breakeven}}$, the dashboard triggers a warning banner: `Under Water: Floor dropped below break-even by $X.XX`.

### 8.3 Resale Readiness Alert
When the unlock timestamp arrives and `marketable == 1`:
* The engine runs a fresh scan of the current order book and sell listings.
* If $E_{\text{net, current}} > C_{\text{acq}}$, it fires a **`[RESALE READY]`** Telegram alert:
  - Item name, wear, and days held.
  - Recommended listing price (buyer pays).
  - Target net seller proceeds.
  - Expected realized profit ($) and ROI (%).
  - Direct link to inventory listing modal.

