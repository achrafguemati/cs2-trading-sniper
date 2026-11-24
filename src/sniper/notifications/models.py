"""Data models for multi-channel notification infrastructure."""

from __future__ import annotations

from enum import Enum
from typing import Any
from pydantic import BaseModel, Field


class ChannelType(str, Enum):
    """Supported notification transport channels."""
    DISCORD = "discord"
    TELEGRAM = "telegram"
    EMAIL = "email"
    MESSENGER = "messenger"


class DeliveryStatus(str, Enum):
    """Delivery status outcome for a notification channel."""
    DELIVERED = "delivered"
    FAILED = "failed"
    SKIPPED = "skipped"
    UNAVAILABLE = "unavailable"


class AlertPayload(BaseModel):
    """Payload representing an actionable SELL alert for notification formatting."""
    alert_id: int | None = None
    item_id: int | None = None
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


class NotificationResult(BaseModel):
    """Result of attempting delivery across a notification channel."""
    channel: str
    status: DeliveryStatus
    attempts: int = 1
    last_error: str | None = None
    delivered_at: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
