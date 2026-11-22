"""Steam Community Market exact fee calculation engine for Counter-Strike 2.

Implements Valve's dual-floor fee schedule using integer minor currency units (cents).
All operations avoid floating-point math to guarantee deterministic results.
Enforces fail-closed safety: rejects financial calculations on unverified currencies.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sniper.core.currency import CurrencyConfig


@dataclass(frozen=True)
class FeeBreakdown:
    """Exact breakdown of fees and pricing for a market transaction."""
    seller_receives: int
    steam_fee: int
    publisher_fee: int
    total_fee: int
    actual_buyer_pays: int
    is_exact_match: bool


def calculate_buyer_pays(
    seller_receives_minor: int,
    publisher_fee_pct: int = 10,
    currency: CurrencyConfig | None = None,
) -> FeeBreakdown:
    """Calculate exact gross buyer cost from seller proceeds.
    
    Formula based on official Valve client logic (economy.js):
    - Steam fee: 5% (floor), minimum min_fee_steam_minor (default 1)
    - Publisher fee: publisher_fee_pct% (10% for CS2, floor), minimum min_fee_publisher_minor (default 1)
    - Total buyer pays: seller_receives + steam_fee + publisher_fee
    
    Fail-closed policy:
    If currency is provided, its fee rules MUST be verified (is_fee_verified=True).
    Unverified currency fee calculations are strictly rejected.
    
    Args:
        seller_receives_minor: Integer minor units desired by seller.
        publisher_fee_pct: Integer percentage for publisher fee (10 for CS2).
        currency: Optional CurrencyConfig specifying verified fee floors.
        
    Returns:
        FeeBreakdown with exact components.
    """
    if seller_receives_minor < 1:
        raise ValueError("Seller proceeds must be at least 1 minor unit")

    if currency is not None and not currency.is_fee_verified:
        raise ValueError(
            f"Financial calculation rejected: fee rules for currency '{currency.code}' "
            f"are unverified/UNKNOWN. Fail-closed safety prohibits unverified fee calculations."
        )

    min_steam = currency.min_fee_steam_minor if currency else 1
    min_pub = currency.min_fee_publisher_minor if currency else 1

    steam_fee = max(min_steam, (seller_receives_minor * 5) // 100)
    publisher_fee = max(min_pub, (seller_receives_minor * publisher_fee_pct) // 100)
    total_fee = steam_fee + publisher_fee
    buyer_pays = seller_receives_minor + total_fee

    return FeeBreakdown(
        seller_receives=seller_receives_minor,
        steam_fee=steam_fee,
        publisher_fee=publisher_fee,
        total_fee=total_fee,
        actual_buyer_pays=buyer_pays,
        is_exact_match=True,
    )


def calculate_seller_receives(
    requested_buyer_pays_minor: int,
    publisher_fee_pct: int = 10,
    currency: CurrencyConfig | None = None,
) -> FeeBreakdown:
    """Invert gross buyer price into seller net proceeds using exact integer boundary search.
    
    Because Valve independently floors each fee, fee step jumps occur. For instance:
    - At seller_receives = 99: Steam fee is 4 (floor(99 * 0.05) = 4), CS2 fee is 9 (floor(99 * 0.10) = 9).
      Total buyer pays is 99 + 4 + 9 = 112 minor units.
    - At seller_receives = 100: Steam fee is 5, CS2 fee is 10. Total buyer pays is 100 + 5 + 10 = 115.
    - Therefore, 113 and 114 minor units are not reachable by any integer seller amount.
    
    Fail-closed policy:
    Rejects financial calculations if currency fee rules, listing minimum, or price steps are unverified.
    
    Args:
        requested_buyer_pays_minor: Integer minor units the buyer pays.
        publisher_fee_pct: Publisher fee percentage (10 for CS2).
        currency: Optional CurrencyConfig with currency-specific listing rules.
        
    Returns:
        FeeBreakdown detailing seller proceeds, fees, resulting buyer price, and exact match flag.
    """
    if currency is not None:
        if not currency.is_fee_verified:
            raise ValueError(
                f"Financial calculation rejected: fee rules for currency '{currency.code}' "
                f"are unverified/UNKNOWN. Fail-closed safety prohibits unverified fee calculations."
            )
        if currency.min_listing_price_minor is not None and not currency.is_listing_min_verified:
            raise ValueError(
                f"Financial calculation rejected: listing minimum for currency '{currency.code}' "
                f"is unverified/UNKNOWN."
            )
        if currency.price_step_minor > 1 and not currency.is_price_step_verified:
            raise ValueError(
                f"Financial calculation rejected: price step for currency '{currency.code}' "
                f"is unverified/UNKNOWN."
            )

    min_steam = currency.min_fee_steam_minor if currency else 1
    min_pub = currency.min_fee_publisher_minor if currency else 1
    fee_floor = 1 + min_steam + min_pub

    # Check market listing minimum if verified for currency
    if currency and currency.min_listing_price_minor is not None:
        if requested_buyer_pays_minor < currency.min_listing_price_minor:
            raise ValueError(
                f"Requested price {requested_buyer_pays_minor} is below minimum market listing price "
                f"of {currency.min_listing_price_minor} for {currency.code}"
            )
    else:
        # Check universal mathematical fee floor
        if requested_buyer_pays_minor < fee_floor:
            raise ValueError(
                f"Requested price {requested_buyer_pays_minor} cannot be less than fee floor of {fee_floor}"
            )

    # Validate allowed price increment if configured
    if currency and currency.price_step_minor > 1:
        if requested_buyer_pays_minor % currency.price_step_minor != 0:
            raise ValueError(
                f"Requested price {requested_buyer_pays_minor} does not conform to allowed price step "
                f"of {currency.price_step_minor} for {currency.code}"
            )

    # Baseline integer estimate
    estimated_r = (requested_buyer_pays_minor * 100) // (100 + 5 + publisher_fee_pct)

    best_breakdown: FeeBreakdown | None = None

    for r in range(max(1, estimated_r - 4), estimated_r + 5):
        forward = calculate_buyer_pays(r, publisher_fee_pct=publisher_fee_pct, currency=currency)
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
            is_exact_match=False,
        )

    # Fallback for lowest possible price
    fallback = calculate_buyer_pays(1, publisher_fee_pct=publisher_fee_pct, currency=currency)
    return FeeBreakdown(
        seller_receives=fallback.seller_receives,
        steam_fee=fallback.steam_fee,
        publisher_fee=fallback.publisher_fee,
        total_fee=fallback.total_fee,
        actual_buyer_pays=fallback.actual_buyer_pays,
        is_exact_match=(fallback.actual_buyer_pays == requested_buyer_pays_minor),
    )


def calculate_exact_breakeven_buyer_pays(
    cost_basis_minor: int,
    publisher_fee_pct: int = 10,
    currency: CurrencyConfig | None = None,
) -> int:
    """Calculate the exact minimum gross listing price (buyer pays) to recover cost basis."""
    forward = calculate_buyer_pays(cost_basis_minor, publisher_fee_pct=publisher_fee_pct, currency=currency)
    return forward.actual_buyer_pays
