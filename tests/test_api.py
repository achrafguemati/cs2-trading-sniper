"""Deterministic offline FastAPI ASGI integration tests (Milestone 8)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import tempfile
import unittest

import httpx

from sniper.api.app import create_app
from sniper.core.http_client import ReadOnlyHttpClient
from sniper.core.rate_limiter import RateLimiterConfig, SequentialRateLimiter


def _build_mock_transport() -> httpx.MockTransport:
    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if "/itemordershistogram" in url:
            return httpx.Response(200, json={
                "success": 1,
                "highest_buy_order": "2400",
                "lowest_sell_order": "2800",
                "buy_order_count": "150",
                "buy_order_graph": [[24.00, 3, "3 buy orders at $24.00 or higher"]],
            })
        if "/render/" in url:
            return httpx.Response(200, json={
                "success": True,
                "start": 0,
                "pagesize": 3,
                "total_count": 3,
                "listinginfo": {
                    "l_cheap": {
                        "listingid": "l_cheap",
                        "price": 1750,
                        "fee": 262,
                        "steam_fee": 87,
                        "publisher_fee": 175,
                        "converted_price": 1750,
                        "converted_fee": 262,
                        "converted_currencyid": 2001,
                        "asset": {"appid": 730, "contextid": "2", "id": "asset_1"},
                    },
                    "l_floor_1": {
                        "listingid": "l_floor_1",
                        "price": 2609,
                        "fee": 391,
                        "steam_fee": 130,
                        "publisher_fee": 261,
                        "converted_price": 2609,
                        "converted_fee": 391,
                        "converted_currencyid": 2001,
                        "asset": {"appid": 730, "contextid": "2", "id": "asset_2"},
                    },
                    "l_floor_2": {
                        "listingid": "l_floor_2",
                        "price": 2653,
                        "fee": 397,
                        "steam_fee": 132,
                        "publisher_fee": 265,
                        "converted_price": 2653,
                        "converted_fee": 397,
                        "converted_currencyid": 2001,
                        "asset": {"appid": 730, "contextid": "2", "id": "asset_3"},
                    },
                },
                "assets": {
                    "730": {
                        "2": {
                            "asset_1": {
                                "id": "asset_1", "classid": "1", "instanceid": "0", "appid": 730,
                                "name": "AK-47 | Redline (Field-Tested)",
                                "market_hash_name": "AK-47 | Redline (Field-Tested)",
                                "descriptions": [{"value": "Sticker: Crown (Foil)"}],
                                "market_actions": [],
                            },
                            "asset_2": {
                                "id": "asset_2", "classid": "1", "instanceid": "0", "appid": 730,
                                "name": "AK-47 | Redline (Field-Tested)",
                                "market_hash_name": "AK-47 | Redline (Field-Tested)",
                                "descriptions": [], "market_actions": [],
                            },
                            "asset_3": {
                                "id": "asset_3", "classid": "1", "instanceid": "0", "appid": 730,
                                "name": "AK-47 | Redline (Field-Tested)",
                                "market_hash_name": "AK-47 | Redline (Field-Tested)",
                                "descriptions": [], "market_actions": [],
                            },
                        }
                    }
                },
            })
        if "/inventory/" in url:
            return httpx.Response(200, json={
                "success": 1,
                "assets": [{"appid": 730, "contextid": "2", "assetid": "998877", "classid": "7993", "instanceid": "0", "amount": "1"}],
                "descriptions": [{
                    "appid": 730, "classid": "7993", "instanceid": "0",
                    "name": "AK-47 | Redline (Field-Tested)",
                    "market_hash_name": "AK-47 | Redline (Field-Tested)",
                    "marketable": 1, "tradable": 1, "market_marketable_restriction": 0,
                }],
            })
        if "/market/listings/730/" in url:
            return httpx.Response(200, text="<html><body>Steam page</body></html>")
        return httpx.Response(404)

    return httpx.MockTransport(handler)


class TestFastApiBackend(unittest.IsolatedAsyncioTestCase):
    """Offline tests covering REST endpoints, secret redaction, safety clamps, and SPA serving."""

    async def asyncSetUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmpdir.name) / "api_test.db"
        self.dist_dir = Path(self.tmpdir.name) / "dist"
        self.dist_dir.mkdir(parents=True, exist_ok=True)
        (self.dist_dir / "index.html").write_text(
            "<html><body>CS2 Sniper Dashboard Test</body></html>", encoding="utf-8"
        )

        limiter = SequentialRateLimiter(
            config=RateLimiterConfig(requests_per_minute=6000.0, jitter_max_seconds=0.0)
        )
        self.http_client = ReadOnlyHttpClient(rate_limiter=limiter, transport=_build_mock_transport())
        self.app = create_app(
            db_path=self.db_path,
            http_client=self.http_client,
            enable_scheduler=False,
            frontend_dist=self.dist_dir,
        )
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url="http://testserver",
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        container = self.app.state.container
        await container.scheduler.stop()
        await self.http_client.aclose()
        if container.nameid_db is not None:
            container.nameid_db.close()
        container.db.close()
        self.tmpdir.cleanup()

    async def test_health_overview_and_spa_root(self) -> None:
        r_root = await self.client.get("/")
        self.assertEqual(r_root.status_code, 200)
        self.assertIn("CS2 Sniper Dashboard Test", r_root.text)

        r_health = await self.client.get("/api/health")
        self.assertEqual(r_health.status_code, 200)
        h = r_health.json()
        self.assertTrue(h["read_only"])
        self.assertTrue(h["nameid_mappings_loaded"])
        self.assertEqual(h["breaker_state"], "CLOSED")

        r_over = await self.client.get("/api/overview")
        self.assertEqual(r_over.status_code, 200)
        self.assertIn("Steam Wallet funds", r_over.json()["wallet_notice"])

        # Unknown API route must return 404 JSON rather than SPA index.html
        r_missing_api = await self.client.get("/api/nonexistent")
        self.assertEqual(r_missing_api.status_code, 404)

    async def test_watchlist_history_buy_scan_sell_scan_and_portfolio_flow(self) -> None:
        # 1. Add watchlist item
        r_wl = await self.client.post("/api/watchlists", json={
            "url": "https://steamcommunity.com/market/listings/730/AK-47%20%7C%20Redline%20%28Field-Tested%29",
            "strategy": "BUY",
            "min_roi_pct": 5.0,
        })
        self.assertEqual(r_wl.status_code, 201)
        wl_data = r_wl.json()
        self.assertEqual(wl_data["item"]["item_nameid"], 7178002)

        # 2. Seed daily history
        now = datetime.now(timezone.utc)
        pts = [
            {
                "bucket_date": (now - timedelta(days=i)).strftime("%Y-%m-%d"),
                "median_price_minor": 3000,
                "volume": 20,
            }
            for i in range(10)
        ]
        r_hist = await self.client.post("/api/history/import", json={
            "market_hash_name": "AK-47 | Redline (Field-Tested)",
            "currency_id": 1,
            "points": pts,
        })
        self.assertEqual(r_hist.status_code, 200)
        self.assertEqual(r_hist.json()["imported"], 10)

        # 3. Trigger BUY scan -> finds l_cheap
        r_buy = await self.client.post("/api/buy/scan?dispatch_notifications=false")
        self.assertEqual(r_buy.status_code, 200)
        self.assertGreaterEqual(r_buy.json()["actionable"], 1)

        r_cands = await self.client.get("/api/buy/candidates?actionable_only=true")
        self.assertEqual(r_cands.status_code, 200)
        self.assertEqual(r_cands.json()["candidates"][0]["steam_listing_id"], "l_cheap")

        # 4. Import manual inventory + trigger SELL scan
        r_inv = await self.client.post("/api/inventory/import-manual", json={
            "format": "csv",
            "content": "market_hash_name,amount,marketable\nAK-47 | Redline (Field-Tested),1,true\n",
        })
        self.assertEqual(r_inv.status_code, 200)
        self.assertEqual(r_inv.json()["imported_count"], 1)

        r_sell = await self.client.post("/api/sell/scan?dispatch_notifications=false")
        self.assertEqual(r_sell.status_code, 200)
        self.assertEqual(r_sell.json()["evaluated"], 1)

        # 5. Add Portfolio holding -> evaluate -> sell
        r_hold = await self.client.post("/api/portfolio/holdings", json={
            "market_hash_name": "AK-47 | Redline (Field-Tested)",
            "acquisition_cost_minor": 2012,
            "restriction_days": 7,
        })
        self.assertEqual(r_hold.status_code, 201)
        hid = r_hold.json()["holding"]["holding_id"]

        r_eval = await self.client.post("/api/portfolio/evaluate?dispatch_notifications=false")
        self.assertEqual(r_eval.status_code, 200)
        self.assertEqual(r_eval.json()["evaluated"], 1)

        r_sell_h = await self.client.post(f"/api/portfolio/holdings/{hid}/sell", json={
            "gross_sale_price_minor": 3000,
        })
        self.assertEqual(r_sell_h.status_code, 200)
        self.assertEqual(r_sell_h.json()["holding"]["status"], "SOLD")

        # 6. Unified Alerts endpoint
        r_alerts = await self.client.get("/api/alerts")
        self.assertEqual(r_alerts.status_code, 200)
        self.assertGreaterEqual(len(r_alerts.json()["alerts"]), 2)

    async def test_settings_zero_secret_leak_and_rpm_clamp_and_breaker_reset(self) -> None:
        old_tg = os.environ.get("TELEGRAM_BOT_TOKEN")
        old_tg_chat = os.environ.get("TELEGRAM_CHAT_ID")
        old_dc = os.environ.get("DISCORD_WEBHOOK_URL")
        try:
            os.environ["TELEGRAM_BOT_TOKEN"] = "123456789:SECRET_BOT_TOKEN_SHOULD_NOT_LEAK_XYZ"
            os.environ["TELEGRAM_CHAT_ID"] = "987654321"
            os.environ["DISCORD_WEBHOOK_URL"] = "https://discord.com/api/webhooks/111/SECRET_WEBHOOK_SHOULD_NOT_LEAK"

            r_set = await self.client.get("/api/settings")
            self.assertEqual(r_set.status_code, 200)
            raw_text = r_set.text
            self.assertNotIn("SECRET_BOT_TOKEN_SHOULD_NOT_LEAK_XYZ", raw_text)
            self.assertNotIn("SECRET_WEBHOOK_SHOULD_NOT_LEAK", raw_text)
            self.assertTrue(r_set.json()["channels"]["telegram"]["configured"])
            self.assertTrue(r_set.json()["channels"]["discord"]["configured"])

            # Attempt RPM > 12 must be rejected cleanly with 422
            r_bad_rpm = await self.client.put("/api/settings", json={
                "default_currency_id": 1,
                "requests_per_minute": 30.0,
                "cycle_interval_seconds": 300.0,
                "min_roi_pct": 5.0,
                "min_sell_multiplier": 1.5,
                "min_bid_minor": 100,
                "cooldown_seconds": 1800,
                "scheduler_enabled": True,
            })
            self.assertEqual(r_bad_rpm.status_code, 422)

            # Reset breaker requires confirm_human_review=true
            self.http_client.rate_limiter.record_403_or_security_block("Simulated 403")
            r_sh = await self.client.get("/api/source-health")
            self.assertEqual(r_sh.json()["breaker"]["state"], "HALTED")
            r_bad_reset = await self.client.post(
                "/api/source-health/reset-breaker", json={"confirm_human_review": False}
            )
            self.assertEqual(r_bad_reset.status_code, 400)
            r_reset = await self.client.post(
                "/api/source-health/reset-breaker", json={"confirm_human_review": True}
            )
            self.assertEqual(r_reset.status_code, 200)
            self.assertEqual(r_reset.json()["breaker_state"], "CLOSED")
        finally:
            if old_tg is not None:
                os.environ["TELEGRAM_BOT_TOKEN"] = old_tg
            else:
                os.environ.pop("TELEGRAM_BOT_TOKEN", None)
            if old_tg_chat is not None:
                os.environ["TELEGRAM_CHAT_ID"] = old_tg_chat
            else:
                os.environ.pop("TELEGRAM_CHAT_ID", None)
            if old_dc is not None:
                os.environ["DISCORD_WEBHOOK_URL"] = old_dc
            else:
                os.environ.pop("DISCORD_WEBHOOK_URL", None)


if __name__ == "__main__":
    unittest.main()
