"""Integer fee model for Steam Community Market Counter-Strike 2 transactions.

Models documented fee rates and minimum components using exact arithmetic under
the project's modeled formula (integer minor currency units). Valve's exact rounding
implementation remains UNKNOWN. All operations avoid floating-point math.
Enforces fail-closed safety: rejects financial calculations on unverified currencies
or invalid inputs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sniper.core.currency import CurrencyConfig


@dataclass(frozen=True)
class FeeBreakdown:
    """Exact arithmetic breakdown under the project's integer fee model."""
    seller_receives: int
    steam_fee: int
    publisher_fee: int
    total_fee: int
    actual_buyer_pays: int
    is_exact_match: bool

    def __post_init__(self) -> None:
        for name in ("seller_receives", "steam_fee", "publisher_fee", "total_fee", "actual_buyer_pays"):
            val = getattr(self, name)
            if type(val) is not int:
                raise TypeError(f"{name} must be an integer, got {type(val).__name__}")
        if type(self.is_exact_match) is not bool:
            raise TypeError(f"is_exact_match must be a bool, got {type(self.is_exact_match).__name__}")


def _validate_currency(currency: CurrencyConfig | None) -> tuple[int, int]:
    """Validate currency configuration for fee calculations and return (min_steam, min_pub).

    Fails closed if currency metadata or fee rules are unverified/UNKNOWN,
    or if configured fee minimums are invalid.
    """
    if currency is None:
        return 1, 1

    from sniper.core.currency import CurrencyConfig
    if not isinstance(currency, CurrencyConfig):
        raise TypeError(f"currency must be a CurrencyConfig instance, got {type(currency).__name__}")

    if not currency.is_metadata_verified or not currency.is_fee_verified:
        raise ValueError(
            f"Financial calculation rejected: currency metadata or fee rules for '{currency.code}' "
            f"are unverified/UNKNOWN. Fail-closed safety prohibits unverified fee calculations."
        )

    if type(currency.min_fee_steam_minor) is not int or currency.min_fee_steam_minor < 1:
        raise ValueError(
            f"min_fee_steam_minor must be an integer >= 1 for '{currency.code}', got {currency.min_fee_steam_minor!r}"
        )
    if type(currency.min_fee_publisher_minor) is not int or currency.min_fee_publisher_minor < 1:
        raise ValueError(
            f"min_fee_publisher_minor must be an integer >= 1 for '{currency.code}', got {currency.min_fee_publisher_minor!r}"
        )

    return currency.min_fee_steam_minor, currency.min_fee_publisher_minor


def calculate_buyer_pays(
    seller_receives_minor: int,
    publisher_fee_pct: int = 10,
    currency: CurrencyConfig | None = None,
) -> FeeBreakdown:
    """Calculate modeled gross buyer cost from seller proceeds.

    Formula modeled from documented rates/minimum components; Valve's exact rounding
    implementation remains UNKNOWN.
    - Steam fee: 5% (floor), minimum min_fee_steam_minor (default 1)
    - Publisher fee: publisher_fee_pct% (10% for CS2, floor), minimum min_fee_publisher_minor (default 1)
    - Total buyer pays: seller_receives + steam_fee + publisher_fee

    Fail-closed policy:
    If currency is provided, its fee rules MUST be verified (is_fee_verified=True).
    Unverified currency fee calculations are strictly rejected.

    Args:
        seller_receives_minor: Integer minor units desired by seller (>= 1).
        publisher_fee_pct: Integer percentage for publisher fee (0-100, default 10 for CS2).
        currency: Optional CurrencyConfig specifying verified fee floors.

    Returns:
        FeeBreakdown with exact arithmetic under the project's modeled formula.

    Raises:
        TypeError: If monetary or percentage inputs are not integer types (e.g. bool, float, str).
        ValueError: If inputs are non-positive or currency fee rules are unverified.
    """
    if type(seller_receives_minor) is not int:
        raise TypeError(f"seller_receives_minor must be an integer, got {type(seller_receives_minor).__name__}")
    if seller_receives_minor < 1:
        raise ValueError(f"Seller proceeds must be at least 1 minor unit, got {seller_receives_minor}")

    if type(publisher_fee_pct) is not int:
        raise TypeError(f"publisher_fee_pct must be an integer, got {type(publisher_fee_pct).__name__}")
    if not (0 <= publisher_fee_pct <= 100):
        raise ValueError(f"publisher_fee_pct must be between 0 and 100 inclusive, got {publisher_fee_pct}")

    min_steam, min_pub = _validate_currency(currency)

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


def validate_market_listing_price(
    buyer_pays_minor: int,
    currency: CurrencyConfig,
) -> None:
    """Validate buyer price against market listing minimum and price step increment.

    Fails closed if listing minimum or price step are UNKNOWN/unverified.

    Args:
        buyer_pays_minor: Gross price in minor units.
        currency: CurrencyConfig with market listing configuration.

    Raises:
        TypeError: If buyer_pays_minor is not int or currency is not CurrencyConfig.
        ValueError: If unverified or if buyer_pays_minor violates minimum or price step.
    """
    if type(buyer_pays_minor) is not int:
        raise TypeError(f"buyer_pays_minor must be an integer, got {type(buyer_pays_minor).__name__}")
    if buyer_pays_minor < 1:
        raise ValueError(f"buyer_pays_minor must be at least 1, got {buyer_pays_minor}")

    from sniper.core.currency import CurrencyConfig
    if not isinstance(currency, CurrencyConfig):
        raise TypeError(f"currency must be a CurrencyConfig instance, got {type(currency).__name__}")

    if not currency.is_metadata_verified or not currency.is_fee_verified:
        raise ValueError(
            f"Financial calculation rejected: currency metadata or fee rules for '{currency.code}' "
            f"are unverified/UNKNOWN. Fail-closed safety prohibits unverified fee calculations."
        )

    if not currency.is_listing_min_verified or currency.min_listing_price_minor is None:
        raise ValueError(
            f"Market listing validation rejected: listing minimum for currency '{currency.code}' "
            f"is unverified/UNKNOWN."
        )
    if type(currency.min_listing_price_minor) is not int or currency.min_listing_price_minor < 1:
        raise ValueError(f"min_listing_price_minor must be an integer >= 1 for '{currency.code}'")

    if buyer_pays_minor < currency.min_listing_price_minor:
        raise ValueError(
            f"Requested price {buyer_pays_minor} is below minimum market listing price "
            f"of {currency.min_listing_price_minor} for {currency.code}"
        )

    if not currency.is_price_step_verified:
        raise ValueError(
            f"Market listing validation rejected: price step for currency '{currency.code}' "
            f"is unverified/UNKNOWN."
        )
    if type(currency.price_step_minor) is not int or currency.price_step_minor < 1:
        raise ValueError(f"price_step_minor must be an integer >= 1 for '{currency.code}'")

    if currency.price_step_minor > 1:
        if buyer_pays_minor % currency.price_step_minor != 0:
            raise ValueError(
                f"Requested price {buyer_pays_minor} does not conform to allowed price step "
                f"of {currency.price_step_minor} for {currency.code}"
            )


def calculate_seller_receives(
    requested_buyer_pays_minor: int,
    publisher_fee_pct: int = 10,
    currency: CurrencyConfig | None = None,
    validate_market_listing: bool = True,
) -> FeeBreakdown:
    """Invert gross buyer price under the fee model using integer boundary search.

    The modeled independent-floor formula creates step jumps. For instance:
    - At seller_receives = 99: Steam fee is 4 (floor(99 * 0.05) = 4), CS2 fee is 9 (floor(99 * 0.10) = 9).
      Total buyer pays is 99 + 4 + 9 = 112 minor units.
    - At seller_receives = 100: Steam fee is 5, CS2 fee is 10. Total buyer pays is 100 + 5 + 10 = 115.
    - Therefore, 113 and 114 minor units are not reachable by any integer seller amount.

    For unreachable prices, returns the largest integer seller proceeds whose forward-calculated
    buyer price does not exceed requested_buyer_pays_minor, with is_exact_match=False.

    Fail-closed policy:
    - Rejects financial calculations if currency fee rules are unverified/UNKNOWN.
    - When validate_market_listing is True (default), rejects calculations if listing minimum
      or price step are unverified/UNKNOWN.
    - When validate_market_listing is False, performs pure mathematical inversion under
      this application's fee model, not market-listing validation.

    Args:
        requested_buyer_pays_minor: Integer minor units the buyer pays.
        publisher_fee_pct: Publisher fee percentage (10 for CS2).
        currency: Optional CurrencyConfig with currency-specific listing rules.
        validate_market_listing: If True (default), validates price against verified market
                                 listing minimums and price step increments.

    Returns:
        FeeBreakdown detailing seller proceeds, fees, resulting buyer price, and exact match flag.

    Raises:
        TypeError: If inputs are invalid types (e.g. bool, float, str).
        ValueError: If price is below fee floor, inputs are invalid, or validation fails closed.
    """
    if type(requested_buyer_pays_minor) is not int:
        raise TypeError(f"requested_buyer_pays_minor must be an integer, got {type(requested_buyer_pays_minor).__name__}")
    if type(publisher_fee_pct) is not int:
        raise TypeError(f"publisher_fee_pct must be an integer, got {type(publisher_fee_pct).__name__}")
    if not (0 <= publisher_fee_pct <= 100):
        raise ValueError(f"publisher_fee_pct must be between 0 and 100 inclusive, got {publisher_fee_pct}")
    if type(validate_market_listing) is not bool:
        raise TypeError(f"validate_market_listing must be a boolean, got {type(validate_market_listing).__name__}")

    if validate_market_listing and currency is None:
        raise ValueError(
            "Market listing validation requires a CurrencyConfig. "
            "For pure mathematical inversion, explicitly pass validate_market_listing=False."
        )

    min_steam, min_pub = _validate_currency(currency)
    fee_floor = 1 + min_steam + min_pub

    # Check universal mathematical fee floor
    if requested_buyer_pays_minor < fee_floor:
        raise ValueError(
            f"Requested price {requested_buyer_pays_minor} cannot be less than fee floor of {fee_floor}"
        )

    if validate_market_listing and currency is not None:
        validate_market_listing_price(requested_buyer_pays_minor, currency)

    # Mathematically safe integer boundary search:
    # Forward function B(R) is strictly monotonically increasing for R >= 1.
    # We find the largest integer R in [1, requested_buyer_pays_minor] such that B(R) <= requested_buyer_pays_minor.
    low = 1
    high = requested_buyer_pays_minor
    best_r = 1

    while low <= high:
        mid = (low + high) // 2
        forward = calculate_buyer_pays(mid, publisher_fee_pct=publisher_fee_pct, currency=currency)
        if forward.actual_buyer_pays <= requested_buyer_pays_minor:
            best_r = mid
            low = mid + 1
        else:
            high = mid - 1

    best_breakdown = calculate_buyer_pays(best_r, publisher_fee_pct=publisher_fee_pct, currency=currency)
    is_exact = (best_breakdown.actual_buyer_pays == requested_buyer_pays_minor)

    if validate_market_listing and currency is not None and not is_exact:
        # Ensure any adjusted buyer-paid amount cannot silently bypass applicable verified listing constraints
        validate_market_listing_price(best_breakdown.actual_buyer_pays, currency)

    return FeeBreakdown(
        seller_receives=best_breakdown.seller_receives,
        steam_fee=best_breakdown.steam_fee,
        publisher_fee=best_breakdown.publisher_fee,
        total_fee=best_breakdown.total_fee,
        actual_buyer_pays=best_breakdown.actual_buyer_pays,
        is_exact_match=is_exact,
    )


def calculate_exact_breakeven_buyer_pays(
    cost_basis_minor: int,
    publisher_fee_pct: int = 10,
    currency: CurrencyConfig | None = None,
) -> int:
    """Calculate the exact minimum gross listing price (buyer pays) to recover cost basis under the modeled formula.

    Uses exact arithmetic under the project's modeled formula (integer fee model).
    Valve's exact rounding implementation remains UNKNOWN; this calculation is
    exact relative to the project's model, not proof of Valve's live implementation.

    Args:
        cost_basis_minor: Integer minor units paid to acquire the item (>= 1).
        publisher_fee_pct: Integer percentage for publisher fee (0-100, default 10 for CS2).
        currency: Optional CurrencyConfig specifying verified fee floors.

    Returns:
        Exact gross buyer cost in integer minor units required so that seller proceeds equal cost_basis_minor.

    Raises:
        TypeError: If inputs are invalid types.
        ValueError: If cost_basis_minor < 1 or currency fee rules are unverified.
    """
    if type(cost_basis_minor) is not int:
        raise TypeError(f"cost_basis_minor must be an integer, got {type(cost_basis_minor).__name__}")
    if cost_basis_minor < 1:
        raise ValueError(f"Cost basis must be at least 1 minor unit, got {cost_basis_minor}")
    if type(publisher_fee_pct) is not int:
        raise TypeError(f"publisher_fee_pct must be an integer, got {type(publisher_fee_pct).__name__}")
    if not (0 <= publisher_fee_pct <= 100):
        raise ValueError(f"publisher_fee_pct must be between 0 and 100 inclusive, got {publisher_fee_pct}")

    forward = calculate_buyer_pays(cost_basis_minor, publisher_fee_pct=publisher_fee_pct, currency=currency)
    return forward.actual_buyer_pays
