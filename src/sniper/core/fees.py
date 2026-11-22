"""Steam Community Market exact fee calculation engine for Counter-Strike 2.

Implements Valve's dual-floor fee schedule using integer minor currency units (cents).
All operations avoid floating-point math to guarantee deterministic results.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FeeBreakdown:
    """Exact breakdown of fees and pricing for a market transaction."""
    seller_receives: int
    steam_fee: int
    publisher_fee: int
    total_fee: int
    actual_buyer_pays: int
    is_exact_match: bool


def calculate_buyer_pays(seller_receives_minor: int, publisher_fee_pct: int = 10) -> FeeBreakdown:
    """Calculate exact gross buyer cost from seller proceeds.
    
    Formula based on official Valve client logic (economy.js):
    - Steam fee: 5% (floor), minimum 1 minor unit
    - Publisher fee: publisher_fee_pct% (10% for CS2, floor), minimum 1 minor unit
    - Total buyer pays: seller_receives + steam_fee + publisher_fee
    
    Args:
        seller_receives_minor: Integer minor units (e.g. cents) desired by seller.
        publisher_fee_pct: Integer percentage for publisher fee (10 for CS2).
        
    Returns:
        FeeBreakdown with exact components.
    """
    if seller_receives_minor < 1:
        raise ValueError("Seller proceeds must be at least 1 minor unit")
        
    steam_fee = max(1, (seller_receives_minor * 5) // 100)
    publisher_fee = max(1, (seller_receives_minor * publisher_fee_pct) // 100)
    total_fee = steam_fee + publisher_fee
    buyer_pays = seller_receives_minor + total_fee
    
    return FeeBreakdown(
        seller_receives=seller_receives_minor,
        steam_fee=steam_fee,
        publisher_fee=publisher_fee,
        total_fee=total_fee,
        actual_buyer_pays=buyer_pays,
        is_exact_match=True
    )


def calculate_seller_receives(requested_buyer_pays_minor: int, publisher_fee_pct: int = 10) -> FeeBreakdown:
    """Invert gross buyer price into seller net proceeds using exact integer boundary search.
    
    Because Valve independently floors each fee, fee step jumps occur. For instance,
    at 114 cents requested buyer price, seller_receives=99 produces 113 cents buyer pays,
    while seller_receives=100 produces 115 cents buyer pays. 114 cents is not exactly reachable.
    
    This function finds the highest seller amount such that forward buyer pays <= requested.
    It returns `is_exact_match=False` when the requested price lands in a gap.
    
    Args:
        requested_buyer_pays_minor: Integer minor units the buyer pays.
        publisher_fee_pct: Publisher fee percentage (10 for CS2).
        
    Returns:
        FeeBreakdown detailing seller proceeds, fees, resulting buyer price, and exact match flag.
    """
    if requested_buyer_pays_minor < 3:
        raise ValueError("Minimum Steam Community Market price is 3 minor units ($0.03)")
        
    # Baseline integer estimate using 1.15 divisor
    # Total percentage is roughly 100 + 5 + 10 = 115
    estimated_r = (requested_buyer_pays_minor * 100) // (100 + 5 + publisher_fee_pct)
    
    # Search locally within +/- 4 units of estimate
    best_breakdown: FeeBreakdown | None = None
    
    for r in range(max(1, estimated_r - 4), estimated_r + 5):
        forward = calculate_buyer_pays(r, publisher_fee_pct=publisher_fee_pct)
        if forward.actual_buyer_pays == requested_buyer_pays_minor:
            return forward
        elif forward.actual_buyer_pays < requested_buyer_pays_minor:
            if best_breakdown is None or forward.actual_buyer_pays > best_breakdown.actual_buyer_pays:
                best_breakdown = forward
                
    if best_breakdown is not None:
        return FeeBreakdown(
            seller_receives=best_breakdown.seller_receives,
            steam_fee=best_breakdown.steam_fee,
            publisher_fee=best_breakdown.publisher_fee,
            total_fee=best_breakdown.total_fee,
            actual_buyer_pays=best_breakdown.actual_buyer_pays,
            is_exact_match=False
        )
        
    # Fallback for extreme low boundary
    fallback = calculate_buyer_pays(1, publisher_fee_pct=publisher_fee_pct)
    return FeeBreakdown(
        seller_receives=fallback.seller_receives,
        steam_fee=fallback.steam_fee,
        publisher_fee=fallback.publisher_fee,
        total_fee=fallback.total_fee,
        actual_buyer_pays=fallback.actual_buyer_pays,
        is_exact_match=(fallback.actual_buyer_pays == requested_buyer_pays_minor)
    )


def calculate_exact_breakeven_buyer_pays(cost_basis_minor: int, publisher_fee_pct: int = 10) -> int:
    """Calculate the exact minimum gross listing price (buyer pays) to recover cost basis.
    
    Avoids naive approximations like `cost * 1.15`. Computes the exact forward price
    when seller proceeds equals cost basis.
    """
    forward = calculate_buyer_pays(cost_basis_minor, publisher_fee_pct=publisher_fee_pct)
    return forward.actual_buyer_pays
