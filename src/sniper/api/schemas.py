"""Strict Pydantic API request & response contracts (Milestone 8).

Never exposes raw Steam credentials or notification secrets (`DISCORD_WEBHOOK_URL`, `TELEGRAM_BOT_TOKEN`).
"""

from __future__ import annotations

from typing import Any
from pydantic import BaseModel, Field, field_validator


class WatchlistCreateRequest(BaseModel):
    url: str
    strategy: str = "BUY"
    currency_id: int = 1
    max_buy_price_minor: int | None = None
    min_discount_pct: float | None = None
    min_net_profit_minor: int | None = None
    min_roi_pct: float = 5.0
    min_sell_multiplier: float = 1.5
    min_bid_minor: int = 100
    poll_interval_seconds: int = 300
    notes: str | None = None

    @field_validator("strategy")
    @classmethod
    def check_strategy(cls, v: str) -> str:
        up = v.strip().upper()
        if up not in ("BUY", "SELL", "BOTH"):
            raise ValueError("strategy must be BUY, SELL, or BOTH.")
        return up


class WatchlistToggleRequest(BaseModel):
    is_active: bool


class HistoryImportRequest(BaseModel):
    market_hash_name: str
    currency_id: int = 1
    points: list[dict[str, Any]]


class SteamInventoryImportRequest(BaseModel):
    steamid64: str
    count: int = 5000
    max_pages: int = 10


class ManualInventoryImportRequest(BaseModel):
    format: str = "csv"  # 'csv' | 'json'
    content: str
    owner_id: str = "manual_user"


class HoldingCreateRequest(BaseModel):
    market_hash_name: str
    acquisition_cost_minor: int
    currency_id: int = 1
    quantity: int = 1
    restriction_days: int | None = 7
    marketable_at: str | None = None
    asset_id: str | None = None
    notes: str | None = None


class HoldingSellRequest(BaseModel):
    gross_sale_price_minor: int
    notes: str | None = None


class NotificationTestRequest(BaseModel):
    channel: str = "all"  # 'all' | 'discord' | 'telegram'


class BreakerResetRequest(BaseModel):
    confirm_human_review: bool = False


class AppSettingsUpdateRequest(BaseModel):
    default_currency_id: int = 1
    requests_per_minute: float = 12.0
    cycle_interval_seconds: float = 300.0
    min_roi_pct: float = 5.0
    min_sell_multiplier: float = 1.5
    min_bid_minor: int = 100
    cooldown_seconds: int = 1800
    scheduler_enabled: bool = True

    @field_validator("requests_per_minute")
    @classmethod
    def clamp_rpm(cls, v: float) -> float:
        if v <= 0 or v > 12.0:
            raise ValueError("requests_per_minute must remain within (0, 12.0] safety limit.")
        return float(v)
