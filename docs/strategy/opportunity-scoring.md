# Opportunity Scoring & Signal Confidence Framework

**Document Version:** 1.1  
**Date:** 22 November 2026  
**Status:** Quantitative Scoring Specification (Provisional Thresholds Pending Backtesting)  

---

## 1. Executive Summary

To prevent the user from being flooded with low-quality alerts or misled by market anomalies, the application employs an explainable, bounded **Opportunity Scoring & Confidence Framework**.

Every candidate signal is assigned:
1. An **Opportunity Score (0 to 100)** measuring financial return and margin buffer.
2. An independent **Confidence Score (0 to 100)** measuring data freshness, sample depth, and order-book consistency.
3. Explicit **Status Tags & Risk Warnings**.

**Important Methodological Note:** All weights, scoring multipliers, and thresholds specified in this document are **provisional hypotheses**. They must be backtested against empirical datasets (`Asalio123/cs2-skins-daily-dataset`) and calibrated during Milestone 6 and 7 before production alerting.

---

## 2. BUY Opportunity Scoring Algorithm

$$\text{RawScore}_{\text{buy}} = w_{\text{roi}} \cdot S_{\text{roi}} + w_{\text{liq}} \cdot S_{\text{liq}} + w_{\text{vol}} \cdot S_{\text{vol}} - \sum \text{Penalties}$$
$$\text{Score}_{\text{buy}} = \max(0, \, \min(100, \, \text{RawScore}_{\text{buy}}))$$

### 2.1 Sub-Score Definitions (Strictly Clamped 0–100)

| Metric | Range | Weight ($w$) | Calculation Formula |
| :--- | :--- | :--- | :--- |
| **ROI Score ($S_{\text{roi}}$)** | 0 – 100 | 0.45 | Based on Conservative Net ROI ($\text{ROI}_{\text{cons}}$): $\max(0, \min(100, \lfloor \text{ROI}_{\text{cons}} \times 4 \rfloor))$. If $\text{ROI}_{\text{cons}} < 5\%$, $S_{\text{roi}} = 0$. |
| **Liquidity Score ($S_{\text{liq}}$)** | 0 – 100 | 0.35 | $\min\left(100, \, \min(60, \text{sales}_{\text{7d}} \times 2) + \min\left(40, \, \lfloor \text{buy\_orders}_{\text{count}} / 5 \rfloor\right)\right)$. Maximum output is mathematically capped at 100. |
| **Price Stability ($S_{\text{vol}}$)** | 0 – 100 | 0.20 | $\max\left(0, \, \min\left(100, \, 100 - \lfloor (\text{std\_dev}_{\text{14d}} / P_{\text{baseline}}) \times 100 \rfloor\right)\right)$. Lower volatility = higher stability score. |

### 2.2 Hard Rejections & Score Deductions
* **Missing Wear/Variant Match:** **HARD REJECT** (Score = 0).
* **Missing or Insufficient Baseline (< 7 daily data points):** **HARD REJECT** (Score = 0, Alert suppressed).
* **7-Day Sales Volume < 10:** Deduct 40 points (Illiquid market penalty).
* **Listing Age > 120s:** Deduct 25 points (Stale listing penalty).
* **Extreme Discount (>60% on liquid item):** Deduct 30 points (Likely baseline anomaly or deceptive item).

---

## 3. SELL Opportunity Scoring Algorithm

$$\text{RawScore}_{\text{sell}} = w_{\text{gain}} \cdot S_{\text{gain}} + w_{\text{depth}} \cdot S_{\text{depth}} + w_{\text{fresh}} \cdot S_{\text{fresh}} - \sum \text{Penalties}$$
$$\text{Score}_{\text{sell}} = \max(0, \, \min(100, \, \text{RawScore}_{\text{sell}}))$$

### 3.1 Sub-Score Definitions (Strictly Clamped 0–100)

| Metric | Range | Weight ($w$) | Calculation Formula |
| :--- | :--- | :--- | :--- |
| **Gain Score ($S_{\text{gain}}$)** | 0 – 100 | 0.50 | Multiple over baseline: $\max(0, \min(100, \lfloor (\text{Bid Multiple} - 1.0) \times 100 \rfloor))$. |
| **Depth Score ($S_{\text{depth}}$)** | 0 – 100 | 0.30 | Top-level quantity score: $100$ if $\text{top\_qty} \ge 5$; $70$ if $\text{top\_qty} \in [2, 4]$; $35$ if $\text{top\_qty} == 1$. |
| **Freshness Score ($S_{\text{fresh}}$)** | 0 – 100 | 0.20 | $\max(0, \, 100 - (\text{data\_age\_seconds} \times 2))$. Output is 0 if age $\ge$ 50s. |

### 3.2 Penalties & Inconsistent Snapshot Handling
* **Inconsistent / Crossed Snapshot (`highest_bid >= lowest_ask`):** Deduct 50 points; tag as `Inconsistent Snapshot / Crossed Book`.
* **Bid > 2.5x Baseline with Top Qty == 1:** Deduct 35 points; tag as `Unverified High Bid`.
* **3-Second Re-check Failed:** **HARD REJECT** (Bid vanished on persistence check).

---

## 4. Signal Confidence Score Formula

The **Confidence Score (0 to 100)** measures whether the underlying data is fresh, robust, and consistent enough to justify trusting the opportunity:

$$\text{Confidence} = \max\left(0, \, \min\left(100, \, 0.40 \cdot C_{\text{fresh}} + 0.35 \cdot C_{\text{samples}} + 0.25 \cdot C_{\text{spread}} - \sum \text{RiskPenalties}\right)\right)$$

### 4.1 Confidence Components
1. **Freshness Component ($C_{\text{fresh}}$):**
   $$C_{\text{fresh}} = \max(0, \, 100 - (\text{age\_seconds} \times 2))$$
2. **Sample Size Depth ($C_{\text{samples}}$):**
   $$C_{\text{samples}} = \min(100, \, \text{verified\_daily\_history\_count} \times 10)$$
   *(Requires at least 10 historical daily points to reach full confidence).*
3. **Spread Consistency ($C_{\text{spread}}$):**
   $$C_{\text{spread}} = \max\left(0, \, 100 - \left\lfloor \frac{\text{lowest\_ask} - \text{highest\_bid}}{\text{lowest\_ask}} \times 100 \right\rfloor\right)$$
   *(Normal tight spread = high consistency; extreme divergence or inverted spread = low score).*

### 4.2 Confidence Penalties
* **Crossed Book Snapshot:** Deduct 60 points (automatically caps confidence $< 40$).
* **Baseline < 7 days of data:** Deduct 50 points.
* **Cache Age > 120 seconds:** Deduct 35 points.

---

## 5. UI Status Labels & Action Thresholds

```
[HIGH-CONFIDENCE SIGNAL]  -> Score >= 75 AND Confidence >= 80 (Robust liquidity, fresh data, positive net profit)
[CANDIDATE]               -> Score >= 50 AND Confidence >= 60 (Standard viable candidate; manual check advised)
[NEEDS VERIFICATION]      -> Low top-level quantity, wider spread, or craft detected
[INCONSISTENT SNAPSHOT]   -> Highest bid >= lowest ask (Snapshot desynchronized or tag mismatch; do not list)
[STALE DATA]              -> Cache age > 120 seconds (Must revalidate before opening Steam)
[UNVERIFIED HIGH BID]     -> Bid significantly above baseline with top-level quantity = 1
[DATA INSUFFICIENT]       -> Missing historical baseline; profit alerts strictly suppressed
```
