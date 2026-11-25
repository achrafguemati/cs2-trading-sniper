"""Command-line interface for CS2 Trading Sniper (Milestone 5)."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import logging
import os
from pathlib import Path
import sys
from typing import Sequence

from dotenv import load_dotenv

from sniper.core.http_client import ReadOnlyHttpClient
from sniper.adapters.inventory import ManualInventoryImporter, PublicInventoryClient, validate_steamid64
from sniper.adapters.steam_market import SteamMarketAdapter
from sniper.core.rate_limiter import SequentialRateLimiter
from sniper.engine.buy_engine import (
    BuyEvaluationConfig,
    BuySniperEngine,
    import_manual_price_history_json,
)
from sniper.engine.portfolio_service import PortfolioService
from sniper.engine.sell_engine import SellEvaluationConfig, SellSniperEngine
from sniper.notifications import (
    AlertPayload,
    BuyAlertPayload,
    DeliveryStatus,
    DiscordNotifier,
    DiscordWebhookChannel,
    NotificationDispatcher,
    TelegramBotChannel,
    TelegramNotifier,
)
from sniper.storage.database import Database
from sniper.storage.models import ItemRecord, WatchlistEntryRecord
from sniper.storage.repositories import (
    AlertRepository,
    BuyEvaluationRepository,
    CurrencyRepository,
    InventoryRepository,
    ItemRepository,
    WatchlistRepository,
)
from sniper.storage.service import ItemIdentityService, load_default_nameid_db

logger = logging.getLogger("sniper.cli")


def get_default_db_path() -> str:
    """Return default database file path."""
    return os.getenv("SNIPER_DB_PATH", "data/sniper.db")


def init_database(db_path: str) -> Database:
    """Open and initialize SQLite database with latest schema migrations."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = Database(str(path))
    db.initialize()
    curr_repo = CurrencyRepository(db)
    curr_repo.seed_default_currencies()
    return db


def _open_identity_service(db: Database, db_path: str) -> tuple[ItemIdentityService, Any]:
    """Build ItemIdentityService backed by the packaged verified item_nameids mapping."""
    nid_path = ":memory:" if db.is_memory else str(Path(db_path).parent / "item_nameids.db")
    nameid_db = load_default_nameid_db(nid_path)
    return ItemIdentityService(db, nameid_db=nameid_db), nameid_db


def _resolve_max_buy_minor(
    db: Database,
    currency_id: int,
    max_buy_cents: int | None,
    max_price: str | None,
) -> int | None:
    """Convert optional --max-price decimal string into exact minor units without float math."""
    parsed_price_minor: int | None = None
    if max_price is not None:
        raw = str(max_price).strip()
        curr = CurrencyRepository(db).get_by_id(currency_id)
        precision = curr.minor_unit_precision if curr is not None else 2
        if curr is not None and raw.startswith(curr.symbol):
            raw = raw[len(curr.symbol):].strip()
        elif raw.startswith("$"):
            raw = raw[1:].strip()
        try:
            dec = Decimal(raw)
        except InvalidOperation as exc:
            raise ValueError(f"Invalid --max-price value '{max_price}'.") from exc
        if not dec.is_finite() or dec <= 0:
            raise ValueError(f"--max-price must be positive, got '{max_price}'.")
        scaled = dec * (Decimal(10) ** precision)
        if scaled != scaled.to_integral_value():
            raise ValueError(
                f"--max-price '{max_price}' exceeds currency minor-unit precision ({precision} decimals)."
            )
        parsed_price_minor = int(scaled)

    if max_buy_cents is not None and parsed_price_minor is not None and max_buy_cents != parsed_price_minor:
        raise ValueError(
            f"Conflicting --max-buy-cents ({max_buy_cents}) and --max-price ({parsed_price_minor} minor units)."
        )
    return parsed_price_minor if parsed_price_minor is not None else max_buy_cents


async def cmd_import_inventory(args: argparse.Namespace) -> int:
    """Import public CS2 inventory using 17-digit SteamID64."""
    steamid64 = args.steamid64.strip()
    try:
        validate_steamid64(steamid64)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    db = init_database(args.db)
    try:
        async with ReadOnlyHttpClient() as http_client:
            client = PublicInventoryClient(http_client=http_client, db=db)
            print(f"Fetching public inventory for SteamID64: {steamid64}...")
            snapshot, items = await client.fetch_and_import(
                steamid64=steamid64,
                count=args.count,
                max_pages=args.max_pages,
            )
            marketable_count = sum(1 for i in items if i.is_marketable)
            restricted_count = len(items) - marketable_count

            print("\n=== Public Inventory Import Succeeded ===")
            print(f"Snapshot ID:       {snapshot.snapshot_id}")
            print(f"Owner ID:          {snapshot.owner_id}")
            print(f"Total Items:       {len(items)}")
            print(f"Marketable:        {marketable_count}")
            print(f"Restricted:        {restricted_count}")
            print(f"Observed At:       {snapshot.observed_at}")
            return 0
    except Exception as exc:
        print(f"Failed to fetch public inventory: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()


def cmd_import_csv(args: argparse.Namespace) -> int:
    """Import manual inventory from CSV file."""
    path = Path(args.file)
    if not path.is_file():
        print(f"Error: CSV file not found at '{path}'.", file=sys.stderr)
        return 1

    db = init_database(args.db)
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        snapshot, items, errors = ManualInventoryImporter.import_csv(
            csv_content=content,
            db=db,
            owner_id=args.owner_id,
        )

        print("\n=== Manual CSV Inventory Import Completed ===")
        print(f"Snapshot ID:       {snapshot.snapshot_id}")
        print(f"Items Imported:    {len(items)}")
        print(f"Errors Encountered:{len(errors)}")

        if errors:
            print("\nValidation Errors:")
            for err in errors[:20]:
                print(f"  • {err}")
            if len(errors) > 20:
                print(f"  ... and {len(errors) - 20} more errors.")
        return 0
    finally:
        db.close()


def cmd_import_json(args: argparse.Namespace) -> int:
    """Import manual inventory from JSON file."""
    path = Path(args.file)
    if not path.is_file():
        print(f"Error: JSON file not found at '{path}'.", file=sys.stderr)
        return 1

    db = init_database(args.db)
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        snapshot, items, errors = ManualInventoryImporter.import_json(
            json_content=content,
            db=db,
            owner_id=args.owner_id,
        )

        print("\n=== Manual JSON Inventory Import Completed ===")
        print(f"Snapshot ID:       {snapshot.snapshot_id}")
        print(f"Items Imported:    {len(items)}")
        print(f"Errors Encountered:{len(errors)}")

        if errors:
            print("\nValidation Errors:")
            for err in errors[:20]:
                print(f"  • {err}")
            if len(errors) > 20:
                print(f"  ... and {len(errors) - 20} more errors.")
        return 0
    finally:
        db.close()


async def cmd_scan_sell(args: argparse.Namespace) -> int:
    """Scan marketable inventory items for profitable SELL opportunities."""
    db = init_database(args.db)
    nameid_db = None
    try:
        identity_service, nameid_db = _open_identity_service(db, args.db)
        async with ReadOnlyHttpClient() as http_client:
            market_adapter = SteamMarketAdapter(http_client=http_client)
            dispatcher = NotificationDispatcher.from_database(db)

            config = SellEvaluationConfig(
                currency_id=args.currency_id,
                min_bid_minor=args.min_bid_cents,
                min_bid_multiple=args.min_multiple,
            )

            engine = SellSniperEngine(
                db=db,
                market_adapter=market_adapter,
                identity_service=identity_service,
                dispatcher=dispatcher,
                config=config,
            )

            print("Running SELL opportunity scan across marketable inventory items...")
            evaluations = await engine.scan_inventory(
                snapshot_id=args.snapshot_id,
                owner_id=args.owner_id,
                dispatch_notifications=not args.no_notifications,
                max_unique_items=getattr(args, "limit", None),
            )

            print(f"\nEvaluated {len(evaluations)} unique items:\n")
            print(f"{'Item Name':<40} {'Gross Bid':<12} {'Seller Net':<12} {'Mult':<8} {'Score':<8} {'Conf':<8} {'Status':<25}")
            print("-" * 115)

            actionable_count = 0
            for ev in evaluations:
                gross_str = f"{ev.currency_symbol}{ev.highest_buy_order_minor / 100:.2f}" if ev.highest_buy_order_minor else "N/A"
                net_str = f"{ev.currency_symbol}{ev.modeled_seller_receives_minor / 100:.2f}" if ev.modeled_seller_receives_minor else "N/A"
                mult_str = f"{ev.bid_multiple:.2f}x" if ev.bid_multiple else "N/A"
                score_str = f"{ev.opportunity_score:.1f}"
                conf_str = f"{ev.confidence_score:.1f}"

                name_trunc = ev.market_hash_name[:38]
                status_trunc = ev.status[:24]

                print(f"{name_trunc:<40} {gross_str:<12} {net_str:<12} {mult_str:<8} {score_str:<8} {conf_str:<8} {status_trunc:<25}")

                if ev.warning_tags:
                    print(f"    Tags: {', '.join(ev.warning_tags)}")
                if ev.delivery_results:
                    deliveries = [f"{ch}: {r.status.value}" for ch, r in ev.delivery_results.items()]
                    print(f"    Notifications: {', '.join(deliveries)}")

                if ev.is_actionable:
                    actionable_count += 1

            print("-" * 115)
            print(f"Total Evaluated: {len(evaluations)} | Actionable Signals: {actionable_count}")
            return 0
    finally:
        if nameid_db is not None:
            nameid_db.close()
        db.close()


async def cmd_test_notifications(args: argparse.Namespace) -> int:
    """Send a test alert across notification channels to verify configuration."""
    test_alert = AlertPayload(
        alert_id=999999,
        market_hash_name="AK-47 | Redline (Field-Tested)",
        currency_code="USD",
        currency_symbol="$",
        highest_buy_order_minor=2550,
        lowest_sell_order_minor=2800,
        top_level_buy_quantity=3,
        cumulative_buy_quantity=15,
        modeled_steam_fee_minor=127,
        modeled_publisher_fee_minor=255,
        modeled_seller_receives_minor=2168,
        historical_baseline_minor=2000,
        bid_multiple=1.28,
        opportunity_score=82.5,
        confidence_score=85.0,
        warning_tags=[],
        status="Candidate",
        created_at=datetime.now(timezone.utc).isoformat(),
        steam_listing_url="https://steamcommunity.com/market/listings/730/AK-47%20%7C%20Redline%20%28Field-Tested%29",
    )

    channels = []
    ch_filter = args.channel.lower() if args.channel else "all"

    if ch_filter in ("all", "discord"):
        channels.append(DiscordNotifier())
    if ch_filter in ("all", "telegram"):
        channels.append(TelegramNotifier())

    dispatcher = NotificationDispatcher(channels=channels)
    print(f"Dispatching test alert across channel(s): {[c.channel_name for c in channels]}...\n")

    results = await dispatcher.dispatch(test_alert, only_configured=False)

    for ch_name, res in results.items():
        symbol = "✅" if res.status == DeliveryStatus.DELIVERED else ("⚠️" if res.status == DeliveryStatus.SKIPPED else "❌")
        print(f"{symbol} Channel: {ch_name:<12} Status: {res.status.value.upper()}")
        if res.last_error:
            print(f"   Details: {res.last_error}")

    return 0


def _print_channel_status() -> None:
    """Print configured and enabled status for Discord and Telegram channels."""
    print("\nNotification Transports Status (Discord + Telegram only):")
    discord = DiscordNotifier()
    telegram = TelegramNotifier()

    if not discord.is_enabled:
        disc_str = "Disabled (set DISCORD_ENABLED=true to enable)"
    elif discord.is_configured:
        disc_str = "Configured & Enabled"
    else:
        disc_str = "Not configured (set DISCORD_WEBHOOK_URL)"

    if not telegram.is_enabled:
        tg_str = "Disabled (set TELEGRAM_ENABLED=true to enable)"
    elif telegram.is_configured:
        tg_str = "Configured & Enabled"
    else:
        tg_str = "Not configured (set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)"

    print(f"  • Discord Webhook:     {disc_str}")
    print(f"  • Telegram Bot:        {tg_str}")
    print("\nNote: Telegram requires that you start the bot (@BotFather) or add it to your group before alerts can be delivered.")


def cmd_channel_status(args: argparse.Namespace) -> int:
    """Display notification channels configuration status without revealing secrets."""
    print("=== CS2 Trading Sniper Notification Channels Status ===")
    _print_channel_status()
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    """Display system status, database stats, and configured channels."""
    db_path = args.db
    exists = os.path.exists(db_path)
    print("=== CS2 Trading Sniper System Status ===\n")
    print(f"Database Path:         {db_path} ({'Found' if exists else 'Not created yet'})")

    if exists:
        db = Database(db_path)
        try:
            ver = db.get_version()
            print(f"Schema Version:        v{ver}")

            item_count = db.fetch_one("SELECT COUNT(*) FROM items")[0]
            snap_count = db.fetch_one("SELECT COUNT(*) FROM inventory_snapshots")[0]
            inv_count = db.fetch_one("SELECT COUNT(*) FROM inventory_items")[0]
            ob_count = db.fetch_one("SELECT COUNT(*) FROM orderbook_snapshots")[0]
            alert_count = db.fetch_one("SELECT COUNT(*) FROM alert_events")[0]
            wl_count = db.fetch_one("SELECT COUNT(*) FROM watchlist_entries")[0] if ver >= 5 else 0
            buy_count = db.fetch_one("SELECT COUNT(*) FROM buy_listing_evaluations")[0] if ver >= 5 else 0

            print(f"Catalog Items:         {item_count}")
            print(f"Watchlist Entries:     {wl_count}")
            print(f"Inventory Snapshots:   {snap_count}")
            print(f"Inventory Items:       {inv_count}")
            print(f"Orderbook Snapshots:   {ob_count}")
            print(f"BUY Evaluations:       {buy_count}")
            print(f"Alert Events Recorded: {alert_count}")
        finally:
            db.close()

    _print_channel_status()
    return 0


async def cmd_watchlist_add(args: argparse.Namespace) -> int:
    """Resolve URL (canonical or grouped G...) and save to watchlist."""
    db = init_database(args.db)
    nameid_db = None
    try:
        nid_path = ":memory:" if db.is_memory else str(Path(args.db).parent / "item_nameids.db")
        nameid_db = load_default_nameid_db(nid_path)
        async with ReadOnlyHttpClient() as http_client:
            adapter = SteamMarketAdapter(http_client=http_client)
            svc = ItemIdentityService(db, nameid_db=nameid_db)
            url = args.url.strip()
            try:
                res = svc.resolve_and_persist(url)
            except Exception:
                # Try SSR hydration for grouped G... URLs
                ssr_ctx = await adapter.get_ssr_listing_context(url)
                res = svc.resolve_and_persist(url, ssr_data=ssr_ctx)

            max_buy_minor = _resolve_max_buy_minor(
                db,
                args.currency_id,
                getattr(args, "max_buy_cents", None),
                getattr(args, "max_price", None),
            )
            repo = WatchlistRepository(db)
            saved = repo.save(
                WatchlistEntryRecord(
                    item_id=res.record.item_id,
                    input_url=url,
                    strategy=args.strategy,
                    currency_id=args.currency_id,
                    max_buy_price_minor=max_buy_minor,
                    min_discount_pct=args.min_discount,
                    min_net_profit_minor=args.min_profit_cents,
                    min_roi_pct=args.min_roi,
                    notes=args.notes,
                )
            )
            print(f"Added watchlist #{saved.watchlist_id}: {res.record.market_hash_name} ({saved.strategy})")
            return 0
    except Exception as exc:
        print(f"Failed to add watchlist entry: {exc}", file=sys.stderr)
        return 1
    finally:
        if nameid_db is not None:
            nameid_db.close()
        db.close()


def cmd_watchlist_list(args: argparse.Namespace) -> int:
    """List configured watchlist entries."""
    db = init_database(args.db)
    try:
        entries = WatchlistRepository(db).list_entries()
        item_repo = ItemRepository(db)
        print(f"Watchlist entries ({len(entries)}):")
        for e in entries:
            item = item_repo.get_by_id(e.item_id)
            name = item.market_hash_name if item else f"item#{e.item_id}"
            max_str = f"${e.max_buy_price_minor / 100:.2f}" if e.max_buy_price_minor else "Auto"
            print(f"  #{e.watchlist_id:<4} [{e.strategy:<4}] {'ON ' if e.is_active else 'OFF'} {name:<42} Max:{max_str} MinROI:{e.min_roi_pct:.1f}%")
        return 0
    finally:
        db.close()


def cmd_watchlist_remove(args: argparse.Namespace) -> int:
    """Remove a watchlist entry by ID."""
    db = init_database(args.db)
    try:
        deleted = WatchlistRepository(db).delete(args.watchlist_id)
        if not deleted:
            print(f"Watchlist entry #{args.watchlist_id} not found.", file=sys.stderr)
            return 1
        print(f"Removed watchlist entry #{args.watchlist_id}.")
        return 0
    finally:
        db.close()


def cmd_import_history(args: argparse.Namespace) -> int:
    """Import daily price history JSON file for cookie-free BUY baseline calculations."""
    path = Path(args.file)
    if not path.is_file():
        print(f"Error: History JSON file not found at '{path}'.", file=sys.stderr)
        return 1
    db = init_database(args.db)
    try:
        content = path.read_text(encoding="utf-8")
        count = import_manual_price_history_json(
            json_content=content,
            market_hash_name=args.market_hash_name,
            db=db,
            currency_id=args.currency_id,
        )
        print(f"Imported {count} daily price history points for '{args.market_hash_name}'.")
        return 0
    except Exception as exc:
        print(f"Failed to import price history: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()


async def cmd_scan_buy(args: argparse.Namespace) -> int:
    """Scan active BUY watchlist entries for underpriced individual listings."""
    db = init_database(args.db)
    nameid_db = None
    try:
        identity_service, nameid_db = _open_identity_service(db, args.db)
        async with ReadOnlyHttpClient() as http_client:
            adapter = SteamMarketAdapter(http_client=http_client)
            dispatcher = NotificationDispatcher.from_database(db)
            max_buy_minor = _resolve_max_buy_minor(
                db,
                args.currency_id,
                getattr(args, "max_buy_cents", None),
                getattr(args, "max_price", None),
            )
            config = BuyEvaluationConfig(
                currency_id=args.currency_id,
                min_roi_pct=args.min_roi,
                max_buy_price_minor=max_buy_minor,
            )
            engine = BuySniperEngine(
                db=db,
                market_adapter=adapter,
                identity_service=identity_service,
                dispatcher=dispatcher,
                config=config,
            )
            evals = await engine.scan_watchlist(
                currency_id=args.currency_id,
                dispatch_notifications=not args.no_notifications,
            )
            actionable = sum(1 for e in evals if e.is_actionable)
            print(f"Evaluated {len(evals)} individual BUY listings | Actionable Signals: {actionable}")
            for ev in evals[:25]:
                p_str = f"{ev.currency_symbol}{ev.purchase_buyer_pays_minor / 100:.2f}" if ev.purchase_buyer_pays_minor else "N/A"
                net_str = f"{ev.currency_symbol}{ev.conservative_net_profit_minor / 100:.2f}"
                print(f"  [{ev.status:<22}] {ev.market_hash_name:<35} #{ev.steam_listing_id:<14} Cost:{p_str} ConsNet:{net_str} ({ev.conservative_roi_pct:.1f}%)")
            return 0
    finally:
        if nameid_db is not None:
            nameid_db.close()
        db.close()


def cmd_portfolio_add(args: argparse.Namespace) -> int:
    """Add an acquired holding with cost basis and 7-day restriction tracking."""
    db = init_database(args.db)
    try:
        svc = PortfolioService(db)
        marketable_dt = None
        if args.marketable_at:
            raw_dt = args.marketable_at.strip()
            if raw_dt.endswith("Z"):
                raw_dt = raw_dt[:-1] + "+00:00"
            marketable_dt = datetime.fromisoformat(raw_dt)
        holding = svc.add_holding(
            market_hash_name=args.market_hash_name,
            acquisition_cost_minor=args.cost_cents,
            currency_id=args.currency_id,
            quantity=args.quantity,
            asset_id=args.asset_id,
            marketable_at=marketable_dt,
            restriction_days=args.days,
            notes=args.notes,
        )
        print(
            f"Added holding #{holding.holding_id}: {holding.market_hash_name} "
            f"[{holding.status}] Cost:{holding.acquisition_cost_minor} BreakEven:{holding.breakeven_buyer_pays_minor} "
            f"Unlock:{holding.marketable_at}"
        )
        return 0
    except Exception as exc:
        print(f"Failed to add holding: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()


def cmd_portfolio_list(args: argparse.Namespace) -> int:
    """Display portfolio holdings, lock countdowns, break-even thresholds, and Wallet P&L."""
    db = init_database(args.db)
    try:
        overview = PortfolioService(db).get_overview(currency_id=args.currency_id)
        sym = overview.currency_symbol
        print(
            f"=== Portfolio Overview ({overview.currency_code} Steam Wallet — non-withdrawable) ===\n"
            f"Holdings: {overview.total_holdings} (Locked:{overview.locked_count}, Ready:{overview.ready_to_list_count}, Sold:{overview.sold_count}, UnderWater:{overview.under_water_count})\n"
            f"Capital Deployed: {sym}{overview.capital_deployed_minor / 100:.2f} | "
            f"Unrealized P&L: {sym}{overview.unrealized_wallet_pnl_minor / 100:.2f} | "
            f"Realized P&L: {sym}{overview.realized_wallet_pnl_minor / 100:.2f}\n"
        )
        for h in overview.holdings:
            lock_str = f"{h.seconds_until_marketable // 3600}h" if h.is_locked else "Unlocked"
            uw_str = f" [UNDER WATER -{sym}{h.under_water_gap_minor / 100:.2f}]" if h.is_under_water else ""
            print(
                f"  #{h.holding_id:<4} [{h.status:<13}] {h.market_hash_name:<35} "
                f"Cost:{sym}{h.acquisition_cost_minor / 100:.2f} BE:{sym}{h.breakeven_buyer_pays_minor / 100:.2f} "
                f"Lock:{lock_str}{uw_str}"
            )
        return 0
    finally:
        db.close()


async def cmd_portfolio_evaluate(args: argparse.Namespace) -> int:
    """Evaluate active holdings for 7-day unlock transitions and Under Water / RESALE READY alerts."""
    db = init_database(args.db)
    try:
        svc = PortfolioService(db)
        views = await svc.evaluate_holdings(dispatch_notifications=not args.no_notifications)
        ready_ct = sum(1 for v in views if v.status == "READY_TO_LIST")
        print(f"Evaluated {len(views)} active holding(s) | Ready to list: {ready_ct}")
        return 0
    finally:
        db.close()


def cmd_portfolio_sell(args: argparse.Namespace) -> int:
    """Record manual sale completion for a holding and compute exact realized Steam Wallet proceeds."""
    db = init_database(args.db)
    try:
        updated = PortfolioService(db).record_sale(
            holding_id=args.holding_id,
            gross_sale_price_minor=args.gross_cents,
            notes=args.notes,
        )
        print(
            f"Marked holding #{updated.holding_id} SOLD | Gross:{args.gross_cents} "
            f"Realized Net Proceeds:{updated.realized_net_proceeds_minor}"
        )
        return 0
    except Exception as exc:
        print(f"Failed to record sale: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()


def cmd_serve(args: argparse.Namespace) -> int:
    """Start the local FastAPI backend & React SPA web server."""
    import uvicorn
    from sniper.api.app import create_app

    app = create_app(
        db_path=args.db,
        enable_scheduler=not args.no_scheduler,
    )
    print(f"Starting CS2 Trading Sniper web dashboard on http://{args.host}:{args.port} (Read-Only)...")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="sniper",
        description="CS2 Trading Sniper — Read-Only Market Research & Alerting Tool",
    )
    subparsers = parser.add_subparsers(dest="subcommand", help="Available commands")

    # import-inventory
    p_inv = subparsers.add_parser("import-inventory", help="Fetch and import public CS2 inventory via SteamID64")
    p_inv.add_argument("steamid64", help="17-digit SteamID64 starting with 7656119")
    p_inv.add_argument("--db", default=get_default_db_path(), help="Database path")
    p_inv.add_argument("--count", type=int, default=1000, help="Items per page (Steam max 2000; default 1000)")
    p_inv.add_argument("--max-pages", type=int, default=25, help="Maximum pagination pages")

    # import-csv
    p_csv = subparsers.add_parser("import-csv", help="Import inventory items from CSV file")
    p_csv.add_argument("file", help="Path to CSV file")
    p_csv.add_argument("--db", default=get_default_db_path(), help="Database path")
    p_csv.add_argument("--owner-id", default="manual_user", help="Owner identifier")

    # import-json
    p_json = subparsers.add_parser("import-json", help="Import inventory items from JSON file")
    p_json.add_argument("file", help="Path to JSON file")
    p_json.add_argument("--db", default=get_default_db_path(), help="Database path")
    p_json.add_argument("--owner-id", default="manual_user", help="Owner identifier")

    # scan-sell
    p_scan = subparsers.add_parser("scan-sell", help="Scan marketable inventory items for sell opportunities")
    p_scan.add_argument("--db", default=get_default_db_path(), help="Database path")
    p_scan.add_argument("--owner-id", default=None, help="Filter inventory by owner")
    p_scan.add_argument("--snapshot-id", type=int, default=None, help="Specific inventory snapshot ID")
    p_scan.add_argument("--currency-id", type=int, default=1, help="Currency ID (1 = USD)")
    p_scan.add_argument("--min-bid-cents", type=int, default=100, help="Minimum bid in minor units (cents)")
    p_scan.add_argument("--min-multiple", type=float, default=1.0, help="Minimum bid multiple over baseline")
    p_scan.add_argument("--limit", type=int, default=None, help="Maximum unique items to evaluate in this run")
    p_scan.add_argument("--no-notifications", action="store_true", help="Do not dispatch notifications")

    # watchlist management (M6)
    p_wl = subparsers.add_parser("watchlist", help="Manage BUY/SELL item watchlists")
    wl_sub = p_wl.add_subparsers(dest="watchlist_action", help="Watchlist actions")

    p_wl_add = wl_sub.add_parser("add", help="Add a Steam URL (standard or grouped G...) to watchlist")
    p_wl_add.add_argument("url", help="Steam Market listing URL")
    p_wl_add.add_argument("--db", default=get_default_db_path(), help="Database path")
    p_wl_add.add_argument("--strategy", choices=["BUY", "SELL", "BOTH"], default="BUY")
    p_wl_add.add_argument("--currency-id", type=int, default=1)
    p_wl_add.add_argument("--max-buy-cents", type=int, default=None, help="Maximum buyer-pays cap in minor units")
    p_wl_add.add_argument("--max-price", default=None, help="Maximum buyer-pays cap in major units (e.g. 51.00)")
    p_wl_add.add_argument("--min-discount", type=float, default=None)
    p_wl_add.add_argument("--min-profit-cents", type=int, default=None)
    p_wl_add.add_argument("--min-roi", type=float, default=5.0)
    p_wl_add.add_argument("--notes", default=None)

    p_wl_list = wl_sub.add_parser("list", help="List configured watchlist entries")
    p_wl_list.add_argument("--db", default=get_default_db_path(), help="Database path")

    p_wl_rm = wl_sub.add_parser("remove", help="Remove a watchlist entry")
    p_wl_rm.add_argument("watchlist_id", type=int, help="Watchlist ID")
    p_wl_rm.add_argument("--db", default=get_default_db_path(), help="Database path")

    # import-history (M6)
    p_hist = subparsers.add_parser("import-history", help="Import daily price history JSON baseline points")
    p_hist.add_argument("market_hash_name", help="Exact CS2 market_hash_name")
    p_hist.add_argument("file", help="Path to daily history JSON file")
    p_hist.add_argument("--db", default=get_default_db_path(), help="Database path")
    p_hist.add_argument("--currency-id", type=int, default=1)

    # scan-buy (M6)
    p_buy = subparsers.add_parser("scan-buy", help="Scan active BUY watchlist entries for underpriced listings")
    p_buy.add_argument("--db", default=get_default_db_path(), help="Database path")
    p_buy.add_argument("--currency-id", type=int, default=1)
    p_buy.add_argument("--min-roi", type=float, default=5.0)
    p_buy.add_argument("--max-buy-cents", type=int, default=None, help="Maximum buyer-pays cap in minor units")
    p_buy.add_argument("--max-price", default=None, help="Maximum buyer-pays cap in major units (e.g. 51.00)")
    p_buy.add_argument("--no-notifications", action="store_true")

    # portfolio (M7)
    p_port = subparsers.add_parser("portfolio", help="Track 7-day locked holdings and realized Wallet P&L")
    port_sub = p_port.add_subparsers(dest="portfolio_action", help="Portfolio actions")

    p_p_add = port_sub.add_parser("add", help="Record an acquired skin holding")
    p_p_add.add_argument("market_hash_name", help="Exact CS2 market_hash_name")
    p_p_add.add_argument("--cost-cents", type=int, required=True, help="Total buyer-paid unit cost in cents")
    p_p_add.add_argument("--currency-id", type=int, default=1)
    p_p_add.add_argument("--quantity", type=int, default=1)
    p_p_add.add_argument("--days", type=int, default=7, help="Restriction days (default 7)")
    p_p_add.add_argument("--marketable-at", default=None, help="Explicit UTC unlock timestamp ISO-8601")
    p_p_add.add_argument("--asset-id", default=None)
    p_p_add.add_argument("--notes", default=None)
    p_p_add.add_argument("--db", default=get_default_db_path())

    p_p_list = port_sub.add_parser("list", help="Show portfolio holdings & Wallet P&L")
    p_p_list.add_argument("--currency-id", type=int, default=1)
    p_p_list.add_argument("--db", default=get_default_db_path())

    p_p_eval = port_sub.add_parser("evaluate", help="Check 7-day unlock readiness and break-even health")
    p_p_eval.add_argument("--no-notifications", action="store_true")
    p_p_eval.add_argument("--db", default=get_default_db_path())

    p_p_sell = port_sub.add_parser("sell", help="Mark a holding as manually sold")
    p_p_sell.add_argument("holding_id", type=int)
    p_p_sell.add_argument("--gross-cents", type=int, required=True, help="Buyer-paid listing gross price in cents")
    p_p_sell.add_argument("--notes", default=None)
    p_p_sell.add_argument("--db", default=get_default_db_path())

    # test-notifications / test-notification
    p_test = subparsers.add_parser(
        "test-notifications",
        aliases=["test-notification"],
        help="Send a test notification alert to verify Discord or Telegram",
    )
    p_test.add_argument("--channel", choices=["all", "discord", "telegram"], default="all")

    # status / channel-status
    p_stat = subparsers.add_parser(
        "status",
        aliases=["channel-status"],
        help="Show system, database, and notification channel status",
    )
    p_stat.add_argument("--db", default=get_default_db_path(), help="Database path")

    # serve (M8)
    p_srv = subparsers.add_parser("serve", help="Start FastAPI backend & web dashboard server")
    p_srv.add_argument("--db", default=get_default_db_path(), help="Database path")
    p_srv.add_argument("--host", default="127.0.0.1", help="Bind host (default 127.0.0.1)")
    p_srv.add_argument("--port", type=int, default=8000, help="Bind port (default 8000)")
    p_srv.add_argument("--no-scheduler", action="store_true", help="Disable periodic background polling loop")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point."""
    load_dotenv(override=False)
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.subcommand:
        parser.print_help()
        return 0

    if args.subcommand == "import-inventory":
        return asyncio.run(cmd_import_inventory(args))
    elif args.subcommand == "import-csv":
        return cmd_import_csv(args)
    elif args.subcommand == "import-json":
        return cmd_import_json(args)
    elif args.subcommand == "scan-sell":
        return asyncio.run(cmd_scan_sell(args))
    elif args.subcommand == "watchlist":
        action = getattr(args, "watchlist_action", None)
        if action == "add":
            return asyncio.run(cmd_watchlist_add(args))
        elif action == "list":
            return cmd_watchlist_list(args)
        elif action == "remove":
            return cmd_watchlist_remove(args)
        parser.print_help()
        return 1
    elif args.subcommand == "import-history":
        return cmd_import_history(args)
    elif args.subcommand == "scan-buy":
        return asyncio.run(cmd_scan_buy(args))
    elif args.subcommand == "portfolio":
        action = getattr(args, "portfolio_action", None)
        if action == "add":
            return cmd_portfolio_add(args)
        elif action == "list":
            return cmd_portfolio_list(args)
        elif action == "evaluate":
            return asyncio.run(cmd_portfolio_evaluate(args))
        elif action == "sell":
            return cmd_portfolio_sell(args)
        parser.print_help()
        return 1
    elif args.subcommand in ("test-notifications", "test-notification"):
        return asyncio.run(cmd_test_notifications(args))
    elif args.subcommand in ("status", "channel-status"):
        return cmd_status(args)
    elif args.subcommand == "serve":
        return cmd_serve(args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
