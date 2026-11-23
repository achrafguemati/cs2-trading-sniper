"""Explicit currency model and minor-unit financial formatting with exact Decimal precision."""

from __future__ import annotations

from decimal import Decimal
import re
from dataclasses import dataclass
from typing import ClassVar


@dataclass(frozen=True)
class CurrencyConfig:
    """Explicit currency configuration model.
    
    Steam assigns numeric IDs to currencies (e.g. 1=USD, 3=EUR, 8=JPY).
    Minor unit precision indicates decimal places (e.g. 2 for USD/EUR, 0 for JPY).
    All conversions and calculations operate strictly without floating-point arithmetic.
    
    Separates independent verification flags:
    - is_metadata_verified: Currency ID, code, symbol, and minor unit precision verified.
    - is_fee_verified: Fee rates and minimum components verified; this does not certify
      the exact rounding implementation, which remains a separate algorithm question.
    - is_listing_min_verified: Steam Community Market listing minimum price verified.
    - is_price_step_verified: Steam Community Market allowed price increment verified.
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
    is_metadata_verified: bool = False
    is_fee_verified: bool = False
    is_listing_min_verified: bool = False
    is_price_step_verified: bool = False
    notes: str = ""

    KNOWN_CURRENCIES: ClassVar[dict[int, CurrencyConfig]] = {}

    def __post_init__(self) -> None:
        if type(self.currency_id) is not int:
            raise TypeError(f"currency_id must be an int, got {type(self.currency_id).__name__}")
        if type(self.minor_unit_precision) is not int:
            raise TypeError(f"minor_unit_precision must be an int, got {type(self.minor_unit_precision).__name__}")
        if self.minor_unit_precision < 0:
            raise ValueError(f"minor_unit_precision must be a non-negative int, got {self.minor_unit_precision!r}")
        if type(self.price_step_minor) is not int:
            raise TypeError(f"price_step_minor must be an int, got {type(self.price_step_minor).__name__}")
        if self.price_step_minor < 1:
            raise ValueError(f"price_step_minor must be an int >= 1, got {self.price_step_minor!r}")
        if type(self.min_fee_steam_minor) is not int:
            raise TypeError(f"min_fee_steam_minor must be an int, got {type(self.min_fee_steam_minor).__name__}")
        if self.min_fee_steam_minor < 1:
            raise ValueError(f"min_fee_steam_minor must be an int >= 1, got {self.min_fee_steam_minor!r}")
        if type(self.min_fee_publisher_minor) is not int:
            raise TypeError(f"min_fee_publisher_minor must be an int, got {type(self.min_fee_publisher_minor).__name__}")
        if self.min_fee_publisher_minor < 1:
            raise ValueError(f"min_fee_publisher_minor must be an int >= 1, got {self.min_fee_publisher_minor!r}")
        if self.min_listing_price_minor is not None:
            if type(self.min_listing_price_minor) is not int:
                raise TypeError(f"min_listing_price_minor must be None or an int, got {type(self.min_listing_price_minor).__name__}")
            if self.min_listing_price_minor < 1:
                raise ValueError(f"min_listing_price_minor must be an int >= 1, got {self.min_listing_price_minor!r}")

    @property
    def is_verified(self) -> bool:
        """Return True only if all metadata, fee, and listing rules are verified."""
        return (
            self.is_metadata_verified
            and self.is_fee_verified
            and self.is_listing_min_verified
            and self.is_price_step_verified
        )

    @classmethod
    def get_default_usd(cls) -> CurrencyConfig:
        return cls(
            currency_id=1,
            code="USD",
            symbol="$",
            minor_unit_precision=2,
            country="US",
            min_listing_price_minor=None,  # UNKNOWN: official FAQ does not specify a USD market minimum
            price_step_minor=1,
            min_fee_steam_minor=1,
            min_fee_publisher_minor=1,
            is_metadata_verified=True,
            is_fee_verified=True,
            is_listing_min_verified=False,
            is_price_step_verified=False,
            notes="USD currency metadata and fee rules verified. Community Market listing minimum and price step are UNKNOWN; the FAQ does not state a USD $0.03 minimum.",
        )

    @classmethod
    def get_eur(cls) -> CurrencyConfig:
        return cls(
            currency_id=3,
            code="EUR",
            symbol="€",
            minor_unit_precision=2,
            country="DE",
            min_listing_price_minor=None,  # UNKNOWN
            price_step_minor=1,
            min_fee_steam_minor=1,
            min_fee_publisher_minor=1,
            is_metadata_verified=True,     # ID 3, EUR, €, 2 decimals verified in Steamworks
            is_fee_verified=True,          # Fee rates/minimum components, not rounding, are verified
            is_listing_min_verified=False, # UNKNOWN: Listing minimum unverified pending primary evidence
            is_price_step_verified=False,  # UNKNOWN: Price step unverified pending primary evidence
            notes="Fee rates/minimum components are recorded separately from rounding evidence. Community Market listing minimum and price step are UNKNOWN.",
        )

    @classmethod
    def get_jpy(cls) -> CurrencyConfig:
        return cls(
            currency_id=8,
            code="JPY",
            symbol="¥",
            minor_unit_precision=0,
            country="JP",
            min_listing_price_minor=None,  # UNKNOWN
            price_step_minor=1,
            min_fee_steam_minor=1,
            min_fee_publisher_minor=1,
            is_metadata_verified=True,     # ID 8, JPY, ¥, 0 decimals verified in Steamworks
            is_fee_verified=False,         # UNKNOWN: Fee rounding edge cases unverified
            is_listing_min_verified=False, # UNKNOWN: Listing minimum unverified
            is_price_step_verified=False,  # UNKNOWN: Price step unverified
            notes="Steamworks metadata reports JPY in sen and specifies 100-sen increments; Community Market raw orderbook units, fee rounding, listing minimum, and price steps remain separate UNKNOWN questions.",
        )

    @classmethod
    def get_tunisia_context(cls) -> CurrencyConfig:
        """Tunisia USD_MENA regional pricing metadata context.
        
        Steamworks lists Tunisia in USD_MENA. Account wallet currency and Community
        Market listing minimum/price-step rules require their own evidence.
        """
        return cls(
            currency_id=1,
            code="USD",
            symbol="$",
            minor_unit_precision=2,
            country="TN",
            min_listing_price_minor=None,  # UNKNOWN
            price_step_minor=1,
            min_fee_steam_minor=1,
            min_fee_publisher_minor=1,
            is_metadata_verified=True,
            is_fee_verified=True,          # USD fee rules verified
            is_listing_min_verified=False, # UNKNOWN: regional minimum unverified without primary evidence
            is_price_step_verified=False,  # UNKNOWN: regional price step unverified without primary evidence
            notes="Steamworks lists Tunisia in USD_MENA. USD metadata and fee rules apply; Community Market listing minimum and price step increments are UNKNOWN without direct market evidence.",
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

    def parse_to_minor_units(self, amount: str | Decimal | int) -> int:
        """Parse monetary amount string or Decimal into exact integer minor units.
        
        Strict grammar & precision rules:
        1. Negative amounts: Must start with a single '-' (e.g. '-$24.88' or '-24.88').
           Misplaced ('2-4.88', '$-24.88', '24.88-') or repeated ('--24.88') minus signs are rejected.
        2. Currency symbol: Optional, but if present must be single and precede the digits (e.g. '$24.88', '-$24.88').
           Multiple symbols ('$$24.88', '$24.88$') are rejected.
        3. Grouping separators (commas): Only valid in the integer part when grouping 3 digits ('1,240.50').
           Malformed groupings ('2,,4.88', ',24.88', '24,88.00', '24,') are rejected.
        4. Fractional precision & exact representation:
           - Zero-decimal currencies (JPY) reject any decimal places.
           - Multi-decimal currencies (USD/EUR) reject values that cannot be represented
             exactly in the configured minor unit precision (e.g. '22.501', '22.505').
           - Never silently rounds a financial price.
        5. Zero arbitrary character stripping: Malformed or unparseable inputs raise ValueError.
        """
        if amount is None:
            raise ValueError("Monetary amount cannot be None")

        if isinstance(amount, (int, Decimal)):
            amount_str = str(amount)
        elif isinstance(amount, str):
            amount_str = amount
        else:
            raise TypeError(f"Unsupported amount type: {type(amount).__name__}")

        raw = amount_str.strip()
        if not raw:
            raise ValueError("Cannot parse empty currency string")

        # 1. Negative sign validation (must be strictly leading)
        is_negative = False
        if raw.startswith("-"):
            is_negative = True
            raw = raw[1:].strip()
            if raw.startswith("-") or "-" in raw:
                raise ValueError(f"Invalid monetary format: repeated or misplaced minus sign in '{amount_str}'")
        elif "-" in raw:
            raise ValueError(f"Invalid monetary format: misplaced minus sign in '{amount_str}'")

        # 2. Currency symbol validation
        symbol_count = raw.count(self.symbol)
        if symbol_count > 1:
            raise ValueError(f"Invalid monetary format: multiple currency symbols in '{amount_str}'")
        elif symbol_count == 1:
            if not raw.startswith(self.symbol):
                raise ValueError(f"Invalid monetary format: misplaced currency symbol in '{amount_str}'")
            raw = raw[len(self.symbol):].strip()

        if not raw:
            raise ValueError(f"No numeric digits found in '{amount_str}'")

        # 3. Decimal and integer component separation
        if "." in raw:
            parts = raw.split(".")
            if len(parts) != 2:
                raise ValueError(f"Invalid monetary format: multiple decimal points in '{amount_str}'")
            int_part, dec_part = parts[0], parts[1]
            if not dec_part or not dec_part.isdigit():
                raise ValueError(f"Invalid decimal component in '{amount_str}'")
        else:
            int_part = raw
            dec_part = None

        # 4. Integer part and comma grouping validation
        if not int_part:
            int_num = 0
        else:
            if "," in int_part:
                if not re.fullmatch(r"\d{1,3}(,\d{3})+", int_part):
                    raise ValueError(f"Invalid grouping separator in '{amount_str}'")
                int_num = int(int_part.replace(",", ""))
            else:
                if not int_part.isdigit():
                    raise ValueError(f"Invalid integer component in '{amount_str}'")
                int_num = int(int_part)

        # 5. Exact precision validation using Decimal
        if self.minor_unit_precision == 0:
            if dec_part is not None:
                raise ValueError(
                    f"Zero-decimal currency {self.code} cannot have fractional decimal places: '{amount_str}'"
                )
            val = int_num
        else:
            if dec_part is not None:
                if len(dec_part) > self.minor_unit_precision:
                    excess = dec_part[self.minor_unit_precision:]
                    if any(c != "0" for c in excess):
                        raise ValueError(
                            f"Exact decimal error: amount '{amount_str}' provides {len(dec_part)} decimal places with "
                            f"non-zero fractional digits exceeding exact minor unit precision of {self.minor_unit_precision} for {self.code}"
                        )
                    significant_dec = dec_part[:self.minor_unit_precision]
                    dec_num = int(significant_dec)
                else:
                    dec_num = int(dec_part.ljust(self.minor_unit_precision, "0"))
            else:
                dec_num = 0
            val = int_num * (10 ** self.minor_unit_precision) + dec_num

        return -val if is_negative else val


def assert_same_currency(c1: CurrencyConfig, c2: CurrencyConfig) -> None:
    """Assert two currency contexts are strictly identical before mathematical comparison."""
    if c1.currency_id != c2.currency_id or c1.code != c2.code:
        raise ValueError(
            f"Currency mismatch error: cannot compare or operate between {c1.code} "
            f"(ID {c1.currency_id}) and {c2.code} (ID {c2.currency_id})"
        )
