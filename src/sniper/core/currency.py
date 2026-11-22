"""Explicit currency model and minor-unit financial formatting without floating-point math."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar


@dataclass(frozen=True)
class CurrencyConfig:
    """Explicit currency configuration model.
    
    Steam assigns numeric IDs to currencies (e.g. 1=USD, 3=EUR, 8=JPY).
    Minor unit precision indicates decimal places (e.g. 2 for USD/EUR, 0 for JPY).
    All conversions and calculations operate strictly without floating-point arithmetic.
    
    Separates:
    - Fee component minimums (min_fee_steam_minor, min_fee_publisher_minor)
    - Market listing minimums (min_listing_price_minor)
    - Price increment steps (price_step_minor)
    - Verification status (is_verified)
    """
    currency_id: int
    code: str
    symbol: str
    minor_unit_precision: int
    country: str = "US"
    min_listing_price_minor: int | None = None
    price_step_minor: int = 1
    min_fee_steam_minor: int = 1
    min_fee_publisher_minor: int = 1
    is_verified: bool = False
    notes: str = ""

    KNOWN_CURRENCIES: ClassVar[dict[int, CurrencyConfig]] = {}

    @classmethod
    def get_default_usd(cls) -> CurrencyConfig:
        return cls(
            currency_id=1,
            code="USD",
            symbol="$",
            minor_unit_precision=2,
            country="US",
            min_listing_price_minor=3,
            price_step_minor=1,
            min_fee_steam_minor=1,
            min_fee_publisher_minor=1,
            is_verified=True,
            notes="Empirically verified against Steam FAQ and live fixtures. Minimum buyer pays is $0.03.",
        )

    @classmethod
    def get_eur(cls) -> CurrencyConfig:
        return cls(
            currency_id=3,
            code="EUR",
            symbol="€",
            minor_unit_precision=2,
            country="DE",
            min_listing_price_minor=3,
            price_step_minor=1,
            min_fee_steam_minor=1,
            min_fee_publisher_minor=1,
            is_verified=True,
            notes="Empirically verified against Steam FAQ and live fixtures. Minimum buyer pays is €0.03.",
        )

    @classmethod
    def get_jpy(cls) -> CurrencyConfig:
        return cls(
            currency_id=8,
            code="JPY",
            symbol="¥",
            minor_unit_precision=0,
            country="JP",
            min_listing_price_minor=None,  # UNKNOWN / unverified minimum listing threshold
            price_step_minor=1,
            min_fee_steam_minor=1,
            min_fee_publisher_minor=1,
            is_verified=False,
            notes="Zero-decimal currency. Minimum market listing price is UNKNOWN pending official Steam verification.",
        )

    @classmethod
    def get_tunisia_context(cls) -> CurrencyConfig:
        """Tunisia does not have a native Steam Wallet currency (no TND).
        
        Steam bills Tunisian accounts in USD (Standard or North Africa/MENA regional USD).
        We return USD with explicit country context and documentation.
        """
        return cls(
            currency_id=1,
            code="USD",
            symbol="$",
            minor_unit_precision=2,
            country="TN",
            min_listing_price_minor=3,
            price_step_minor=1,
            min_fee_steam_minor=1,
            min_fee_publisher_minor=1,
            is_verified=True,
            notes="Tunisian accounts operate in USD on Steam. TND is not a supported Steam wallet currency.",
        )

    def format_amount(self, minor_units: int) -> str:
        """Format integer minor units into human-readable currency string.
        
        Properly places negative signs before the currency symbol (e.g. -$24.88, -$0.01).
        """
        is_negative = minor_units < 0
        abs_units = abs(minor_units)

        if self.minor_unit_precision == 0:
            formatted = f"{self.symbol}{abs_units}"
        else:
            divisor = 10 ** self.minor_unit_precision
            integer_part = abs_units // divisor
            remainder = abs_units % divisor
            formatted = f"{self.symbol}{integer_part}.{remainder:0{self.minor_unit_precision}d}"

        return f"-{formatted}" if is_negative else formatted

    def parse_to_minor_units(self, amount_str: str) -> int:
        """Parse clean amount string (e.g. '24.88', '$24.88', '-$24.88') into integer minor units.
        
        Rules:
        - Floating-point conversions are strictly prohibited.
        - Zero-decimal currencies (precision=0) reject any fractional components.
        - Fractional currencies reject excess decimal digits beyond minor_unit_precision.
        """
        if not amount_str or not amount_str.strip():
            raise ValueError("Cannot parse empty currency string")

        raw = amount_str.strip()

        # Detect negative sign before or after symbol
        is_negative = False
        if "-" in raw:
            is_negative = True
            raw = raw.replace("-", "")

        # Remove currency symbol, commas, and whitespace
        cleaned = raw.replace(self.symbol, "").replace(",", "").strip()
        if not cleaned:
            raise ValueError(f"No numeric digits found in currency string: {amount_str}")

        if self.minor_unit_precision == 0:
            if "." in cleaned:
                raise ValueError(
                    f"Zero-decimal currency {self.code} cannot have fractional decimal places: {amount_str}"
                )
            if not cleaned.isdigit():
                raise ValueError(f"Invalid monetary format for {self.code}: {amount_str}")
            val = int(cleaned)
            return -val if is_negative else val

        parts = cleaned.split(".")
        if len(parts) == 1:
            int_str = parts[0]
            if not int_str.isdigit():
                raise ValueError(f"Invalid integer component in: {amount_str}")
            val = int(int_str) * (10 ** self.minor_unit_precision)
            return -val if is_negative else val
        elif len(parts) == 2:
            int_str, dec_str = parts[0], parts[1]
            if int_str and not int_str.isdigit():
                raise ValueError(f"Invalid integer component in: {amount_str}")
            if not dec_str.isdigit():
                raise ValueError(f"Invalid decimal component in: {amount_str}")
                
            int_part = int(int_str) if int_str else 0
            if len(dec_str) > self.minor_unit_precision:
                raise ValueError(
                    f"Excess decimal precision: {len(dec_str)} digits provided, "
                    f"maximum allowed for {self.code} is {self.minor_unit_precision} in {amount_str}"
                )
            padded_dec = dec_str.ljust(self.minor_unit_precision, "0")
            dec_part = int(padded_dec)
            val = int_part * (10 ** self.minor_unit_precision) + dec_part
            return -val if is_negative else val
        else:
            raise ValueError(f"Invalid monetary format with multiple dots: {amount_str}")


def assert_same_currency(c1: CurrencyConfig, c2: CurrencyConfig) -> None:
    """Assert two currency contexts are strictly identical before mathematical comparison."""
    if c1.currency_id != c2.currency_id or c1.code != c2.code:
        raise ValueError(
            f"Currency mismatch error: cannot compare or operate between {c1.code} "
            f"(ID {c1.currency_id}) and {c2.code} (ID {c2.currency_id})"
        )
