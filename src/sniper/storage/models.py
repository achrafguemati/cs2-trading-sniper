"""Data transfer objects and domain models for SQLite storage layer."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from pydantic import BaseModel, Field, field_validator

from sniper.core.currency import CurrencyConfig
from sniper.core.identity import ItemIdentityResult, ResolutionStatus


def current_utc_iso() -> str:
    """Return current UTC timestamp in ISO-8601 format."""
    return datetime.now(timezone.utc).isoformat()


class ItemRecord(BaseModel):
    """Normalized CS2 market item record with provenance tracking."""
    item_id: int | None = None
    appid: int = 730
    market_hash_name: str
    base_name: str
    exterior: str | None = None
    is_stattrak: bool = False
    is_souvenir: bool = False
    group_id: str | None = None
    item_nameid: int | None = None
    item_nameid_source: str | None = None
    item_nameid_revision: str | None = None
    classid: str | None = None
    classid_source: str | None = None
    classid_revision: str | None = None
    first_seen_at: str = Field(default_factory=current_utc_iso)
    updated_at: str = Field(default_factory=current_utc_iso)

    @field_validator("market_hash_name")
    @classmethod
    def validate_market_hash_name(cls, v: str) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("market_hash_name must not be empty or blank.")
        return v.strip()

    @field_validator("base_name")
    @classmethod
    def validate_base_name(cls, v: str) -> str:
        if not isinstance(v, str) or not v.strip():
            raise ValueError("base_name must not be empty or blank.")
        return v.strip()

    @classmethod
    def from_identity_result(
        cls,
        identity: ItemIdentityResult,
        first_seen_at: str | None = None,
        updated_at: str | None = None,
    ) -> ItemRecord:
        """Create an ItemRecord from an ItemIdentityResult."""
        if identity.resolution_status == ResolutionStatus.PARSED_URL_ONLY:
            raise ValueError(
                "Cannot construct ItemRecord from PARSED_URL_ONLY identity result. "
                "Canonical market_hash_name resolution is required."
            )
        if not identity.market_hash_name or not identity.market_hash_name.strip():
            raise ValueError(
                "Cannot construct ItemRecord with an empty or blank market_hash_name."
            )
        now = current_utc_iso()
        return cls(
            appid=identity.appid,
            market_hash_name=identity.market_hash_name,
            base_name=identity.base_name,
            exterior=identity.exterior.value if identity.exterior else None,
            is_stattrak=identity.is_stattrak,
            is_souvenir=identity.is_souvenir,
            group_id=identity.group_id,
            item_nameid=identity.item_nameid,
            item_nameid_source=identity.item_nameid_source,
            item_nameid_revision=identity.item_nameid_revision,
            classid=identity.classid,
            classid_source=identity.classid_source,
            classid_revision=identity.classid_revision,
            first_seen_at=first_seen_at or now,
            updated_at=updated_at or now,
        )


class CurrencyRecord(BaseModel):
    """Storage model for currency configuration and verification states."""
    currency_id: int
    code: str
    symbol: str
    minor_unit_precision: int
    country: str = "US"
    min_listing_price_minor: int | None = None  # None indicates UNKNOWN
    price_step_minor: int = 1
    min_fee_steam_minor: int = 1
    min_fee_publisher_minor: int = 1
    is_metadata_verified: bool = False
    is_fee_verified: bool = False
    is_listing_min_verified: bool = False
    is_price_step_verified: bool = False
    notes: str = ""
    updated_at: str = Field(default_factory=current_utc_iso)

    @classmethod
    def from_currency_config(
        cls,
        config: CurrencyConfig,
        updated_at: str | None = None,
    ) -> CurrencyRecord:
        """Create a CurrencyRecord from a CurrencyConfig domain object."""
        return cls(
            currency_id=config.currency_id,
            code=config.code,
            symbol=config.symbol,
            minor_unit_precision=config.minor_unit_precision,
            country=config.country,
            min_listing_price_minor=config.min_listing_price_minor,
            price_step_minor=config.price_step_minor,
            min_fee_steam_minor=config.min_fee_steam_minor,
            min_fee_publisher_minor=config.min_fee_publisher_minor,
            is_metadata_verified=config.is_metadata_verified,
            is_fee_verified=config.is_fee_verified,
            is_listing_min_verified=config.is_listing_min_verified,
            is_price_step_verified=config.is_price_step_verified,
            notes=config.notes,
            updated_at=updated_at or current_utc_iso(),
        )


class ListingRecord(BaseModel):
    """Immutable market listing observation."""
    listing_id: int | None = None
    steam_listing_id: str
    item_id: int
    price_minor: int
    fee_minor: int | None = None
    steam_fee_minor: int | None = None
    publisher_fee_minor: int | None = None
    currency_id: int
    observed_at: str = Field(default_factory=current_utc_iso)
    source: str = "render"
    status: str = "active"
    asset_id: str | None = None


class OrderbookSnapshotRecord(BaseModel):
    """Immutable orderbook depth and price snapshot."""
    snapshot_id: int | None = None
    item_id: int
    currency_id: int
    highest_buy_order_minor: int | None = None
    lowest_sell_order_minor: int | None = None
    buy_order_count: int | None = None
    sell_order_count: int | None = None
    top_level_buy_quantity: int | None = None
    cumulative_buy_quantity: int | None = None
    buy_graph_json: str | None = None
    sell_graph_json: str | None = None
    observed_at: str = Field(default_factory=current_utc_iso)
    source_payload_version: str = "histogram_v1"


class InventorySnapshotRecord(BaseModel):
    """Inventory snapshot import event."""
    snapshot_id: int | None = None
    owner_id: str | None = None
    total_count: int = 0
    observed_at: str = Field(default_factory=current_utc_iso)
    source: str = "public_inventory"


class InventoryItemRecord(BaseModel):
    """Inventory item observation belonging to a snapshot."""
    inventory_item_id: int | None = None
    snapshot_id: int
    asset_id: str
    class_id: str
    instance_id: str = "0"
    market_hash_name: str
    amount: int = 1
    is_marketable: bool = False
    is_tradable: bool = False
    market_restriction_days: int | None = None
    item_id: int | None = None


class PortfolioTransactionRecord(BaseModel):
    """Manual portfolio acquisition or disposal transaction."""
    transaction_id: int | None = None
    item_id: int
    transaction_type: str  # 'BUY' | 'SELL'
    quantity: int
    unit_price_minor: int
    total_fees_minor: int = 0
    currency_id: int
    timestamp: str = Field(default_factory=current_utc_iso)
    cost_basis_metadata: str | None = None
    notes: str | None = None


class PortfolioSummary(BaseModel):
    """Portfolio accounting summary computed using exact integer arithmetic."""
    item_id: int | None = None
    currency_id: int
    total_bought_quantity: int = 0
    total_sold_quantity: int = 0
    net_quantity: int = 0
    total_acquisition_cost_minor: int = 0
    total_fees_paid_minor: int = 0
    total_realized_proceeds_minor: int = 0
