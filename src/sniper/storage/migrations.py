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

    # 3. Listings observations (immutable history, ON DELETE RESTRICT)
    cur.execute("""
        CREATE TABLE listings (
            listing_id INTEGER PRIMARY KEY AUTOINCREMENT,
            steam_listing_id TEXT NOT NULL,
            item_id INTEGER NOT NULL REFERENCES items(item_id) ON DELETE RESTRICT,
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

    # 4. Orderbook snapshots (immutable history, ON DELETE RESTRICT)
    cur.execute("""
        CREATE TABLE orderbook_snapshots (
            snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER NOT NULL REFERENCES items(item_id) ON DELETE RESTRICT,
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

    # 7. Database-level immutability triggers for listings observations
    cur.execute("""
        CREATE TRIGGER prevent_listings_update
        BEFORE UPDATE ON listings
        BEGIN
            SELECT RAISE(ABORT, 'Historical listing observations are immutable; UPDATE is prohibited.');
        END;
    """)
    cur.execute("""
        CREATE TRIGGER prevent_listings_delete
        BEFORE DELETE ON listings
        BEGIN
            SELECT RAISE(ABORT, 'Historical listing observations are immutable; DELETE is prohibited.');
        END;
    """)

    # 8. Database-level immutability triggers for orderbook snapshots
    cur.execute("""
        CREATE TRIGGER prevent_orderbook_snapshots_update
        BEFORE UPDATE ON orderbook_snapshots
        BEGIN
            SELECT RAISE(ABORT, 'Historical orderbook snapshots are immutable; UPDATE is prohibited.');
        END;
    """)
    cur.execute("""
        CREATE TRIGGER prevent_orderbook_snapshots_delete
        BEFORE DELETE ON orderbook_snapshots
        BEGIN
            SELECT RAISE(ABORT, 'Historical orderbook snapshots are immutable; DELETE is prohibited.');
        END;
    """)


def _migration_1_down(conn: sqlite3.Connection) -> None:
    """Migration 1 Down."""
    cur = conn.cursor()
    cur.execute("DROP TRIGGER IF EXISTS prevent_listings_update")
    cur.execute("DROP TRIGGER IF EXISTS prevent_listings_delete")
    cur.execute("DROP TRIGGER IF EXISTS prevent_orderbook_snapshots_update")
    cur.execute("DROP TRIGGER IF EXISTS prevent_orderbook_snapshots_delete")
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


MIGRATIONS: list[Migration] = [
    Migration(
        version=1,
        description="Core market items, currencies, listings, orderbook, and inventory",
        up=_migration_1_up,
        down=_migration_1_down,
        affected_tables=("inventory_items", "inventory_snapshots", "orderbook_snapshots", "listings", "items", "currency_configs"),
    ),
    Migration(
        version=2,
        description="Portfolio transactions and tracking",
        up=_migration_2_up,
        down=_migration_2_down,
        affected_tables=("portfolio_transactions",),
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

    Returns list of newly applied migration versions.
    Enforces atomic migration transactions with rollback on failure.
    """
    ensure_migrations_table(conn)
    current_ver = get_current_version(conn)

    if current_ver > MAX_SUPPORTED_VERSION:
        raise IncompatibleSchemaVersionError(
            f"Database schema version {current_ver} exceeds maximum supported version {MAX_SUPPORTED_VERSION}."
        )

    target = target_version if target_version is not None else MAX_SUPPORTED_VERSION
    if target > MAX_SUPPORTED_VERSION:
        raise IncompatibleSchemaVersionError(
            f"Target version {target} exceeds maximum supported version {MAX_SUPPORTED_VERSION}."
        )

    applied_now: list[int] = []

    for migration in MIGRATIONS:
        if migration.version <= current_ver:
            continue
        if migration.version > target:
            break

        now_iso = datetime.now(timezone.utc).isoformat()
        in_tx = conn.in_transaction
        if not in_tx:
            conn.execute("BEGIN IMMEDIATE")
        try:
            migration.up(conn)
            conn.execute(
                "INSERT INTO schema_migrations (version, applied_at, description) VALUES (?, ?, ?)",
                (migration.version, now_iso, migration.description),
            )
            if not in_tx:
                conn.execute("COMMIT")
            applied_now.append(migration.version)
        except Exception as exc:
            if not in_tx:
                try:
                    conn.execute("ROLLBACK")
                except Exception:
                    pass
            raise MigrationError(
                f"Failed to apply migration {migration.version} ({migration.description}): {exc}"
            ) from exc

    return applied_now


def rollback_migration(
    conn: sqlite3.Connection,
    target_version: int,
    allow_data_loss: bool = False,
) -> list[int]:
    """Roll back migrations down to target_version.

    If any affected table contains records and allow_data_loss is False,
    raises DestructiveMigrationError before executing any destructive operations.

    Returns list of rolled back versions in reverse order.
    """
    ensure_migrations_table(conn)
    current_ver = get_current_version(conn)

    if target_version >= current_ver:
        return []

    # 1. Safety check: inspect all affected tables for existing data
    if not allow_data_loss:
        for migration in reversed(MIGRATIONS):
            if migration.version > current_ver:
                continue
            if migration.version <= target_version:
                break
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

    # 2. Execute rollback in reverse order
    rolled_back: list[int] = []
    for migration in reversed(MIGRATIONS):
        if migration.version > current_ver:
            continue
        if migration.version <= target_version:
            break

        in_tx = conn.in_transaction
        if not in_tx:
            conn.execute("BEGIN IMMEDIATE")
        try:
            migration.down(conn)
            conn.execute(
                "DELETE FROM schema_migrations WHERE version = ?",
                (migration.version,),
            )
            if not in_tx:
                conn.execute("COMMIT")
            rolled_back.append(migration.version)
        except Exception as exc:
            if not in_tx:
                try:
                    conn.execute("ROLLBACK")
                except Exception:
                    pass
            raise MigrationError(
                f"Failed to rollback migration {migration.version} ({migration.description}): {exc}"
            ) from exc

    return rolled_back
