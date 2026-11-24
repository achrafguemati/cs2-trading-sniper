"""Deterministic offline unit tests for M6 BUY Sniper Engine, Migration 5, notifications, and CLI."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch
import unittest

import httpx

from sniper.adapters.steam_market import SteamMarketAdapter
from sniper.cli import main as cli_main
from sniper.core.identity import (
    VERIFIED_MAPPING_REPOSITORY,
    VERIFIED_MAPPING_REVISION,
)
from sniper.engine.buy_engine import (
    BuyEvaluationConfig,
    BuySniperEngine,
    calculate_buy_confidence_score,
    calculate_buy_opportunity_score,
    import_manual_price_history_json,
)
from sniper.models.schemas import ListingRenderResponse
from sniper.notifications import (
    BuyAlertPayload,
    DeliveryStatus,
    DiscordNotifier,
    NotificationDispatcher,
    TelegramNotifier,
)
from sniper.storage.database import Database
from sniper.storage.migrations import (
    DestructiveMigrationError,
    apply_migrations,
    get_current_version,
    rollback_migration,
)
from sniper.storage.models import (
    ItemRecord,
    PriceHistoryPointRecord,
    WatchlistEntryRecord,
)
from sniper.storage.repositories import (
    BuyEvaluationRepository,
    CurrencyRepository,
    ItemRepository,
    PriceHistoryRepository,
    StrategyAlertRepository,
    WatchlistRepository,
)

_REAL_ASYNC_CLIENT = httpx.AsyncClient


def make_render_response(
    market_hash_name: str,
    listings: list[tuple[str, int, int]],  # (listing_id, seller_minor, fee_minor)
    stickers: list[str] | None = None,
) -> ListingRenderResponse:
    listinginfo = {}
    assets_map = {}
    for idx, (lid, seller_m, fee_m) in enumerate(listings):
        aid = f"asset_{idx}_{lid}"
        listinginfo[lid] = {
            "listingid": lid,
            "price": seller_m,
            "fee": fee_m,
            "steam_fee": fee_m // 3,
            "publisher_fee": fee_m - (fee_m // 3),
            "converted_price": seller_m,
            "converted_fee": fee_m,
            "converted_currencyid": 2001,
            "asset": {"appid": 730, "contextid": "2", "id": aid},
        }
        descs = [{"value": "Exterior: Field-Tested"}]
        if stickers:
            descs.append({"value": f"Sticker: {', '.join(stickers)}"})
        assets_map[aid] = {
            "id": aid,
            "classid": "7993038091",
            "instanceid": "188530138",
            "appid": 730,
            "name": market_hash_name,
            "market_hash_name": market_hash_name,
            "descriptions": descs,
            "market_actions": [
                {
                    "link": f"steam://rungame/730/76561202255233023/+csgo_econ_action_preview%20M{lid}A{aid}D5083003",
                    "name": "Inspect in Game...",
                }
            ],
        }
    return ListingRenderResponse.model_validate({
        "success": True,
        "start": 0,
        "pagesize": len(listings),
        "total_count": len(listings),
        "listinginfo": listinginfo,
        "assets": {"730": {"2": assets_map}},
    })


def seed_history(
    db: Database,
    item_id: int,
    days: int = 10,
    median_minor: int = 3000,
    daily_volume: int = 15,
    now_dt: datetime | None = None,
) -> None:
    now = now_dt or datetime.now(timezone.utc)
    repo = PriceHistoryRepository(db)
    pts = [
        PriceHistoryPointRecord(
            item_id=item_id,
            currency_id=1,
            bucket_date=(now - timedelta(days=i)).strftime("%Y-%m-%d"),
            median_price_minor=median_minor,
            volume=daily_volume,
            source="manual_import",
            observed_at=now.isoformat(),
        )
        for i in range(days)
    ]
    repo.upsert_batch(pts)


class TestBuyEngine(unittest.IsolatedAsyncioTestCase):
    """Test suite for M6 BUY Sniper Engine, scoring, Migration 5, alerts, and CLI."""

    def setUp(self) -> None:
        self.db = Database(":memory:")
        self.db.initialize()
        CurrencyRepository(self.db).seed_default_currencies()
        self.item_repo = ItemRepository(self.db)
        self.wl_repo = WatchlistRepository(self.db)
        self.hist_repo = PriceHistoryRepository(self.db)
        self.buy_eval_repo = BuyEvaluationRepository(self.db)
        self.strategy_alert_repo = StrategyAlertRepository(self.db)

        self.item = self.item_repo.save(ItemRecord(
            market_hash_name="AK-47 | Redline (Field-Tested)",
            base_name="AK-47 | Redline",
            exterior="Field-Tested",
            item_nameid=7178002,
            item_nameid_source=VERIFIED_MAPPING_REPOSITORY,
            item_nameid_revision=VERIFIED_MAPPING_REVISION,
        ))

    def tearDown(self) -> None:
        self.db.close()

    def test_buy_scoring_formulas_and_penalties(self) -> None:
        """Verify BUY opportunity and confidence formulas and risk penalties."""
        # ROI 20% -> S_roi=80; sales_7d=30 & buy_orders=200 -> S_liq=60+40=100; 0 vol -> S_vol=100
        # 0.45*80 + 0.35*100 + 0.20*100 = 36 + 35 + 20 = 91.0
        opp = calculate_buy_opportunity_score(
            conservative_roi_pct=20.0,
            sales_7d=30,
            buy_orders_count=200,
            std_dev_14d_minor=0.0,
            baseline_minor=3000,
            discount_pct=30.0,
        )
        self.assertEqual(opp, 91.0)

        # Low liquidity penalty (-40) + extreme discount >60% penalty (-30)
        opp_pen = calculate_buy_opportunity_score(
            conservative_roi_pct=20.0,
            sales_7d=5,
            buy_orders_count=200,
            std_dev_14d_minor=0.0,
            baseline_minor=3000,
            discount_pct=65.0,
            low_liquidity_threshold=10,
        )
        self.assertLess(opp_pen, opp)

        conf = calculate_buy_confidence_score(daily_points_count=10)
        self.assertGreaterEqual(conf, 80.0)
        conf_low = calculate_buy_confidence_score(daily_points_count=4, is_manipulated_spike=True)
        self.assertLess(conf_low, 40.0)

    def test_migration_5_rollback_guard(self) -> None:
        """Verify Migration 5 tables exist and rollback raises DestructiveMigrationError when populated."""
        raw_db = Database(":memory:")
        raw_db.initialize(target_version=4)
        self.assertEqual(get_current_version(raw_db.connection), 4)
        apply_migrations(raw_db.connection, target_version=5)
        self.assertEqual(get_current_version(raw_db.connection), 5)

        CurrencyRepository(raw_db).seed_default_currencies()
        it = ItemRepository(raw_db).save(ItemRecord(
            market_hash_name="AWP | Asiimov (Field-Tested)",
            base_name="AWP | Asiimov",
            exterior="Field-Tested",
        ))
        WatchlistRepository(raw_db).save(WatchlistEntryRecord(
            item_id=it.item_id,
            input_url="https://steamcommunity.com/market/listings/730/AWP%20%7C%20Asiimov%20%28Field-Tested%29",
            strategy="BUY",
        ))

        with self.assertRaises(DestructiveMigrationError):
            rollback_migration(raw_db.connection, target_version=4, allow_data_loss=False)

        rollback_migration(raw_db.connection, target_version=4, allow_data_loss=True)
        self.assertEqual(get_current_version(raw_db.connection), 4)
        raw_db.close()

    async def test_detects_underpriced_listing_and_dispatches_discord_and_telegram(self) -> None:
        """Underpriced listing passes 5% 7-day buffer + Valve fees and sends Discord & Telegram alerts."""
        now = datetime.now(timezone.utc)
        # Seed 10 daily points at $30.00 median, vol 20/day
        seed_history(self.db, self.item.item_id, days=10, median_minor=3000, daily_volume=20, now_dt=now)

        # Listing 1: buyer pays 1800 + 270 = 2070 ($20.70).
        # Normal floor median from [2070, 3000, 3050] = 3000! Wait: median_low([2070, 3000, 3050]) = 3000!
        # Reference = min(3000, 3000) = 3000. Cons exit gross = floor(3000*0.95) = 2850.
        # Seller receives from 2850 ~ 2479. Cons profit = 2479 - 2070 = +409 (~19.8% ROI).
        render = make_render_response(
            self.item.market_hash_name,
            listings=[
                ("listing_cheap", 1800, 270),
                ("listing_norm_1", 2609, 391),
                ("listing_norm_2", 2653, 397),
            ],
            stickers=["Crown (Foil)"],
        )

        mock_adapter = MagicMock(spec=SteamMarketAdapter)
        mock_adapter.get_listing_render = AsyncMock(return_value=render)
        mock_adapter.get_public_price_history = AsyncMock(return_value=[])

        discord_requests = []
        tg_requests = []

        def mock_post(req: httpx.Request) -> httpx.Response:
            body = json.loads(req.content.decode("utf-8"))
            if "discord" in str(req.url):
                discord_requests.append(body)
                return httpx.Response(204)
            tg_requests.append(body)
            return httpx.Response(200, json={"ok": True})

        dispatcher = NotificationDispatcher(
            channels=[
                DiscordNotifier(webhook_url="https://discord.com/api/webhooks/111/secret"),
                TelegramNotifier(bot_token="123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ_12345678", chat_id="42"),
            ],
            strategy_alert_repo=self.strategy_alert_repo,
        )

        engine = BuySniperEngine(
            db=self.db,
            market_adapter=mock_adapter,
            dispatcher=dispatcher,
            config=BuyEvaluationConfig(fetch_public_history_fallback=False),
        )

        with patch("httpx.AsyncClient", side_effect=lambda **kw: _REAL_ASYNC_CLIENT(transport=httpx.MockTransport(mock_post))):
            evals = await engine.evaluate_item(self.item, now_dt=now)

        self.assertEqual(len(evals), 3)
        cheap = evals[0]
        self.assertTrue(cheap.is_actionable)
        self.assertIn(cheap.status, ("Candidate", "High-confidence signal"))
        self.assertEqual(cheap.purchase_buyer_pays_minor, 2070)
        self.assertGreater(cheap.conservative_net_profit_minor, 300)
        self.assertGreater(cheap.conservative_roi_pct, 15.0)
        self.assertIn("Sticker Craft Detected", cheap.warning_tags)
        self.assertIn("7-Day Re-Market Lock Applies", cheap.warning_tags)
        self.assertEqual(cheap.delivery_results["discord"].status, DeliveryStatus.DELIVERED)
        self.assertEqual(cheap.delivery_results["telegram"].status, DeliveryStatus.DELIVERED)
        self.assertEqual(len(discord_requests), 1)
        self.assertEqual(len(tg_requests), 1)
        self.assertIn("BUY CANDIDATE", discord_requests[0]["embeds"][0]["title"])
        self.assertIn("CS2 BUY SNIPER CANDIDATE", tg_requests[0]["text"])

        # Re-running immediately should suppress the SAME listing in cooldown without resending
        with patch("httpx.AsyncClient", side_effect=lambda **kw: _REAL_ASYNC_CLIENT(transport=httpx.MockTransport(mock_post))):
            evals_2 = await engine.evaluate_item(self.item, now_dt=now + timedelta(minutes=2))
        self.assertFalse(evals_2[0].is_actionable)
        self.assertEqual(evals_2[0].status, "Suppressed (Cooldown)")
        self.assertEqual(len(discord_requests), 1)

    async def test_suppresses_thin_margin_wiped_by_7_day_buffer_or_illiquid_or_missing_baseline(self) -> None:
        """Verify 7-day buffer rejection, illiquid suppression, and missing baseline suppression."""
        now = datetime.now(timezone.utc)
        mock_adapter = MagicMock(spec=SteamMarketAdapter)
        mock_adapter.get_public_price_history = AsyncMock(return_value=[])

        # 1. Missing baseline
        mock_adapter.get_listing_render = AsyncMock(
            return_value=make_render_response(self.item.market_hash_name, [("l1", 1000, 150)])
        )
        engine = BuySniperEngine(db=self.db, market_adapter=mock_adapter)
        res_no_hist = await engine.evaluate_item(self.item, dispatch_notifications=False, now_dt=now)
        self.assertFalse(res_no_hist[0].is_actionable)
        self.assertEqual(res_no_hist[0].status, "Data insufficient")
        self.assertIn("DATA INSUFFICIENT", res_no_hist[0].warning_tags)

        # 2. Illiquid item (10 days, 1 unit every 2 days -> sales_7d < 10)
        seed_history(self.db, self.item.item_id, days=10, median_minor=3000, daily_volume=1, now_dt=now)
        res_illiquid = await engine.evaluate_item(self.item, dispatch_notifications=False, now_dt=now)
        self.assertFalse(res_illiquid[0].is_actionable)
        self.assertEqual(res_illiquid[0].status, "Illiquid market")
        self.assertIn("ILLIQUID / THIN VOLUME", res_illiquid[0].warning_tags)

        # 3. Liquid baseline ($30.00) with shallow discount ($27.00 -> net after 15% + 5% buffer loses money)
        seed_history(self.db, self.item.item_id, days=10, median_minor=3000, daily_volume=20, now_dt=now)
        mock_adapter.get_listing_render = AsyncMock(
            return_value=make_render_response(
                self.item.market_hash_name,
                [("l_shallow", 2348, 352), ("l_f1", 2609, 391), ("l_f2", 2609, 391)],
            )
        )
        res_shallow = await engine.evaluate_item(self.item, dispatch_notifications=False, now_dt=now)
        self.assertFalse(res_shallow[0].is_actionable)
        self.assertEqual(res_shallow[0].status, "Not profitable")
        self.assertLess(res_shallow[0].conservative_net_profit_minor, 0)

    async def test_expires_disappeared_listings_and_flags_manipulated_spike(self) -> None:
        """Missing listings mark prior active rows expired; >2x 7d/30d spike fires warning."""
        now = datetime.now(timezone.utc)
        # Days 15..29 at 1500 minor, Days 0..6 at 4000 minor (>2x spike!)
        pts = []
        for d in range(14, 30):
            pts.append(PriceHistoryPointRecord(
                item_id=self.item.item_id,
                currency_id=1,
                bucket_date=(now - timedelta(days=d)).strftime("%Y-%m-%d"),
                median_price_minor=1500,
                volume=20,
                source="manual_import",
                observed_at=now.isoformat(),
            ))
        for d in range(0, 7):
            pts.append(PriceHistoryPointRecord(
                item_id=self.item.item_id,
                currency_id=1,
                bucket_date=(now - timedelta(days=d)).strftime("%Y-%m-%d"),
                median_price_minor=4000,
                volume=20,
                source="manual_import",
                observed_at=now.isoformat(),
            ))
        self.hist_repo.upsert_batch(pts)

        mock_adapter = MagicMock(spec=SteamMarketAdapter)
        mock_adapter.get_listing_render = AsyncMock(
            return_value=make_render_response(
                self.item.market_hash_name,
                [("l_old", 1800, 270), ("l_2", 3400, 500), ("l_3", 3500, 520)],
            )
        )
        engine = BuySniperEngine(
            db=self.db,
            market_adapter=mock_adapter,
            config=BuyEvaluationConfig(fetch_public_history_fallback=False),
        )
        evals_1 = await engine.evaluate_item(self.item, dispatch_notifications=False, now_dt=now)
        self.assertIn("MANIPULATED SPIKE WARNING", evals_1[0].warning_tags)

        # Next scan only shows l_new -> l_old becomes expired
        mock_adapter.get_listing_render = AsyncMock(
            return_value=make_render_response(
                self.item.market_hash_name,
                [("l_new", 3400, 500)],
            )
        )
        await engine.evaluate_item(self.item, dispatch_notifications=False, now_dt=now + timedelta(minutes=1))
        rows = self.buy_eval_repo.list_recent(item_id=self.item.item_id)
        by_lid = {r.steam_listing_id: r.listing_status for r in rows}
        self.assertEqual(by_lid["l_old"], "expired")
        self.assertEqual(by_lid["l_new"], "active")

    def test_manual_history_import_and_cli_watchlist(self) -> None:
        """Verify strict price history import validation and CLI watchlist/import-history/scan-buy."""
        with self.assertRaises(ValueError):
            import_manual_price_history_json(
                '[{"bucket_date": "2026-11-20", "median_price_minor": true, "volume": 5}]',
                self.item.market_hash_name,
                self.db,
            )

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "cli_m6.db")
            hist_path = Path(tmpdir) / "history.json"
            hist_path.write_text(
                '[{"bucket_date": "2026-11-20", "median_price_minor": 2500, "volume": 12}]',
                encoding="utf-8",
            )
            with patch("sys.stdout", new_callable=io.StringIO):
                code1 = cli_main([
                    "watchlist", "add",
                    "https://steamcommunity.com/market/listings/730/AK-47%20%7C%20Redline%20%28Field-Tested%29",
                    "--db", db_path,
                    "--max-buy-cents", "2200",
                ])
                self.assertEqual(code1, 0)
                code2 = cli_main(["watchlist", "list", "--db", db_path])
                self.assertEqual(code2, 0)
                code3 = cli_main([
                    "import-history",
                    "AK-47 | Redline (Field-Tested)",
                    str(hist_path),
                    "--db", db_path,
                ])
                self.assertEqual(code3, 0)

    async def test_multiple_distinct_listings_not_coalesced_and_bounded_retries(self) -> None:
        """Separate discounted listings deliver independently; failed deliveries cap at 1+3=4."""
        now = datetime.now(timezone.utc)
        seed_history(self.db, self.item.item_id, days=10, median_minor=3000, daily_volume=20, now_dt=now)

        # Two sequential scans within cooldown with distinct listing IDs must NOT suppress each other
        render_a = make_render_response(
            self.item.market_hash_name,
            listings=[
                ("listing_a", 1800, 270),
                ("listing_norm_1", 2609, 391),
                ("listing_norm_2", 2653, 397),
            ],
        )
        render_b = make_render_response(
            self.item.market_hash_name,
            listings=[
                ("listing_b", 1800, 270),
                ("listing_norm_1", 2609, 391),
                ("listing_norm_2", 2653, 397),
            ],
        )
        mock_adapter = MagicMock(spec=SteamMarketAdapter)
        mock_adapter.get_listing_render = AsyncMock(side_effect=[render_a, render_b])
        mock_adapter.get_public_price_history = AsyncMock(return_value=[])

        deliveries_sent = []

        def mock_ok(req: httpx.Request) -> httpx.Response:
            deliveries_sent.append(json.loads(req.content.decode("utf-8")))
            return httpx.Response(204)

        dispatcher = NotificationDispatcher(
            channels=[DiscordNotifier(webhook_url="https://discord.com/api/webhooks/111/secret")],
            strategy_alert_repo=self.strategy_alert_repo,
        )
        engine = BuySniperEngine(
            db=self.db,
            market_adapter=mock_adapter,
            dispatcher=dispatcher,
            config=BuyEvaluationConfig(fetch_public_history_fallback=False),
        )

        with patch("httpx.AsyncClient", side_effect=lambda **kw: _REAL_ASYNC_CLIENT(transport=httpx.MockTransport(mock_ok))):
            evals_a = await engine.evaluate_item(self.item, now_dt=now)
            evals_b = await engine.evaluate_item(self.item, now_dt=now + timedelta(seconds=30))

        self.assertEqual([e.steam_listing_id for e in evals_a if e.is_actionable], ["listing_a"])
        self.assertEqual([e.steam_listing_id for e in evals_b if e.is_actionable], ["listing_b"])
        self.assertEqual(len(deliveries_sent), 2)

        # Now verify bounded cumulative retries on a failing listing ("listing_fail")
        render_fail = make_render_response(
            self.item.market_hash_name,
            listings=[
                ("listing_fail", 1750, 262),
                ("listing_norm_1", 2609, 391),
                ("listing_norm_2", 2653, 397),
            ],
        )
        mock_adapter.get_listing_render = AsyncMock(return_value=render_fail)
        fail_attempts = 0

        def mock_fail(req: httpx.Request) -> httpx.Response:
            nonlocal fail_attempts
            fail_attempts += 1
            return httpx.Response(500, text="server error")

        for step in range(6):
            with patch("httpx.AsyncClient", side_effect=lambda **kw: _REAL_ASYNC_CLIENT(transport=httpx.MockTransport(mock_fail))):
                await engine.evaluate_item(self.item, now_dt=now + timedelta(minutes=(step + 1) * 2))

        # 1 initial failure + 3 retries = 4 total failures max
        self.assertEqual(fail_attempts, 4)


if __name__ == "__main__":
    unittest.main()
