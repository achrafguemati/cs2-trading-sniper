"""Listing-level Steam render parser, metadata extractor, and cookie-free chart extractor (M6)."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
import html
from html.parser import HTMLParser
import json
import re
from typing import Any

from pydantic import BaseModel, Field

from sniper.core.currency import CurrencyConfig
from sniper.core.identity import WearCategory
from sniper.models.schemas import AssetInfoItem, ListingInfoItem, ListingRenderResponse

_NOTABLE_STICKER_KEYWORDS = (
    "katowice 2014",
    "katowice 2015",
    "crown",
    "howling dawn",
    "ibuypower",
    "titan",
    "reason gaming",
    "vox eminor",
    "dignitas",
    "(holo)",
    "(foil)",
    "(gold)",
)

_LINE1_REGEX = re.compile(r"var\s+line1\s*=\s*(\[\[.*?\]\])\s*;", re.DOTALL)
_FLOAT_TEXT_REGEX = re.compile(r"(?:float\s*value|wear\s*rating)\s*[:=]\s*([01]\.\d+)", re.IGNORECASE)
_SEED_TEXT_REGEX = re.compile(r"(?:paint\s*seed|pattern\s*template)\s*[:=]\s*(\d+)", re.IGNORECASE)
_PAINT_INDEX_REGEX = re.compile(r"(?:paint\s*index|finish\s*catalog)\s*[:=]\s*(\d+)", re.IGNORECASE)


class _TextStripper(HTMLParser):
    """Strip HTML tags while preserving line breaks cleanly."""

    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in ("br", "p", "div", "li"):
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        self._chunks.append(data)

    def get_text(self) -> str:
        return html.unescape("".join(self._chunks))


def strip_html_text(raw_html: str) -> str:
    """Convert an HTML fragment into plain unescaped text."""
    if not raw_html:
        return ""
    stripper = _TextStripper()
    stripper.feed(raw_html)
    stripper.close()
    return stripper.get_text()


class ParsedListingCard(BaseModel):
    """Normalized, verified active Steam Market individual sell listing."""
    steam_listing_id: str
    asset_id: str | None = None
    class_id: str | None = None
    instance_id: str | None = None
    market_hash_name: str
    currency_id: int
    seller_receives_minor: int
    fee_minor: int
    steam_fee_minor: int | None = None
    publisher_fee_minor: int | None = None
    buyer_pays_minor: int
    price_source: str = "converted"  # 'converted' | 'native'
    float_value: float | None = None
    paint_seed: int | None = None
    paint_index: int | None = None
    is_low_float_tier: bool = False
    stickers: list[str] = Field(default_factory=list)
    charms: list[str] = Field(default_factory=list)
    has_notable_craft: bool = False
    inspect_link: str | None = None
    rejection_reason: str | None = None
    is_valid: bool = True


def _currency_matches(raw_currency_id: int | None, expected_currency_id: int) -> bool:
    """Match Steam's raw or +2000 converted currency IDs against expected Steam currency ID."""
    if raw_currency_id is None:
        return False
    return raw_currency_id == expected_currency_id or raw_currency_id == (2000 + expected_currency_id)


def resolve_inspect_link(asset: AssetInfoItem, steam_listing_id: str) -> str | None:
    """Extract and hydrate csgo_econ_action_preview steam:// inspect link if present."""
    for action in asset.market_actions:
        link = (action.link or "").strip()
        if link.startswith("steam://rungame/730/"):
            return (
                link.replace("%listingid%", steam_listing_id)
                .replace("%assetid%", asset.id)
            )
    return None


def extract_stickers_and_charms(asset: AssetInfoItem) -> tuple[list[str], list[str], bool]:
    """Extract applied stickers, CS2 charms/keychains, and notable craft status from descriptions."""
    stickers: list[str] = []
    charms: list[str] = []

    for desc in asset.descriptions:
        plain = strip_html_text(desc.value)
        for line in plain.splitlines():
            clean = line.strip()
            if not clean:
                continue
            if clean.lower().startswith("sticker:"):
                raw_list = clean[len("sticker:"):].strip()
                for part in raw_list.split(","):
                    st = part.strip()
                    if st and st not in stickers:
                        stickers.append(st)
            elif clean.lower().startswith("charm:") or clean.lower().startswith("keychain:"):
                raw_charm = clean.split(":", 1)[1].strip()
                for part in raw_charm.split(","):
                    ch = part.strip()
                    if ch and ch not in charms:
                        charms.append(ch)

    has_notable = any(
        any(kw in s.lower() for kw in _NOTABLE_STICKER_KEYWORDS)
        for s in stickers
    )
    return stickers, charms, has_notable


def _is_low_float_tier(float_val: float | None, exterior: WearCategory | str | None) -> bool:
    """Determine if an item sits in the collectible low-float range for its exterior."""
    if float_val is None or exterior is None:
        return False
    ext_val = exterior.value if isinstance(exterior, WearCategory) else str(exterior)
    if ext_val == WearCategory.FACTORY_NEW.value:
        return float_val < 0.015
    if ext_val == WearCategory.MINIMAL_WEAR.value:
        return float_val < 0.09
    if ext_val == WearCategory.FIELD_TESTED.value:
        return float_val < 0.18
    return False


def extract_float_and_pattern(
    asset: AssetInfoItem,
) -> tuple[float | None, int | None, int | None]:
    """Extract float_value, paint_seed, and paint_index from asset_properties or descriptions.

    Returns explicit None when unavailable — never fabricates wear floats or seeds.
    """
    float_val: float | None = None
    paint_seed: int | None = None
    paint_index: int | None = None

    for prop in asset.asset_properties:
        prop_name = (prop.name or "").strip().lower()
        if prop.float_value is not None and (
            prop.propertyid == 1 or "wear" in prop_name or "float" in prop_name
        ):
            try:
                parsed_f = float(prop.float_value)
                if 0.0 <= parsed_f <= 1.0:
                    float_val = parsed_f
            except (TypeError, ValueError):
                pass
        elif prop.int_value is not None and (
            prop.propertyid == 2 or "pattern" in prop_name or "seed" in prop_name
        ):
            try:
                parsed_seed = int(prop.int_value)
                if 0 <= parsed_seed <= 1000:
                    paint_seed = parsed_seed
            except (TypeError, ValueError):
                pass
        elif prop.int_value is not None and (
            prop.propertyid == 3 or "finish" in prop_name or "paint index" in prop_name
        ):
            try:
                parsed_idx = int(prop.int_value)
                if parsed_idx >= 0:
                    paint_index = parsed_idx
            except (TypeError, ValueError):
                pass

    if float_val is None or paint_seed is None or paint_index is None:
        for desc in asset.descriptions:
            plain = strip_html_text(desc.value)
            if float_val is None:
                m_f = _FLOAT_TEXT_REGEX.search(plain)
                if m_f:
                    try:
                        parsed_f = float(m_f.group(1))
                        if 0.0 <= parsed_f <= 1.0:
                            float_val = parsed_f
                    except ValueError:
                        pass
            if paint_seed is None:
                m_s = _SEED_TEXT_REGEX.search(plain)
                if m_s:
                    parsed_seed = int(m_s.group(1))
                    if 0 <= parsed_seed <= 1000:
                        paint_seed = parsed_seed
            if paint_index is None:
                m_p = _PAINT_INDEX_REGEX.search(plain)
                if m_p:
                    parsed_idx = int(m_p.group(1))
                    if parsed_idx >= 0:
                        paint_index = parsed_idx

    return float_val, paint_seed, paint_index


def parse_single_listing(
    listing: ListingInfoItem,
    render: ListingRenderResponse,
    expected_market_hash_name: str,
    expected_currency_id: int,
    expected_exterior: WearCategory | str | None = None,
) -> ParsedListingCard:
    """Validate and normalize a single listing entry from ListingRenderResponse."""
    lid = listing.listingid
    asset_id = listing.asset.id if listing.asset else None
    contextid = listing.asset.contextid if listing.asset else "2"

    # 1. Currency & Sold / Price Integrity Check
    if listing.converted_currencyid is not None and not _currency_matches(
        listing.converted_currencyid, expected_currency_id
    ):
        return ParsedListingCard(
            steam_listing_id=lid,
            asset_id=asset_id,
            market_hash_name=expected_market_hash_name,
            currency_id=expected_currency_id,
            seller_receives_minor=0,
            fee_minor=0,
            buyer_pays_minor=0,
            is_valid=False,
            rejection_reason=f"CURRENCY_MISMATCH (converted_currencyid={listing.converted_currencyid})",
        )

    if listing.converted_price is not None:
        if listing.converted_price <= 0:
            return ParsedListingCard(
                steam_listing_id=lid,
                asset_id=asset_id,
                market_hash_name=expected_market_hash_name,
                currency_id=expected_currency_id,
                seller_receives_minor=0,
                fee_minor=0,
                buyer_pays_minor=0,
                is_valid=False,
                rejection_reason="LISTING_SOLD_OR_UNAVAILABLE",
            )
        if listing.converted_fee is None or listing.converted_fee < 0:
            return ParsedListingCard(
                steam_listing_id=lid,
                asset_id=asset_id,
                market_hash_name=expected_market_hash_name,
                currency_id=expected_currency_id,
                seller_receives_minor=0,
                fee_minor=0,
                buyer_pays_minor=0,
                is_valid=False,
                rejection_reason="MISSING_CONVERTED_FEE",
            )
        seller_minor = int(listing.converted_price)
        fee_minor = int(listing.converted_fee)
        steam_fee = listing.converted_steam_fee
        pub_fee = listing.converted_publisher_fee
        price_source = "converted"
    else:
        # Native price allowed ONLY when listing currency is absent (test fixture) or matches target currency
        if listing.currencyid is not None and not _currency_matches(
            listing.currencyid, expected_currency_id
        ):
            return ParsedListingCard(
                steam_listing_id=lid,
                asset_id=asset_id,
                market_hash_name=expected_market_hash_name,
                currency_id=expected_currency_id,
                seller_receives_minor=0,
                fee_minor=0,
                buyer_pays_minor=0,
                is_valid=False,
                rejection_reason=f"CURRENCY_MISMATCH (native currencyid={listing.currencyid} without converted_price)",
            )
        if listing.price <= 0 or listing.fee < 0:
            return ParsedListingCard(
                steam_listing_id=lid,
                asset_id=asset_id,
                market_hash_name=expected_market_hash_name,
                currency_id=expected_currency_id,
                seller_receives_minor=0,
                fee_minor=0,
                buyer_pays_minor=0,
                is_valid=False,
                rejection_reason="LISTING_SOLD_OR_UNAVAILABLE",
            )
        seller_minor = int(listing.price)
        fee_minor = int(listing.fee)
        steam_fee = listing.steam_fee
        pub_fee = listing.publisher_fee
        price_source = "native"

    buyer_pays_minor = seller_minor + fee_minor

    # 2. Asset lookup & exact CS2 identity preservation
    assets_by_ctx = render.assets.get("730", {}).get(contextid, {})
    asset = assets_by_ctx.get(asset_id) if asset_id else None
    if asset is None:
        return ParsedListingCard(
            steam_listing_id=lid,
            asset_id=asset_id,
            market_hash_name=expected_market_hash_name,
            currency_id=expected_currency_id,
            seller_receives_minor=seller_minor,
            fee_minor=fee_minor,
            steam_fee_minor=steam_fee,
            publisher_fee_minor=pub_fee,
            buyer_pays_minor=buyer_pays_minor,
            price_source=price_source,
            is_valid=False,
            rejection_reason="MISSING_ASSET_METADATA",
        )

    if asset.appid != 730:
        return ParsedListingCard(
            steam_listing_id=lid,
            asset_id=asset_id,
            market_hash_name=asset.market_hash_name,
            currency_id=expected_currency_id,
            seller_receives_minor=seller_minor,
            fee_minor=fee_minor,
            buyer_pays_minor=buyer_pays_minor,
            is_valid=False,
            rejection_reason=f"APPID_MISMATCH ({asset.appid})",
        )

    if asset.market_hash_name != expected_market_hash_name:
        return ParsedListingCard(
            steam_listing_id=lid,
            asset_id=asset_id,
            class_id=asset.classid,
            instance_id=asset.instanceid,
            market_hash_name=asset.market_hash_name,
            currency_id=expected_currency_id,
            seller_receives_minor=seller_minor,
            fee_minor=fee_minor,
            buyer_pays_minor=buyer_pays_minor,
            is_valid=False,
            rejection_reason=(
                f"VARIANT_MISMATCH (expected '{expected_market_hash_name}', "
                f"got '{asset.market_hash_name}')"
            ),
        )

    stickers, charms, has_notable = extract_stickers_and_charms(asset)
    float_val, paint_seed, paint_index = extract_float_and_pattern(asset)
    inspect_link = resolve_inspect_link(asset, lid)

    return ParsedListingCard(
        steam_listing_id=lid,
        asset_id=asset.id,
        class_id=asset.classid,
        instance_id=asset.instanceid,
        market_hash_name=asset.market_hash_name,
        currency_id=expected_currency_id,
        seller_receives_minor=seller_minor,
        fee_minor=fee_minor,
        steam_fee_minor=steam_fee,
        publisher_fee_minor=pub_fee,
        buyer_pays_minor=buyer_pays_minor,
        price_source=price_source,
        float_value=float_val,
        paint_seed=paint_seed,
        paint_index=paint_index,
        is_low_float_tier=_is_low_float_tier(float_val, expected_exterior),
        stickers=stickers,
        charms=charms,
        has_notable_craft=has_notable,
        inspect_link=inspect_link,
        is_valid=True,
    )


def parse_listing_render_cards(
    render: ListingRenderResponse,
    expected_market_hash_name: str,
    expected_currency_id: int,
    expected_exterior: WearCategory | str | None = None,
) -> tuple[list[ParsedListingCard], list[ParsedListingCard]]:
    """Split ListingRenderResponse into (valid_cards_sorted_by_buyer_pays, rejected_cards)."""
    valid: list[ParsedListingCard] = []
    rejected: list[ParsedListingCard] = []

    for listing in render.listinginfo.values():
        card = parse_single_listing(
            listing=listing,
            render=render,
            expected_market_hash_name=expected_market_hash_name,
            expected_currency_id=expected_currency_id,
            expected_exterior=expected_exterior,
        )
        if card.is_valid:
            valid.append(card)
        else:
            rejected.append(card)

    valid.sort(key=lambda c: (c.buyer_pays_minor, c.steam_listing_id))
    return valid, rejected


def _parse_steam_chart_date(raw_date: Any) -> str | None:
    """Parse Steam embedded price chart timestamp strings into YYYY-MM-DD."""
    if not isinstance(raw_date, str):
        return None
    cleaned = raw_date.strip()
    if not cleaned:
        return None

    # Format 1: Steam chart date "Nov 10 2026 01: +0"
    m = re.match(r"^([A-Za-z]{3}\s+\d{1,2}\s+\d{4})\b", cleaned)
    if m:
        try:
            dt = datetime.strptime(m.group(1), "%b %d %Y").replace(tzinfo=timezone.utc)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            pass

    # Format 2: YYYY-MM-DD prefix
    if len(cleaned) >= 10 and re.match(r"^\d{4}-\d{2}-\d{2}", cleaned):
        try:
            dt = datetime.strptime(cleaned[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            pass

    return None


def compute_weighted_median_minor(points: list[tuple[int, int]]) -> int:
    """Compute exact integer volume-weighted median from (price_minor, volume) pairs."""
    if not points:
        raise ValueError("Cannot compute weighted median of empty point list.")
    sorted_pts = sorted((int(p), max(1, int(v))) for p, v in points)
    total_weight = sum(v for _, v in sorted_pts)
    cum = 0
    for price, weight in sorted_pts:
        cum += weight
        if (cum * 2) >= total_weight:
            return price
    return sorted_pts[-1][0]


def extract_public_price_history(
    html_content: str,
    currency: CurrencyConfig,
) -> list[tuple[str, int, int]]:
    """Extract cookie-free daily (bucket_date, vwm_minor, total_volume) from public HTML chart series.

    Safely looks for embedded `var line1=[...];` on public Steam listing pages.
    Returns [] cleanly if unavailable on unauthenticated pages — never raises or triggers halt
    merely because Valve omitted historical chart arrays on public HTML.
    """
    if not isinstance(html_content, str) or not html_content:
        return []
    if not currency.is_metadata_verified:
        return []

    m = _LINE1_REGEX.search(html_content)
    if not m:
        return []

    try:
        raw_series = json.loads(m.group(1), parse_float=Decimal)
    except Exception:
        return []

    if not isinstance(raw_series, list):
        return []

    daily_buckets: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for row in raw_series:
        if not isinstance(row, (list, tuple)) or len(row) < 3:
            continue
        bdate = _parse_steam_chart_date(row[0])
        if bdate is None:
            continue

        raw_price = row[1]
        raw_vol = row[2]
        if type(raw_price) is bool or raw_price is None:
            continue

        try:
            price_dec = raw_price if isinstance(raw_price, Decimal) else Decimal(str(raw_price))
            if price_dec <= 0:
                continue
            price_minor = currency.parse_to_minor_units(price_dec)
            vol = int(str(raw_vol).replace(",", "").strip())
            if vol <= 0:
                continue
        except Exception:
            continue

        daily_buckets[bdate].append((price_minor, vol))

    results: list[tuple[str, int, int]] = []
    for bdate in sorted(daily_buckets.keys()):
        pts = daily_buckets[bdate]
        total_vol = sum(v for _, v in pts)
        vwm_minor = compute_weighted_median_minor(pts)
        results.append((bdate, vwm_minor, total_vol))

    return results
