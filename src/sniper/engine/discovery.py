"""Self-discovering Buy-to-Sell screener, preset collections & grouped inventory analytics."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import logging
import statistics
from typing import Any
from urllib.parse import quote

from sniper.adapters.listing_parser import (
    compute_weighted_median_minor,
    parse_listing_render_cards,
)
from sniper.adapters.steam_market import SteamMarketAdapter
from sniper.core.currency import CurrencyConfig
from sniper.core.fees import (
    calculate_exact_breakeven_buyer_pays,
    calculate_seller_receives,
)
from sniper.core.identity import (
    ItemNameIdDatabase,
    decompose_market_hash_name,
    is_trusted_mapping_source,
)
from sniper.storage.database import Database
from sniper.storage.models import (
    CurrencyRecord,
    OrderbookSnapshotRecord,
    PriceHistoryPointRecord,
    WatchlistEntryRecord,
)
from sniper.storage.repositories import (
    CurrencyRepository,
    InventoryRepository,
    ItemRepository,
    OrderbookRepository,
    PriceHistoryRepository,
    WatchlistRepository,
)
from sniper.storage.service import ItemIdentityService

logger = logging.getLogger("sniper.engine.discovery")


CURATED_LIQUID_PRESETS: list[dict[str, Any]] = [
    {
        "id": "play_skins",
        "label": "Liquid Play Skins",
        "description": "High-turnover AK-47, AWP, M4A1-S & USP-S skins with active buy books.",
        "items": [
            "AK-47 | Redline (Field-Tested)",
            "AWP | Asiimov (Field-Tested)",
            "M4A1-S | Printstream (Field-Tested)",
            "USP-S | Kill Confirmed (Field-Tested)",
            "Desert Eagle | Printstream (Field-Tested)",
            "Glock-18 | Vogue (Field-Tested)",
            "AK-47 | Slate (Field-Tested)",
            "AWP | Atheris (Field-Tested)",
        ],
    },
    {
        "id": "mid_rifles",
        "label": "High-Volume Rifles",
        "description": "Liquid mid-tier rifle finishes ideal for dip sniping & sticker crafts.",
        "items": [
            "AK-47 | Ice Coaled (Field-Tested)",
            "M4A4 | Neo-Noir (Field-Tested)",
            "AWP | Neo-Noir (Field-Tested)",
            "AK-47 | Nightwish (Field-Tested)",
            "M4A1-S | Decimator (Field-Tested)",
            "AWP | Chromatic Aberration (Field-Tested)",
        ],
    },
    {
        "id": "budget_knives",
        "label": "Liquid Knives",
        "description": "Popular CS2 knives where dip buys and low floats create spread alpha.",
        "items": [
            "★ Gut Knife | Doppler (Factory New)",
            "★ Huntsman Knife | Autotronic (Field-Tested)",
            "★ Bowie Knife | Black Laminate (Field-Tested)",
            "★ Shadow Daggers | Lore (Field-Tested)",
            "★ Flip Knife | Freehand (Field-Tested)",
            "★ Navaja Knife | Tiger Tooth (Factory New)",
        ],
    },
    {
        "id": "cases",
        "label": "Liquid Cases",
        "description": "High-velocity CS2 weapon cases with deep orderbooks.",
        "items": [
            "Kilowatt Case",
            "Dreams & Nightmares Case",
            "Revolution Case",
            "Recoil Case",
            "Fracture Case",
            "Clutch Case",
        ],
    },
]


def load_currency_config(db: Database, currency_id: int) -> tuple[CurrencyConfig, CurrencyRecord]:
    """Load verified CurrencyConfig + CurrencyRecord from database."""
    curr_repo = CurrencyRepository(db)
    curr_rec = curr_repo.get_by_id(currency_id)
    if curr_rec is None:
        curr_repo.seed_default_currencies()
        curr_rec = curr_repo.get_by_id(currency_id)
        if curr_rec is None:
            raise ValueError(f"Currency ID {currency_id} is not configured.")
    cfg = CurrencyConfig(
        currency_id=curr_rec.currency_id,
        code=curr_rec.code,
        symbol=curr_rec.symbol,
        minor_unit_precision=curr_rec.minor_unit_precision,
        country=curr_rec.country,
        min_listing_price_minor=curr_rec.min_listing_price_minor,
        price_step_minor=curr_rec.price_step_minor,
        min_fee_steam_minor=curr_rec.min_fee_steam_minor,
        min_fee_publisher_minor=curr_rec.min_fee_publisher_minor,
        is_metadata_verified=curr_rec.is_metadata_verified,
        is_fee_verified=curr_rec.is_fee_verified,
        is_listing_min_verified=curr_rec.is_listing_min_verified,
        is_price_step_verified=curr_rec.is_price_step_verified,
        notes=curr_rec.notes,
    )
    return cfg, curr_rec


def compute_target_buy_price_minor(
    target_exit_gross_minor: int | None,
    min_roi_pct: float,
    currency_cfg: CurrencyConfig,
) -> tuple[int, int]:
    """Return exact integer (breakeven_buy_minor, target_buy_minor) for an expected resale gross price.

    Uses integer fee inversion via `calculate_seller_receives` and Decimal basis-point division
    so buying at or below `target_buy_minor` preserves at least `min_roi_pct` Wallet ROI.
    """
    if target_exit_gross_minor is None or target_exit_gross_minor <= 0:
        return 0, 0
    fee_floor = 1 + currency_cfg.min_fee_steam_minor + currency_cfg.min_fee_publisher_minor
    if target_exit_gross_minor < fee_floor:
        return 0, 0

    exit_net = calculate_seller_receives(
        target_exit_gross_minor,
        currency=currency_cfg,
        validate_market_listing=False,
    ).seller_receives
    if exit_net <= 0:
        return 0, 0

    roi_bps = int(
        (Decimal(str(max(0.0, float(min_roi_pct)))) * Decimal(100)).to_integral_value(rounding=ROUND_HALF_UP)
    )
    target_buy = (exit_net * 10000) // (10000 + roi_bps)
    return exit_net, max(0, target_buy)


def get_presets_with_metadata(nameid_db: ItemNameIdDatabase | None) -> list[dict[str, Any]]:
    """Enrich curated discovery presets with offline nameid presence and exact listing URLs."""
    enriched: list[dict[str, Any]] = []
    for preset in CURATED_LIQUID_PRESETS:
        items_out: list[dict[str, Any]] = []
        for mname in preset["items"]:
            _, ext, is_st, is_souv = decompose_market_hash_name(mname)
            mapping = nameid_db.lookup_verified_mapping(mname) if nameid_db else None
            items_out.append({
                "market_hash_name": mname,
                "item_nameid": int(mapping["item_nameid"]) if mapping else None,
                "exterior": ext.value if ext else None,
                "is_stattrak": is_st,
                "is_souvenir": is_souv,
                "steam_listing_url": f"https://steamcommunity.com/market/listings/730/{quote(mname, safe='')}",
            })
        enriched.append({
            "id": preset["id"],
            "label": preset["label"],
            "description": preset["description"],
            "items": items_out,
        })
    return enriched


def build_grouped_inventory_views(
    db: Database,
    currency_id: int = 1,
    min_samples_needed: int = 3,
    nameid_db: ItemNameIdDatabase | None = None,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Group latest inventory snapshot items into marketable stacks with cached orderbook telemetry."""
    inv_repo = InventoryRepository(db)
    snap = inv_repo.get_latest_snapshot()
    if snap is None or snap.snapshot_id is None:
        return None, []

    items = inv_repo.get_items_for_snapshot(snap.snapshot_id)
    currency_cfg, curr_rec = load_currency_config(db, currency_id)
    item_repo = ItemRepository(db)
    ob_repo = OrderbookRepository(db)
    fee_floor = 1 + currency_cfg.min_fee_steam_minor + currency_cfg.min_fee_publisher_minor

    grouped: dict[str, dict[str, Any]] = {}
    for item in items:
        key = item.market_hash_name
        qty = max(1, int(item.amount or 1))
        entry = grouped.get(key)
        if entry is None:
            entry = {
                "market_hash_name": key,
                "item_type": None,
                "quantity": 0,
                "marketable_count": 0,
                "tradable_count": 0,
                "icon_url": None,
                "sample_asset_id": item.asset_id,
                "steam_listing_url": f"https://steamcommunity.com/market/listings/730/{quote(key, safe='')}",
            }
            grouped[key] = entry
        entry["quantity"] += qty
        if item.is_marketable:
            entry["marketable_count"] += qty
        if item.is_tradable:
            entry["tradable_count"] += qty

    out: list[dict[str, Any]] = []
    now_dt = datetime.now(timezone.utc)
    for mname, entry in grouped.items():
        cat = item_repo.get_by_market_hash_name(mname)
        has_nameid = bool(
            cat
            and cat.item_nameid
            and is_trusted_mapping_source(cat.item_nameid_source, cat.item_nameid_revision)
        )
        if not has_nameid and nameid_db is not None:
            mapped = nameid_db.lookup_verified_mapping(mname)
            has_nameid = mapped is not None

        latest_ob = ob_repo.get_latest(cat.item_id, currency_id) if (cat and cat.item_id) else None
        hist_samples = (
            ob_repo.get_historical_buy_orders(
                item_id=cat.item_id,
                currency_id=currency_id,
                max_age_seconds=86400 * 30,
                now_dt=now_dt,
            )
            if (cat and cat.item_id)
            else []
        )
        highest_bid = latest_ob.highest_buy_order_minor if latest_ob else None
        lowest_ask = latest_ob.lowest_sell_order_minor if latest_ob else None
        modeled_net: int | None = None
        if highest_bid is not None and highest_bid >= fee_floor:
            modeled_net = calculate_seller_receives(
                highest_bid, currency=currency_cfg, validate_market_listing=False
            ).seller_receives

        out.append({
            **entry,
            "currency_code": curr_rec.code,
            "currency_symbol": curr_rec.symbol,
            "has_trusted_nameid": has_nameid,
            "latest_highest_bid_minor": highest_bid,
            "latest_lowest_ask_minor": lowest_ask,
            "modeled_seller_net_minor": modeled_net,
            "baseline_samples_count": len(hist_samples),
            "min_samples_needed": min_samples_needed,
            "latest_observed_at": latest_ob.observed_at if latest_ob else None,
        })

    out.sort(
        key=lambda x: (
            0 if x["marketable_count"] > 0 else 1,
            -(x["modeled_seller_net_minor"] or 0),
            -x["quantity"],
            x["market_hash_name"],
        )
    )
    return snap.model_dump(), out


async def screen_market_items(
    market_hash_names: list[str],
    adapter: SteamMarketAdapter,
    identity_service: ItemIdentityService,
    db: Database,
    currency_id: int = 1,
    min_roi_pct: float = 5.0,
    risk_buffer_pct: int = 5,
    auto_watchlist: bool = False,
    max_budget_minor: int | None = None,
) -> list[dict[str, Any]]:
    """Run strictly read-only Buy-to-Sell evaluation across 1..8 CS2 items using SSR bundles."""
    currency_cfg, curr_rec = load_currency_config(db, currency_id)
    if not currency_cfg.is_metadata_verified or not currency_cfg.is_fee_verified:
        raise ValueError(f"Unverified currency rules for {curr_rec.code}.")

    deduped: list[str] = []
    seen: set[str] = set()
    for raw_name in market_hash_names:
        clean = raw_name.strip()
        if clean and clean not in seen:
            seen.add(clean)
            deduped.append(clean)
        if len(deduped) >= 8:
            break

    history_repo = PriceHistoryRepository(db)
    ob_repo = OrderbookRepository(db)
    wl_repo = WatchlistRepository(db)
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    fee_floor = 1 + currency_cfg.min_fee_steam_minor + currency_cfg.min_fee_publisher_minor

    results: list[dict[str, Any]] = []
    for name in deduped:
        listing_url = f"https://steamcommunity.com/market/listings/730/{quote(name, safe='')}"
        try:
            ingested = identity_service.resolve_and_persist(listing_url)
            item_rec = ingested.record
            identity = ingested.identity

            ob_snap: dict[str, Any] | None = None
            ssr_pts: list[tuple[str, int, int]] = []
            if hasattr(adapter, "get_ssr_market_bundle"):
                render, ssr_pts, ob_snap = await adapter.get_ssr_market_bundle(
                    identity=identity,
                    currency=currency_cfg,
                )
            elif hasattr(adapter, "get_ssr_market_page"):
                render, ssr_pts = await adapter.get_ssr_market_page(
                    identity=identity,
                    currency=currency_cfg,
                )
            else:
                render = await adapter.get_listing_render(identity=identity, currency=currency_id)

            if item_rec.item_id is not None and ssr_pts:
                history_repo.upsert_batch([
                    PriceHistoryPointRecord(
                        item_id=item_rec.item_id,
                        currency_id=currency_id,
                        bucket_date=bdate,
                        median_price_minor=median_p,
                        volume=vol,
                        source="steam_ssr_chart",
                        observed_at=now_iso,
                    )
                    for bdate, median_p, vol in ssr_pts
                ])

            if item_rec.item_id is not None and ob_snap is not None:
                ob_repo.insert_snapshot(
                    OrderbookSnapshotRecord(
                        item_id=item_rec.item_id,
                        currency_id=currency_id,
                        highest_buy_order_minor=ob_snap.get("highest_buy_order_minor"),
                        lowest_sell_order_minor=ob_snap.get("lowest_sell_order_minor"),
                        buy_order_count=ob_snap.get("buy_order_count"),
                        sell_order_count=ob_snap.get("sell_order_count"),
                        top_level_buy_quantity=ob_snap.get("top_level_buy_quantity"),
                        cumulative_buy_quantity=ob_snap.get("buy_order_count"),
                        observed_at=now_iso,
                        source_payload_version="ssr_orderbook_v1",
                    )
                )

            valid_cards, _ = parse_listing_render_cards(
                render=render,
                expected_market_hash_name=name,
                expected_currency_id=currency_id,
                expected_exterior=identity.exterior,
            )

            lowest_ask = (
                valid_cards[0].buyer_pays_minor
                if valid_cards
                else (ob_snap.get("lowest_sell_order_minor") if ob_snap else None)
            )
            floor_median = (
                int(statistics.median_low([c.buyer_pays_minor for c in valid_cards[:3]]))
                if valid_cards
                else lowest_ask
            )
            highest_bid = ob_snap.get("highest_buy_order_minor") if ob_snap else None
            buy_order_count = ob_snap.get("buy_order_count") if ob_snap else None
            sell_order_count = (
                ob_snap.get("sell_order_count") if ob_snap and ob_snap.get("sell_order_count") else render.total_count
            )

            pts_30d = (
                history_repo.get_recent_points(item_rec.item_id, currency_id, days=30, now_dt=now)
                if item_rec.item_id is not None
                else []
            )
            cutoff_14d = now.date().toordinal() - 14
            cutoff_7d = now.date().toordinal() - 7
            pts_14d = [p for p in pts_30d if datetime.strptime(p.bucket_date, "%Y-%m-%d").date().toordinal() >= cutoff_14d]
            pts_7d = [p for p in pts_30d if datetime.strptime(p.bucket_date, "%Y-%m-%d").date().toordinal() >= cutoff_7d]

            vwm_30d = compute_weighted_median_minor([(p.median_price_minor, p.volume) for p in pts_30d]) if pts_30d else None
            vwm_14d = compute_weighted_median_minor([(p.median_price_minor, p.volume) for p in pts_14d]) if pts_14d else None
            vwm_7d = compute_weighted_median_minor([(p.median_price_minor, p.volume) for p in pts_7d]) if pts_7d else None
            sales_30d = sum(p.volume for p in pts_30d)
            sales_7d = sum(p.volume for p in pts_7d)

            baseline_exit = vwm_30d or vwm_14d or floor_median
            net_at_base: int | None = None
            profit_at_base: int | None = None
            roi_at_base: float | None = None
            cons_gross: int | None = None
            cons_net: int | None = None
            cons_profit: int | None = None
            cons_roi: float | None = None
            breakeven_exit_for_ask: int | None = None

            if baseline_exit and baseline_exit >= fee_floor:
                net_at_base = calculate_seller_receives(
                    baseline_exit, currency=currency_cfg, validate_market_listing=False
                ).seller_receives
                cons_gross = (baseline_exit * (100 - risk_buffer_pct)) // 100
                if cons_gross >= fee_floor:
                    cons_net = calculate_seller_receives(
                        cons_gross, currency=currency_cfg, validate_market_listing=False
                    ).seller_receives

            if lowest_ask and lowest_ask > 0:
                breakeven_exit_for_ask = calculate_exact_breakeven_buyer_pays(
                    lowest_ask, currency=currency_cfg
                )
                if net_at_base is not None:
                    profit_at_base = net_at_base - lowest_ask
                    roi_at_base = round((float(profit_at_base) / float(lowest_ask)) * 100.0, 2)
                if cons_net is not None:
                    cons_profit = cons_net - lowest_ask
                    cons_roi = round((float(cons_profit) / float(lowest_ask)) * 100.0, 2)

            breakeven_buy, target_buy = compute_target_buy_price_minor(
                baseline_exit, min_roi_pct, currency_cfg
            )
            dip_vs_30d_pct: float | None = None
            if vwm_30d and vwm_30d > 0 and lowest_ask and lowest_ask > 0:
                dip_vs_30d_pct = round(((float(vwm_30d - lowest_ask)) / float(vwm_30d)) * 100.0, 2)

            bid_ask_spread_pct: float | None = None
            if lowest_ask and lowest_ask > 0 and highest_bid and highest_bid > 0:
                bid_ask_spread_pct = round(((float(lowest_ask - highest_bid)) / float(lowest_ask)) * 100.0, 2)

            floats = [c.float_value for c in valid_cards if c.float_value is not None]
            lowest_float = min(floats) if floats else None
            has_low_float_card = any(c.is_low_float_tier for c in valid_cards)
            stickers_seen: list[str] = []
            for card in valid_cards[:5]:
                for stk in card.stickers:
                    if stk not in stickers_seen:
                        stickers_seen.append(stk)
            has_notable_craft = any(c.has_notable_craft for c in valid_cards[:5])
            best_inspect = next((c.inspect_link for c in valid_cards if c.inspect_link), None)

            if lowest_ask is not None and target_buy > 0 and lowest_ask <= target_buy and sales_7d >= 5:
                verdict = "STRONG BUY NOW"
                verdict_tone = "good"
            elif (
                (lowest_ask is not None and breakeven_buy > 0 and lowest_ask <= breakeven_buy)
                or (dip_vs_30d_pct is not None and dip_vs_30d_pct >= 8.0 and sales_7d >= 5)
            ):
                verdict = "DIP CANDIDATE"
                verdict_tone = "good"
            elif has_notable_craft or has_low_float_card:
                verdict = "CRAFT / FLOAT ALPHA"
                verdict_tone = "warn"
            elif target_buy > 0:
                verdict = "PLACE BID <= TARGET"
                verdict_tone = "neutral"
            else:
                verdict = "INSUFFICIENT HISTORY"
                verdict_tone = "neutral"

            roi_component = max(-30.0, min(60.0, (roi_at_base if roi_at_base is not None else -15.0) + 20.0))
            liq_component = min(25.0, float(sales_7d) * 0.5)
            alpha_component = (10.0 if has_notable_craft else 0.0) + (5.0 if has_low_float_card else 0.0)
            screener_score = round(max(0.0, min(100.0, roi_component + liq_component + alpha_component)), 1)

            watchlist_added = False
            if auto_watchlist and item_rec.item_id is not None:
                cap = target_buy if target_buy > 0 else max_budget_minor
                if max_budget_minor and cap:
                    cap = min(cap, max_budget_minor)
                wl_repo.save(
                    WatchlistEntryRecord(
                        item_id=item_rec.item_id,
                        input_url=listing_url,
                        strategy="BUY",
                        currency_id=currency_id,
                        max_buy_price_minor=cap if cap and cap > 0 else None,
                        min_roi_pct=min_roi_pct,
                        notes="Added via Auto-Hunt Screener",
                    )
                )
                watchlist_added = True

            results.append({
                "market_hash_name": name,
                "exterior": item_rec.exterior,
                "item_nameid": item_rec.item_nameid,
                "has_trusted_nameid": bool(
                    item_rec.item_nameid
                    and is_trusted_mapping_source(item_rec.item_nameid_source, item_rec.item_nameid_revision)
                ),
                "currency_id": currency_id,
                "currency_code": curr_rec.code,
                "currency_symbol": curr_rec.symbol,
                "steam_listing_url": listing_url,
                "lowest_ask_minor": lowest_ask,
                "floor_median_minor": floor_median,
                "highest_buy_order_minor": highest_bid,
                "buy_order_count": buy_order_count,
                "sell_order_count": sell_order_count,
                "bid_ask_spread_pct": bid_ask_spread_pct,
                "vwm_7d_minor": vwm_7d,
                "vwm_14d_minor": vwm_14d,
                "vwm_30d_minor": vwm_30d,
                "sales_7d": sales_7d,
                "sales_30d": sales_30d,
                "dip_vs_30d_pct": dip_vs_30d_pct,
                "baseline_exit_gross_minor": baseline_exit,
                "net_at_baseline_exit_minor": net_at_base,
                "profit_at_baseline_minor": profit_at_base,
                "roi_at_baseline_pct": roi_at_base,
                "cons_exit_gross_minor": cons_gross,
                "cons_net_minor": cons_net,
                "cons_profit_minor": cons_profit,
                "cons_roi_pct": cons_roi,
                "breakeven_exit_for_ask_minor": breakeven_exit_for_ask,
                "breakeven_buy_minor": breakeven_buy,
                "target_buy_minor": target_buy,
                "lowest_float": lowest_float,
                "has_notable_craft": has_notable_craft,
                "notable_stickers": stickers_seen[:6],
                "best_inspect_link": best_inspect,
                "active_listings_count": len(valid_cards),
                "verdict": verdict,
                "verdict_tone": verdict_tone,
                "screener_score": screener_score,
                "watchlist_added": watchlist_added,
                "error": None,
            })
        except Exception as exc:
            logger.warning("Screener error for '%s': %s", name, exc)
            results.append({
                "market_hash_name": name,
                "exterior": None,
                "item_nameid": None,
                "has_trusted_nameid": False,
                "currency_id": currency_id,
                "currency_code": curr_rec.code,
                "currency_symbol": curr_rec.symbol,
                "steam_listing_url": listing_url,
                "lowest_ask_minor": None,
                "floor_median_minor": None,
                "highest_buy_order_minor": None,
                "buy_order_count": None,
                "sell_order_count": None,
                "bid_ask_spread_pct": None,
                "vwm_7d_minor": None,
                "vwm_14d_minor": None,
                "vwm_30d_minor": None,
                "sales_7d": 0,
                "sales_30d": 0,
                "dip_vs_30d_pct": None,
                "baseline_exit_gross_minor": None,
                "net_at_baseline_exit_minor": None,
                "profit_at_baseline_minor": None,
                "roi_at_baseline_pct": None,
                "cons_exit_gross_minor": None,
                "cons_net_minor": None,
                "cons_profit_minor": None,
                "cons_roi_pct": None,
                "breakeven_exit_for_ask_minor": None,
                "breakeven_buy_minor": 0,
                "target_buy_minor": 0,
                "lowest_float": None,
                "has_notable_craft": False,
                "notable_stickers": [],
                "best_inspect_link": None,
                "active_listings_count": 0,
                "verdict": "SCAN ERROR",
                "verdict_tone": "neutral",
                "screener_score": 0.0,
                "watchlist_added": False,
                "error": str(exc),
            })

    results.sort(
        key=lambda x: (
            x["screener_score"],
            x["roi_at_baseline_pct"] if x["roi_at_baseline_pct"] is not None else -999.0,
        ),
        reverse=True,
    )
    return results
