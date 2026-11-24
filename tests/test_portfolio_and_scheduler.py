"""Deterministic offline tests for M7 Portfolio 7-Day Tracker, Steam unlock parser, and Unified Scheduler."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import tempfile
from unittest.mock import patch
import unittest

import httpx

from sniper.cli import main as cli_main
from sniper.core.http_client import ReadOnlyHttpClient
from sniper.core.rate_limiter import RateLimiterConfig, SequentialRateLimiter
from sniper.engine.portfolio_service import PortfolioService, parse_steam_marketable_timestamp
from sniper.engine.scheduler import SchedulerConfig, SniperScheduler
from sniper.notifications import DeliveryStatus, DiscordNotifier, NotificationDispatcher, TelegramNotifier
from sniper.storage.database import Database
from sniper.storage.migrations import DestructiveMigrationError, apply_migrations, get_current_version, rollback_migration
from sniper.storage.repositories import CurrencyRepository, HoldingRepository, SourceHealthRepository

_REAL_ASYNC_CLIENT = httpx.AsyncClient


class TestPortfolioAndScheduler(unittest.IsolatedAsyncioTestCase):
    """Tests for M7 Migration 6, 7-day restriction lifecycle, Under Water alerts, and Scheduler."""

    def setUp(self) -> None:
        self.db = Database(":memory:")
        self.db.initialize()
        CurrencyRepository(self.db).seed_default_currencies()
        self.holding_repo = HoldingRepository(self.db)
        self.health_repo = SourceHealthRepository(self.db)

    def tearDown(self) -> None:
        self.db.close()

    def test_migration_6_rollback_guard(self) -> None:
        """Verify Migration 6 tables and DestructiveMigrationError guard."""
        raw = Database(":memory:")
        raw.initialize(target_version=5)
        self.assertEqual(get_current_version(raw.connection), 5)
        apply_migrations(raw.connection, target_version=6)
        self.assertEqual(get_current_version(raw.connection), 6)

        CurrencyRepository(raw).seed_default_currencies()
        PortfolioService(raw).add_holding("AK-47 | Redline (Field-Tested)", acquisition_cost_minor=2000)

        with self.assertRaises(DestructiveMigrationError):
            rollback_migration(raw.connection, target_version=5, allow_data_loss=False)

        rollback_migration(raw.connection, target_version=5, allow_data_loss=True)
        self.assertEqual(get_current_version(raw.connection), 5)
        raw.close()

    def test_parse_authoritative_steam_marketable_timestamp(self) -> None:
        """Prefer explicit Steam restriction date from descriptions over generic +7 days."""
        dt, src = parse_steam_marketable_timestamp(
            descriptions=[
                {"type": "html", "value": "<span>Cannot be traded or listed on Community Market until Nov 29, 2026 (7:00:00) GMT</span>"}
            ],
            restriction_days=7,
            acquired_at=datetime(2026, 11, 20, 12, 0, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(src, "steam_observed")
        self.assertEqual(dt, datetime(2026, 11, 29, 7, 0, 0, tzinfo=timezone.utc))

        # Fallback to restriction_days when explicit date absent
        acq = datetime(2026, 11, 20, 12, 0, 0, tzinfo=timezone.utc)
        dt2, src2 = parse_steam_marketable_timestamp(descriptions=[], restriction_days=7, acquired_at=acq)
        self.assertEqual(src2, "manual_days")
        self.assertEqual(dt2, datetime(2026, 11, 27, 12, 0, 0, tzinfo=timezone.utc))

    async def test_holding_lock_underwater_and_unlock_resale_ready_lifecycle(self) -> None:
        """Ensure RESALE READY never fires while locked, detects Under Water, and alerts on unlock."""
        now = datetime(2026, 11, 20, 12, 0, 0, tzinfo=timezone.utc)
        sent_messages = []

        def mock_post(req: httpx.Request) -> httpx.Response:
            sent_messages.append(json.loads(req.content.decode("utf-8")))
            return httpx.Response(204)

        dispatcher = NotificationDispatcher(
            channels=[DiscordNotifier(webhook_url="https://discord.com/api/webhooks/111/secret")],
            strategy_alert_repo=PortfolioService(self.db)._strategy_alert_repo,
        )
        svc = PortfolioService(self.db, dispatcher=dispatcher)

        holding = svc.add_holding(
            market_hash_name="AK-47 | Redline (Field-Tested)",
            acquisition_cost_minor=2000,  # $20.00 -> breakeven = 2000 + 100 + 200 = 2300 ($23.00)
            currency_id=1,
            steam_descriptions=[
                {"value": "Cannot be traded or listed on Community Market until Nov 27, 2026 (7:00:00) GMT"}
            ],
            acquired_at=now,
            now_dt=now,
        )
        self.assertEqual(holding.status, "HOLDING")
        self.assertEqual(holding.restriction_source, "steam_observed")
        self.assertEqual(holding.breakeven_buyer_pays_minor, 2300)

        # Day 2: Floor rises to $30.00, STILL LOCKED -> RESALE READY must NOT trigger!
        with patch("httpx.AsyncClient", side_effect=lambda **kw: _REAL_ASYNC_CLIENT(transport=httpx.MockTransport(mock_post))):
            views_d2 = await svc.evaluate_holdings(
                floor_overrides={holding.holding_id: 3000},
                now_dt=now + timedelta(days=2),
            )
        self.assertEqual(views_d2[0].status, "HOLDING")
        self.assertTrue(views_d2[0].is_locked)
        self.assertEqual(len(sent_messages), 0)

        # Day 4: Floor drops to $21.00 (< $23.00 breakeven) -> UNDER WATER alert!
        with patch("httpx.AsyncClient", side_effect=lambda **kw: _REAL_ASYNC_CLIENT(transport=httpx.MockTransport(mock_post))):
            views_d4 = await svc.evaluate_holdings(
                floor_overrides={holding.holding_id: 2100},
                now_dt=now + timedelta(days=4),
            )
        self.assertTrue(views_d4[0].is_under_water)
        self.assertEqual(views_d4[0].under_water_gap_minor, 200)
        self.assertEqual(len(sent_messages), 1)
        self.assertIn("UNDER WATER", sent_messages[0]["embeds"][0]["title"])

        # Day 7 after 07:00 GMT: Unlock occurs, floor is $28.00 -> READY_TO_LIST + RESALE READY!
        unlock_plus = datetime(2026, 11, 27, 8, 0, 0, tzinfo=timezone.utc)
        with patch("httpx.AsyncClient", side_effect=lambda **kw: _REAL_ASYNC_CLIENT(transport=httpx.MockTransport(mock_post))):
            views_unlock = await svc.evaluate_holdings(
                floor_overrides={holding.holding_id: 2800},
                now_dt=unlock_plus,
            )
        self.assertEqual(views_unlock[0].status, "READY_TO_LIST")
        self.assertFalse(views_unlock[0].is_locked)
        self.assertFalse(views_unlock[0].is_under_water)
        self.assertTrue(views_unlock[0].alert_dispatched)
        self.assertEqual(views_unlock[0].delivery_results["discord"].status, DeliveryStatus.DELIVERED)
        self.assertEqual(len(sent_messages), 2)
        self.assertIn("RESALE READY", sent_messages[1]["embeds"][0]["title"])

        # Record manual sale at $28.00 gross -> net = 2800 - fees (using exact M4 inversion)
        sold = svc.record_sale(holding.holding_id, gross_sale_price_minor=2800, now_dt=unlock_plus)
        self.assertEqual(sold.status, "SOLD")
        overview = svc.get_overview(currency_id=1, now_dt=unlock_plus)
        self.assertEqual(overview.sold_count, 1)
        self.assertGreater(overview.realized_wallet_pnl_minor, 400)

        # Double sell rejected
        with self.assertRaises(ValueError):
            svc.record_sale(holding.holding_id, gross_sale_price_minor=2800)

    async def test_scheduler_enforces_rpm_ceiling_and_handles_circuit_breaker(self) -> None:
        """Scheduler rejects >12 RPM, shares single breaker, and defers cleanly on OPEN/HALTED."""
        with self.assertRaises(ValueError):
            SchedulerConfig(max_requests_per_minute=15.0)

        fake_clock = [1000.0]

        async def noop_sleep(_d: float) -> None:
            return None

        limiter = SequentialRateLimiter(
            config=RateLimiterConfig(requests_per_minute=12.0, jitter_max_seconds=0.0),
            time_func=lambda: fake_clock[0],
            sleep_func=noop_sleep,
        )
        client = ReadOnlyHttpClient(
            rate_limiter=limiter,
            transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"success": True})),
        )
        scheduler = SniperScheduler(db=self.db, http_client=client)

        res1 = await scheduler.run_once()
        self.assertEqual(res1.breaker_state, "CLOSED")
        self.assertIsNone(res1.skipped_reason)

        # Trigger 429 OPEN state
        limiter.record_429("300")
        res2 = await scheduler.run_once()
        self.assertEqual(res2.breaker_state, "OPEN")
        self.assertIn("OPEN", res2.skipped_reason or "")

        # Trigger permanent HALTED state
        limiter.record_403_or_security_block("HTTP 403 block")
        res3 = await scheduler.run_once()
        self.assertEqual(res3.breaker_state, "HALTED")
        self.assertIn("HALTED", res3.skipped_reason or "")

        events = self.health_repo.list_recent()
        self.assertGreaterEqual(len(events), 3)
        await client.aclose()

    def test_cli_portfolio_workflow(self) -> None:
        """Verify CLI portfolio add, list, evaluate, and sell subcommands."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "cli_m7.db")
            with patch("sys.stdout", new_callable=io.StringIO):
                self.assertEqual(cli_main([
                    "portfolio", "add", "AK-47 | Redline (Field-Tested)",
                    "--cost-cents", "2000", "--days", "7", "--db", db_path,
                ]), 0)
                self.assertEqual(cli_main(["portfolio", "list", "--db", db_path]), 0)
                self.assertEqual(cli_main(["portfolio", "evaluate", "--no-notifications", "--db", db_path]), 0)
                self.assertEqual(cli_main(["portfolio", "sell", "1", "--gross-cents", "2600", "--db", db_path]), 0)


if __name__ == "__main__":
    unittest.main()
