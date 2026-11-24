"""Comprehensive offline unit tests for SQLite persistence, migrations, and repositories."""

from __future__ import annotations

import concurrent.futures
from pathlib import Path
import re
import sqlite3
import tempfile
import threading
import unittest

from sniper.core.currency import CurrencyConfig
from sniper.core.identity import (
    VERIFIED_MAPPING_REPOSITORY,
    VERIFIED_MAPPING_REVISION,
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
    IntegrityDowngradeError,
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
    # 1. Database Execution API Contract (Hotfix 1 & Hotfix 2)
    # -------------------------------------------------------------------------

    def test_database_execute_returns_execution_result(self) -> None:
        """Verify execute() returns an ExecutionResult with rowcount and lastrowid, not a closed cursor.

        Verifies that:
        - INSERT returns the inserted row ID.
        - Subsequent UPDATE, SELECT, DDL, and DELETE strictly return lastrowid=None (no stale row ID).
        """
        res = self.db.execute(
            "INSERT INTO items (market_hash_name, base_name, first_seen_at, updated_at) VALUES (?, ?, ?, ?)",
            ("Test Hash", "Test Base", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        )
        self.assertIsInstance(res, ExecutionResult)
        self.assertEqual(res.rowcount, 1)
        self.assertIsNotNone(res.lastrowid)
        self.assertGreater(res.lastrowid, 0)
        inserted_id = res.lastrowid

        # UPDATE statement must strictly return lastrowid=None (not the previous inserted_id)
        res_update = self.db.execute(
            "UPDATE items SET base_name = ? WHERE item_id = ?",
            ("Updated Base", inserted_id),
        )
        self.assertIsInstance(res_update, ExecutionResult)
        self.assertEqual(res_update.rowcount, 1)
        self.assertIsNone(res_update.lastrowid)

        # SELECT statement must strictly return lastrowid=None
        res_select = self.db.execute("SELECT * FROM items WHERE item_id = ?", (inserted_id,))
        self.assertIsInstance(res_select, ExecutionResult)
        self.assertIsNone(res_select.lastrowid)

        # DDL statement must strictly return lastrowid=None
        res_ddl = self.db.execute("CREATE TABLE temp_contract_test (id INTEGER PRIMARY KEY)")
        self.assertIsInstance(res_ddl, ExecutionResult)
        self.assertIsNone(res_ddl.lastrowid)

        # DELETE statement must strictly return lastrowid=None
        res_delete = self.db.execute("DELETE FROM temp_contract_test WHERE id = 999")
        self.assertIsInstance(res_delete, ExecutionResult)
        self.assertEqual(res_delete.rowcount, 0)
        self.assertIsNone(res_delete.lastrowid)

    def test_insert_or_ignore_lastrowid_semantics(self) -> None:
        """Verify INSERT OR IGNORE returns rowid when inserted, and None when ignored."""
        res1 = self.db.execute(
            "INSERT OR IGNORE INTO items (market_hash_name, base_name, first_seen_at, updated_at) VALUES (?, ?, ?, ?)",
            ("Unique Item", "Base", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        )
        self.assertEqual(res1.rowcount, 1)
        self.assertIsNotNone(res1.lastrowid)

        # Duplicate insert ignored: rowcount = 0, lastrowid must be None (never stale res1.lastrowid)
        res2 = self.db.execute(
            "INSERT OR IGNORE INTO items (market_hash_name, base_name, first_seen_at, updated_at) VALUES (?, ?, ?, ?)",
            ("Unique Item", "Base", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        )
        self.assertEqual(res2.rowcount, 0)
        self.assertIsNone(res2.lastrowid)

    def test_database_executemany_returns_execution_result(self) -> None:
        """Verify executemany() returns an ExecutionResult with total rowcount and lastrowid=None."""
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
        self.assertIsNone(res.lastrowid)

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
    # 2. Database Initialization & Migrations (M2.2 & M2.2.2)
    # -------------------------------------------------------------------------

    def test_database_initialization(self) -> None:
        """Verify initial database version matches latest migration version (V4)."""
        self.assertEqual(self.db.get_version(), 4)
        applied = get_applied_versions(self.db.connection)
        self.assertEqual(applied, [1, 2, 3, 4])

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
        self.assertEqual(self.db.get_version(), 4)

        # Existing record remains intact
        retrieved = self.item_repo.get_by_id(saved.item_id)
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.market_hash_name, item.market_hash_name)

    def test_migration_from_v1_to_v2_to_v3(self) -> None:
        """Verify incremental step-by-step migration from V1 to V2 to V3."""
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

        # Immutability triggers should not exist yet in V2
        trig = raw_db.fetch_one(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND name='prevent_listings_update'"
        )
        self.assertIsNone(trig)

        # Migrate to V3
        applied_v3 = raw_db.initialize(target_version=3)
        self.assertEqual(applied_v3, [3])
        self.assertEqual(raw_db.get_version(), 3)

        # Triggers now exist in V3
        trig_after = raw_db.fetch_one(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND name='prevent_listings_update'"
        )
        self.assertIsNotNone(trig_after)
        raw_db.close()

    def test_upgrade_existing_m2_2_v2_database_to_v3(self) -> None:
        """Verify that an existing M2.2 database (schema v2) upgrades cleanly to v3.

        Requirements verified:
        - Schema advances to version 3.
        - All existing rows (items, listings, orderbooks) survive unchanged.
        - Direct SQL UPDATE and DELETE on listings and orderbook are rejected.
        - Deleting an item with historical observations is rejected (preventing cascade delete).
        """
        raw_db = Database(":memory:")
        # 1. Initialize strictly to Version 2 (representing original M2.2 state)
        raw_db.initialize(target_version=2)
        self.assertEqual(raw_db.get_version(), 2)

        raw_curr_repo = CurrencyRepository(raw_db)
        raw_curr_repo.seed_default_currencies()
        raw_item_repo = ItemRepository(raw_db)
        raw_listing_repo = ListingRepository(raw_db)
        raw_orderbook_repo = OrderbookRepository(raw_db)

        # 2. Populate with existing item, listing, and orderbook observations
        item1 = raw_item_repo.save(ItemRecord(
            market_hash_name="AK-47 | Redline (Field-Tested)",
            base_name="AK-47 | Redline",
            exterior="Field-Tested",
        ))
        listing = raw_listing_repo.insert_observation(ListingRecord(
            steam_listing_id="listing_m2_2",
            item_id=item1.item_id,
            price_minor=2000,
            currency_id=1,
            observed_at="2026-05-01T00:00:00Z",
        ))

        item2 = raw_item_repo.save(ItemRecord(
            market_hash_name="AWP | Asiimov (Field-Tested)",
            base_name="AWP | Asiimov",
            exterior="Field-Tested",
        ))
        snapshot = raw_orderbook_repo.insert_snapshot(OrderbookSnapshotRecord(
            item_id=item2.item_id,
            currency_id=1,
            highest_buy_order_minor=1900,
            lowest_sell_order_minor=2100,
            observed_at="2026-05-01T00:00:00Z",
        ))

        # 3. Run initialize(target_version=3) on the existing V2 database
        applied = raw_db.initialize(target_version=3)
        self.assertEqual(applied, [3])
        self.assertEqual(raw_db.get_version(), 3)

        # 4. Verify existing rows survived unchanged
        retrieved_item1 = raw_item_repo.get_by_id(item1.item_id)
        self.assertIsNotNone(retrieved_item1)
        self.assertEqual(retrieved_item1.market_hash_name, "AK-47 | Redline (Field-Tested)")

        retrieved_item2 = raw_item_repo.get_by_id(item2.item_id)
        self.assertIsNotNone(retrieved_item2)
        self.assertEqual(retrieved_item2.market_hash_name, "AWP | Asiimov (Field-Tested)")

        retrieved_listing = raw_listing_repo.get_latest(item1.item_id, 1)
        self.assertIsNotNone(retrieved_listing)
        self.assertEqual(retrieved_listing.steam_listing_id, "listing_m2_2")
        self.assertEqual(retrieved_listing.price_minor, 2000)

        retrieved_ob = raw_orderbook_repo.get_latest(item2.item_id, 1)
        self.assertIsNotNone(retrieved_ob)
        self.assertEqual(retrieved_ob.highest_buy_order_minor, 1900)

        # 5. Verify direct UPDATE and DELETE on listings are rejected
        with self.assertRaises(sqlite3.IntegrityError):
            raw_db.execute("UPDATE listings SET price_minor = 9999 WHERE listing_id = ?", (listing.listing_id,))
        with self.assertRaises(sqlite3.IntegrityError):
            raw_db.execute("DELETE FROM listings WHERE listing_id = ?", (listing.listing_id,))

        # 6. Verify direct UPDATE and DELETE on orderbook_snapshots are rejected
        with self.assertRaises(sqlite3.IntegrityError):
            raw_db.execute("UPDATE orderbook_snapshots SET highest_buy_order_minor = 9999 WHERE snapshot_id = ?", (snapshot.snapshot_id,))
        with self.assertRaises(sqlite3.IntegrityError):
            raw_db.execute("DELETE FROM orderbook_snapshots WHERE snapshot_id = ?", (snapshot.snapshot_id,))

        # 7. Verify deleting parent item is blocked, protecting historical observations
        with self.assertRaises(sqlite3.IntegrityError) as ctx_listing:
            raw_db.execute("DELETE FROM items WHERE item_id = ?", (item1.item_id,))
        self.assertIn("historical listing observations exist", str(ctx_listing.exception).lower())

        with self.assertRaises(sqlite3.IntegrityError) as ctx_ob:
            raw_db.execute("DELETE FROM items WHERE item_id = ?", (item2.item_id,))
        self.assertIn("historical orderbook snapshots exist", str(ctx_ob.exception).lower())

        # Verify parent items and observations still exist
        self.assertIsNotNone(raw_item_repo.get_by_id(item1.item_id))
        self.assertIsNotNone(raw_item_repo.get_by_id(item2.item_id))
        self.assertIsNotNone(raw_listing_repo.get_latest(item1.item_id, 1))
        self.assertIsNotNone(raw_orderbook_repo.get_latest(item2.item_id, 1))

        raw_db.close()

    def test_fresh_installation_converges_to_v3(self) -> None:
        """Verify fresh installation applies all migrations up to v3 with identical protections."""
        fresh_db = Database(":memory:")
        applied = fresh_db.initialize(target_version=3)
        self.assertEqual(applied, [1, 2, 3])
        self.assertEqual(fresh_db.get_version(), 3)

        fresh_curr_repo = CurrencyRepository(fresh_db)
        fresh_curr_repo.seed_default_currencies()
        fresh_item_repo = ItemRepository(fresh_db)
        fresh_listing_repo = ListingRepository(fresh_db)
        fresh_orderbook_repo = OrderbookRepository(fresh_db)

        item = fresh_item_repo.save(ItemRecord(
            market_hash_name="M4A4 | Howl (Factory New)",
            base_name="M4A4 | Howl",
            exterior="Factory New",
        ))
        listing = fresh_listing_repo.insert_observation(ListingRecord(
            steam_listing_id="fresh_listing",
            item_id=item.item_id,
            price_minor=500000,
            currency_id=1,
            observed_at="2026-05-01T00:00:00Z",
        ))
        snapshot = fresh_orderbook_repo.insert_snapshot(OrderbookSnapshotRecord(
            item_id=item.item_id,
            currency_id=1,
            highest_buy_order_minor=480000,
            lowest_sell_order_minor=510000,
            observed_at="2026-05-01T00:00:00Z",
        ))

        # Check immutability & parent-delete protections
        with self.assertRaises(sqlite3.IntegrityError):
            fresh_db.execute("UPDATE listings SET price_minor = 9999 WHERE listing_id = ?", (listing.listing_id,))
        with self.assertRaises(sqlite3.IntegrityError):
            fresh_db.execute("DELETE FROM listings WHERE listing_id = ?", (listing.listing_id,))
        with self.assertRaises(sqlite3.IntegrityError):
            fresh_db.execute("UPDATE orderbook_snapshots SET highest_buy_order_minor = 9999 WHERE snapshot_id = ?", (snapshot.snapshot_id,))
        with self.assertRaises(sqlite3.IntegrityError):
            fresh_db.execute("DELETE FROM orderbook_snapshots WHERE snapshot_id = ?", (snapshot.snapshot_id,))
        with self.assertRaises(sqlite3.IntegrityError):
            fresh_db.execute("DELETE FROM items WHERE item_id = ?", (item.item_id,))

        fresh_db.close()

    def test_migration_rollback_empty_schema(self) -> None:
        """Verify rolling back a migration on empty schema cleanly removes schema changes."""
        raw_db = Database(":memory:")
        raw_db.initialize(target_version=3)
        self.assertEqual(raw_db.get_version(), 3)

        # Roll back to Version 2 on empty schema (drops V3 triggers)
        rolled_back_v2 = rollback_migration(raw_db.connection, target_version=2)
        self.assertEqual(rolled_back_v2, [3])
        self.assertEqual(raw_db.get_version(), 2)

        # Triggers dropped
        trig = raw_db.fetch_one(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND name='prevent_listings_update'"
        )
        self.assertIsNone(trig)

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
    # 3. Destructive Rollback Protection & Atomicity (Hotfix 3 & Hotfix 4)
    # -------------------------------------------------------------------------

    def test_rollback_target_version_validation(self) -> None:
        """Verify input validation on rollback target versions."""
        raw_db = Database(":memory:")
        raw_db.initialize()

        # Negative target version rejected
        with self.assertRaises(MigrationError):
            raw_db.rollback(target_version=-1)

        # Target version exceeding max supported rejected
        with self.assertRaises(IncompatibleSchemaVersionError):
            raw_db.rollback(target_version=999)

        # Target version greater than current rejected
        raw_db.rollback(target_version=2, allow_data_loss=True)
        self.assertEqual(raw_db.get_version(), 2)
        with self.assertRaises(MigrationError):
            raw_db.rollback(target_version=3)

        # Target version equal to current is clean no-op
        res = raw_db.rollback(target_version=2)
        self.assertEqual(res, [])
        self.assertEqual(raw_db.get_version(), 2)
        raw_db.close()

    def test_rollback_failure_restores_pre_rollback_state(self) -> None:
        """Verify that failure during a multi-step rollback aborts atomically and restores original state."""
        raw_db = Database(":memory:")
        raw_db.initialize(target_version=3)
        self.assertEqual(raw_db.get_version(), 3)

        from sniper.storage import migrations
        # Simulate a failure in migration 2 down during rollback from 3 down to 1
        m2 = next(m for m in migrations.MIGRATIONS if m.version == 2)
        broken_m2 = Migration(
            version=m2.version,
            description=m2.description,
            up=m2.up,
            down=lambda conn: conn.execute("SYNTAX ERROR IN DOWN MIGRATION;"),
            affected_tables=m2.affected_tables,
        )
        original_migrations = list(migrations.MIGRATIONS)
        try:
            migrations.MIGRATIONS[1] = broken_m2
            with self.assertRaises(MigrationError):
                raw_db.rollback(target_version=1, allow_data_loss=True)
        finally:
            migrations.MIGRATIONS = original_migrations

        # Entire rollback was undone: schema version must still be 3, triggers must still exist
        self.assertEqual(raw_db.get_version(), 3)
        trig = raw_db.fetch_one(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND name='prevent_listings_update'"
        )
        self.assertIsNotNone(trig)
        raw_db.close()

    def test_destructive_rollback_rejected_on_populated_v2(self) -> None:
        """Verify rollback from V2 to V1 fails with DestructiveMigrationError when portfolio data exists."""
        raw_db = Database(":memory:")
        raw_db.initialize(target_version=2)
        c_repo = CurrencyRepository(raw_db)
        c_repo.seed_default_currencies()
        i_repo = ItemRepository(raw_db)
        p_repo = PortfolioRepository(raw_db)

        item = i_repo.save(ItemRecord(
            market_hash_name="AK-47 | Redline (Field-Tested)",
            base_name="AK-47 | Redline",
        ))
        p_repo.record_transaction(PortfolioTransactionRecord(
            item_id=item.item_id,
            transaction_type="BUY",
            quantity=1,
            unit_price_minor=2000,
            currency_id=1,
        ))

        # Rollback without allow_data_loss=True must be rejected
        with self.assertRaises(DestructiveMigrationError) as ctx:
            raw_db.rollback(target_version=1, allow_data_loss=False)

        self.assertIn("portfolio_transactions", str(ctx.exception))
        self.assertIn("1 record(s)", str(ctx.exception))

        # Data and schema version remain unchanged
        self.assertEqual(raw_db.get_version(), 2)
        txs = p_repo.get_transactions()
        self.assertEqual(len(txs), 1)

        # Rollback with allow_data_loss=True succeeds
        rolled_back = raw_db.rollback(target_version=1, allow_data_loss=True)
        self.assertEqual(rolled_back, [2])
        self.assertEqual(raw_db.get_version(), 1)
        raw_db.close()

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

    def test_schema_version_read_occurs_under_write_reservation(self) -> None:
        """Verify that rollback_migration and apply_migrations acquire BEGIN IMMEDIATE before reading version."""
        raw_db = Database(":memory:")
        raw_db.initialize()

        conn = raw_db.connection
        stmts: list[str] = []
        conn.set_trace_callback(lambda s: stmts.append(s.strip().upper()))
        select_version_pattern = re.compile(
            r"^\s*SELECT\s+version\s+FROM\s+schema_migrations",
            re.IGNORECASE,
        )
        try:
            # 1. Test rollback_migration
            stmts.clear()
            rollback_migration(conn, target_version=2)
            begin_indices = [i for i, s in enumerate(stmts) if s.strip() == "BEGIN IMMEDIATE"]
            select_indices = [i for i, s in enumerate(stmts) if select_version_pattern.search(s)]

            self.assertTrue(len(begin_indices) > 0, "Expected BEGIN IMMEDIATE statement in rollback_migration trace")
            self.assertTrue(len(select_indices) > 0, "Expected SELECT version FROM schema_migrations in rollback trace")
            self.assertLess(
                begin_indices[0],
                select_indices[0],
                "rollback_migration must acquire BEGIN IMMEDIATE before executing SELECT version FROM schema_migrations",
            )

            # 2. Test apply_migrations
            stmts.clear()
            apply_migrations(conn, target_version=3)
            begin_indices_apply = [i for i, s in enumerate(stmts) if s.strip() == "BEGIN IMMEDIATE"]
            select_indices_apply = [i for i, s in enumerate(stmts) if select_version_pattern.search(s)]

            self.assertTrue(len(begin_indices_apply) > 0, "Expected BEGIN IMMEDIATE statement in apply_migrations trace")
            self.assertTrue(len(select_indices_apply) > 0, "Expected SELECT version FROM schema_migrations in apply trace")
            self.assertLess(
                begin_indices_apply[0],
                select_indices_apply[0],
                "apply_migrations must acquire BEGIN IMMEDIATE before executing SELECT version FROM schema_migrations",
            )
        finally:
            conn.set_trace_callback(None)
            raw_db.close()

    def test_rollback_inside_active_transaction_rejected(self) -> None:
        """Verify rollback_migration rejects execution on a connection with an active outer transaction."""
        raw_db = Database(":memory:")
        raw_db.initialize()
        conn = raw_db.connection

        # Start outer transaction manually
        conn.execute("BEGIN IMMEDIATE")
        self.assertTrue(conn.in_transaction)

        with self.assertRaises(MigrationError) as ctx:
            rollback_migration(conn, target_version=2)
        self.assertIn("active transaction", str(ctx.exception).lower())

        # Roll back outer transaction cleanly
        conn.execute("ROLLBACK")
        self.assertEqual(raw_db.get_version(), 4)
        raw_db.close()

    def test_apply_migrations_inside_active_transaction_rejected(self) -> None:
        """Verify apply_migrations rejects execution on a connection with an active outer transaction."""
        raw_db = Database(":memory:")
        raw_db.initialize(target_version=2)
        conn = raw_db.connection

        # Start outer transaction manually
        conn.execute("BEGIN IMMEDIATE")
        self.assertTrue(conn.in_transaction)

        with self.assertRaises(MigrationError) as ctx:
            apply_migrations(conn, target_version=3)
        self.assertIn("active transaction", str(ctx.exception).lower())

        conn.execute("ROLLBACK")
        self.assertEqual(raw_db.get_version(), 2)
        raw_db.close()

    def test_concurrent_migrations_serialize_cleanly(self) -> None:
        """Verify multiple connections attempting concurrent migrations serialize without state corruption."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "concurrent_mig.db"

            # Pre-create database
            with Database(db_path) as init_db:
                pass

            def run_init(i: int) -> int:
                with Database(db_path) as db:
                    db.initialize()
                    return db.get_version()

            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
                futures = [executor.submit(run_init, i) for i in range(4)]
                results = [f.result() for f in concurrent.futures.as_completed(futures)]

            self.assertEqual(results, [4, 4, 4, 4])
            with Database(db_path) as final_db:
                self.assertEqual(final_db.get_version(), 4)
                applied = get_applied_versions(final_db.connection)
                self.assertEqual(applied, [1, 2, 3, 4])

    def test_concurrent_apply_versus_rollback_serialize_cleanly(self) -> None:
        """Verify concurrent apply and rollback across independent connections serialize cleanly without corruption."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "concurrent_apply_rollback.db"

            # 1. Initialize database to version 2
            with Database(db_path) as init_db:
                applied = init_db.initialize(target_version=2)
                self.assertEqual(applied, [1, 2])
                self.assertEqual(init_db.get_version(), 2)

            # 2. Set up synchronization barrier for 2 threads
            barrier = threading.Barrier(2)
            results: dict[str, list[int]] = {}
            exceptions: dict[str, Exception] = {}

            def worker_apply() -> None:
                try:
                    with Database(db_path) as db_a:
                        barrier.wait(timeout=10.0)
                        results["apply"] = apply_migrations(db_a.connection, target_version=3)
                except Exception as exc:
                    exceptions["apply"] = exc

            def worker_rollback() -> None:
                try:
                    with Database(db_path) as db_b:
                        barrier.wait(timeout=10.0)
                        results["rollback"] = rollback_migration(
                            db_b.connection,
                            target_version=1,
                            allow_data_loss=True,
                            allow_integrity_downgrade=True,
                        )
                except Exception as exc:
                    exceptions["rollback"] = exc

            thread_a = threading.Thread(target=worker_apply, name="worker_apply")
            thread_b = threading.Thread(target=worker_rollback, name="worker_rollback")

            thread_a.start()
            thread_b.start()

            thread_a.join(timeout=15.0)
            thread_b.join(timeout=15.0)

            self.assertFalse(thread_a.is_alive(), "worker_apply thread timed out")
            self.assertFalse(thread_b.is_alive(), "worker_rollback thread timed out")
            self.assertEqual(exceptions, {}, f"Unexpected exceptions during concurrent migration: {exceptions}")

            # 3. Verify SQLite serialized the write operations into a valid serial order
            with Database(db_path) as verify_db:
                final_version = verify_db.get_version()
                applied_versions = get_applied_versions(verify_db.connection)

                # Query physical schema
                portfolio_tbl = verify_db.fetch_one(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='portfolio_transactions'"
                )
                listing_trig = verify_db.fetch_one(
                    "SELECT name FROM sqlite_master WHERE type='trigger' AND name='prevent_listings_update'"
                )

                if results["apply"] == [3] and results["rollback"] == [3, 2]:
                    # Serial Order 1: Apply ran first (2 -> 3), then Rollback ran (3 -> 1)
                    self.assertEqual(final_version, 1)
                    self.assertEqual(applied_versions, [1])
                    self.assertIsNone(portfolio_tbl, "portfolio_transactions table must not exist at v1")
                    self.assertIsNone(listing_trig, "prevent_listings_update trigger must not exist at v1")
                elif results["rollback"] == [2] and results["apply"] == [2, 3]:
                    # Serial Order 2: Rollback ran first (2 -> 1), then Apply ran (1 -> 3)
                    self.assertEqual(final_version, 3)
                    self.assertEqual(applied_versions, [1, 2, 3])
                    self.assertIsNotNone(portfolio_tbl, "portfolio_transactions table must exist at v3")
                    self.assertIsNotNone(listing_trig, "prevent_listings_update trigger must exist at v3")
                else:
                    self.fail(
                        f"Non-serial outcome observed: apply={results.get('apply')}, "
                        f"rollback={results.get('rollback')}, final_version={final_version}"
                    )

                # Core items table from v1 must always exist intact
                items_tbl = verify_db.fetch_one(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='items'"
                )
                self.assertIsNotNone(items_tbl, "Core items table must exist under both serial outcomes")

    def test_concurrent_apply_vs_rollback_rejects_integrity_downgrade(self) -> None:
        """Verify concurrent apply vs rollback rejects integrity downgrade when listings exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "concurrent_integrity.db"

            # Initialize database to version 2 and insert an item and listing observation
            with Database(db_path) as init_db:
                init_db.initialize(target_version=2)
                curr_repo = CurrencyRepository(init_db)
                curr_repo.seed_default_currencies()
                item_repo = ItemRepository(init_db)
                listing_repo = ListingRepository(init_db)

                item = item_repo.save(ItemRecord(
                    market_hash_name="AK-47 | Slate (Field-Tested)",
                    base_name="AK-47 | Slate",
                ))
                listing_repo.insert_observation(ListingRecord(
                    steam_listing_id="integrity_test_listing",
                    item_id=item.item_id,
                    price_minor=1200,
                    currency_id=1,
                    observed_at="2026-05-15T00:00:00Z",
                ))

            barrier = threading.Barrier(2)
            results: dict[str, list[int]] = {}
            exceptions: dict[str, Exception] = {}

            def worker_apply() -> None:
                try:
                    with Database(db_path) as db_a:
                        barrier.wait(timeout=10.0)
                        results["apply"] = apply_migrations(db_a.connection, target_version=3)
                except Exception as exc:
                    exceptions["apply"] = exc

            def worker_rollback() -> None:
                try:
                    with Database(db_path) as db_b:
                        barrier.wait(timeout=10.0)
                        # allow_integrity_downgrade is False (default)
                        results["rollback"] = rollback_migration(
                            db_b.connection,
                            target_version=1,
                            allow_data_loss=True,
                            allow_integrity_downgrade=False,
                        )
                except Exception as exc:
                    exceptions["rollback"] = exc

            thread_a = threading.Thread(target=worker_apply, name="worker_apply_int")
            thread_b = threading.Thread(target=worker_rollback, name="worker_rollback_int")

            thread_a.start()
            thread_b.start()

            thread_a.join(timeout=15.0)
            thread_b.join(timeout=15.0)

            self.assertFalse(thread_a.is_alive())
            self.assertFalse(thread_b.is_alive())

            with Database(db_path) as verify_db:
                final_version = verify_db.get_version()
                applied_versions = get_applied_versions(verify_db.connection)

                if "rollback" in exceptions:
                    # Apply ran first (2 -> 3), installing V3 triggers.
                    # Then Rollback saw V3 with listings present and raised IntegrityDowngradeError.
                    self.assertIsInstance(exceptions["rollback"], IntegrityDowngradeError)
                    self.assertEqual(results.get("apply"), [3])
                    self.assertEqual(final_version, 3)
                    self.assertEqual(applied_versions, [1, 2, 3])
                else:
                    # Rollback ran first (2 -> 1, V2 doesn't have observation triggers, only portfolio table).
                    # Then Apply ran (1 -> 3).
                    self.assertEqual(results.get("rollback"), [2])
                    self.assertEqual(results.get("apply"), [2, 3])
                    self.assertEqual(final_version, 3)
                    self.assertEqual(applied_versions, [1, 2, 3])

                # Under both execution paths, the listing data and schema remain completely intact
                v_listing_repo = ListingRepository(verify_db)
                obs = v_listing_repo.get_latest(item.item_id, 1)
                self.assertIsNotNone(obs, "Historical listing observation must remain intact")
                self.assertEqual(obs.steam_listing_id, "integrity_test_listing")
                self.assertEqual(obs.price_minor, 1200)

    def test_v3_integrity_downgrade_blocked_when_listings_exist(self) -> None:
        """Verify rolling back V3 to V2 is blocked by default when historical listings exist."""
        raw_db = Database(":memory:")
        raw_db.initialize(target_version=3)
        c_repo = CurrencyRepository(raw_db)
        c_repo.seed_default_currencies()
        i_repo = ItemRepository(raw_db)
        l_repo = ListingRepository(raw_db)

        item = i_repo.save(ItemRecord(
            market_hash_name="AK-47 | Redline (Field-Tested)",
            base_name="AK-47 | Redline",
        ))
        listing = l_repo.insert_observation(ListingRecord(
            steam_listing_id="listing_protect_test",
            item_id=item.item_id,
            price_minor=2500,
            currency_id=1,
            observed_at="2026-05-12T10:00:00Z",
        ))

        # 1. Rollback without allow_integrity_downgrade is rejected
        with self.assertRaises(IntegrityDowngradeError) as ctx:
            raw_db.rollback(target_version=2)
        self.assertIn("listings", str(ctx.exception))
        self.assertIn("immutability triggers", str(ctx.exception))

        # 2. allow_data_loss=True does NOT authorize integrity downgrade (separate semantics)
        with self.assertRaises(IntegrityDowngradeError):
            raw_db.rollback(target_version=2, allow_data_loss=True)

        # 3. Schema version and triggers remain intact
        self.assertEqual(raw_db.get_version(), 3)
        with self.assertRaises(sqlite3.IntegrityError):
            raw_db.execute("UPDATE listings SET price_minor = 9999 WHERE listing_id = ?", (listing.listing_id,))
        with self.assertRaises(sqlite3.IntegrityError):
            raw_db.execute("DELETE FROM listings WHERE listing_id = ?", (listing.listing_id,))

        # History remains intact
        self.assertIsNotNone(l_repo.get_latest(item.item_id, 1))
        raw_db.close()

    def test_v3_integrity_downgrade_blocked_when_orderbook_snapshots_exist(self) -> None:
        """Verify rolling back V3 to V2 is blocked by default when orderbook snapshots exist."""
        raw_db = Database(":memory:")
        raw_db.initialize(target_version=3)
        c_repo = CurrencyRepository(raw_db)
        c_repo.seed_default_currencies()
        i_repo = ItemRepository(raw_db)
        ob_repo = OrderbookRepository(raw_db)

        item = i_repo.save(ItemRecord(
            market_hash_name="AWP | Asiimov (Field-Tested)",
            base_name="AWP | Asiimov",
        ))
        ob_repo.insert_snapshot(OrderbookSnapshotRecord(
            item_id=item.item_id,
            currency_id=1,
            highest_buy_order_minor=5000,
            lowest_sell_order_minor=6000,
            observed_at="2026-05-12T10:00:00Z",
        ))

        with self.assertRaises(IntegrityDowngradeError) as ctx:
            raw_db.rollback(target_version=2)
        self.assertIn("orderbook_snapshots", str(ctx.exception))
        self.assertEqual(raw_db.get_version(), 3)
        raw_db.close()

    def test_v3_integrity_downgrade_explicit_opt_in_preserves_history(self) -> None:
        """Verify explicit allow_integrity_downgrade=True removes triggers but preserves all historical data."""
        raw_db = Database(":memory:")
        raw_db.initialize(target_version=3)
        c_repo = CurrencyRepository(raw_db)
        c_repo.seed_default_currencies()
        i_repo = ItemRepository(raw_db)
        l_repo = ListingRepository(raw_db)
        ob_repo = OrderbookRepository(raw_db)

        item = i_repo.save(ItemRecord(
            market_hash_name="AK-47 | Redline (Field-Tested)",
            base_name="AK-47 | Redline",
        ))
        listing = l_repo.insert_observation(ListingRecord(
            steam_listing_id="listing_downgrade_test",
            item_id=item.item_id,
            price_minor=2000,
            currency_id=1,
            observed_at="2026-05-12T10:00:00Z",
        ))
        snapshot = ob_repo.insert_snapshot(OrderbookSnapshotRecord(
            item_id=item.item_id,
            currency_id=1,
            highest_buy_order_minor=1900,
            lowest_sell_order_minor=2100,
            observed_at="2026-05-12T10:00:00Z",
        ))

        # Explicit downgrade with allow_integrity_downgrade=True
        rolled_back = raw_db.rollback(target_version=2, allow_integrity_downgrade=True)
        self.assertEqual(rolled_back, [3])
        self.assertEqual(raw_db.get_version(), 2)

        # Triggers are removed in V2
        trig = raw_db.fetch_one(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND name='prevent_listings_update'"
        )
        self.assertIsNone(trig)

        # Historical records survive completely intact
        retrieved_listing = l_repo.get_latest(item.item_id, 1)
        self.assertIsNotNone(retrieved_listing)
        self.assertEqual(retrieved_listing.steam_listing_id, "listing_downgrade_test")

        retrieved_snapshot = ob_repo.get_latest(item.item_id, 1)
        self.assertIsNotNone(retrieved_snapshot)
        self.assertEqual(retrieved_snapshot.highest_buy_order_minor, 1900)
        raw_db.close()

    # -------------------------------------------------------------------------
    # 4. Identity Provenance Upsert Consistency (Hotfix 2)
    # -------------------------------------------------------------------------

    def test_provenance_replacement_with_new_provenance(self) -> None:
        """When a new item_nameid is supplied with verified provenance, all three fields update atomically."""
        initial = self.item_repo.save(ItemRecord(
            market_hash_name="AK-47 | Redline (Field-Tested)",
            base_name="AK-47 | Redline",
        ))
        self.assertIsNone(initial.item_nameid)
        self.assertIsNone(initial.item_nameid_source)
        self.assertIsNone(initial.item_nameid_revision)

        # Update with new identifier and verified provenance
        updated = self.item_repo.save(ItemRecord(
            market_hash_name="AK-47 | Redline (Field-Tested)",
            base_name="AK-47 | Redline",
            item_nameid=7178002,
            item_nameid_source=VERIFIED_MAPPING_REPOSITORY,
            item_nameid_revision=VERIFIED_MAPPING_REVISION,
        ))
        self.assertEqual(updated.item_nameid, 7178002)
        self.assertEqual(updated.item_nameid_source, VERIFIED_MAPPING_REPOSITORY)
        self.assertEqual(updated.item_nameid_revision, VERIFIED_MAPPING_REVISION)

    def test_provenance_replacement_clears_stale_provenance_when_unknown(self) -> None:
        """When an incoming item_nameid has unknown/missing provenance, it is normalized to NULL."""
        # Brand-new record with missing provenance fails closed to NULL
        new_unknown = self.item_repo.save(ItemRecord(
            market_hash_name="AK-47 | Redline (Field-Tested)",
            base_name="AK-47 | Redline",
            item_nameid=300,
            item_nameid_source=None,
            item_nameid_revision=None,
        ))
        self.assertIsNone(new_unknown.item_nameid)
        self.assertIsNone(new_unknown.item_nameid_source)
        self.assertIsNone(new_unknown.item_nameid_revision)

    def test_absent_incoming_identifier_preserves_existing_provenance_tuple(self) -> None:
        """When incoming identifier is absent (None), the entire existing provenance tuple is preserved."""
        self.item_repo.save(ItemRecord(
            market_hash_name="AK-47 | Redline (Field-Tested)",
            base_name="AK-47 | Redline",
            item_nameid=7178002,
            item_nameid_source=VERIFIED_MAPPING_REPOSITORY,
            item_nameid_revision=VERIFIED_MAPPING_REVISION,
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
        self.assertEqual(updated.item_nameid, 7178002)
        self.assertEqual(updated.item_nameid_source, VERIFIED_MAPPING_REPOSITORY)
        self.assertEqual(updated.item_nameid_revision, VERIFIED_MAPPING_REVISION)
        self.assertEqual(updated.exterior, "Field-Tested")

    def test_classid_provenance_upsert_consistency(self) -> None:
        """Apply identical atomic provenance rules to classid, classid_source, and classid_revision."""
        initial = self.item_repo.save(ItemRecord(
            market_hash_name="AK-47 | Redline (Field-Tested)",
            base_name="AK-47 | Redline",
            classid="7993038091",
            classid_source="steam_ssr_bucket",
            classid_revision=None,
        ))
        self.assertEqual(initial.classid, "7993038091")
        self.assertEqual(initial.classid_source, "steam_ssr_bucket")
        self.assertIsNone(initial.classid_revision)

        # Replace with unverified classid: trusted classid is preserved
        updated = self.item_repo.save(ItemRecord(
            market_hash_name="AK-47 | Redline (Field-Tested)",
            base_name="AK-47 | Redline",
            classid="8000000000",
            classid_source=None,
            classid_revision=None,
        ))
        self.assertEqual(updated.classid, "7993038091")
        self.assertEqual(updated.classid_source, "steam_ssr_bucket")
        self.assertIsNone(updated.classid_revision)

        # Incoming classid is None: preserves existing classid and provenance
        preserved = self.item_repo.save(ItemRecord(
            market_hash_name="AK-47 | Redline (Field-Tested)",
            base_name="AK-47 | Redline",
            classid=None,
        ))
        self.assertEqual(preserved.classid, "7993038091")

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

        # Attempting to delete the item with listings must fail with IntegrityError
        with self.assertRaises(sqlite3.IntegrityError) as ctx1:
            self.db.execute("DELETE FROM items WHERE item_id = ?", (item.item_id,))
        self.assertIn("historical listing observations exist", str(ctx1.exception).lower())

        # Item and listing remain intact
        self.assertIsNotNone(self.item_repo.get_by_id(item.item_id))
        self.assertIsNotNone(self.listing_repo.get_latest(item.item_id, 1))

        # Test item with orderbook snapshots
        item2 = self.item_repo.save(ItemRecord(
            market_hash_name="AWP | Asiimov (Field-Tested)",
            base_name="AWP | Asiimov",
        ))
        self.orderbook_repo.insert_snapshot(OrderbookSnapshotRecord(
            item_id=item2.item_id,
            currency_id=1,
            highest_buy_order_minor=5000,
            lowest_sell_order_minor=6000,
            observed_at="2026-05-12T10:00:00Z",
        ))
        with self.assertRaises(sqlite3.IntegrityError) as ctx2:
            self.db.execute("DELETE FROM items WHERE item_id = ?", (item2.item_id,))
        self.assertIn("historical orderbook snapshots exist", str(ctx2.exception).lower())
        self.assertIsNotNone(self.item_repo.get_by_id(item2.item_id))

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
            item_nameid_source=VERIFIED_MAPPING_REPOSITORY,
            item_nameid_revision=VERIFIED_MAPPING_REVISION,
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
                    item_nameid_source=VERIFIED_MAPPING_REPOSITORY,
                    item_nameid_revision=VERIFIED_MAPPING_REVISION,
                ))
                saved_id = saved_item.item_id

            with Database(db_file) as reopened_db:
                self.assertEqual(reopened_db.get_version(), 4)

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
