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
from sniper.storage.models import AlertEventRecord, ItemRecord, NotificationDeliveryRecord, OrderbookSnapshotRecord
from sniper.storage.repositories import AlertRepository, CurrencyRepository, ItemRepository, OrderbookRepository


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
        raw_db.initialize(target_version=4)
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

    def test_cumulative_retry_limits_and_fingerprint_budget(self) -> None:
        """Verify cumulative retry accounting, fingerprint independence, and success clearance."""
        base_time = datetime(2026, 5, 20, 12, 0, 0, tzinfo=timezone.utc)
        alert1 = self.alert_repo.record_alert(AlertEventRecord(
            item_id=self.item.item_id,
            currency_id=1,
            alert_type="sell_target_bid",
            alert_fingerprint="fp_retry_test_1",
            highest_buy_order_minor=2000,
            lowest_sell_order_minor=2500,
            top_level_buy_quantity=1,
            cumulative_buy_quantity=5,
            modeled_steam_fee_minor=100,
            modeled_publisher_fee_minor=200,
            modeled_seller_receives_minor=1700,
            opportunity_score=70.0,
            confidence_score=75.0,
            status="Candidate",
            created_at=base_time.isoformat(),
        ))

        # 0. Initial state: no deliveries -> not in backoff
        self.assertFalse(self.alert_repo.is_channel_in_retry_backoff(
            self.item.item_id, 1, "sell_target_bid", "telegram",
            alert_fingerprint="fp_retry_test_1", now_dt=base_time,
        ))

        # 1. First failure (initial send failure)
        self.alert_repo.record_delivery(NotificationDeliveryRecord(
            alert_id=alert1.alert_id, channel="telegram", status="failed", attempts=1,
            created_at=base_time.isoformat(),
        ))
        # Within 60s backoff (at t=10s): in backoff -> True
        self.assertTrue(self.alert_repo.is_channel_in_retry_backoff(
            self.item.item_id, 1, "sell_target_bid", "telegram",
            alert_fingerprint="fp_retry_test_1", now_dt=base_time + timedelta(seconds=10),
        ))
        # After 60s backoff (at t=70s): backoff expired, 0 retries used (< 3 max_retries) -> False
        self.assertFalse(self.alert_repo.is_channel_in_retry_backoff(
            self.item.item_id, 1, "sell_target_bid", "telegram",
            alert_fingerprint="fp_retry_test_1", now_dt=base_time + timedelta(seconds=70),
        ))

        # Skipped delivery (e.g. while in backoff): does NOT consume retry budget
        self.alert_repo.record_delivery(NotificationDeliveryRecord(
            alert_id=alert1.alert_id, channel="telegram", status="skipped", attempts=0,
            created_at=(base_time + timedelta(seconds=15)).isoformat(),
        ))
        # Still 1 failure recorded; at t=70s still eligible for retry
        self.assertFalse(self.alert_repo.is_channel_in_retry_backoff(
            self.item.item_id, 1, "sell_target_bid", "telegram",
            alert_fingerprint="fp_retry_test_1", now_dt=base_time + timedelta(seconds=70),
        ))

        # 2. Retry 1 fails at t=70s
        self.alert_repo.record_delivery(NotificationDeliveryRecord(
            alert_id=alert1.alert_id, channel="telegram", status="failed", attempts=1,
            created_at=(base_time + timedelta(seconds=70)).isoformat(),
        ))
        # 3. Retry 2 fails at t=140s
        self.alert_repo.record_delivery(NotificationDeliveryRecord(
            alert_id=alert1.alert_id, channel="telegram", status="failed", attempts=1,
            created_at=(base_time + timedelta(seconds=140)).isoformat(),
        ))
        # 4. Retry 3 fails at t=210s (total failures = 4: 1 initial + 3 retries)
        self.alert_repo.record_delivery(NotificationDeliveryRecord(
            alert_id=alert1.alert_id, channel="telegram", status="failed", attempts=1,
            created_at=(base_time + timedelta(seconds=210)).isoformat(),
        ))

        # Reaching cumulative maximum (4 failures >= 1 + 3): permanently suppressed for fp_retry_test_1
        self.assertTrue(self.alert_repo.is_channel_in_retry_backoff(
            self.item.item_id, 1, "sell_target_bid", "telegram",
            alert_fingerprint="fp_retry_test_1", now_dt=base_time + timedelta(seconds=5000),
        ))

        # Discord remains completely independent (0 failures on Discord)
        self.assertFalse(self.alert_repo.is_channel_in_retry_backoff(
            self.item.item_id, 1, "sell_target_bid", "discord",
            alert_fingerprint="fp_retry_test_1", now_dt=base_time + timedelta(seconds=5000),
        ))

        # 5. New bid / fingerprint receives fresh retry budget:
        alert2 = self.alert_repo.record_alert(AlertEventRecord(
            item_id=self.item.item_id,
            currency_id=1,
            alert_type="sell_target_bid",
            alert_fingerprint="fp_retry_test_2_new_bid",
            highest_buy_order_minor=2200,
            lowest_sell_order_minor=2600,
            top_level_buy_quantity=1,
            cumulative_buy_quantity=5,
            modeled_steam_fee_minor=110,
            modeled_publisher_fee_minor=220,
            modeled_seller_receives_minor=1870,
            opportunity_score=75.0,
            confidence_score=80.0,
            status="Candidate",
            created_at=(base_time + timedelta(seconds=5001)).isoformat(),
        ))
        self.assertFalse(self.alert_repo.is_channel_in_retry_backoff(
            self.item.item_id, 1, "sell_target_bid", "telegram",
            alert_fingerprint="fp_retry_test_2_new_bid", now_dt=base_time + timedelta(seconds=5001),
        ))

        # 6. Successful delivery clears retry state for that fingerprint:
        self.alert_repo.record_delivery(NotificationDeliveryRecord(
            alert_id=alert2.alert_id, channel="telegram", status="failed", attempts=1,
            created_at=(base_time + timedelta(seconds=5002)).isoformat(),
        ))
        self.assertTrue(self.alert_repo.is_channel_in_retry_backoff(
            self.item.item_id, 1, "sell_target_bid", "telegram",
            alert_fingerprint="fp_retry_test_2_new_bid", now_dt=base_time + timedelta(seconds=5010),
        ))
        # Now delivery succeeds:
        self.alert_repo.record_delivery(NotificationDeliveryRecord(
            alert_id=alert2.alert_id, channel="telegram", status="delivered", attempts=1,
            delivered_at=(base_time + timedelta(seconds=5070)).isoformat(),
            created_at=(base_time + timedelta(seconds=5070)).isoformat(),
        ))
        # Retry backoff is cleared!
        self.assertFalse(self.alert_repo.is_channel_in_retry_backoff(
            self.item.item_id, 1, "sell_target_bid", "telegram",
            alert_fingerprint="fp_retry_test_2_new_bid", now_dt=base_time + timedelta(seconds=5080),
        ))


class TestHistoricalBuyOrdersTimestamps(unittest.TestCase):
    """Test suite for timestamp validation and clock skew policy in OrderbookRepository."""

    def setUp(self) -> None:
        self.db = Database(":memory:")
        self.db.initialize()
        self.curr_repo = CurrencyRepository(self.db)
        self.curr_repo.seed_default_currencies()
        self.item_repo = ItemRepository(self.db)
        self.ob_repo = OrderbookRepository(self.db)

        self.item = self.item_repo.save(ItemRecord(
            market_hash_name="AK-47 | Redline (Field-Tested)",
            base_name="AK-47 | Redline",
            exterior="Field-Tested",
        ))

    def tearDown(self) -> None:
        self.db.close()

    def test_historical_buy_orders_valid_recent_timestamp(self) -> None:
        """Verify valid recent timestamp is included in historical buy orders."""
        now = datetime(2026, 5, 20, 12, 0, 0, tzinfo=timezone.utc)
        self.ob_repo.insert_snapshot(OrderbookSnapshotRecord(
            item_id=self.item.item_id,
            currency_id=1,
            highest_buy_order_minor=2500,
            observed_at=(now - timedelta(minutes=5)).isoformat(),
        ))
        bids = self.ob_repo.get_historical_buy_orders(
            item_id=self.item.item_id,
            currency_id=1,
            max_age_seconds=3600,
            now_dt=now,
        )
        self.assertEqual(bids, [2500])

    def test_historical_buy_orders_older_than_max_age(self) -> None:
        """Verify observations older than max_age_seconds are excluded."""
        now = datetime(2026, 5, 20, 12, 0, 0, tzinfo=timezone.utc)
        self.ob_repo.insert_snapshot(OrderbookSnapshotRecord(
            item_id=self.item.item_id,
            currency_id=1,
            highest_buy_order_minor=2500,
            observed_at=(now - timedelta(hours=2)).isoformat(),  # 2 hours old
        ))
        bids = self.ob_repo.get_historical_buy_orders(
            item_id=self.item.item_id,
            currency_id=1,
            max_age_seconds=3600,  # 1 hour max
            now_dt=now,
        )
        self.assertEqual(bids, [])

    def test_historical_buy_orders_malformed_timestamp(self) -> None:
        """Verify malformed timestamps are excluded from calculations and not deleted."""
        now = datetime(2026, 5, 20, 12, 0, 0, tzinfo=timezone.utc)
        # Insert raw record with malformed timestamp
        self.db.execute("""
            INSERT INTO orderbook_snapshots (item_id, currency_id, highest_buy_order_minor, observed_at, source_payload_version)
            VALUES (?, ?, ?, ?, ?)
        """, (self.item.item_id, 1, 3000, "not_a_valid_iso_timestamp", "v1"))

        bids = self.ob_repo.get_historical_buy_orders(
            item_id=self.item.item_id,
            currency_id=1,
            max_age_seconds=3600,
            now_dt=now,
        )
        self.assertEqual(bids, [])

        # Confirm record still exists in SQLite (never deleted)
        row_count = self.db.fetch_one("SELECT COUNT(*) FROM orderbook_snapshots")[0]
        self.assertEqual(row_count, 1)

    def test_historical_buy_orders_missing_timestamp(self) -> None:
        """Verify missing/empty observed_at timestamps are excluded."""
        now = datetime(2026, 5, 20, 12, 0, 0, tzinfo=timezone.utc)
        self.db.execute("""
            INSERT INTO orderbook_snapshots (item_id, currency_id, highest_buy_order_minor, observed_at, source_payload_version)
            VALUES (?, ?, ?, ?, ?)
        """, (self.item.item_id, 1, 3500, "", "v1"))
        self.db.execute("""
            INSERT INTO orderbook_snapshots (item_id, currency_id, highest_buy_order_minor, observed_at, source_payload_version)
            VALUES (?, ?, ?, ?, ?)
        """, (self.item.item_id, 1, 3600, "   ", "v1"))

        bids = self.ob_repo.get_historical_buy_orders(
            item_id=self.item.item_id,
            currency_id=1,
            max_age_seconds=3600,
            now_dt=now,
        )
        self.assertEqual(bids, [])

    def test_historical_buy_orders_future_timestamp(self) -> None:
        """Verify timestamps beyond clock skew tolerance are excluded while minor skew is accepted."""
        now = datetime(2026, 5, 20, 12, 0, 0, tzinfo=timezone.utc)

        # 1. Tolerable clock skew: 2 seconds into the future (<= MAX_CLOCK_SKEW_SECONDS=5.0) -> accepted
        self.ob_repo.insert_snapshot(OrderbookSnapshotRecord(
            item_id=self.item.item_id,
            currency_id=1,
            highest_buy_order_minor=2600,
            observed_at=(now + timedelta(seconds=2)).isoformat(),
        ))
        bids = self.ob_repo.get_historical_buy_orders(
            item_id=self.item.item_id,
            currency_id=1,
            max_age_seconds=3600,
            now_dt=now,
        )
        self.assertEqual(bids, [2600])

        # 2. Far future timestamp: 60 seconds into the future (> 5.0s tolerance) -> excluded
        self.ob_repo.insert_snapshot(OrderbookSnapshotRecord(
            item_id=self.item.item_id,
            currency_id=1,
            highest_buy_order_minor=9999,
            observed_at=(now + timedelta(seconds=60)).isoformat(),
        ))
        bids2 = self.ob_repo.get_historical_buy_orders(
            item_id=self.item.item_id,
            currency_id=1,
            max_age_seconds=3600,
            now_dt=now,
        )
        self.assertEqual(bids2, [2600])  # 9999 excluded

    def test_historical_buy_orders_mixed_valid_and_invalid(self) -> None:
        """Verify get_historical_buy_orders accurately filters mixed snapshots."""
        now = datetime(2026, 5, 20, 12, 0, 0, tzinfo=timezone.utc)

        # Valid 1: 5m ago ($10.00)
        self.ob_repo.insert_snapshot(OrderbookSnapshotRecord(
            item_id=self.item.item_id, currency_id=1, highest_buy_order_minor=1000,
            observed_at=(now - timedelta(minutes=5)).isoformat(),
        ))
        # Invalid 1: malformed timestamp
        self.db.execute("INSERT INTO orderbook_snapshots (item_id, currency_id, highest_buy_order_minor, observed_at, source_payload_version) VALUES (?, ?, ?, ?, ?)",
                        (self.item.item_id, 1, 2000, "corrupt-ts", "v1"))
        # Invalid 2: empty timestamp
        self.db.execute("INSERT INTO orderbook_snapshots (item_id, currency_id, highest_buy_order_minor, observed_at, source_payload_version) VALUES (?, ?, ?, ?, ?)",
                        (self.item.item_id, 1, 3000, "", "v1"))
        # Invalid 3: Far future timestamp (+2 hours)
        self.ob_repo.insert_snapshot(OrderbookSnapshotRecord(
            item_id=self.item.item_id, currency_id=1, highest_buy_order_minor=4000,
            observed_at=(now + timedelta(hours=2)).isoformat(),
        ))
        # Invalid 4: Expired timestamp (> max_age=86400, observed 3 days ago)
        self.ob_repo.insert_snapshot(OrderbookSnapshotRecord(
            item_id=self.item.item_id, currency_id=1, highest_buy_order_minor=5000,
            observed_at=(now - timedelta(days=3)).isoformat(),
        ))
        # Valid 2: 15m ago ($60.00)
        self.ob_repo.insert_snapshot(OrderbookSnapshotRecord(
            item_id=self.item.item_id, currency_id=1, highest_buy_order_minor=6000,
            observed_at=(now - timedelta(minutes=15)).isoformat(),
        ))

        bids = self.ob_repo.get_historical_buy_orders(
            item_id=self.item.item_id,
            currency_id=1,
            max_age_seconds=86400,
            now_dt=now,
        )
        # Should return exactly the two valid observations: 1000 (newer) and 6000 (older)
        self.assertEqual(bids, [1000, 6000])


if __name__ == "__main__":
    unittest.main()
