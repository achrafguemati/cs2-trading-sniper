"""Pydantic schemas for Steam Community Market responses and SSR hydration payloads."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from sniper.core.currency import CurrencyConfig


class ListingAssetPointer(BaseModel):
    id: str
    appid: int = 730
    contextid: str = "2"


class ListingInfoItem(BaseModel):
    listingid: str
    price: int = 0
    fee: int = 0
    steam_fee: int = 0
    publisher_fee: int = 0
    currencyid: int | None = None
    converted_currencyid: int | None = None
    converted_price: int | None = None
    converted_fee: int | None = None
    converted_steam_fee: int | None = None
    converted_publisher_fee: int | None = None
    converted_price_per_unit: int | None = None
    converted_fee_per_unit: int | None = None
    asset: ListingAssetPointer


class AssetDescriptionItem(BaseModel):
    type: str | None = None
    value: str
    color: str | None = None


class AssetMarketAction(BaseModel):
    link: str
    name: str


class AssetPropertyItem(BaseModel):
    """May 2026 Steam asset property entry (float rating, paint seed, finish index)."""
    propertyid: int | None = None
    name: str | None = None
    float_value: str | float | None = None
    int_value: str | int | None = None
    string_value: str | None = None


class AssetInfoItem(BaseModel):
    id: str
    classid: str
    instanceid: str
    name: str
    market_hash_name: str
    appid: int = 730
    descriptions: list[AssetDescriptionItem] = Field(default_factory=list)
    market_actions: list[AssetMarketAction] = Field(default_factory=list)
    asset_properties: list[AssetPropertyItem] = Field(default_factory=list)


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
    """Depth entry using exact Decimal representation for orderbook price."""
    price: Decimal | int | str
    cumulative_quantity: int
    description: str


class NormalizedDepthEntry(BaseModel):
    """Normalized depth entry from buy_order_graph or sell_order_graph.
    
    Preserves raw response value and converts to integer minor units strictly
    without binary float arithmetic.
    If currency context is missing or unverified, price_minor is None (UNKNOWN)
    and financial comparison is prohibited.
    """
    raw_price: Any
    cumulative_quantity: int
    description: str
    price_minor: int | None = None
    currency_code: str | None = None
    is_currency_verified: bool = False

    def require_price_minor(self) -> int:
        """Return price_minor if verified, else raise ValueError prohibiting financial comparison."""
        if self.price_minor is None or not self.is_currency_verified:
            raise ValueError(
                f"Financial comparison prohibited: price_minor is UNKNOWN for raw price "
                f"'{self.raw_price}' (currency: {self.currency_code}, verified: {self.is_currency_verified})"
            )
        return self.price_minor


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

    def normalize_graph_entry(
        self,
        entry: list[Any],
        currency: CurrencyConfig | None = None,
    ) -> NormalizedDepthEntry:
        """Normalize a single graph entry [raw_price, quantity, description].
        
        Converts exact Decimal price to integer minor units strictly without binary float arithmetic.
        Rejects non-positive prices and values that cannot be represented exactly in minor units (e.g. 22.501).
        Accepts trailing zeroes representing the exact same minor unit amount (e.g. 22.500 -> 2250).
        Validates quantity as non-negative integer without silently truncating fractional quantities.
        If currency metadata or minor-unit precision is unverified, marks price_minor as None (UNKNOWN).
        Listing minimums and listing price steps are intentionally not required for orderbook normalization.
        """
        if len(entry) < 3:
            raise ValueError(f"Malformed graph entry: {entry}")

        raw_price = entry[0]
        raw_qty = entry[1]
        desc = str(entry[2])

        # Validate quantity: must be non-negative integer without silent fractional truncation
        if isinstance(raw_qty, bool):
            raise ValueError(f"Invalid graph quantity: {raw_qty}. Booleans cannot be quantities.")
        elif isinstance(raw_qty, int):
            if raw_qty < 0:
                raise ValueError(f"Invalid graph quantity: {raw_qty}. Quantity must be non-negative.")
            qty = raw_qty
        elif isinstance(raw_qty, (Decimal, float, str)):
            try:
                d_qty = Decimal(str(raw_qty).strip())
            except Exception as e:
                raise ValueError(f"Invalid graph quantity '{raw_qty}': could not parse as number.") from e
            if d_qty < 0:
                raise ValueError(f"Invalid graph quantity: {d_qty}. Quantity must be non-negative.")
            if d_qty != d_qty.to_integral_value():
                raise ValueError(
                    f"Invalid graph quantity: {raw_qty}. Fractional quantities cannot be silently truncated."
                )
            qty = int(d_qty)
        else:
            raise TypeError(f"Unsupported graph quantity type: {type(raw_qty).__name__}")

        # Validate price: convert to Decimal and ensure strictly positive
        if isinstance(raw_price, bool):
            raise ValueError(f"Invalid orderbook price: {raw_price}. Booleans cannot be prices.")
        elif isinstance(raw_price, float):
            d = Decimal(str(raw_price))
        elif isinstance(raw_price, (Decimal, int)):
            d = Decimal(raw_price)
        elif isinstance(raw_price, str):
            try:
                d = Decimal(raw_price.strip())
            except Exception as e:
                raise ValueError(f"Invalid orderbook price: '{raw_price}'. Non-numeric string.") from e
        else:
            raise TypeError(f"Unsupported price token type: {type(raw_price).__name__}")

        if d <= 0:
            raise ValueError(f"Invalid orderbook price: {d}. Orderbook prices must be strictly positive.")

        precision = currency.minor_unit_precision if currency is not None else None
        if (
            currency is None
            or not currency.is_metadata_verified
            or isinstance(precision, bool)
            or not isinstance(precision, int)
            or precision < 0
        ):
            return NormalizedDepthEntry(
                raw_price=raw_price,
                cumulative_quantity=qty,
                description=desc,
                price_minor=None,
                currency_code=currency.code if currency else None,
                is_currency_verified=False,
            )

        price_minor = currency.parse_to_minor_units(d)

        return NormalizedDepthEntry(
            raw_price=raw_price,
            cumulative_quantity=qty,
            description=desc,
            price_minor=price_minor,
            currency_code=currency.code,
            is_currency_verified=True,
        )

    def get_normalized_buy_graph(
        self,
        currency: CurrencyConfig | None = None,
    ) -> list[NormalizedDepthEntry]:
        """Return all buy order graph entries normalized to integer minor units."""
        return [self.normalize_graph_entry(e, currency) for e in self.buy_order_graph]

    def get_normalized_sell_graph(
        self,
        currency: CurrencyConfig | None = None,
    ) -> list[NormalizedDepthEntry]:
        """Return all sell order graph entries normalized to integer minor units."""
        return [self.normalize_graph_entry(e, currency) for e in self.sell_order_graph]


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
    appid: int
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
    more_items: int | bool | None = None
    last_assetid: str | None = None
