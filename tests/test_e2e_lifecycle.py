"""End-to-end offline lifecycle integration suite (Milestone 9).

Exercises the full cross-milestone workflow through the FastAPI ASGI interface,
SQLite persistence, integer M4 fee math, notification formatting & redaction,
and the shared sequential rate limiter without issuing live Steam traffic:
  1. Watchlist addition & verified item identity resolution
  2. Daily baseline seeding & BUY Sniper listing scan + Discord/Telegram alert
  3. Portfolio holding under Steam authoritative 7-day lock (suppressed while locked)
  4. Under Water break-even threshold detection & alert dispatch
  5. Post-unlock transition HOLDING -> READY_TO_LIST & [RESALE READY] dispatch
  6. Public Steam inventory import (strict (classid, instanceid) matching)
  7. SELL Sniper unusual top-bid detection & alert dispatch
  8. Manual sale settlement & exact integer Steam Wallet P&L accounting
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

import httpx

from sniper.api.app import create_app
from sniper.core.currency import CurrencyConfig
from sniper.core.fees import calculate_exact_breakeven_buyer_pays, calculate_seller_receives
from sniper.core.http_client import ReadOnlyHttpClient
from sniper.core.rate_limiter import RateLimiterConfig, SequentialRateLimiter
from sniper.engine.portfolio_service import parse_steam_marketable_timestamp
from sniper.notifications.discord import DiscordNotifier
from sniper.notifications.models import (
    AlertPayload,
    BuyAlertPayload,
    DeliveryStatus,
    NotificationResult,
    PortfolioAlertPayload,
)
from sniper.notifications.telegram import TelegramNotifier
from sniper.storage.models import OrderbookSnapshotRecord
from sniper.storage.repositories import OrderbookRepository


class _RecordingDiscordNotifier(DiscordNotifier):
    """Uses production embed builder while capturing outbound payloads offline."""

    def __init__(self, sent_log: list[tuple[str, dict]]) -> None:
        super().__init__(
            webhook_url="https://discord.com/api/webhooks/123/OFFLINE_RECORDING_TOKEN",
            enabled=True,
        )
        self._sent_log = sent_log

    async def send(self, alert: AlertPayload | BuyAlertPayload | PortfolioAlertPayload) -> NotificationResult:
        embed = self.build_embed(alert)
        self._sent_log.append(("discord", embed))
        return NotificationResult(
            channel=self.channel_name,
            status=DeliveryStatus.DELIVERED,
            attempts=1,
            delivered_at=datetime.now(timezone.utc).isoformat(),
        )


class _RecordingTelegramNotifier(TelegramNotifier):
    """Uses production HTML formatter while capturing outbound messages offline."""

    def __init__(self, sent_log: list[tuple[str, str]]) -> None:
        super().__init__(
            bot_token="987654:OFFLINE_RECORDING_TOKEN",
            chat_id="100200300",
            enabled=True,
        )
        self._sent_log = sent_log

    async def send(self, alert: AlertPayload | BuyAlertPayload | PortfolioAlertPayload) -> NotificationResult:
        html_body = self.format_html_message(alert)
        self._sent_log.append(("telegram", html_body))
        return NotificationResult(
            channel=self.channel_name,
            status=DeliveryStatus.DELIVERED,
            attempts=1,
            delivered_at=datetime.now(timezone.utc).isoformat(),
        )


def _build_e2e_steam_transport(state: dict[str, int]) -> httpx.MockTransport:
    """Mock Steam Community Market responses reflecting live progression."""

    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if "/itemordershistogram" in url:
            # Histogram price varies dynamically based on lifecycle phase
            buy_minor = state["highest_buy_minor"]
            ask_minor = state["lowest_ask_minor"]
            buy_graph = (
                [
                    [buy_minor / 100.0, 3, f"3 buy orders at ${buy_minor / 100.0:.2f} or higher"],
                    [24.00, 18, "18 buy orders at $24.00 or higher"],
                ]
                if buy_minor > 2400
                else [[24.00, 18, "18 buy orders at $24.00 or higher"]]
            )
            return httpx.Response(200, json={
                "success": 1,
                "highest_buy_order": str(buy_minor),
                "lowest_sell_order": str(ask_minor),
                "buy_order_count": "120",
                "sell_order_count": "45",
                "buy_order_graph": buy_graph,
                "sell_order_graph": [
                    [ask_minor / 100.0, 2, f"2 sell orders at ${ask_minor / 100.0:.2f} or lower"],
                ],
            })

        if "/render/" in url:
            return httpx.Response(200, json={
                "success": True,
                "start": 0,
                "pagesize": 3,
                "total_count": 3,
                "listinginfo": {
                    "e2e_snipe_1": {
                        "listingid": "e2e_snipe_1",
                        "price": 1750,
                        "fee": 262,
                        "steam_fee": 87,
                        "publisher_fee": 175,
                        "converted_price": 1750,
                        "converted_fee": 262,
                        "converted_currencyid": 2001,
                        "asset": {"appid": 730, "contextid": "2", "id": "asset_snipe_1"},
                    },
                    "e2e_floor_2": {
                        "listingid": "e2e_floor_2",
                        "price": 2609,
                        "fee": 391,
                        "steam_fee": 130,
                        "publisher_fee": 261,
                        "converted_price": 2609,
                        "converted_fee": 391,
                        "converted_currencyid": 2001,
                        "asset": {"appid": 730, "contextid": "2", "id": "asset_floor_2"},
                    },
                    "e2e_floor_3": {
                        "listingid": "e2e_floor_3",
                        "price": 2653,
                        "fee": 397,
                        "steam_fee": 132,
                        "publisher_fee": 265,
                        "converted_price": 2653,
                        "converted_fee": 397,
                        "converted_currencyid": 2001,
                        "asset": {"appid": 730, "contextid": "2", "id": "asset_floor_3"},
                    },
                },
                "assets": {
                    "730": {
                        "2": {
                            "asset_snipe_1": {
                                "id": "asset_snipe_1", "classid": "10", "instanceid": "0", "appid": 730,
                                "name": "AK-47 | Redline (Field-Tested)",
                                "market_hash_name": "AK-47 | Redline (Field-Tested)",
                                "descriptions": [{"value": "Sticker: Crown (Foil)"}],
                                "market_actions": [],
                            },
                            "asset_floor_2": {
                                "id": "asset_floor_2", "classid": "10", "instanceid": "0", "appid": 730,
                                "name": "AK-47 | Redline (Field-Tested)",
                                "market_hash_name": "AK-47 | Redline (Field-Tested)",
                                "descriptions": [], "market_actions": [],
                            },
                            "asset_floor_3": {
                                "id": "asset_floor_3", "classid": "10", "instanceid": "0", "appid": 730,
                                "name": "AK-47 | Redline (Field-Tested)",
                                "market_hash_name": "AK-47 | Redline (Field-Tested)",
                                "descriptions": [], "market_actions": [],
                            },
                        }
                    }
                },
            })

        if "/inventory/" in url:
            # Verify strict (classid, instanceid) matching: asset_ok matches ("7993", "0"),
            # while asset_mismatch ("7993", "999") has NO matching description and must remain locked.
            return httpx.Response(200, json={
                "success": 1,
                "assets": [
                    {"appid": 730, "contextid": "2", "assetid": "111001", "classid": "7993", "instanceid": "0", "amount": "1"},
                    {"appid": 730, "contextid": "2", "assetid": "111002", "classid": "7993", "instanceid": "999", "amount": "1"},
                ],
                "descriptions": [
                    {
                        "appid": 730,
                        "classid": "7993",
                        "instanceid": "0",
                        "name": "AK-47 | Redline (Field-Tested)",
                        "market_hash_name": "AK-47 | Redline (Field-Tested)",
                        "tradable": 1,
                        "marketable": 1,
                    }
                ],
                "more_items": False,
                "total_inventory_count": 2,
            })

        return httpx.Response(404, json={"error": "unhandled"})

    return httpx.MockTransport(handler)


class TestEndToEndLifecycle(unittest.IsolatedAsyncioTestCase):
    """Complete offline lifecycle audit across M1–M9 modules."""

    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._steam_state = {"highest_buy_minor": 2400, "lowest_ask_minor": 3000}
        self._discord_sent: list[tuple[str, dict]] = []
        self._telegram_sent: list[tuple[str, str]] = []

        self.http_client = ReadOnlyHttpClient(
            rate_limiter=SequentialRateLimiter(
                RateLimiterConfig(requests_per_minute=6000.0, jitter_max_seconds=0.0)
            ),
            transport=_build_e2e_steam_transport(self._steam_state),
        )
        self.app = create_app(
            db_path=Path(self._tmp.name) / "e2e_lifecycle.db",
            http_client=self.http_client,
        )
        self.container = self.app.state.container
        self.container.scheduler.dispatcher._channels = [
            _RecordingDiscordNotifier(self._discord_sent),
            _RecordingTelegramNotifier(self._telegram_sent),
        ]
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url="http://127.0.0.1:8000",
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        await self.http_client.aclose()
        if self.container.nameid_db is not None:
            self.container.nameid_db.close()
        self.container.db.close()
        self._tmp.cleanup()

    async def test_complete_trading_research_lifecycle(self) -> None:
        item_name = "AK-47 | Redline (Field-Tested)"

        # 1. Watchlist Add -> Verified item_nameid resolution
        r_wl = await self.client.post("/api/watchlists", json={
            "url": "https://steamcommunity.com/market/listings/730/AK-47%20%7C%20Redline%20%28Field-Tested%29",
            "strategy": "BUY",
            "min_roi_pct": 5.0,
        })
        self.assertEqual(r_wl.status_code, 201)
        item_id = r_wl.json()["item"]["item_id"]
        self.assertEqual(r_wl.json()["item"]["item_nameid"], 7178002)

        # 2. Seed daily historical price points + run BUY scan with Discord & Telegram dispatch
        now = datetime.now(timezone.utc)
        pts = [
            {
                "bucket_date": (now - timedelta(days=i)).strftime("%Y-%m-%d"),
                "median_price_minor": 3000,
                "volume": 25,
            }
            for i in range(12)
        ]
        r_hist = await self.client.post("/api/history/import", json={
            "market_hash_name": item_name,
            "currency_id": 1,
            "points": pts,
        })
        self.assertEqual(r_hist.status_code, 200)

        r_buy = await self.client.post("/api/buy/scan?dispatch_notifications=true")
        self.assertEqual(r_buy.status_code, 200)
        self.assertGreaterEqual(r_buy.json()["actionable"], 1)
        self.assertTrue(
            any("BUY CANDIDATE" in embed.get("title", "") for _, embed in self._discord_sent),
            f"Expected Discord BUY alert, got {self._discord_sent}",
        )
        self.assertTrue(
            any("BUY SNIPER CANDIDATE" in html_body for _, html_body in self._telegram_sent),
            f"Expected Telegram BUY alert, got {self._telegram_sent}",
        )

        # 3. Acquire holding under Steam authoritative 7-day lock ("Tradable After ...")
        lock_dt, lock_src = parse_steam_marketable_timestamp(
            [{"value": "Tradable After Nov 27, 2026 (8:00:00) GMT"}]
        )
        self.assertEqual(lock_src, "steam_observed")
        self.assertEqual(lock_dt, datetime(2026, 11, 27, 8, 0, 0, tzinfo=timezone.utc))

        r_hold = await self.client.post("/api/portfolio/holdings", json={
            "market_hash_name": item_name,
            "acquisition_cost_minor": 2012,
            "marketable_at": lock_dt.isoformat(),
            "notes": "Bought e2e_snipe_1 manually on SCM",
        })
        self.assertEqual(r_hold.status_code, 201)
        hid = r_hold.json()["holding"]["holding_id"]

        # Exact integer break-even check against M4 fee calculator
        usd_cfg = CurrencyConfig(
            currency_id=1,
            code="USD",
            symbol="$",
            minor_unit_precision=2,
            is_metadata_verified=True,
            is_fee_verified=True,
        )
        expected_be = calculate_exact_breakeven_buyer_pays(2012, currency=usd_cfg)
        self.assertEqual(r_hold.json()["holding"]["breakeven_buyer_pays_minor"], expected_be)

        # Evaluate while locked -> zero RESALE READY notifications dispatched
        views_locked = await self.container.portfolio_service.evaluate_holdings(
            floor_overrides={hid: 3000},
            dispatch_notifications=True,
            now_dt=datetime(2026, 11, 23, 12, 0, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(len(views_locked), 1)
        self.assertTrue(views_locked[0].is_locked)
        self.assertEqual(views_locked[0].status, "HOLDING")
        self.assertFalse(any("RESALE READY" in e.get("title", "") for _, e in self._discord_sent))

        # 4. Drop market floor sharply -> verify Under Water warning & alert
        views_drop = await self.container.portfolio_service.evaluate_holdings(
            floor_overrides={hid: 1800},
            dispatch_notifications=True,
            now_dt=datetime(2026, 11, 24, 12, 0, 0, tzinfo=timezone.utc),
        )
        self.assertTrue(views_drop[0].is_under_water)
        self.assertTrue(any("UNDER WATER" in e.get("title", "") for _, e in self._discord_sent))

        # 5. Advance clock past unlock timestamp & recover floor -> HOLDING -> READY_TO_LIST + [RESALE READY]
        self._steam_state["lowest_ask_minor"] = 3100
        views_unlocked = await self.container.portfolio_service.evaluate_holdings(
            market_adapter=self.container.market_adapter,
            dispatch_notifications=True,
            now_dt=datetime(2026, 11, 27, 9, 0, 0, tzinfo=timezone.utc),
        )
        self.assertFalse(views_unlocked[0].is_locked)
        self.assertEqual(views_unlocked[0].status, "READY_TO_LIST")
        self.assertTrue(any("RESALE READY" in e.get("title", "") for _, e in self._discord_sent))
        self.assertTrue(any("RESALE READY" in h for _, h in self._telegram_sent))

        # 6. Import public Steam inventory -> check exact (classid, instanceid) isolation
        r_inv = await self.client.post("/api/inventory/import-steam", json={
            "steamid64": "76561198000000001",
            "count": 100,
            "max_pages": 2,
        })
        self.assertEqual(r_inv.status_code, 200)
        self.assertEqual(r_inv.json()["imported_count"], 2)
        self.assertEqual(r_inv.json()["marketable_count"], 1)  # asset 111002 lacked exact instanceid=999 desc

        # 7. Seed orderbook baseline (~2400) & trigger high top bid (3800) -> SELL SNIPER alert
        ob_repo = OrderbookRepository(self.container.db)
        recent_ts = datetime.now(timezone.utc)
        for idx in range(30):
            ob_repo.insert_snapshot(OrderbookSnapshotRecord(
                item_id=item_id,
                currency_id=1,
                highest_buy_order_minor=2400,
                lowest_sell_order_minor=2800,
                top_level_buy_quantity=5,
                cumulative_buy_quantity=50,
                observed_at=(recent_ts - timedelta(minutes=30 * (idx + 1))).isoformat(),
            ))
        self._steam_state["highest_buy_minor"] = 4800
        self._steam_state["lowest_ask_minor"] = 4900

        r_sell = await self.client.post("/api/sell/scan?dispatch_notifications=true")
        self.assertEqual(r_sell.status_code, 200)
        self.assertEqual(r_sell.json()["evaluated"], 1)
        self.assertEqual(r_sell.json()["actionable"], 1)
        self.assertTrue(any("SELL" in e.get("title", "") for _, e in self._discord_sent))
        self.assertTrue(any("CS2 SELL SNIPER ALERT" in h for _, h in self._telegram_sent))

        # 8. Record manual sale -> verify exact integer Steam Wallet P&L & Wallet segregation notice
        r_sale = await self.client.post(f"/api/portfolio/holdings/{hid}/sell", json={
            "gross_sale_price_minor": 3200,
            "notes": "Sold manually after 7d unlock",
        })
        self.assertEqual(r_sale.status_code, 200)
        sold = r_sale.json()["holding"]
        expected_net = calculate_seller_receives(
            3200, currency=usd_cfg, validate_market_listing=False
        ).seller_receives
        expected_pnl = expected_net - 2012
        self.assertEqual(sold["status"], "SOLD")
        self.assertEqual(sold["realized_net_proceeds_minor"], expected_net)
        self.assertEqual(sold["realized_net_proceeds_minor"] - sold["acquisition_cost_minor"], expected_pnl)

        r_over = await self.client.get("/api/overview")
        self.assertEqual(r_over.status_code, 200)
        over = r_over.json()
        self.assertEqual(over["portfolio"]["realized_wallet_pnl_minor"], expected_pnl)
        self.assertIn("Steam Wallet funds", over["wallet_notice"])

        r_alerts = await self.client.get("/api/alerts")
        self.assertEqual(r_alerts.status_code, 200)
        strategies_seen = {a["strategy"] for a in r_alerts.json()["alerts"]}
        self.assertEqual(strategies_seen, {"BUY", "PORTFOLIO", "SELL"})
