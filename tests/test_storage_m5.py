"""Deterministic tests for SQLite Migration 4 and AlertRepository."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sqlite3
import unittest

from sniper.storage.database import Database
from sniper.storage.migrations import (
    DestructiveMigrationError,
    apply_migrations,
    get_applied_versions,
    get_current_version,
    rollback_migration,
)
from sniper.storage.models import AlertEventRecord, ItemRecord, NotificationDeliveryRecord
from sniper.storage.repositories import AlertRepository, CurrencyRepository, ItemRepository


class TestStorageM5(unittest.TestCase):
    """Test suite for Migration 4 (alert_events, notification_deliveries) and AlertRepository."""

    def setUp(self) -> None:
        self.db = Database(":memory:")
        self.db.initialize()
        self.curr_repo = CurrencyRepository(self.db)
        self.curr_repo.seed_default_currencies()
        self.item_repo = ItemRepository(self.db)
        self.alert_repo = AlertRepository(self.db)

        self.item = self.item_repo.save(ItemRecord(
            market_hash_name="AK-47 | Redline (Field-Tested)",
            base_name="AK-47 | Redline",
            exterior="Field-Tested",
        ))

    def tearDown(self) -> None:
        self.db.close()

    def test_migration_4_upgrade_and_tables(self) -> None:
        """Verify Migration 4 creates alert_events and notification_deliveries tables."""
        raw_db = Database(":memory:")
        # Initialize to V3 first
        applied = raw_db.initialize(target_version=3)
        self.assertEqual(applied, [1, 2, 3])
        self.assertEqual(raw_db.get_version(), 3)

        # Apply Migration 4
        newly_applied = apply_migrations(raw_db.connection, target_version=4)
        self.assertEqual(newly_applied, [4])
        self.assertEqual(get_current_version(raw_db.connection), 4)
        self.assertEqual(get_applied_versions(raw_db.connection), [1, 2, 3, 4])

        # Verify physical tables exist
        tables = [
            row[0]
            for row in raw_db.fetch_all("SELECT name FROM sqlite_master WHERE type='table'")
        ]
        self.assertIn("alert_events", tables)
        self.assertIn("notification_deliveries", tables)
        raw_db.close()

    def test_migration_4_rollback_data_loss_protection(self) -> None:
        """Verify rolling back V4 to V3 is blocked when alerts exist, unless allow_data_loss=True."""
        raw_db = Database(":memory:")
        raw_db.initialize()
        curr_repo = CurrencyRepository(raw_db)
        curr_repo.seed_default_currencies()
        item_repo = ItemRepository(raw_db)
        alert_repo = AlertRepository(raw_db)

        item = item_repo.save(ItemRecord(
            market_hash_name="M4A4 | The Emperor (Field-Tested)",
            base_name="M4A4 | The Emperor",
        ))

        # Insert alert record
        alert_repo.record_alert(AlertEventRecord(
            item_id=item.item_id,
            currency_id=1,
            alert_type="sell_target_bid",
            alert_fingerprint="fp1",
            highest_buy_order_minor=2000,
            lowest_sell_order_minor=2500,
            top_level_buy_quantity=1,
            cumulative_buy_quantity=10,
            modeled_steam_fee_minor=100,
            modeled_publisher_fee_minor=200,
            modeled_seller_receives_minor=1700,
            opportunity_score=80.0,
            confidence_score=75.0,
            warning_tags="[]",
            status="Candidate",
        ))

        # Rollback without allow_data_loss=True must raise DestructiveMigrationError
        with self.assertRaises(DestructiveMigrationError) as ctx:
            rollback_migration(raw_db.connection, target_version=3, allow_data_loss=False)
        self.assertIn("alert_events", str(ctx.exception))

        # Rollback with allow_data_loss=True succeeds
        rolled_back = rollback_migration(raw_db.connection, target_version=3, allow_data_loss=True)
        self.assertEqual(rolled_back, [4])
        self.assertEqual(get_current_version(raw_db.connection), 3)

        raw_db.close()

    def test_alert_recording_and_retrieval(self) -> None:
        """Verify recording an alert event and querying by ID or item."""
        alert = self.alert_repo.record_alert(AlertEventRecord(
            item_id=self.item.item_id,
            currency_id=1,
            alert_type="sell_target_bid",
            alert_fingerprint="fp_abc",
            highest_buy_order_minor=5000,
            lowest_sell_order_minor=5500,
            top_level_buy_quantity=2,
            cumulative_buy_quantity=20,
            modeled_steam_fee_minor=250,
            modeled_publisher_fee_minor=500,
            modeled_seller_receives_minor=4250,
            historical_baseline_minor=4000,
            bid_multiple=1.25,
            opportunity_score=88.0,
            confidence_score=82.0,
            warning_tags='["Single-Item Top Bid"]',
            status="High-confidence signal",
        ))
        self.assertIsNotNone(alert.alert_id)

        # Retrieve by ID
        fetched = self.alert_repo.get_alert_by_id(alert.alert_id)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.highest_buy_order_minor, 5000)
        self.assertEqual(fetched.modeled_seller_receives_minor, 4250)
        self.assertEqual(fetched.opportunity_score, 88.0)

        # Retrieve latest for item
        latest = self.alert_repo.get_latest_alert_for_item(self.item.item_id, 1, "sell_target_bid")
        self.assertIsNotNone(latest)
        self.assertEqual(latest.alert_id, alert.alert_id)

    def test_is_in_cooldown_semantics(self) -> None:
        """Verify cooldown suppression and meaningful bid increase override."""
        base_time = datetime(2026, 5, 20, 12, 0, 0, tzinfo=timezone.utc)
        alert = self.alert_repo.record_alert(AlertEventRecord(
            item_id=self.item.item_id,
            currency_id=1,
            alert_type="sell_target_bid",
            alert_fingerprint="fp_cool",
            highest_buy_order_minor=1000,
            lowest_sell_order_minor=1200,
            top_level_buy_quantity=1,
            cumulative_buy_quantity=5,
            modeled_steam_fee_minor=50,
            modeled_publisher_fee_minor=100,
            modeled_seller_receives_minor=850,
            opportunity_score=70.0,
            confidence_score=70.0,
            warning_tags="[]",
            status="Candidate",
            created_at=base_time.isoformat(),
        ))
        self.alert_repo.record_delivery(NotificationDeliveryRecord(
            alert_id=alert.alert_id,
            channel="discord",
            status="delivered",
            attempts=1,
            delivered_at=base_time.isoformat(),
            created_at=base_time.isoformat(),
        ))

        # 1. 10 minutes later (within 30m cooldown), same bid: IN COOLDOWN (suppressed)
        t_10m = base_time + timedelta(minutes=10)
        in_cool = self.alert_repo.is_in_cooldown(
            item_id=self.item.item_id,
            currency_id=1,
            alert_type="sell_target_bid",
            cooldown_seconds=1800,
            new_bid_minor=1000,
            now_dt=t_10m,
        )
        self.assertTrue(in_cool)

        # 2. 10 minutes later, slight 2% increase ($10.20 vs $10.00): still in cooldown (< 5% threshold)
        in_cool = self.alert_repo.is_in_cooldown(
            item_id=self.item.item_id,
            currency_id=1,
            alert_type="sell_target_bid",
            cooldown_seconds=1800,
            new_bid_minor=1020,
            meaningful_increase_pct=0.05,
            now_dt=t_10m,
        )
        self.assertTrue(in_cool)

        # 3. 10 minutes later, meaningful 6% increase ($10.60 vs $10.00): BYPASSES COOLDOWN (not suppressed)
        in_cool = self.alert_repo.is_in_cooldown(
            item_id=self.item.item_id,
            currency_id=1,
            alert_type="sell_target_bid",
            cooldown_seconds=1800,
            new_bid_minor=1060,
            meaningful_increase_pct=0.05,
            now_dt=t_10m,
        )
        self.assertFalse(in_cool)

        # 4. 35 minutes later (outside 30m cooldown), same bid: EXPIRED COOLDOWN (not suppressed)
        t_35m = base_time + timedelta(minutes=35)
        in_cool = self.alert_repo.is_in_cooldown(
            item_id=self.item.item_id,
            currency_id=1,
            alert_type="sell_target_bid",
            cooldown_seconds=1800,
            new_bid_minor=1000,
            now_dt=t_35m,
        )
        self.assertFalse(in_cool)

    def test_notification_delivery_logging(self) -> None:
        """Verify delivery recording and querying for an alert."""
        alert = self.alert_repo.record_alert(AlertEventRecord(
            item_id=self.item.item_id,
            currency_id=1,
            alert_type="sell_target_bid",
            alert_fingerprint="fp_del",
            highest_buy_order_minor=3000,
            top_level_buy_quantity=1,
            cumulative_buy_quantity=5,
            modeled_steam_fee_minor=150,
            modeled_publisher_fee_minor=300,
            modeled_seller_receives_minor=2550,
            opportunity_score=75.0,
            confidence_score=80.0,
            warning_tags="[]",
            status="Candidate",
        ))

        d1 = self.alert_repo.record_delivery(NotificationDeliveryRecord(
            alert_id=alert.alert_id,
            channel="discord",
            status="delivered",
            attempts=1,
            delivered_at="2026-05-20T12:01:00Z",
        ))
        self.assertIsNotNone(d1.delivery_id)

        d2 = self.alert_repo.record_delivery(NotificationDeliveryRecord(
            alert_id=alert.alert_id,
            channel="telegram",
            status="failed",
            attempts=2,
            last_error="HTTP 500 Internal Server Error",
        ))
        self.assertIsNotNone(d2.delivery_id)

        deliveries = self.alert_repo.get_deliveries_for_alert(alert.alert_id)
        self.assertEqual(len(deliveries), 2)
        self.assertEqual(deliveries[0].channel, "discord")
        self.assertEqual(deliveries[0].status, "delivered")
        self.assertEqual(deliveries[1].channel, "telegram")
        self.assertEqual(deliveries[1].status, "failed")


if __name__ == "__main__":
    unittest.main()
