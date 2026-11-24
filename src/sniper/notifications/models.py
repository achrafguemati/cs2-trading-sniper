"""Data models for multi-channel notification infrastructure."""

from __future__ import annotations

from enum import Enum
from typing import Any
from pydantic import BaseModel, Field


class ChannelType(str, Enum):
    """Supported notification transport channels (Discord + Telegram only)."""
    DISCORD = "discord"
    TELEGRAM = "telegram"


class DeliveryStatus(str, Enum):
    """Delivery status outcome for a notification channel."""
    DELIVERED = "delivered"
    FAILED = "failed"
    SKIPPED = "skipped"


class AlertPayload(BaseModel):
    """Payload representing an actionable SELL alert for notification formatting."""
    alert_id: int | None = None
    alert_fingerprint: str | None = None
    item_id: int | None = None
    currency_id: int = 1
    market_hash_name: str
    exterior: str | None = None
    currency_code: str = "USD"
    currency_symbol: str = "$"
    highest_buy_order_minor: int
    lowest_sell_order_minor: int | None = None
    top_level_buy_quantity: int
    cumulative_buy_quantity: int
    modeled_steam_fee_minor: int
    modeled_publisher_fee_minor: int
    modeled_seller_receives_minor: int
    historical_baseline_minor: int | None = None
    bid_multiple: float | None = None
    opportunity_score: float = 0.0
    confidence_score: float = 0.0
    warning_tags: list[str] = Field(default_factory=list)
    status: str = "Candidate"
    created_at: str | None = None
    steam_listing_url: str | None = None

    def formatted_bid_gross(self) -> str:
        """Format gross buy order price with currency symbol."""
        cents = self.highest_buy_order_minor
        return f"{self.currency_symbol}{cents / 100:.2f}"

    def formatted_seller_net(self) -> str:
        """Format seller net proceeds with currency symbol."""
        cents = self.modeled_seller_receives_minor
        return f"{self.currency_symbol}{cents / 100:.2f}"

    def formatted_fees(self) -> str:
        """Format total modeled fees."""
        total_fees = self.modeled_steam_fee_minor + self.modeled_publisher_fee_minor
        return f"{self.currency_symbol}{total_fees / 100:.2f} (Steam: {self.currency_symbol}{self.modeled_steam_fee_minor / 100:.2f}, CS2: {self.currency_symbol}{self.modeled_publisher_fee_minor / 100:.2f})"

    def formatted_lowest_ask(self) -> str:
        """Format lowest ask if available."""
        if self.lowest_sell_order_minor is None:
            return "None (Empty Orderbook)"
        return f"{self.currency_symbol}{self.lowest_sell_order_minor / 100:.2f}"

    def formatted_baseline(self) -> str:
        """Format historical median baseline."""
        if self.historical_baseline_minor is None:
            return "N/A (Insufficient Data)"
        return f"{self.currency_symbol}{self.historical_baseline_minor / 100:.2f}"


def _fmt_minor(minor: int, symbol: str, precision: int = 2) -> str:
    """Format integer minor units deterministically without binary float loss."""
    neg = minor < 0
    abs_u = abs(minor)
    if precision <= 0:
        formatted = f"{symbol}{abs_u}"
    else:
        div = 10 ** precision
        formatted = f"{symbol}{abs_u // div}.{abs_u % div:0{precision}d}"
    return f"-{formatted}" if neg else formatted


class BuyAlertPayload(BaseModel):
    """Payload representing an actionable BUY CANDIDATE alert (M6)."""
    alert_category: str = "BUY"
    alert_id: int | None = None
    alert_fingerprint: str | None = None
    item_id: int | None = None
    currency_id: int = 1
    minor_unit_precision: int = 2
    market_hash_name: str
    exterior: str | None = None
    steam_listing_id: str
    asset_id: str | None = None
    currency_code: str = "USD"
    currency_symbol: str = "$"
    purchase_buyer_pays_minor: int
    reference_price_minor: int
    baseline_vwm_minor: int | None = None
    floor_median_minor: int | None = None
    discount_pct: float = 0.0
    conservative_exit_gross_minor: int
    conservative_exit_net_minor: int
    conservative_net_profit_minor: int
    conservative_roi_pct: float
    base_net_profit_minor: int | None = None
    optimistic_net_profit_minor: int | None = None
    float_value: float | None = None
    paint_seed: int | None = None
    stickers: list[str] = Field(default_factory=list)
    opportunity_score: float = 0.0
    confidence_score: float = 0.0
    warning_tags: list[str] = Field(default_factory=list)
    status: str = "Candidate"
    created_at: str | None = None
    steam_listing_url: str | None = None
    inspect_link: str | None = None

    def formatted_buy_price(self) -> str:
        return _fmt_minor(self.purchase_buyer_pays_minor, self.currency_symbol, self.minor_unit_precision)

    def formatted_reference(self) -> str:
        return _fmt_minor(self.reference_price_minor, self.currency_symbol, self.minor_unit_precision)

    def formatted_conservative_exit_gross(self) -> str:
        return _fmt_minor(self.conservative_exit_gross_minor, self.currency_symbol, self.minor_unit_precision)

    def formatted_conservative_exit_net(self) -> str:
        return _fmt_minor(self.conservative_exit_net_minor, self.currency_symbol, self.minor_unit_precision)

    def formatted_conservative_profit(self) -> str:
        sign = "+" if self.conservative_net_profit_minor > 0 else ""
        return f"{sign}{_fmt_minor(self.conservative_net_profit_minor, self.currency_symbol, self.minor_unit_precision)}"


class PortfolioAlertPayload(BaseModel):
    """Payload representing a 7-day Portfolio unlock (RESALE READY) or Under Water alert (M7)."""
    alert_category: str = "PORTFOLIO"
    alert_type: str = "portfolio_resale_ready"  # 'portfolio_resale_ready' | 'portfolio_under_water'
    alert_id: int | None = None
    alert_fingerprint: str | None = None
    item_id: int | None = None
    holding_id: int
    currency_id: int = 1
    minor_unit_precision: int = 2
    market_hash_name: str
    currency_code: str = "USD"
    currency_symbol: str = "$"
    acquisition_cost_minor: int
    breakeven_buyer_pays_minor: int
    recommended_list_gross_minor: int
    expected_net_proceeds_minor: int
    expected_profit_minor: int
    expected_roi_pct: float
    days_held: int = 7
    status: str = "RESALE READY"
    created_at: str | None = None
    steam_listing_url: str | None = None

    def formatted_cost(self) -> str:
        return _fmt_minor(self.acquisition_cost_minor, self.currency_symbol, self.minor_unit_precision)

    def formatted_breakeven(self) -> str:
        return _fmt_minor(self.breakeven_buyer_pays_minor, self.currency_symbol, self.minor_unit_precision)

    def formatted_recommended_list(self) -> str:
        return _fmt_minor(self.recommended_list_gross_minor, self.currency_symbol, self.minor_unit_precision)

    def formatted_expected_net(self) -> str:
        return _fmt_minor(self.expected_net_proceeds_minor, self.currency_symbol, self.minor_unit_precision)

    def formatted_expected_profit(self) -> str:
        sign = "+" if self.expected_profit_minor > 0 else ""
        return f"{sign}{_fmt_minor(self.expected_profit_minor, self.currency_symbol, self.minor_unit_precision)}"


class NotificationResult(BaseModel):
    """Result of attempting delivery across a notification channel."""
    channel: str
    status: DeliveryStatus
    attempts: int = 1
    last_error: str | None = None
    delivered_at: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
