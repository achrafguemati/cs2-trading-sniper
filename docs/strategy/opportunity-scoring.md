# Opportunity Scoring & Signal Confidence Framework

**Document Version:** 1.0  
**Date:** 22 November 2026  
**Status:** Quantitative Scoring Specification  

---

## 1. Executive Summary

To prevent the user from being flooded with low-quality alerts or misled by market anomalies, the application employs a transparent, explainable **Opportunity Scoring Framework**.

Every candidate signal is assigned:
1. An **Opportunity Score (0 to 100)** measuring commercial attractiveness (expected return, margin buffer).
2. A **Confidence Score (0 to 100)** measuring data freshness, liquidity, and execution probability.
3. Explicit **Status Tags & Risk Warnings** indicating why a signal may fail.

---

## 2. BUY Opportunity Scoring Algorithm

$$\text{Score}_{\text{buy}} = w_{\text{roi}} \cdot S_{\text{roi}} + w_{\text{liq}} \cdot S_{\text{liq}} + w_{\text{vol}} \cdot S_{\text{vol}} - \sum \text{Penalties}$$

### 2.1 Sub-Score Definitions

| Metric | Range | Weight ($w$) | Calculation Formula |
| :--- | :--- | :--- | :--- |
| **ROI Score ($S_{\text{roi}}$)** | 0 – 100 | 0.45 | Based on Conservative Net ROI: $\min(100, \text{ROI}_{\text{cons}} \times 4)$. If $\text{ROI}_{\text{cons}} < 5\%$, score is 0. |
| **Liquidity Score ($S_{\text{liq}}$)** | 0 – 100 | 0.35 | Based on 7-day sales volume: $\min(100, \text{sales}_{\text{7d}} \times 2) + \min(50, \text{buy\_orders}_{\text{count}} / 10)$. |
| **Price Stability ($S_{\text{vol}}$)** | 0 – 100 | 0.20 | $100 - (\text{standard\_deviation}_{\text{14d}} / P_{\text{baseline}} \times 100)$. Lower volatility = higher stability score. |

### 2.2 Deductions & Hard Rejections
* **Missing Wear/Variant Match:** **REJECT** (Score = 0).
* **7-Day Sales < 10:** Deduct 40 points (Illiquid market penalty).
* **Listing Age > 120s:** Deduct 25 points (Likely stale / already purchased).
* **Extreme Discount (>60% on liquid item):** Deduct 30 points (Likely manipulated baseline or deceptive item).

---

## 3. SELL Opportunity Scoring Algorithm

$$\text{Score}_{\text{sell}} = w_{\text{gain}} \cdot S_{\text{gain}} + w_{\text{depth}} \cdot S_{\text{depth}} + w_{\text{fresh}} \cdot S_{\text{fresh}} - \sum \text{GhostPenalties}$$

### 3.1 Sub-Score Definitions

| Metric | Range | Weight ($w$) | Calculation Formula |
| :--- | :--- | :--- | :--- |
| **Gain Score ($S_{\text{gain}}$)** | 0 – 100 | 0.50 | Multiple over baseline: $\min(100, (\text{Bid Multiple} - 1.0) \times 100)$. |
| **Depth Score ($S_{\text{depth}}$)** | 0 – 100 | 0.30 | Top-level quantity score: $100$ if $\text{top\_qty} \ge 5$, $70$ if $\text{top\_qty} \in [2, 4]$, $40$ if $\text{top\_qty} == 1$. |
| **Freshness Score ($S_{\text{fresh}}$)** | 0 – 100 | 0.20 | $100 - (\text{data\_age\_seconds} \times 2)$. 0 if age > 50s. |

### 3.2 Ghost & Crossed-Book Penalties
* **Crossed Book (`highest_bid >= lowest_ask`):** Deduct 50 points; tag as `Crossed Book`.
* **Bid > 2.5x Baseline with Top Qty == 1:** Deduct 40 points; tag as `Probable Ghost Order`.
* **Re-check Failed:** **REJECT** (Bid disappeared on second check).

---

## 4. Confidence Classification & UI Status Labels

Every alert rendered on the dashboard or dispatched to Telegram must bear one primary status tag:

```
[HIGH-CONFIDENCE SIGNAL]  -> Score >= 75 and Confidence >= 80 (Strong liquidity, fresh data, robust profit)
[CANDIDATE]               -> Score >= 50 and Confidence >= 60 (Standard viable opportunity)
[NEEDS VERIFICATION]      -> Spread wide, low top-level quantity, or sticker craft suspected
[CROSSED BOOK]            -> Highest bid >= lowest ask (Anomalous book state, likely ghost bid)
[STALE DATA]              -> Cache age > 120 seconds (Must revalidate before opening Steam)
[UNVERIFIED ORDER]        -> Second confirmation check pending or inconclusive
```
