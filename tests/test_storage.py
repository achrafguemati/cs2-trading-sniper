"""Comprehensive offline unit tests for SQLite persistence, migrations, and repositories."""

from __future__ import annotations

import concurrent.futures
from pathlib import Path
import sqlite3
import tempfile
import unittest

from sniper.core.currency import CurrencyConfig
from sniper.core.identity import (
    ItemIdentityResult,
    ResolutionSource,
    ResolutionStatus,
    UrlType,
    WearCategory,
)
from sniper.storage.database import Database, ExecutionResult
from sniper.storage.migrations import (
    DestructiveMigrationError,
    IncompatibleSchemaVersionError,
    Migration,
    MigrationError,
    apply_migrations,
    get_applied_versions,
    get_current_version,
    rollback_migration,
)
from sniper.storage.models import (
    CurrencyRecord,
    InventoryItemRecord,
    InventorySnapshotRecord,
    ItemRecord,
    ListingRecord,
    OrderbookSnapshotRecord,
    PortfolioTransactionRecord,
)
from sniper.storage.repositories import (
    CurrencyRepository,
    InventoryRepository,
    ItemRepository,
    ListingRepository,
    OrderbookRepository,
    PortfolioRepository,
)


class TestStorage(unittest.TestCase):
    """Test suite for SQLite storage, migrations, and repository layers."""

    def setUp(self) -> None:
        self.db = Database(":memory:")
        self.db.initialize()
        self.currency_repo = CurrencyRepository(self.db)
        self.currency_repo.seed_default_currencies()
        self.item_repo = ItemRepository(self.db)
        self.listing_repo = ListingRepository(self.db)
        self.orderbook_repo = OrderbookRepository(self.db)
        self.inv_repo = InventoryRepository(self.db)
        self.portfolio_repo = PortfolioRepository(self.db)

    def tearDown(self) -> None:
        self.db.close()

    # -------------------------------------------------------------------------
    # 1. Database Execution API Contract (Hotfix 1)
    # -------------------------------------------------------------------------

    def test_database_execute_returns_execution_result(self) -> None:
        """Verify execute() returns an ExecutionResult with rowcount and lastrowid, not a closed cursor."""
        res = self.db.execute(
            "INSERT INTO items (market_hash_name, base_name, first_seen_at, updated_at) VALUES (?, ?, ?, ?)",
            ("Test Hash", "Test Base", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        )
        self.assertIsInstance(res, ExecutionResult)
        self.assertEqual(res.rowcount, 1)
        self.assertIsNotNone(res.lastrowid)
        self.assertGreater(res.lastrowid, 0)

        # UPDATE statement
        res_update = self.db.execute(
            "UPDATE items SET base_name = ? WHERE item_id = ?",
            ("Updated Base", res.lastrowid),
        )
        self.assertIsInstance(res_update, ExecutionResult)
        self.assertEqual(res_update.rowcount, 1)
        # SQLite's connection preserves the last inserted rowid
        self.assertEqual(res_update.lastrowid, res.lastrowid)

    def test_database_executemany_returns_execution_result(self) -> None:
        """Verify executemany() returns an ExecutionResult with total rowcount."""
        items = [
            ("Item A", "Base A", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
            ("Item B", "Base B", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        ]
        res = self.db.executemany(
            "INSERT INTO items (market_hash_name, base_name, first_seen_at, updated_at) VALUES (?, ?, ?, ?)",
            items,
        )
        self.assertIsInstance(res, ExecutionResult)
        self.assertEqual(res.rowcount, 2)

    def test_supported_query_result_methods(self) -> None:
        """Verify fetch_one and fetch_all remain the supported query retrieval methods."""
        self.db.execute(
            "INSERT INTO items (market_hash_name, base_name, first_seen_at, updated_at) VALUES (?, ?, ?, ?)",
            ("Item Q1", "Base Q1", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        )
        self.db.execute(
            "INSERT INTO items (market_hash_name, base_name, first_seen_at, updated_at) VALUES (?, ?, ?, ?)",
            ("Item Q2", "Base Q2", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        )

        row = self.db.fetch_one("SELECT * FROM items WHERE market_hash_name = ?", ("Item Q1",))
        self.assertIsNotNone(row)
        self.assertEqual(row["market_hash_name"], "Item Q1")

        rows = self.db.fetch_all("SELECT * FROM items WHERE market_hash_name LIKE 'Item Q%' ORDER BY item_id ASC")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["market_hash_name"], "Item Q1")
        self.assertEqual(rows[1]["market_hash_name"], "Item Q2")

    # -------------------------------------------------------------------------
    # 2. Database Initialization & Migrations
    # -------------------------------------------------------------------------

    def test_database_initialization(self) -> None:
        """Verify initial database version matches latest migration version."""
        self.assertEqual(self.db.get_version(), 2)
        applied = get_applied_versions(self.db.connection)
        self.assertEqual(applied, [1, 2])

    def test_repeated_initialization_idempotent(self) -> None:
        """Verify repeated initialization is a safe no-op that preserves existing data."""
        item = ItemRecord(
            market_hash_name="AK-47 | Redline (Field-Tested)",
            base_name="AK-47 | Redline",
            exterior="Field-Tested",
        )
        saved = self.item_repo.save(item)
        self.assertIsNotNone(saved.item_id)

        # Re-initialize database
        applied_again = self.db.initialize()
        self.assertEqual(applied_again, [])
        self.assertEqual(self.db.get_version(), 2)

        # Existing record remains intact
        retrieved = self.item_repo.get_by_id(saved.item_id)
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.market_hash_name, item.market_hash_name)

    def test_migration_from_v1_to_v2(self) -> None:
        """Verify incremental migration from V1 to V2."""
        raw_db = Database(":memory:")
        applied_v1 = raw_db.initialize(target_version=1)
        self.assertEqual(applied_v1, [1])
        self.assertEqual(raw_db.get_version(), 1)

        # portfolio_transactions should not exist in V1
        row = raw_db.fetch_one(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='portfolio_transactions'"
        )
        self.assertIsNone(row)

        # Migrate to V2
        applied_v2 = raw_db.initialize(target_version=2)
        self.assertEqual(applied_v2, [2])
        self.assertEqual(raw_db.get_version(), 2)

        # portfolio_transactions now exists
        row_after = raw_db.fetch_one(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='portfolio_transactions'"
        )
        self.assertIsNotNone(row_after)
        raw_db.close()

    def test_migration_rollback_empty_schema(self) -> None:
        """Verify rolling back a migration on empty schema cleanly removes schema changes."""
        raw_db = Database(":memory:")
        raw_db.initialize(target_version=2)
        self.assertEqual(raw_db.get_version(), 2)

        # Roll back to Version 1 on empty schema
        rolled_back = rollback_migration(raw_db.connection, target_version=1)
        self.assertEqual(rolled_back, [2])
        self.assertEqual(raw_db.get_version(), 1)

        # portfolio_transactions table dropped
        row = raw_db.fetch_one(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='portfolio_transactions'"
        )
        self.assertIsNone(row)

        # Roll back to Version 0 on empty schema
        rolled_back_v0 = rollback_migration(raw_db.connection, target_version=0)
        self.assertEqual(rolled_back_v0, [1])
        self.assertEqual(raw_db.get_version(), 0)

        # Core items table dropped
        items_row = raw_db.fetch_one(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='items'"
        )
        self.assertIsNone(items_row)
        raw_db.close()

    def test_incompatible_schema_version_error(self) -> None:
        """Verify error when database schema version exceeds maximum supported version."""
        raw_db = Database(":memory:")
        raw_db.initialize()
        raw_db.execute(
            "INSERT INTO schema_migrations (version, applied_at, description) VALUES (999, '2099-01-01', 'Future schema')"
        )
        with self.assertRaises(IncompatibleSchemaVersionError):
            apply_migrations(raw_db.connection)
        raw_db.close()

    def test_migration_failure_atomic_rollback(self) -> None:
        """Verify that an error inside a migration rolls back atomically."""
        raw_db = Database(":memory:")
        raw_db.initialize(target_version=1)

        def broken_up(conn: sqlite3.Connection) -> None:
            conn.execute("CREATE TABLE valid_test (id INTEGER PRIMARY KEY);")
            conn.execute("INVALID SQL STATEMENT SYNTAX;")

        broken_migration = Migration(
            version=99,
            description="Broken migration",
            up=broken_up,
            down=lambda c: None,
        )

        from sniper.storage import migrations
        original_migrations = list(migrations.MIGRATIONS)
        original_max = migrations.MAX_SUPPORTED_VERSION
        try:
            migrations.MIGRATIONS.append(broken_migration)
            migrations.MAX_SUPPORTED_VERSION = 99
            with self.assertRaises(MigrationError):
                apply_migrations(raw_db.connection, target_version=99)
        finally:
            migrations.MIGRATIONS = original_migrations
            migrations.MAX_SUPPORTED_VERSION = original_max

        # Verify valid_test was rolled back and does not exist
        row = raw_db.fetch_one(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='valid_test'"
        )
        self.assertIsNone(row)
        raw_db.close()

    # -------------------------------------------------------------------------
    # 3. Destructive Rollback Protection (Hotfix 4)
    # -------------------------------------------------------------------------

    def test_destructive_rollback_rejected_on_populated_v2(self) -> None:
        """Verify rollback from V2 to V1 fails with DestructiveMigrationError when portfolio data exists."""
        item = self.item_repo.save(ItemRecord(
            market_hash_name="AK-47 | Redline (Field-Tested)",
            base_name="AK-47 | Redline",
        ))
        self.portfolio_repo.record_transaction(PortfolioTransactionRecord(
            item_id=item.item_id,
            transaction_type="BUY",
            quantity=1,
            unit_price_minor=2000,
            currency_id=1,
        ))

        # Rollback without allow_data_loss=True must be rejected
        with self.assertRaises(DestructiveMigrationError) as ctx:
            self.db.rollback(target_version=1, allow_data_loss=False)

        self.assertIn("portfolio_transactions", str(ctx.exception))
        self.assertIn("1 record(s)", str(ctx.exception))

        # Data and schema version remain unchanged
        self.assertEqual(self.db.get_version(), 2)
        txs = self.portfolio_repo.get_transactions()
        self.assertEqual(len(txs), 1)

        # Rollback with allow_data_loss=True succeeds
        rolled_back = self.db.rollback(target_version=1, allow_data_loss=True)
        self.assertEqual(rolled_back, [2])
        self.assertEqual(self.db.get_version(), 1)

    def test_destructive_rollback_rejected_on_populated_v1(self) -> None:
        """Verify rollback from V1 to V0 fails when core tables contain items."""
        raw_db = Database(":memory:")
        raw_db.initialize(target_version=1)
        raw_item_repo = ItemRepository(raw_db)
        raw_item_repo.save(ItemRecord(
            market_hash_name="AK-47 | Redline (Field-Tested)",
            base_name="AK-47 | Redline",
        ))

        # Rollback to V0 without allow_data_loss must be rejected
        with self.assertRaises(DestructiveMigrationError) as ctx:
            rollback_migration(raw_db.connection, target_version=0, allow_data_loss=False)

        self.assertIn("items", str(ctx.exception))
        self.assertEqual(raw_db.get_version(), 1)

        # Explicit allow_data_loss=True succeeds
        rolled_back = rollback_migration(raw_db.connection, target_version=0, allow_data_loss=True)
        self.assertEqual(rolled_back, [1])
        self.assertEqual(raw_db.get_version(), 0)
        raw_db.close()

    # -------------------------------------------------------------------------
    # 4. Identity Provenance Upsert Consistency (Hotfix 2)
    # -------------------------------------------------------------------------

    def test_provenance_replacement_with_new_provenance(self) -> None:
        """When a new item_nameid is supplied with new provenance, all three fields update atomically."""
        initial = self.item_repo.save(ItemRecord(
            market_hash_name="AK-47 | Redline (Field-Tested)",
            base_name="AK-47 | Redline",
            item_nameid=100,
            item_nameid_source="source_A",
            item_nameid_revision="rev_A",
        ))
        self.assertEqual(initial.item_nameid, 100)
        self.assertEqual(initial.item_nameid_source, "source_A")
        self.assertEqual(initial.item_nameid_revision, "rev_A")

        # Replace with new identifier and new provenance
        updated = self.item_repo.save(ItemRecord(
            market_hash_name="AK-47 | Redline (Field-Tested)",
            base_name="AK-47 | Redline",
            item_nameid=200,
            item_nameid_source="source_B",
            item_nameid_revision="rev_B",
        ))
        self.assertEqual(updated.item_nameid, 200)
        self.assertEqual(updated.item_nameid_source, "source_B")
        self.assertEqual(updated.item_nameid_revision, "rev_B")

    def test_provenance_replacement_clears_stale_provenance_when_unknown(self) -> None:
        """When a new item_nameid is supplied with missing provenance, old provenance is NOT retained."""
        self.item_repo.save(ItemRecord(
            market_hash_name="AK-47 | Redline (Field-Tested)",
            base_name="AK-47 | Redline",
            item_nameid=100,
            item_nameid_source="source_A",
            item_nameid_revision="rev_A",
        ))

        # Replace with new ID but unknown/missing source and revision
        updated = self.item_repo.save(ItemRecord(
            market_hash_name="AK-47 | Redline (Field-Tested)",
            base_name="AK-47 | Redline",
            item_nameid=300,
            item_nameid_source=None,
            item_nameid_revision=None,
        ))
        self.assertEqual(updated.item_nameid, 300)
        # Must NOT retain source_A or rev_A
        self.assertIsNone(updated.item_nameid_source)
        self.assertIsNone(updated.item_nameid_revision)

    def test_absent_incoming_identifier_preserves_existing_provenance_tuple(self) -> None:
        """When incoming identifier is absent (None), the entire existing provenance tuple is preserved."""
        self.item_repo.save(ItemRecord(
            market_hash_name="AK-47 | Redline (Field-Tested)",
            base_name="AK-47 | Redline",
            item_nameid=100,
            item_nameid_source="source_A",
            item_nameid_revision="rev_A",
        ))

        # Update unrelated attribute without supplying item_nameid
        updated = self.item_repo.save(ItemRecord(
            market_hash_name="AK-47 | Redline (Field-Tested)",
            base_name="AK-47 | Redline",
            exterior="Field-Tested",
            item_nameid=None,
            item_nameid_source=None,
            item_nameid_revision=None,
        ))
        # Entire provenance tuple preserved
        self.assertEqual(updated.item_nameid, 100)
        self.assertEqual(updated.item_nameid_source, "source_A")
        self.assertEqual(updated.item_nameid_revision, "rev_A")
        self.assertEqual(updated.exterior, "Field-Tested")

    def test_classid_provenance_upsert_consistency(self) -> None:
        """Apply identical atomic provenance rules to classid, classid_source, and classid_revision."""
        initial = self.item_repo.save(ItemRecord(
            market_hash_name="AK-47 | Redline (Field-Tested)",
            base_name="AK-47 | Redline",
            classid="7993038091",
            classid_source="steam_ssr_bucket",
            classid_revision="rev_1",
        ))
        self.assertEqual(initial.classid, "7993038091")
        self.assertEqual(initial.classid_source, "steam_ssr_bucket")

        # Replace with new classid and None provenance
        updated = self.item_repo.save(ItemRecord(
            market_hash_name="AK-47 | Redline (Field-Tested)",
            base_name="AK-47 | Redline",
            classid="8000000000",
            classid_source=None,
            classid_revision=None,
        ))
        self.assertEqual(updated.classid, "8000000000")
        self.assertIsNone(updated.classid_source)
        self.assertIsNone(updated.classid_revision)

        # Incoming classid is None: preserves existing classid and provenance
        preserved = self.item_repo.save(ItemRecord(
            market_hash_name="AK-47 | Redline (Field-Tested)",
            base_name="AK-47 | Redline",
            classid=None,
        ))
        self.assertEqual(preserved.classid, "8000000000")

    # -------------------------------------------------------------------------
    # 5. Observation Immutability Enforcement (Hotfix 3)
    # -------------------------------------------------------------------------

    def test_database_trigger_rejects_listing_update(self) -> None:
        """Direct SQL UPDATE on listings must be rejected by database triggers."""
        item = self.item_repo.save(ItemRecord(
            market_hash_name="AK-47 | Redline (Field-Tested)",
            base_name="AK-47 | Redline",
        ))
        listing = self.listing_repo.insert_observation(ListingRecord(
            steam_listing_id="12345",
            item_id=item.item_id,
            price_minor=2500,
            currency_id=1,
            observed_at="2026-05-12T10:00:00Z",
        ))

        # Direct SQL UPDATE must fail
        with self.assertRaises(sqlite3.IntegrityError) as ctx:
            self.db.execute(
                "UPDATE listings SET price_minor = 9999 WHERE listing_id = ?",
                (listing.listing_id,),
            )
        self.assertIn("immutable", str(ctx.exception).lower())

        # Verify listing was not altered
        latest = self.listing_repo.get_latest(item.item_id, 1)
        self.assertEqual(latest.price_minor, 2500)

    def test_database_trigger_rejects_listing_delete(self) -> None:
        """Direct SQL DELETE on listings must be rejected by database triggers."""
        item = self.item_repo.save(ItemRecord(
            market_hash_name="AK-47 | Redline (Field-Tested)",
            base_name="AK-47 | Redline",
        ))
        listing = self.listing_repo.insert_observation(ListingRecord(
            steam_listing_id="12345",
            item_id=item.item_id,
            price_minor=2500,
            currency_id=1,
            observed_at="2026-05-12T10:00:00Z",
        ))

        # Direct SQL DELETE must fail
        with self.assertRaises(sqlite3.IntegrityError) as ctx:
            self.db.execute(
                "DELETE FROM listings WHERE listing_id = ?",
                (listing.listing_id,),
            )
        self.assertIn("immutable", str(ctx.exception).lower())

        # Verify listing still exists
        latest = self.listing_repo.get_latest(item.item_id, 1)
        self.assertIsNotNone(latest)

    def test_database_trigger_rejects_orderbook_snapshot_update(self) -> None:
        """Direct SQL UPDATE on orderbook_snapshots must be rejected by database triggers."""
        item = self.item_repo.save(ItemRecord(
            market_hash_name="AK-47 | Redline (Field-Tested)",
            base_name="AK-47 | Redline",
        ))
        snapshot = self.orderbook_repo.insert_snapshot(OrderbookSnapshotRecord(
            item_id=item.item_id,
            currency_id=1,
            highest_buy_order_minor=2250,
            lowest_sell_order_minor=2488,
            observed_at="2026-05-12T10:00:00Z",
        ))

        with self.assertRaises(sqlite3.IntegrityError) as ctx:
            self.db.execute(
                "UPDATE orderbook_snapshots SET highest_buy_order_minor = 9999 WHERE snapshot_id = ?",
                (snapshot.snapshot_id,),
            )
        self.assertIn("immutable", str(ctx.exception).lower())

    def test_database_trigger_rejects_orderbook_snapshot_delete(self) -> None:
        """Direct SQL DELETE on orderbook_snapshots must be rejected by database triggers."""
        item = self.item_repo.save(ItemRecord(
            market_hash_name="AK-47 | Redline (Field-Tested)",
            base_name="AK-47 | Redline",
        ))
        snapshot = self.orderbook_repo.insert_snapshot(OrderbookSnapshotRecord(
            item_id=item.item_id,
            currency_id=1,
            highest_buy_order_minor=2250,
            lowest_sell_order_minor=2488,
            observed_at="2026-05-12T10:00:00Z",
        ))

        with self.assertRaises(sqlite3.IntegrityError) as ctx:
            self.db.execute(
                "DELETE FROM orderbook_snapshots WHERE snapshot_id = ?",
                (snapshot.snapshot_id,),
            )
        self.assertIn("immutable", str(ctx.exception).lower())

    def test_parent_item_deletion_rejected_protecting_history(self) -> None:
        """Deleting an item that has listing or orderbook observations is blocked by ON DELETE RESTRICT."""
        item = self.item_repo.save(ItemRecord(
            market_hash_name="AK-47 | Redline (Field-Tested)",
            base_name="AK-47 | Redline",
        ))
        self.listing_repo.insert_observation(ListingRecord(
            steam_listing_id="12345",
            item_id=item.item_id,
            price_minor=2500,
            currency_id=1,
            observed_at="2026-05-12T10:00:00Z",
        ))

        # Attempting to delete the item must fail with IntegrityError
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("DELETE FROM items WHERE item_id = ?", (item.item_id,))

        # Item and listing remain intact
        self.assertIsNotNone(self.item_repo.get_by_id(item.item_id))
        self.assertIsNotNone(self.listing_repo.get_latest(item.item_id, 1))

    # -------------------------------------------------------------------------
    # 6. Foreign Key & Check Constraints
    # -------------------------------------------------------------------------

    def test_foreign_key_enforcement_on_listings(self) -> None:
        """Verify foreign key constraint: cannot insert listing with non-existent item_id or currency_id."""
        listing_bad_item = ListingRecord(
            steam_listing_id="111",
            item_id=999999,
            price_minor=1000,
            currency_id=1,
            observed_at="2026-01-01T00:00:00Z",
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.listing_repo.insert_observation(listing_bad_item)

        item = self.item_repo.save(ItemRecord(
            market_hash_name="AK-47 | Redline (Field-Tested)",
            base_name="AK-47 | Redline",
            exterior="Field-Tested",
        ))

        listing_bad_curr = ListingRecord(
            steam_listing_id="112",
            item_id=item.item_id,
            price_minor=1000,
            currency_id=999999,
            observed_at="2026-01-01T00:00:00Z",
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.listing_repo.insert_observation(listing_bad_curr)

    def test_check_constraints_positive_monetary_values(self) -> None:
        """Verify check constraints prevent non-positive prices or quantities."""
        item = self.item_repo.save(ItemRecord(
            market_hash_name="AK-47 | Redline (Field-Tested)",
            base_name="AK-47 | Redline",
            exterior="Field-Tested",
        ))

        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("""
                INSERT INTO listings (
                    steam_listing_id, item_id, price_minor, currency_id, observed_at, source
                ) VALUES ('101', ?, 0, 1, '2026-01-01T00:00:00Z', 'test')
            """, (item.item_id,))

        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("""
                INSERT INTO listings (
                    steam_listing_id, item_id, price_minor, currency_id, observed_at, source
                ) VALUES ('102', ?, -50, 1, '2026-01-01T00:00:00Z', 'test')
            """, (item.item_id,))

    def test_check_constraint_stattrak_and_souvenir_mutual_exclusion(self) -> None:
        """Verify an item cannot have both is_stattrak=1 and is_souvenir=1."""
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("""
                INSERT INTO items (
                    appid, market_hash_name, base_name, is_stattrak, is_souvenir, first_seen_at, updated_at
                ) VALUES (730, 'Impossible Item', 'Impossible', 1, 1, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')
            """)

    # -------------------------------------------------------------------------
    # 7. Item Identity & Variant Separation
    # -------------------------------------------------------------------------

    def test_different_wear_variants_remain_separate(self) -> None:
        """Different wear variants must be stored as separate items with distinct item_ids."""
        ft = self.item_repo.save(ItemRecord(
            market_hash_name="AK-47 | Redline (Field-Tested)",
            base_name="AK-47 | Redline",
            exterior="Field-Tested",
        ))
        mw = self.item_repo.save(ItemRecord(
            market_hash_name="AK-47 | Redline (Minimal Wear)",
            base_name="AK-47 | Redline",
            exterior="Minimal Wear",
        ))

        self.assertNotEqual(ft.item_id, mw.item_id)
        self.assertEqual(ft.base_name, mw.base_name)
        self.assertNotEqual(ft.exterior, mw.exterior)
        self.assertEqual(self.item_repo.count(), 2)

    def test_stattrak_vs_ordinary_remain_separate(self) -> None:
        """StatTrak items must remain separate from non-StatTrak counterparts."""
        normal = self.item_repo.save(ItemRecord(
            market_hash_name="AK-47 | Redline (Field-Tested)",
            base_name="AK-47 | Redline",
            exterior="Field-Tested",
            is_stattrak=False,
        ))
        stattrak = self.item_repo.save(ItemRecord(
            market_hash_name="StatTrak™ AK-47 | Redline (Field-Tested)",
            base_name="AK-47 | Redline",
            exterior="Field-Tested",
            is_stattrak=True,
        ))

        self.assertNotEqual(normal.item_id, stattrak.item_id)
        self.assertFalse(normal.is_stattrak)
        self.assertTrue(stattrak.is_stattrak)

    def test_from_identity_result_adapter(self) -> None:
        """Test ItemRecord.from_identity_result factory method."""
        id_res = ItemIdentityResult(
            appid=730,
            input_url="https://steamcommunity.com/market/listings/730/AK-47%20%7C%20Redline%20%28Field-Tested%29",
            url_type=UrlType.CANONICAL,
            base_name="AK-47 | Redline",
            market_hash_name="AK-47 | Redline (Field-Tested)",
            exterior=WearCategory.FIELD_TESTED,
            is_stattrak=False,
            is_souvenir=False,
            classid="7993038091",
            classid_source="steam_ssr_bucket",
            item_nameid=176000000,
            item_nameid_source="somespecialone/steam-market-ids",
            resolution_status=ResolutionStatus.ITEM_NAMEID_RESOLVED,
            resolution_source=ResolutionSource.COMBINED,
        )

        rec = ItemRecord.from_identity_result(id_res)
        saved = self.item_repo.save(rec)

        self.assertEqual(saved.market_hash_name, "AK-47 | Redline (Field-Tested)")
        self.assertEqual(saved.exterior, "Field-Tested")
        self.assertEqual(saved.item_nameid, 176000000)
        self.assertEqual(saved.classid, "7993038091")

    # -------------------------------------------------------------------------
    # 8. Currency Configuration & UNKNOWN Rules
    # -------------------------------------------------------------------------

    def test_currency_unknown_rules_preserved_as_null(self) -> None:
        """Verify UNKNOWN listing minimums are stored as SQL NULL and not converted to arbitrary numbers."""
        usd = self.currency_repo.get_by_id(1)
        self.assertIsNotNone(usd)
        self.assertEqual(usd.code, "USD")
        self.assertIsNone(usd.min_listing_price_minor)  # UNKNOWN
        self.assertFalse(usd.is_listing_min_verified)
        self.assertFalse(usd.is_price_step_verified)
        self.assertTrue(usd.is_metadata_verified)
        self.assertTrue(usd.is_fee_verified)

    def test_currency_separation(self) -> None:
        """Verify different currencies remain cleanly separated by currency_id."""
        usd = self.currency_repo.get_by_code("USD")
        eur = self.currency_repo.get_by_code("EUR")
        jpy = self.currency_repo.get_by_code("JPY")

        self.assertEqual(usd.currency_id, 1)
        self.assertEqual(eur.currency_id, 3)
        self.assertEqual(jpy.currency_id, 8)
        self.assertEqual(jpy.minor_unit_precision, 0)

    # -------------------------------------------------------------------------
    # 9. Listing History & Immutability
    # -------------------------------------------------------------------------

    def test_listing_history_immutability(self) -> None:
        """Verify listing observations are recorded as an immutable time-series history."""
        item = self.item_repo.save(ItemRecord(
            market_hash_name="AK-47 | Redline (Field-Tested)",
            base_name="AK-47 | Redline",
            exterior="Field-Tested",
        ))

        self.listing_repo.insert_observation(ListingRecord(
            steam_listing_id="49827391028374",
            item_id=item.item_id,
            price_minor=2500,
            fee_minor=325,
            currency_id=1,
            observed_at="2026-05-12T10:00:00Z",
            status="active",
        ))

        self.listing_repo.insert_observation(ListingRecord(
            steam_listing_id="49827391028374",
            item_id=item.item_id,
            price_minor=2450,
            fee_minor=318,
            currency_id=1,
            observed_at="2026-05-12T11:00:00Z",
            status="active",
        ))

        history = self.listing_repo.get_history(item.item_id, currency_id=1)
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0].observed_at, "2026-05-12T11:00:00Z")
        self.assertEqual(history[0].price_minor, 2450)
        self.assertEqual(history[1].observed_at, "2026-05-12T10:00:00Z")
        self.assertEqual(history[1].price_minor, 2500)

        latest = self.listing_repo.get_latest(item.item_id, currency_id=1)
        self.assertIsNotNone(latest)
        self.assertEqual(latest.price_minor, 2450)

    # -------------------------------------------------------------------------
    # 10. Orderbook Snapshots & Immutability
    # -------------------------------------------------------------------------

    def test_orderbook_snapshot_history(self) -> None:
        """Verify orderbook depth snapshots are recorded as immutable history."""
        item = self.item_repo.save(ItemRecord(
            market_hash_name="AK-47 | Redline (Field-Tested)",
            base_name="AK-47 | Redline",
            exterior="Field-Tested",
        ))

        self.orderbook_repo.insert_snapshot(OrderbookSnapshotRecord(
            item_id=item.item_id,
            currency_id=1,
            highest_buy_order_minor=2250,
            lowest_sell_order_minor=2488,
            buy_order_count=211731,
            sell_order_count=254,
            top_level_buy_quantity=14,
            cumulative_buy_quantity=211731,
            observed_at="2026-05-12T10:00:00Z",
            source_payload_version="histogram_v1",
        ))

        self.orderbook_repo.insert_snapshot(OrderbookSnapshotRecord(
            item_id=item.item_id,
            currency_id=1,
            highest_buy_order_minor=2260,
            lowest_sell_order_minor=2480,
            buy_order_count=211800,
            sell_order_count=250,
            top_level_buy_quantity=20,
            cumulative_buy_quantity=211800,
            observed_at="2026-05-12T11:00:00Z",
            source_payload_version="histogram_v1",
        ))

        history = self.orderbook_repo.get_history(item.item_id, currency_id=1)
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0].highest_buy_order_minor, 2260)
        self.assertEqual(history[1].highest_buy_order_minor, 2250)

        latest = self.orderbook_repo.get_latest(item.item_id, currency_id=1)
        self.assertIsNotNone(latest)
        self.assertEqual(latest.highest_buy_order_minor, 2260)

    # -------------------------------------------------------------------------
    # 11. Inventory Snapshots & Atomic Import
    # -------------------------------------------------------------------------

    def test_inventory_snapshot_atomic_import(self) -> None:
        """Verify transactional import of inventory snapshots and linking to items catalog."""
        known_item = self.item_repo.save(ItemRecord(
            market_hash_name="AK-47 | Redline (Field-Tested)",
            base_name="AK-47 | Redline",
            exterior="Field-Tested",
        ))

        items_to_import = [
            InventoryItemRecord(
                snapshot_id=0,
                asset_id="38291049281",
                class_id="7993038091",
                instance_id="0",
                market_hash_name="AK-47 | Redline (Field-Tested)",
                amount=1,
                is_marketable=True,
                is_tradable=False,
                market_restriction_days=7,
            ),
            InventoryItemRecord(
                snapshot_id=0,
                asset_id="38291049282",
                class_id="7993038099",
                instance_id="0",
                market_hash_name="AWP | Asiimov (Field-Tested)",
                amount=1,
                is_marketable=True,
                is_tradable=True,
                market_restriction_days=0,
            ),
        ]

        snapshot, imported = self.inv_repo.import_snapshot(
            items=items_to_import,
            owner_id="76561198000000000",
            observed_at="2026-05-12T12:00:00Z",
        )

        self.assertIsNotNone(snapshot.snapshot_id)
        self.assertEqual(snapshot.total_count, 2)
        self.assertEqual(len(imported), 2)
        self.assertEqual(imported[0].item_id, known_item.item_id)
        self.assertIsNone(imported[1].item_id)

        snapshot_items = self.inv_repo.get_items_for_snapshot(snapshot.snapshot_id)
        self.assertEqual(len(snapshot_items), 2)

        history = self.inv_repo.get_item_history("AK-47 | Redline (Field-Tested)")
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].asset_id, "38291049281")

    # -------------------------------------------------------------------------
    # 12. Portfolio Accounting & Exact Integer Calculations
    # -------------------------------------------------------------------------

    def test_portfolio_transaction_recording_and_integer_cost_basis(self) -> None:
        """Verify exact integer arithmetic for acquisition cost and portfolio summary."""
        item = self.item_repo.save(ItemRecord(
            market_hash_name="AK-47 | Redline (Field-Tested)",
            base_name="AK-47 | Redline",
            exterior="Field-Tested",
        ))

        # 1. Buy 2 units @ $20.00 (2000 cents) + $1.50 (150 cents) fee -> cost = 4150
        tx1 = self.portfolio_repo.record_transaction(PortfolioTransactionRecord(
            item_id=item.item_id,
            transaction_type="BUY",
            quantity=2,
            unit_price_minor=2000,
            total_fees_minor=150,
            currency_id=1,
            timestamp="2026-05-01T10:00:00Z",
            cost_basis_metadata='{"source": "steam_market_listing"}',
        ))
        self.assertIsNotNone(tx1.transaction_id)

        # 2. Buy 1 unit @ $22.50 (2250 cents) + $1.00 (100 cents) fee -> cost = 2350
        self.portfolio_repo.record_transaction(PortfolioTransactionRecord(
            item_id=item.item_id,
            transaction_type="BUY",
            quantity=1,
            unit_price_minor=2250,
            total_fees_minor=100,
            currency_id=1,
            timestamp="2026-05-02T10:00:00Z",
        ))

        total_acq_cost = self.portfolio_repo.calculate_acquisition_cost(item_id=item.item_id, currency_id=1)
        self.assertEqual(total_acq_cost, 6500)

        # 3. Sell 1 unit @ $26.00 (2600 cents) with $3.38 (338 cents) fee -> net = 2262
        self.portfolio_repo.record_transaction(PortfolioTransactionRecord(
            item_id=item.item_id,
            transaction_type="SELL",
            quantity=1,
            unit_price_minor=2600,
            total_fees_minor=338,
            currency_id=1,
            timestamp="2026-05-10T10:00:00Z",
        ))

        summary = self.portfolio_repo.calculate_portfolio_summary(currency_id=1, item_id=item.item_id)
        self.assertEqual(summary.total_bought_quantity, 3)
        self.assertEqual(summary.total_sold_quantity, 1)
        self.assertEqual(summary.net_quantity, 2)
        self.assertEqual(summary.total_acquisition_cost_minor, 6500)
        self.assertEqual(summary.total_fees_paid_minor, 150 + 100 + 338)
        self.assertEqual(summary.total_realized_proceeds_minor, 2262)

    # -------------------------------------------------------------------------
    # 13. Persistence & Reopen (Disk-Backed Database)
    # -------------------------------------------------------------------------

    def test_database_persistence_close_and_reopen(self) -> None:
        """Verify data written to file-backed database persists across connection close and reopen."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_file = Path(tmpdir) / "test_sniper.db"

            with Database(db_file) as disk_db:
                disk_db.initialize()
                c_repo = CurrencyRepository(disk_db)
                c_repo.seed_default_currencies()
                i_repo = ItemRepository(disk_db)

                saved_item = i_repo.save(ItemRecord(
                    market_hash_name="AK-47 | Redline (Field-Tested)",
                    base_name="AK-47 | Redline",
                    exterior="Field-Tested",
                    item_nameid=176000000,
                    item_nameid_source="somespecialone/steam-market-ids",
                ))
                saved_id = saved_item.item_id

            with Database(db_file) as reopened_db:
                self.assertEqual(reopened_db.get_version(), 2)

                re_i_repo = ItemRepository(reopened_db)
                re_item = re_i_repo.get_by_id(saved_id)
                self.assertIsNotNone(re_item)
                self.assertEqual(re_item.market_hash_name, "AK-47 | Redline (Field-Tested)")
                self.assertEqual(re_item.item_nameid, 176000000)

                re_c_repo = CurrencyRepository(reopened_db)
                usd = re_c_repo.get_by_id(1)
                self.assertIsNotNone(usd)
                self.assertEqual(usd.code, "USD")

    # -------------------------------------------------------------------------
    # 14. Concurrency & Thread-Safety
    # -------------------------------------------------------------------------

    def test_concurrent_readers_and_writes(self) -> None:
        """Verify thread-safe concurrent reads and serialized writes under thread lock."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_file = Path(tmpdir) / "concurrent.db"
            db = Database(db_file)
            db.initialize()
            c_repo = CurrencyRepository(db)
            c_repo.seed_default_currencies()
            i_repo = ItemRepository(db)

            item = i_repo.save(ItemRecord(
                market_hash_name="AK-47 | Redline (Field-Tested)",
                base_name="AK-47 | Redline",
                exterior="Field-Tested",
            ))

            l_repo = ListingRepository(db)

            def worker_write(idx: int) -> int:
                rec = ListingRecord(
                    steam_listing_id=f"listing_{idx}",
                    item_id=item.item_id,
                    price_minor=2000 + idx,
                    currency_id=1,
                    observed_at=f"2026-05-12T10:{idx:02d}:00Z",
                )
                saved = l_repo.insert_observation(rec)
                return saved.listing_id

            def worker_read() -> int:
                hist = l_repo.get_history(item.item_id, currency_id=1)
                return len(hist)

            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
                write_futures = [executor.submit(worker_write, i) for i in range(10)]
                read_futures = [executor.submit(worker_read) for _ in range(10)]

                for f in concurrent.futures.as_completed(write_futures):
                    self.assertIsNotNone(f.result())
                for f in concurrent.futures.as_completed(read_futures):
                    self.assertGreaterEqual(f.result(), 0)

            history = l_repo.get_history(item.item_id, currency_id=1, limit=50)
            self.assertEqual(len(history), 10)
            db.close()


if __name__ == "__main__":
    unittest.main()
