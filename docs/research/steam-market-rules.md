# Official Steam Community Market Rules, Matching Semantics & Fee Math

**Document Version:** 1.0  
**Verification Date:** 22 November 2026  
**Primary Authorities:** Valve Corporation Steam Community Market FAQ, Steam Economy Scripts, Steamworks Documentation  

---

## 1. Executive Summary

This document formalizes the official economic rules governing the Steam Community Market (SCM) for Counter-Strike 2 items. It provides:
1. The exact mathematical fee formula executed by Valve's client and backend (operating in integer cents).
2. The exact inversion algorithm to compute seller net proceeds from buyer-pays gross bids.
3. Matching rules and priority semantics for buy orders versus sell listings.
4. Item restriction rules (the 7-day re-market restriction vs player Trade Protection).
5. Steam Wallet financial boundaries (closed-loop virtual currency).

---

## 2. Official Fee Schedule & Integer Cents Math

### 2.1 The Two-Component Fee Structure
Every Counter-Strike 2 transaction on the Steam Community Market incurs two distinct fees:
1. **Steam Transaction Fee:** 5% of seller proceeds, minimum **$0.01 USD** (or 1 minor currency unit).
2. **Counter-Strike 2 Publisher Fee:** 10% of seller proceeds, minimum **$0.01 USD** (or 1 minor currency unit).

Total combined nominal fee rate is **15%**. However, because Valve applies mathematical **floor** rounding to each fee component independently and enforces a minimum fee on each, the actual effective fee rate deviates substantially on low-value items.

### 2.2 Forward Formula (Seller Receives $\rightarrow$ Buyer Pays)
Valve calculates market prices from the seller's desired net proceeds upwards.

Given $R_{\text{cents}}$ (integer cents received by the seller):
$$\text{steam\_fee}_{\text{cents}} = \max\left(1, \, \left\lfloor R_{\text{cents}} \times 0.05 \right\rfloor\right)$$
$$\text{publisher\_fee}_{\text{cents}} = \max\left(1, \, \left\lfloor R_{\text{cents}} \times 0.10 \right\rfloor\right)$$
$$B_{\text{cents}} = R_{\text{cents}} + \text{steam\_fee}_{\text{cents}} + \text{publisher\_fee}_{\text{cents}}$$

Where $B_{\text{cents}}$ is the gross price paid by the buyer.

#### Concrete Boundary Examples:
* **Minimum Item Price ($0.03 USD):**
  - Seller Receives: $R = 1\text{ cent}$ ($0.01)
  - Steam Fee: $\max(1, \lfloor 0.05 \rfloor) = 1\text{ cent}$
  - CS2 Fee: $\max(1, \lfloor 0.10 \rfloor) = 1\text{ cent}$
  - Buyer Pays: $1 + 1 + 1 = 3\text{ cents}$ ($0.03)
  - **Effective Fee Rate:** $\frac{2}{3} = \mathbf{66.67\%}$!
* **$1.00 USD Buyer Pays:**
  - If Seller Receives $R = 88\text{ cents}$:
    - Steam Fee: $\max(1, \lfloor 4.40 \rfloor) = 4\text{ cents}$
    - CS2 Fee: $\max(1, \lfloor 8.80 \rfloor) = 8\text{ cents}$
    - Buyer Pays: $88 + 4 + 8 = 100\text{ cents}$ ($1.00)
    - **Effective Fee Rate:** $\frac{12}{100} = \mathbf{12.0\%}$ (not 15%)!

### 2.3 Backward Inversion (Buyer Pays $\rightarrow$ Seller Receives)
Because of independent flooring, there is no single closed-form division that works for all cents without rounding errors. The exact seller proceeds must be derived via integer boundary search matching Valve's algorithm:

```python
def calculate_seller_receives(buyer_pays_cents: int) -> tuple[int, int, int]:
    """
    Given what the buyer pays (gross in integer cents), returns:
    (seller_receives_cents, steam_fee_cents, publisher_fee_cents)
    """
    if buyer_pays_cents < 3:
        raise ValueError("Minimum Steam market price is 3 cents")
    
    # Initial estimate using 1.15 divisor
    estimated_r = int(buyer_pays_cents / 1.15)
    
    # Search locally within +/- 2 cents of estimate
    for r in range(max(1, estimated_r - 2), estimated_r + 3):
        steam_fee = max(1, int(r * 0.05))
        pub_fee = max(1, int(r * 0.10))
        if r + steam_fee + pub_fee == buyer_pays_cents:
            return r, steam_fee, pub_fee
        elif r + steam_fee + pub_fee > buyer_pays_cents:
            # If no exact match (due to step jumps), take the closest floor
            prev_r = r - 1
            return prev_r, max(1, int(prev_r * 0.05)), max(1, int(prev_r * 0.10))
            
    # Fallback to direct calculation
    r = max(1, estimated_r)
    return r, max(1, int(r * 0.05)), max(1, int(r * 0.10))
```

---

## 3. Order Book Matching Semantics & Priority

### 3.1 Buy Order Characteristics
* A buy order is a **maximum bid commitment**: "I am willing to pay up to $\$X$ for any item meeting this canonical identity."
* **Matching Priority:** When multiple buy orders exist at the same price, Steam fills them in **First-In, First-Out (FIFO)** order based on timestamp.
* **Price Priority:** Higher buy orders take strict precedence over lower buy orders.

### 3.2 What Price Does the Seller Receive?
* **Case 1: Standard Listing (Seller specifies price):**
  If the highest buy order is $\$10.00$ and a seller lists their skin for $\$10.00$, the listing matches immediately.
  The buyer pays $\$10.00$, and the seller receives $\$8.70$ (after $\$1.30$ fees).
* **Case 2: Seller lists Below the Highest Buy Order:**
  If the highest buy order is $\$10.00$ and a seller lists for $\$8.00$:
  - Official matching behavior: The transaction executes immediately at the **existing buy order price** ($\$10.00$) or gives the seller their requested proceeds while refunding the buyer the difference.
  - **Engineering Directive for SELL SNIPER:** Always assume conservative execution: the seller receives the proceeds corresponding to the exact listed price or the verified top bid, never a speculative bonus.

---

## 4. Market Restrictions: 7-Day Re-List vs Trade Protection

| Dimension | 7-Day Market Re-List Restriction | Trade Protection (Reversal Feature) |
| :--- | :--- | :--- |
| **Trigger Event** | Purchasing an item on the Steam Community Market. | Receiving an item via a player-to-player Steam Trade. |
| **Applicable Items** | CS2 items acquired via SCM. | All CS2 traded items. |
| **Visual Indicator** | Red text / note in item description: "Cannot be traded or listed on Community Market until [Date]". | Yellow shield icon on item tile in inventory. |
| **Marketability** | **Unmarketable** on SCM for 7 days (`marketable: 0`). | **Unmarketable** on SCM and untradable for 7 days. |
| **Reversibility** | **Irreversible:** SCM purchases are final clearinghouse transactions. Buyer cannot unilaterally reverse the purchase. | **Reversible:** The sender can reverse all trades from the past 7 days if their account was compromised. |
| **Holding Volatility Risk** | **High:** The buyer must hold the asset through 7 days of price fluctuation before reselling. | **High + Chargeback Risk:** P2P marketplace buyers risk the seller reversing the trade. |

---

## 5. Steam Wallet Financial Realities

1. **Closed-Loop Virtual Stored Value:**
   - Steam Wallet funds cannot be withdrawn, wired, transferred to PayPal, or cashed out to any bank account under Valve’s Steam Subscriber Agreement.
   - Any "cash-out" requires purchasing liquid skins, enduring a 7-day restriction, transferring them to an external marketplace, and paying secondary marketplace seller fees (2%–12%) plus payment withdrawal fees (1%–5%).
2. **Strict Currency Segregation in Reporting:**
   - All app calculations must report:
     - **Steam Wallet Net Profit:** e.g. `+12.45 USD (Steam Wallet)`.
     - **Estimated External Cash Net:** e.g. `+7.80 USD (Est. Cashout after -15% external liquidity spread)`.
   - Never combine or confuse the two.
