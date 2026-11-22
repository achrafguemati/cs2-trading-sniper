"""Pydantic schemas for Steam Community Market responses and SSR hydration payloads."""

from __future__ import annotations

from typing import Any
from pydantic import BaseModel, Field, field_validator


class ListingAssetPointer(BaseModel):
    id: str
    appid: int = 730
    contextid: str = "2"


class ListingInfoItem(BaseModel):
    listingid: str
    price: int
    fee: int
    steam_fee: int
    publisher_fee: int
    converted_price: int | None = None
    converted_fee: int | None = None
    converted_steam_fee: int | None = None
    converted_publisher_fee: int | None = None
    asset: ListingAssetPointer


class AssetDescriptionItem(BaseModel):
    type: str | None = None
    value: str
    color: str | None = None


class AssetMarketAction(BaseModel):
    link: str
    name: str


class AssetInfoItem(BaseModel):
    id: str
    classid: str
    instanceid: str
    name: str
    market_hash_name: str
    descriptions: list[AssetDescriptionItem] = Field(default_factory=list)
    market_actions: list[AssetMarketAction] = Field(default_factory=list)


class ListingRenderResponse(BaseModel):
    """Schema for /market/listings/{appid}/{market_hash_name}/render/ response."""
    success: bool
    start: int = 0
    pagesize: int = 0
    total_count: int = 0
    results_html: str | None = None
    listinginfo: dict[str, ListingInfoItem] = Field(default_factory=dict)
    assets: dict[str, dict[str, dict[str, AssetInfoItem]]] = Field(default_factory=dict)


class HistogramDepthEntry(BaseModel):
    price: float
    cumulative_quantity: int
    description: str


class OrderbookHistogramResponse(BaseModel):
    """Schema for /market/itemordershistogram response."""
    success: int | bool
    sell_order_count: str | None = None
    sell_order_price: str | None = None
    buy_order_count: str | None = None
    buy_order_price: str | None = None
    highest_buy_order: str | None = None
    lowest_sell_order: str | None = None
    buy_order_graph: list[list[Any]] = Field(default_factory=list)
    sell_order_graph: list[list[Any]] = Field(default_factory=list)
    buy_order_table: str | None = None
    sell_order_table: str | None = None

    @property
    def highest_buy_order_minor(self) -> int | None:
        """Parse highest_buy_order string (e.g. '2250') into integer minor units."""
        if self.highest_buy_order:
            try:
                return int(self.highest_buy_order)
            except ValueError:
                return None
        return None

    @property
    def lowest_sell_order_minor(self) -> int | None:
        """Parse lowest_sell_order string (e.g. '2488') into integer minor units."""
        if self.lowest_sell_order:
            try:
                return int(self.lowest_sell_order)
            except ValueError:
                return None
        return None

    @property
    def top_level_buy_quantity(self) -> int:
        """Extract exact quantity at top bid level from buy_order_graph."""
        if self.buy_order_graph and len(self.buy_order_graph) > 0:
            top = self.buy_order_graph[0]
            if len(top) >= 2 and isinstance(top[1], int):
                return top[1]
        return 0

    @property
    def cumulative_buy_quantity(self) -> int:
        """Parse total buy_order_count string (e.g. '211,731') into integer."""
        if self.buy_order_count:
            cleaned = self.buy_order_count.replace(",", "").strip()
            try:
                return int(cleaned)
            except ValueError:
                return 0
        return 0


class SsrBucketItem(BaseModel):
    bucket_id: str
    localized_name: str
    min_price: str
    classid: str
    localized_name_inside_group: str | None = None
    strPrice: str | None = None
    filters: list[list[str]] = Field(default_factory=list)

    @property
    def min_price_minor(self) -> int:
        try:
            return int(self.min_price)
        except ValueError:
            return 0


class SsrListingLoaderData(BaseModel):
    """Schema for loaderData[3] in modern 2026 SSR hydration payload."""
    success: bool
    appid: int = 730
    buckets: list[SsrBucketItem] = Field(default_factory=list)
    initialSelectedBucketID: str | None = None
    initialFallbackBucketID: str | None = None
    relevantAssetProperties: dict[str, Any] = Field(default_factory=dict)
    bCommodity: bool = False


class InventoryAssetItem(BaseModel):
    appid: int = 730
    contextid: str = "2"
    assetid: str
    classid: str
    instanceid: str
    amount: str = "1"


class InventoryDescriptionItem(BaseModel):
    appid: int = 730
    classid: str
    instanceid: str
    name: str
    market_hash_name: str
    marketable: int = 0
    tradable: int = 0
    market_marketable_restriction: int | None = None
    descriptions: list[dict[str, Any]] = Field(default_factory=list)


class PublicInventoryResponse(BaseModel):
    """Schema for /inventory/{steamid64}/730/2 response."""
    assets: list[InventoryAssetItem] = Field(default_factory=list)
    descriptions: list[InventoryDescriptionItem] = Field(default_factory=list)
    total_inventory_count: int | None = None
    success: int | bool = 1
