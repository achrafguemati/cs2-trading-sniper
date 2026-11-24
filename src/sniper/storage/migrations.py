"""Database schema migrations engine for SQLite storage."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import sqlite3
from typing import Callable


class MigrationError(Exception):
    """Base error for migration failures."""
    pass


class IncompatibleSchemaVersionError(MigrationError):
    """Raised when the database has a newer schema version than supported by code."""
    pass


class DestructiveMigrationError(MigrationError):
    """Raised when a rollback would destroy existing data without explicit authorization."""
    pass


class IntegrityDowngradeError(MigrationError):
    """Raised when attempting to roll back integrity protections while protected records exist."""
    pass


@dataclass(frozen=True)
class Migration:
    """Represents an atomic schema migration step."""
    version: int
    description: str
    up: Callable[[sqlite3.Connection], None]
    down: Callable[[sqlite3.Connection], None]
    affected_tables: tuple[str, ...] = ()


def _migration_1_up(conn: sqlite3.Connection) -> None:
    """Migration 1: Core market items, currencies, listings, orderbook, and inventory."""
    cur = conn.cursor()
    # 1. Currency configurations
    cur.execute("""
        CREATE TABLE currency_configs (
            currency_id INTEGER PRIMARY KEY,
            code TEXT UNIQUE NOT NULL,
            symbol TEXT NOT NULL,
            minor_unit_precision INTEGER NOT NULL CHECK (minor_unit_precision >= 0),
            country TEXT NOT NULL,
            min_listing_price_minor INTEGER CHECK (min_listing_price_minor IS NULL OR min_listing_price_minor > 0),
            price_step_minor INTEGER NOT NULL CHECK (price_step_minor > 0),
            min_fee_steam_minor INTEGER NOT NULL CHECK (min_fee_steam_minor >= 0),
            min_fee_publisher_minor INTEGER NOT NULL CHECK (min_fee_publisher_minor >= 0),
            is_metadata_verified INTEGER NOT NULL CHECK (is_metadata_verified IN (0, 1)),
            is_fee_verified INTEGER NOT NULL CHECK (is_fee_verified IN (0, 1)),
            is_listing_min_verified INTEGER NOT NULL CHECK (is_listing_min_verified IN (0, 1)),
            is_price_step_verified INTEGER NOT NULL CHECK (is_price_step_verified IN (0, 1)),
            notes TEXT,
            updated_at TEXT NOT NULL
        )
    """)

    # 2. Items
    cur.execute("""
        CREATE TABLE items (
            item_id INTEGER PRIMARY KEY AUTOINCREMENT,
            appid INTEGER NOT NULL DEFAULT 730 CHECK (appid > 0),
            market_hash_name TEXT UNIQUE NOT NULL,
            base_name TEXT NOT NULL,
            exterior TEXT,
            is_stattrak INTEGER NOT NULL DEFAULT 0 CHECK (is_stattrak IN (0, 1)),
            is_souvenir INTEGER NOT NULL DEFAULT 0 CHECK (is_souvenir IN (0, 1)),
            group_id TEXT,
            item_nameid INTEGER CHECK (item_nameid IS NULL OR item_nameid > 0),
            item_nameid_source TEXT,
            item_nameid_revision TEXT,
            classid TEXT,
            classid_source TEXT,
            classid_revision TEXT,
            first_seen_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            CONSTRAINT check_not_both_stattrak_and_souvenir CHECK (NOT (is_stattrak = 1 AND is_souvenir = 1))
        )
    """)
    cur.execute("CREATE INDEX idx_items_hash_name ON items(market_hash_name)")
    cur.execute("CREATE INDEX idx_items_base_name ON items(base_name)")
    cur.execute("CREATE INDEX idx_items_group_id ON items(group_id)")
    cur.execute("CREATE INDEX idx_items_nameid ON items(item_nameid)")

    # 3. Listings observations
    cur.execute("""
        CREATE TABLE listings (
            listing_id INTEGER PRIMARY KEY AUTOINCREMENT,
            steam_listing_id TEXT NOT NULL,
            item_id INTEGER NOT NULL REFERENCES items(item_id) ON DELETE CASCADE,
            price_minor INTEGER NOT NULL CHECK (price_minor > 0),
            fee_minor INTEGER CHECK (fee_minor IS NULL OR fee_minor >= 0),
            steam_fee_minor INTEGER CHECK (steam_fee_minor IS NULL OR steam_fee_minor >= 0),
            publisher_fee_minor INTEGER CHECK (publisher_fee_minor IS NULL OR publisher_fee_minor >= 0),
            currency_id INTEGER NOT NULL REFERENCES currency_configs(currency_id) ON DELETE RESTRICT,
            observed_at TEXT NOT NULL,
            source TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            asset_id TEXT
        )
    """)
    cur.execute("CREATE INDEX idx_listings_item_curr ON listings(item_id, currency_id, observed_at)")
    cur.execute("CREATE INDEX idx_listings_steam_id ON listings(steam_listing_id)")

    # 4. Orderbook snapshots
    cur.execute("""
        CREATE TABLE orderbook_snapshots (
            snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER NOT NULL REFERENCES items(item_id) ON DELETE CASCADE,
            currency_id INTEGER NOT NULL REFERENCES currency_configs(currency_id) ON DELETE RESTRICT,
            highest_buy_order_minor INTEGER CHECK (highest_buy_order_minor IS NULL OR highest_buy_order_minor > 0),
            lowest_sell_order_minor INTEGER CHECK (lowest_sell_order_minor IS NULL OR lowest_sell_order_minor > 0),
            buy_order_count INTEGER CHECK (buy_order_count IS NULL OR buy_order_count >= 0),
            sell_order_count INTEGER CHECK (sell_order_count IS NULL OR sell_order_count >= 0),
            top_level_buy_quantity INTEGER CHECK (top_level_buy_quantity IS NULL OR top_level_buy_quantity >= 0),
            cumulative_buy_quantity INTEGER CHECK (cumulative_buy_quantity IS NULL OR cumulative_buy_quantity >= 0),
            buy_graph_json TEXT,
            sell_graph_json TEXT,
            observed_at TEXT NOT NULL,
            source_payload_version TEXT NOT NULL
        )
    """)
    cur.execute("CREATE INDEX idx_orderbook_item_curr ON orderbook_snapshots(item_id, currency_id, observed_at)")

    # 5. Inventory snapshots
    cur.execute("""
        CREATE TABLE inventory_snapshots (
            snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id TEXT,
            total_count INTEGER NOT NULL DEFAULT 0 CHECK (total_count >= 0),
            observed_at TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'public_inventory'
        )
    """)
    cur.execute("CREATE INDEX idx_inv_snapshots_owner ON inventory_snapshots(owner_id, observed_at)")

    # 6. Inventory items
    cur.execute("""
        CREATE TABLE inventory_items (
            inventory_item_id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_id INTEGER NOT NULL REFERENCES inventory_snapshots(snapshot_id) ON DELETE CASCADE,
            asset_id TEXT NOT NULL,
            class_id TEXT NOT NULL,
            instance_id TEXT NOT NULL DEFAULT '0',
            market_hash_name TEXT NOT NULL,
            amount INTEGER NOT NULL DEFAULT 1 CHECK (amount > 0),
            is_marketable INTEGER NOT NULL DEFAULT 0 CHECK (is_marketable IN (0, 1)),
            is_tradable INTEGER NOT NULL DEFAULT 0 CHECK (is_tradable IN (0, 1)),
            market_restriction_days INTEGER CHECK (market_restriction_days IS NULL OR market_restriction_days >= 0),
            item_id INTEGER REFERENCES items(item_id) ON DELETE SET NULL
        )
    """)
    cur.execute("CREATE INDEX idx_inv_items_snapshot ON inventory_items(snapshot_id)")
    cur.execute("CREATE INDEX idx_inv_items_asset ON inventory_items(asset_id)")
    cur.execute("CREATE INDEX idx_inv_items_hash_name ON inventory_items(market_hash_name)")


def _migration_1_down(conn: sqlite3.Connection) -> None:
    """Migration 1 Down."""
    cur = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS inventory_items")
    cur.execute("DROP TABLE IF EXISTS inventory_snapshots")
    cur.execute("DROP TABLE IF EXISTS orderbook_snapshots")
    cur.execute("DROP TABLE IF EXISTS listings")
    cur.execute("DROP TABLE IF EXISTS items")
    cur.execute("DROP TABLE IF EXISTS currency_configs")


def _migration_2_up(conn: sqlite3.Connection) -> None:
    """Migration 2: Portfolio transactions and tracking."""
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE portfolio_transactions (
            transaction_id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER NOT NULL REFERENCES items(item_id) ON DELETE RESTRICT,
            transaction_type TEXT NOT NULL CHECK (transaction_type IN ('BUY', 'SELL')),
            quantity INTEGER NOT NULL CHECK (quantity > 0),
            unit_price_minor INTEGER NOT NULL CHECK (unit_price_minor > 0),
            total_fees_minor INTEGER NOT NULL DEFAULT 0 CHECK (total_fees_minor >= 0),
            currency_id INTEGER NOT NULL REFERENCES currency_configs(currency_id) ON DELETE RESTRICT,
            timestamp TEXT NOT NULL,
            cost_basis_metadata TEXT,
            notes TEXT
        )
    """)
    cur.execute("CREATE INDEX idx_portfolio_item_curr ON portfolio_transactions(item_id, currency_id, timestamp)")
    cur.execute("CREATE INDEX idx_portfolio_type ON portfolio_transactions(transaction_type)")


def _migration_2_down(conn: sqlite3.Connection) -> None:
    """Migration 2 Down."""
    cur = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS portfolio_transactions")


def _migration_3_up(conn: sqlite3.Connection) -> None:
    """Migration 3: Listing/orderbook observation immutability and parent-delete protections."""
    cur = conn.cursor()
    # 1. Database-level immutability triggers for listings observations
    cur.execute("""
        CREATE TRIGGER IF NOT EXISTS prevent_listings_update
        BEFORE UPDATE ON listings
        BEGIN
            SELECT RAISE(ABORT, 'Historical listing observations are immutable; UPDATE is prohibited.');
        END;
    """)
    cur.execute("""
        CREATE TRIGGER IF NOT EXISTS prevent_listings_delete
        BEFORE DELETE ON listings
        BEGIN
            SELECT RAISE(ABORT, 'Historical listing observations are immutable; DELETE is prohibited.');
        END;
    """)

    # 2. Database-level immutability triggers for orderbook snapshots
    cur.execute("""
        CREATE TRIGGER IF NOT EXISTS prevent_orderbook_snapshots_update
        BEFORE UPDATE ON orderbook_snapshots
        BEGIN
            SELECT RAISE(ABORT, 'Historical orderbook snapshots are immutable; UPDATE is prohibited.');
        END;
    """)
    cur.execute("""
        CREATE TRIGGER IF NOT EXISTS prevent_orderbook_snapshots_delete
        BEFORE DELETE ON orderbook_snapshots
        BEGIN
            SELECT RAISE(ABORT, 'Historical orderbook snapshots are immutable; DELETE is prohibited.');
        END;
    """)

    # 3. Parent-delete protection triggers on items
    # Protects existing databases where listings/orderbook_snapshots had ON DELETE CASCADE
    cur.execute("""
        CREATE TRIGGER IF NOT EXISTS prevent_items_delete_with_listings
        BEFORE DELETE ON items
        FOR EACH ROW
        WHEN EXISTS (SELECT 1 FROM listings WHERE item_id = OLD.item_id)
        BEGIN
            SELECT RAISE(ABORT, 'Cannot delete item: historical listing observations exist.');
        END;
    """)
    cur.execute("""
        CREATE TRIGGER IF NOT EXISTS prevent_items_delete_with_orderbooks
        BEFORE DELETE ON items
        FOR EACH ROW
        WHEN EXISTS (SELECT 1 FROM orderbook_snapshots WHERE item_id = OLD.item_id)
        BEGIN
            SELECT RAISE(ABORT, 'Cannot delete item: historical orderbook snapshots exist.');
        END;
    """)


def _migration_3_down(conn: sqlite3.Connection) -> None:
    """Migration 3 Down."""
    cur = conn.cursor()
    cur.execute("DROP TRIGGER IF EXISTS prevent_items_delete_with_orderbooks")
    cur.execute("DROP TRIGGER IF EXISTS prevent_items_delete_with_listings")
    cur.execute("DROP TRIGGER IF EXISTS prevent_orderbook_snapshots_delete")
    cur.execute("DROP TRIGGER IF EXISTS prevent_orderbook_snapshots_update")
    cur.execute("DROP TRIGGER IF EXISTS prevent_listings_delete")
    cur.execute("DROP TRIGGER IF EXISTS prevent_listings_update")


def _migration_4_up(conn: sqlite3.Connection) -> None:
    """Migration 4: Alert events and multi-channel notification deliveries."""
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE alert_events (
            alert_id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER NOT NULL REFERENCES items(item_id),
            currency_id INTEGER NOT NULL REFERENCES currency_configs(currency_id),
            alert_type TEXT NOT NULL,
            alert_fingerprint TEXT NOT NULL,
            highest_buy_order_minor INTEGER NOT NULL,
            lowest_sell_order_minor INTEGER,
            top_level_buy_quantity INTEGER NOT NULL,
            cumulative_buy_quantity INTEGER NOT NULL,
            modeled_steam_fee_minor INTEGER NOT NULL,
            modeled_publisher_fee_minor INTEGER NOT NULL,
            modeled_seller_receives_minor INTEGER NOT NULL,
            historical_baseline_minor INTEGER,
            bid_multiple REAL,
            opportunity_score REAL NOT NULL,
            confidence_score REAL NOT NULL,
            warning_tags TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    cur.execute("CREATE INDEX idx_alert_events_item_id ON alert_events(item_id)")
    cur.execute("CREATE INDEX idx_alert_events_fingerprint ON alert_events(alert_fingerprint)")
    cur.execute("CREATE INDEX idx_alert_events_item_curr_created ON alert_events(item_id, currency_id, created_at)")
    cur.execute("CREATE INDEX idx_alert_events_created_at ON alert_events(created_at)")

    cur.execute("""
        CREATE TABLE notification_deliveries (
            delivery_id INTEGER PRIMARY KEY AUTOINCREMENT,
            alert_id INTEGER NOT NULL REFERENCES alert_events(alert_id) ON DELETE CASCADE,
            channel TEXT NOT NULL,
            status TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 1,
            last_error TEXT,
            delivered_at TEXT,
            created_at TEXT NOT NULL
        )
    """)
    cur.execute("CREATE INDEX idx_notification_deliveries_alert_id ON notification_deliveries(alert_id)")
    cur.execute("CREATE INDEX idx_notification_deliveries_channel_status ON notification_deliveries(channel, status)")


def _migration_4_down(conn: sqlite3.Connection) -> None:
    """Migration 4 Down."""
    cur = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS notification_deliveries")
    cur.execute("DROP TABLE IF EXISTS alert_events")


MIGRATIONS: list[Migration] = [
    Migration(
        version=1,
        description="Core market items, currencies, listings, orderbook, and inventory",
        up=_migration_1_up,
        down=_migration_1_down,
        affected_tables=(
            "inventory_items",
            "inventory_snapshots",
            "orderbook_snapshots",
            "listings",
            "items",
            "currency_configs",
        ),
    ),
    Migration(
        version=2,
        description="Portfolio transactions and tracking",
        up=_migration_2_up,
        down=_migration_2_down,
        affected_tables=("portfolio_transactions",),
    ),
    Migration(
        version=3,
        description="Listing and orderbook observation immutability and parent-delete protections",
        up=_migration_3_up,
        down=_migration_3_down,
        affected_tables=(),
    ),
    Migration(
        version=4,
        description="Alert events and multi-channel notification deliveries",
        up=_migration_4_up,
        down=_migration_4_down,
        affected_tables=("notification_deliveries", "alert_events"),
    ),
]

MAX_SUPPORTED_VERSION = max(m.version for m in MIGRATIONS)


def ensure_migrations_table(conn: sqlite3.Connection) -> None:
    """Ensure the schema_migrations table exists."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL,
            description TEXT NOT NULL
        )
    """)


def get_applied_versions(conn: sqlite3.Connection) -> list[int]:
    """Return sorted list of applied migration versions."""
    ensure_migrations_table(conn)
    cur = conn.cursor()
    cur.execute("SELECT version FROM schema_migrations ORDER BY version ASC")
    return [row[0] for row in cur.fetchall()]


def get_current_version(conn: sqlite3.Connection) -> int:
    """Return the highest applied migration version, or 0 if none."""
    applied = get_applied_versions(conn)
    return applied[-1] if applied else 0


def apply_migrations(
    conn: sqlite3.Connection,
    target_version: int | None = None,
) -> list[int]:
    """Apply pending migrations up to target_version (or latest if None).

    Acquires an atomic BEGIN IMMEDIATE write reservation before reading the schema version,
    preventing concurrent processes from applying migrations based on stale reads.
    Re-checks schema version under the write reservation and safely skips already-applied migrations.

    Rejects active outer transactions: apply_migrations must own its transaction boundary.

    Returns list of newly applied migration versions.
    Enforces atomic migration transactions with rollback on failure.
    """
    if conn.in_transaction:
        raise MigrationError(
            "Cannot execute apply_migrations() on a connection with an active transaction. "
            "apply_migrations must own its BEGIN IMMEDIATE / COMMIT / ROLLBACK transaction boundary."
        )

    if target_version is not None and target_version < 0:
        raise MigrationError(f"Target migration version {target_version} is invalid (must be >= 0).")

    target = target_version if target_version is not None else MAX_SUPPORTED_VERSION
    if target > MAX_SUPPORTED_VERSION:
        raise IncompatibleSchemaVersionError(
            f"Target version {target} exceeds maximum supported version {MAX_SUPPORTED_VERSION}."
        )

    # Acquire write reservation first, before checking version or applying migrations
    conn.execute("BEGIN IMMEDIATE")
    try:
        ensure_migrations_table(conn)
        applied_versions = get_applied_versions(conn)
        current_ver = applied_versions[-1] if applied_versions else 0

        if current_ver > MAX_SUPPORTED_VERSION:
            raise IncompatibleSchemaVersionError(
                f"Database schema version {current_ver} exceeds maximum supported version {MAX_SUPPORTED_VERSION}."
            )

        applied_now: list[int] = []
        for migration in MIGRATIONS:
            if migration.version <= current_ver:
                continue
            if migration.version > target:
                break

            now_iso = datetime.now(timezone.utc).isoformat()
            migration.up(conn)
            conn.execute(
                "INSERT INTO schema_migrations (version, applied_at, description) VALUES (?, ?, ?)",
                (migration.version, now_iso, migration.description),
            )
            applied_now.append(migration.version)

        conn.execute("COMMIT")
        return applied_now
    except Exception as exc:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        if isinstance(exc, MigrationError):
            raise
        raise MigrationError(f"Failed to apply migrations: {exc}") from exc


def rollback_migration(
    conn: sqlite3.Connection,
    target_version: int,
    allow_data_loss: bool = False,
    allow_integrity_downgrade: bool = False,
) -> list[int]:
    """Roll back migrations down to target_version.

    Acquires an atomic BEGIN IMMEDIATE transaction before inspecting schema version,
    calculating rollback targets, or inspecting tables, ensuring no concurrent writes
    or migrations can occur between the check and table/trigger operations.

    Rejects active outer transactions: rollback_migration must own its transaction boundary.

    Protections:
    - Integrity downgrade guard: Refuses to remove Migration 3 observation protection
      triggers while historical observations exist in listings or orderbook_snapshots,
      unless allow_integrity_downgrade=True.
    - Data loss guard: Refuses to drop tables containing data unless allow_data_loss=True.
    - Atomicity: If any step fails, rolls back the entire transaction, leaving no partial state.

    Returns list of rolled back versions in reverse order.
    """
    if conn.in_transaction:
        raise MigrationError(
            "Cannot execute rollback_migration() on a connection with an active transaction. "
            "rollback_migration must own its BEGIN IMMEDIATE / COMMIT / ROLLBACK transaction boundary."
        )

    # Acquire write reservation first, before checking version or calculating rollback
    conn.execute("BEGIN IMMEDIATE")
    try:
        ensure_migrations_table(conn)
        applied_versions = get_applied_versions(conn)
        current_ver = applied_versions[-1] if applied_versions else 0

        if target_version < 0:
            raise MigrationError(f"Target rollback version {target_version} is invalid (must be >= 0).")

        if target_version > MAX_SUPPORTED_VERSION:
            raise IncompatibleSchemaVersionError(
                f"Target rollback version {target_version} exceeds maximum supported version {MAX_SUPPORTED_VERSION}."
            )

        if current_ver > MAX_SUPPORTED_VERSION:
            raise IncompatibleSchemaVersionError(
                f"Database schema version {current_ver} exceeds maximum supported version {MAX_SUPPORTED_VERSION}."
            )

        if target_version > current_ver:
            raise MigrationError(
                f"Cannot rollback to target version {target_version} because it is greater than current version {current_ver}."
            )

        if target_version == current_ver:
            conn.execute("COMMIT")
            return []

        migrations_to_rollback = [
            m for m in reversed(MIGRATIONS)
            if target_version < m.version <= current_ver
        ]

        # 1. Integrity downgrade guard: check if any migration to be rolled back removes
        # observation protection triggers while protected records exist
        if not allow_integrity_downgrade:
            for migration in migrations_to_rollback:
                if migration.version == 3:
                    for protected_table in ("listings", "orderbook_snapshots"):
                        cur = conn.cursor()
                        cur.execute(
                            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name = ?",
                            (protected_table,),
                        )
                        if cur.fetchone()[0] > 0:
                            cur.execute(f"SELECT COUNT(*) FROM {protected_table}")
                            count = cur.fetchone()[0]
                            if count > 0:
                                raise IntegrityDowngradeError(
                                    f"Refusing to rollback migration 3 ({migration.description}): "
                                    f"protected observations exist in '{protected_table}' ({count} record(s)). "
                                    f"Rolling back will remove observation immutability triggers. "
                                    f"Pass allow_integrity_downgrade=True to confirm integrity downgrade."
                                )

        # 2. Safety check: inspect all affected tables for existing data (data loss)
        if not allow_data_loss:
            for migration in migrations_to_rollback:
                for table_name in migration.affected_tables:
                    cur = conn.cursor()
                    cur.execute(
                        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name = ?",
                        (table_name,),
                    )
                    exists = cur.fetchone()[0] > 0
                    if exists:
                        cur.execute(f"SELECT COUNT(*) FROM {table_name}")
                        count = cur.fetchone()[0]
                        if count > 0:
                            raise DestructiveMigrationError(
                                f"Refusing to rollback migration {migration.version} ({migration.description}): "
                                f"table '{table_name}' contains {count} record(s). "
                                f"Pass allow_data_loss=True to confirm destructive rollback."
                            )

        # 3. Execute down migrations atomically
        rolled_back: list[int] = []
        for migration in migrations_to_rollback:
            migration.down(conn)
            conn.execute(
                "DELETE FROM schema_migrations WHERE version = ?",
                (migration.version,),
            )
            rolled_back.append(migration.version)

        conn.execute("COMMIT")
        return rolled_back

    except Exception as exc:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        if isinstance(exc, MigrationError):
            raise
        raise MigrationError(f"Failed to rollback migrations down to version {target_version}: {exc}") from exc
