# Official Steam Community Market Rules, Matching Semantics & Fee Math

**Document Version:** 1.1  
**Verification Date:** 22 November 2026  
**Primary Authorities:** Valve Corporation Steam Community Market FAQ (<https://help.steampowered.com/en/faqs/view/61F0-72B7-9A18-C70B>), Steam Economy Client Scripts (`economy.js`), Steamworks Documentation  

---

## 1. Executive Summary

This document defines the verified economic rules and matching mechanics governing the Steam Community Market (SCM) for Counter-Strike 2 items. It provides:
1. Exact integer-arithmetic fee formulas (avoiding floating-point math).
2. The integer boundary search function for backward fee inversion, reporting exact reachability.
3. Official buy-order matching behavior, pricing rules, and FIFO priority semantics based directly on Valve's official FAQ.
4. Exact break-even calculation rules (avoiding approximate multipliers).
5. 7-day re-market restriction vs player Trade Protection distinction.
6. Steam Wallet virtual balance boundaries.

---

## 2. Official Fee Schedule & Integer Arithmetic Math

### 2.1 The Two-Component Fee Structure
Every Counter-Strike 2 transaction on the Steam Community Market incurs two distinct fees:
1. **Steam Transaction Fee:** 5% of seller proceeds, minimum **1 minor unit** ($0.01 USD).
2. **Counter-Strike 2 Publisher Fee:** 10% of seller proceeds, minimum **1 minor unit** ($0.01 USD).

Nominal combined fee rate is 15%. However, because Valve applies mathematical flooring independently to each component using integer math and enforces a 1-cent floor on each, the effective percentage varies substantially across price tiers.

### 2.2 Forward Formula (Seller Receives $\rightarrow$ Buyer Pays)
Valve calculates market prices from the seller's desired net proceeds upwards.

Given $R$ (integer cents received by the seller):
$$\text{steam\_fee} = \max\left(1, \, \lfloor (R \times 5) / 100 \rfloor\right)$$
$$\text{publisher\_fee} = \max\left(1, \, \lfloor (R \times 10) / 100 \rfloor\right)$$
$$B = R + \text{steam\_fee} + \text{publisher\_fee}$$

Where $B$ is the gross price in integer cents paid by the buyer. Notice that this formula uses purely integer operations (`// 100` and `*`), eliminating floating-point imprecision.

#### Boundary Examples:
* **Minimum Item Price ($0.03 USD):**
  - Seller Receives: $R = 1\text{ cent}$
  - Steam Fee: $\max(1, (1 \times 5) // 100) = 1\text{ cent}$
  - CS2 Fee: $\max(1, (1 \times 10) // 100) = 1\text{ cent}$
  - Buyer Pays: $1 + 1 + 1 = 3\text{ cents}$ ($0.03)
  - Effective Fee Rate: $2/3 = \mathbf{66.67\%}$
* **$1.00 USD Buyer Pays:**
  - If Seller Receives $R = 88\text{ cents}$:
    - Steam Fee: $\max(1, (88 \times 5) // 100) = 4\text{ cents}$
    - CS2 Fee: $\max(1, (88 \times 10) // 100) = 8\text{ cents}$
    - Buyer Pays: $88 + 4 + 8 = 100\text{ cents}$ ($1.00)
    - Effective Fee Rate: $12/100 = \mathbf{12.00\%}$

### 2.3 Backward Inversion (Buyer Pays $\rightarrow$ Seller Receives)
Due to independent flooring, not every integer buyer total is exactly reachable by a seller proceeds integer (step jumps occur, e.g., 114 cents is not reachable because $R=99 \rightarrow B=113$ and $R=100 \rightarrow B=115$).

The inverse fee function must never return a seller amount and claim it produces the requested buyer total if the forward formula produces a different total. It must explicitly return:
1. `seller_receives_cents`: The largest integer seller proceeds whose resulting buyer cost does not exceed the target.
2. `steam_fee_cents`: Resulting Steam fee.
3. `publisher_fee_cents`: Resulting publisher fee.
4. `actual_buyer_pays_cents`: The exact buyer total produced by the forward formula.
5. `is_exact_match`: Boolean indicating whether `actual_buyer_pays_cents == requested_buyer_pays_cents`.

```python
def calculate_seller_receives(requested_buyer_pays_cents: int) -> dict:
    """
    Given what the buyer pays (gross in integer cents), returns an exact breakdown:
    {
        'seller_receives': int,
        'steam_fee': int,
        'publisher_fee': int,
        'actual_buyer_pays': int,
        'is_exact_match': bool
    }
    """
    if requested_buyer_pays_cents < 3:
        raise ValueError("Minimum Steam market price is 3 cents")
    
    # Estimate base seller amount using integer division
    # 1.15 approx divisor: (requested * 100) // 115
    estimated_r = (requested_buyer_pays_cents * 100) // 115
    
    # Search locally within +/- 3 cents of estimate
    best_r = 1
    best_buyer = 3
    best_steam = 1
    best_pub = 1
    
    for r in range(max(1, estimated_r - 3), estimated_r + 4):
        steam_fee = max(1, (r * 5) // 100)
        pub_fee = max(1, (r * 10) // 100)
        buyer_pays = r + steam_fee + pub_fee
        
        if buyer_pays == requested_buyer_pays_cents:
            return {
                "seller_receives": r,
                "steam_fee": steam_fee,
                "publisher_fee": pub_fee,
                "actual_buyer_pays": buyer_pays,
                "is_exact_match": True
            }
        elif buyer_pays < requested_buyer_pays_cents:
            if buyer_pays > best_buyer:
                best_r = r
                best_buyer = buyer_pays
                best_steam = steam_fee
                best_pub = pub_fee
                
    return {
        "seller_receives": best_r,
        "steam_fee": best_steam,
        "publisher_fee": best_pub,
        "actual_buyer_pays": best_buyer,
        "is_exact_match": False
    }
```

### 2.4 Exact Break-Even Calculation
Never approximate break-even sale price with `cost * 1.15`.
The exact break-even gross listing price $B_{\text{breakeven}}$ is the smallest integer buyer price whose resulting seller proceeds equal or exceed the acquisition cost $C_{\text{acq}}$:

$$B_{\text{breakeven}} = C_{\text{acq}} + \max\left(1, \, (C_{\text{acq}} \times 5) // 100\right) + \max\left(1, \, (C_{\text{acq}} \times 10) // 100\right)$$

---

## 3. Order Book Matching Semantics & Priority (Official Valve Rules)

Source: Valve Community Market FAQ (<https://help.steampowered.com/en/faqs/view/61F0-72B7-9A18-C70B>)

### 3.1 Buy Order Definition
* A buy order is the **maximum amount** the buyer is willing to spend for an item.
* **Price Execution Rule:** If an eligible item is listed for **less** than the buy order price, **the buyer pays the lower price** (the listing price). The buyer is refunded the difference into their Steam Wallet.
* **Seller Proceeds Rule:** A seller who lists below the highest buy order **does NOT automatically receive the highest buy order price**. The transaction matches at the seller's listing price, meaning the seller receives the proceeds corresponding to their listed price, and the buyer gets the discount.

### 3.2 Matching Priority: Oldest Matching Buy Order
* When a new sell listing is posted that could fulfill multiple active buy orders, Valve's matching engine selects the **oldest matching buy order** (the buy order that was placed first among all orders whose bid is $\ge$ the listing price).
* Price priority: Higher buy orders take precedence. Among buy orders at the same price, FIFO (oldest timestamp) determines execution order.

### 3.3 Buy Order Cancellation & Wallet Funds
* Valve's official documentation states: **"Orders are automatically cancelled if your wallet balance falls below the amount needed to fulfill the buy order."**
* Over-allocated wallet bids: While users can place multiple buy orders that exceed their balance, Valve's system cancels active orders when a wallet cannot fulfill them upon execution or during periodic balance verification.
* Therefore, the application **must not assert** that visible orders are definitively unfunded or "ghosts" based on speculation. Label any unverified anomalous bid as `Unverified High Bid` rather than `Probable Ghost Order`.

### 3.4 Inconsistent & Crossed Snapshots (`highest_bid >= lowest_ask`)
* On SCM, if `highest_bid >= lowest_ask`, this is an **inconsistent snapshot** resulting from:
  - Cache/CDN desynchronization between `/market/listings/.../render/` and `/market/itemordershistogram`.
  - Exterior tag or wear category mismatches.
* It is **not proof of an unfunded order**. The engine must pause, re-query, and flag as `Inconsistent Snapshot / Crossed Book`.

---

## 4. Market Restrictions: 7-Day Re-List vs Trade Protection

| Dimension | 7-Day Market Re-List Restriction | Trade Protection (Reversal Feature) |
| :--- | :--- | :--- |
| **Trigger Event** | Purchasing an item on the Steam Community Market. | Receiving an item via a player-to-player Steam Trade. |
| **Applicable Items** | CS2 items acquired via SCM. | All CS2 traded items. |
| **Visual Indicator** | Red text in item description: "Cannot be traded or listed on Community Market until [Date]". | Yellow shield icon on item tile in inventory. |
| **Marketability** | **Unmarketable** on SCM for 7 days (`marketable: 0`). | **Unmarketable** on SCM and untradable for 7 days. |
| **Reversibility** | **Irreversible:** SCM purchases are final clearinghouse transactions. | **Reversible:** The sender can reverse all trades from the past 7 days if their account was compromised. |
| **Holding Volatility Risk** | **High:** The buyer must hold the asset through 7 days of price fluctuation before reselling. | **High + Chargeback Risk:** P2P marketplace buyers risk the seller reversing the trade. |

---

## 5. Steam Wallet Financial Realities

1. **Closed-Loop Virtual Stored Value:**
   - Steam Wallet funds cannot be withdrawn, wired, transferred to PayPal, or cashed out to any bank account under Valve’s Steam Subscriber Agreement.
   - Any "cash-out" requires purchasing liquid skins, enduring a 7-day restriction, transferring them to an external marketplace, and paying secondary marketplace seller fees (2%–12%) plus payment withdrawal fees (1%–5%).
2. **Strict Currency Segregation in Reporting:**
   - All app calculations must report:
     - **Steam Wallet Net Profit:** e.g. `+12.45 USD (Steam Wallet)`.
     - **Estimated External Cash Net:** e.g. `+7.80 USD (Est. Cashout after external liquidity haircut)`.
   - Never combine or confuse the two.
