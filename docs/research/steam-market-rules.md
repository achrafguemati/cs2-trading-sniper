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
Due to independent flooring, not every integer buyer total is exactly reachable by a seller proceeds integer (step jumps occur). For example:
* At $R = 99\text{ minor units}$: Steam fee is $\lfloor 99 \times 0.05 \rfloor = 4$, CS2 fee is $\lfloor 99 \times 0.10 \rfloor = 9$, producing total buyer cost $99 + 4 + 9 = 112\text{ minor units}$.
* At $R = 100\text{ minor units}$: Steam fee is 5, CS2 fee is 10, producing total buyer cost $100 + 5 + 10 = 115\text{ minor units}$.
* Consequently, 113 and 114 minor units are impossible buyer totals under integer seller proceeds.

#### Separation of Transaction Fee Floors vs. Market Listing Minimums
- **Transaction Fee Floors:** Mathematically, each fee component enforces a 1-minor-unit floor ($\max(1, \dots)$), giving an absolute mathematical lower bound of $1 + 1 + 1 = 3\text{ minor units}$ ($R=1$).
- **Market Listing Minimums:** Valve enforces distinct minimum listing prices and price steps per currency. For USD, the official Valve Community Market FAQ states: *"Currently the minimum listing price is $0.03."* For EUR, JPY, and all other currencies, listing minimums and allowed price steps are marked as **UNKNOWN** until verified via direct primary captures or official documentation. Synthetic orderbook fixtures are not empirical evidence of live listing minimums.
- **Empirical Basis:** Fee rules are verified from Valve's public client implementation (`economy.js`). Listing minimums without primary empirical or official documentation evidence remain strictly marked as UNKNOWN.

The inverse fee function must never return a seller amount and claim it produces the requested buyer total if the forward formula produces a different total. It must explicitly return:
1. `seller_receives_cents`: The largest integer seller proceeds whose resulting buyer cost does not exceed the target.
2. `steam_fee_cents`: Resulting Steam fee.
3. `publisher_fee_cents`: Resulting publisher fee.
4. `actual_buyer_pays_cents`: The exact buyer total produced by the forward formula.
5. `is_exact_match`: Boolean indicating whether `actual_buyer_pays_cents == requested_buyer_pays_cents`.

```python
def calculate_seller_receives(
    requested_buyer_pays_minor: int,
    publisher_fee_pct: int = 10,
    currency: CurrencyConfig | None = None,
    validate_market_listing: bool = True,
) -> FeeBreakdown:
    """Invert gross buyer price into seller net proceeds using exact integer boundary search.
    
    Validates against currency-specific listing minimums and fee floors when validate_market_listing=True.
    Fails closed if currency fee rules or listing minimums/price steps are unverified/UNKNOWN.
    Performs pure mathematical fee inversion when validate_market_listing=False.
    """
    if currency is not None:
        if not currency.is_fee_verified:
            raise ValueError(f"Fee rules for {currency.code} are unverified/UNKNOWN")
        if validate_market_listing:
            validate_market_listing_price(requested_buyer_pays_minor, currency)

    min_steam = currency.min_fee_steam_minor if currency else 1
    min_pub = currency.min_fee_publisher_minor if currency else 1
    fee_floor = 1 + min_steam + min_pub

    if requested_buyer_pays_minor < fee_floor:
        raise ValueError(f"Below fee floor of {fee_floor}")
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
* **Valve Official Rule:** When a new sell listing is posted that can fulfill multiple active buy orders, Valve's matching engine selects the **oldest matching buy order**.
* **Direct Valve Example:** If User A places a buy order for $10.00 at 12:00, and User B places a buy order for $15.00 at 12:05, and a seller subsequently lists the item for $5.00, **User A's order ($10.00 bid placed first) is selected to fulfill the transaction**. User A pays the listed price of $5.00. Higher bid amounts do NOT jump ahead of older matching orders.
* **FIFO Execution:** Order matching priority is strictly governed by timestamp (first-in, first-out among all matching bids $\ge$ listing price). Higher bids do not override earlier matching bids.

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
