"""Explicit currency model and minor-unit financial formatting."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar


@dataclass(frozen=True)
class CurrencyConfig:
    """Explicit currency configuration model.
    
    Steam assigns numeric IDs to currencies (e.g. 1=USD, 3=EUR, 8=JPY).
    Minor unit precision indicates decimal places (e.g. 2 for USD/EUR, 0 for JPY).
    """
    currency_id: int
    code: str
    symbol: str
    minor_unit_precision: int
    country: str = "US"

    KNOWN_CURRENCIES: ClassVar[dict[int, CurrencyConfig]] = {}

    @classmethod
    def get_default_usd(cls) -> CurrencyConfig:
        return cls(currency_id=1, code="USD", symbol="$", minor_unit_precision=2, country="US")

    @classmethod
    def get_eur(cls) -> CurrencyConfig:
        return cls(currency_id=3, code="EUR", symbol="€", minor_unit_precision=2, country="DE")

    @classmethod
    def get_jpy(cls) -> CurrencyConfig:
        return cls(currency_id=8, code="JPY", symbol="¥", minor_unit_precision=0, country="JP")

    def format_amount(self, minor_units: int) -> str:
        """Format integer minor units into human readable currency string."""
        if self.minor_unit_precision == 0:
            return f"{self.symbol}{minor_units}"
        divisor = 10 ** self.minor_unit_precision
        integer_part = minor_units // divisor
        remainder = abs(minor_units % divisor)
        return f"{self.symbol}{integer_part}.{remainder:0{self.minor_unit_precision}d}"

    def parse_to_minor_units(self, amount_str: str) -> int:
        """Parse clean amount string (e.g. '24.88' or '$24.88') into integer minor units."""
        cleaned = amount_str.strip().replace(self.symbol, "").replace(",", "").strip()
        if not cleaned:
            raise ValueError("Empty currency string")
        
        if self.minor_unit_precision == 0:
            return int(float(cleaned))
            
        parts = cleaned.split(".")
        if len(parts) == 1:
            return int(parts[0]) * (10 ** self.minor_unit_precision)
        elif len(parts) == 2:
            int_part = int(parts[0])
            dec_str = parts[1][:self.minor_unit_precision].ljust(self.minor_unit_precision, "0")
            dec_part = int(dec_str)
            return int_part * (10 ** self.minor_unit_precision) + dec_part
        else:
            raise ValueError(f"Invalid monetary format: {amount_str}")


def assert_same_currency(c1: CurrencyConfig, c2: CurrencyConfig) -> None:
    """Assert two currency contexts are strictly identical before mathematical comparison."""
    if c1.currency_id != c2.currency_id or c1.code != c2.code:
        raise ValueError(
            f"Currency mismatch error: cannot compare or operate between {c1.code} "
            f"(ID {c1.currency_id}) and {c2.code} (ID {c2.currency_id})"
        )
