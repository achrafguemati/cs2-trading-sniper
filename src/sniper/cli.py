"""Command-line interface for CS2 Trading Sniper (Milestone 5)."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import sys
from typing import Sequence

from dotenv import load_dotenv

# Load local .env early without overriding existing process environment variables
load_dotenv(override=False)

from sniper.core.http_client import ReadOnlyHttpClient
from sniper.adapters.inventory import ManualInventoryImporter, PublicInventoryClient, validate_steamid64
from sniper.adapters.steam_market import SteamMarketAdapter
from sniper.core.rate_limiter import SequentialRateLimiter
from sniper.engine.sell_engine import SellEvaluationConfig, SellSniperEngine
from sniper.notifications import (
    AlertPayload,
    DeliveryStatus,
    DiscordNotifier,
    DiscordWebhookChannel,
    NotificationDispatcher,
    TelegramBotChannel,
    TelegramNotifier,
)
from sniper.storage.database import Database
from sniper.storage.models import ItemRecord
from sniper.storage.repositories import (
    AlertRepository,
    CurrencyRepository,
    InventoryRepository,
    ItemRepository,
)
from sniper.storage.service import ItemIdentityService

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
    try:
        async with ReadOnlyHttpClient() as http_client:
            market_adapter = SteamMarketAdapter(http_client=http_client)
            identity_service = ItemIdentityService(db)
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

            print(f"Catalog Items:         {item_count}")
            print(f"Inventory Snapshots:   {snap_count}")
            print(f"Inventory Items:       {inv_count}")
            print(f"Orderbook Snapshots:   {ob_count}")
            print(f"Alert Events Recorded: {alert_count}")
        finally:
            db.close()

    _print_channel_status()
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
    p_inv.add_argument("--count", type=int, default=5000, help="Items per page")
    p_inv.add_argument("--max-pages", type=int, default=10, help="Maximum pagination pages")

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
    p_scan.add_argument("--no-notifications", action="store_true", help="Do not dispatch notifications")

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

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point."""
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
    elif args.subcommand in ("test-notifications", "test-notification"):
        return asyncio.run(cmd_test_notifications(args))
    elif args.subcommand in ("status", "channel-status"):
        return cmd_status(args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
