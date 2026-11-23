"""Typed repository implementations for SQLite persistence layer."""

from __future__ import annotations

from typing import Any, Sequence
import sqlite3

from sniper.core.currency import CurrencyConfig
from sniper.storage.database import Database
from sniper.storage.models import (
    CurrencyRecord,
    InventoryItemRecord,
    InventorySnapshotRecord,
    ItemRecord,
    ListingRecord,
    OrderbookSnapshotRecord,
    PortfolioSummary,
    PortfolioTransactionRecord,
    current_utc_iso,
)


class ItemRepository:
    """Repository for CS2 item catalog records and identity provenance."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def save(self, item: ItemRecord) -> ItemRecord:
        """Insert or update an exact item identity record.

        Preserves original item_id and first_seen_at if record already exists.
        """
        now = current_utc_iso()
        first_seen = item.first_seen_at or now
        updated = item.updated_at or now

        with self._db.transaction() as cur:
            cur.execute("""
                INSERT INTO items (
                    appid, market_hash_name, base_name, exterior,
                    is_stattrak, is_souvenir, group_id,
                    item_nameid, item_nameid_source, item_nameid_revision,
                    classid, classid_source, classid_revision,
                    first_seen_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(market_hash_name) DO UPDATE SET
                    base_name = excluded.base_name,
                    exterior = excluded.exterior,
                    is_stattrak = excluded.is_stattrak,
                    is_souvenir = excluded.is_souvenir,
                    group_id = coalesce(excluded.group_id, items.group_id),
                    item_nameid = CASE
                        WHEN excluded.item_nameid IS NOT NULL THEN excluded.item_nameid
                        ELSE items.item_nameid
                    END,
                    item_nameid_source = CASE
                        WHEN excluded.item_nameid IS NOT NULL THEN excluded.item_nameid_source
                        ELSE items.item_nameid_source
                    END,
                    item_nameid_revision = CASE
                        WHEN excluded.item_nameid IS NOT NULL THEN excluded.item_nameid_revision
                        ELSE items.item_nameid_revision
                    END,
                    classid = CASE
                        WHEN excluded.classid IS NOT NULL THEN excluded.classid
                        ELSE items.classid
                    END,
                    classid_source = CASE
                        WHEN excluded.classid IS NOT NULL THEN excluded.classid_source
                        ELSE items.classid_source
                    END,
                    classid_revision = CASE
                        WHEN excluded.classid IS NOT NULL THEN excluded.classid_revision
                        ELSE items.classid_revision
                    END,
                    updated_at = excluded.updated_at
            """, (
                item.appid,
                item.market_hash_name,
                item.base_name,
                item.exterior,
                1 if item.is_stattrak else 0,
                1 if item.is_souvenir else 0,
                item.group_id,
                item.item_nameid,
                item.item_nameid_source,
                item.item_nameid_revision,
                item.classid,
                item.classid_source,
                item.classid_revision,
                first_seen,
                updated,
            ))

            cur.execute("SELECT * FROM items WHERE market_hash_name = ?", (item.market_hash_name,))
            row = cur.fetchone()
            if row is None:
                raise RuntimeError(f"Failed to retrieve item for market_hash_name '{item.market_hash_name}' after upsert.")

            return self._row_to_record(row)

    def get_by_id(self, item_id: int) -> ItemRecord | None:
        """Fetch item by primary key."""
        row = self._db.fetch_one("SELECT * FROM items WHERE item_id = ?", (item_id,))
        if row is None:
            return None
        return self._row_to_record(row)

    def get_by_market_hash_name(self, market_hash_name: str) -> ItemRecord | None:
        """Fetch item by exact market hash name."""
        row = self._db.fetch_one("SELECT * FROM items WHERE market_hash_name = ?", (market_hash_name,))
        if row is None:
            return None
        return self._row_to_record(row)

    def list_items(
        self,
        base_name: str | None = None,
        exterior: str | None = None,
        is_stattrak: bool | None = None,
        is_souvenir: bool | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ItemRecord]:
        """List items with optional criteria filters."""
        query = "SELECT * FROM items WHERE 1=1"
        params: list[Any] = []

        if base_name is not None:
            query += " AND base_name = ?"
            params.append(base_name)
        if exterior is not None:
            query += " AND exterior = ?"
            params.append(exterior)
        if is_stattrak is not None:
            query += " AND is_stattrak = ?"
            params.append(1 if is_stattrak else 0)
        if is_souvenir is not None:
            query += " AND is_souvenir = ?"
            params.append(1 if is_souvenir else 0)

        query += " ORDER BY item_id ASC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = self._db.fetch_all(query, params)
        return [self._row_to_record(r) for r in rows]

    def count(self) -> int:
        """Return total items in catalog."""
        row = self._db.fetch_one("SELECT COUNT(*) AS total FROM items")
        return int(row["total"]) if row else 0

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> ItemRecord:
        return ItemRecord(
            item_id=row["item_id"],
            appid=row["appid"],
            market_hash_name=row["market_hash_name"],
            base_name=row["base_name"],
            exterior=row["exterior"],
            is_stattrak=bool(row["is_stattrak"]),
            is_souvenir=bool(row["is_souvenir"]),
            group_id=row["group_id"],
            item_nameid=row["item_nameid"],
            item_nameid_source=row["item_nameid_source"],
            item_nameid_revision=row["item_nameid_revision"],
            classid=row["classid"],
            classid_source=row["classid_source"],
            classid_revision=row["classid_revision"],
            first_seen_at=row["first_seen_at"],
            updated_at=row["updated_at"],
        )


class CurrencyRepository:
    """Repository for currency configuration and verification records."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def save(self, currency: CurrencyRecord) -> CurrencyRecord:
        """Save or update currency metadata and rules.

        Preserves UNKNOWN listing minimums as None (SQL NULL).
        """
        now = current_utc_iso()
        updated = currency.updated_at or now

        with self._db.transaction() as cur:
            cur.execute("""
                INSERT INTO currency_configs (
                    currency_id, code, symbol, minor_unit_precision, country,
                    min_listing_price_minor, price_step_minor,
                    min_fee_steam_minor, min_fee_publisher_minor,
                    is_metadata_verified, is_fee_verified,
                    is_listing_min_verified, is_price_step_verified,
                    notes, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(currency_id) DO UPDATE SET
                    code = excluded.code,
                    symbol = excluded.symbol,
                    minor_unit_precision = excluded.minor_unit_precision,
                    country = excluded.country,
                    min_listing_price_minor = excluded.min_listing_price_minor,
                    price_step_minor = excluded.price_step_minor,
                    min_fee_steam_minor = excluded.min_fee_steam_minor,
                    min_fee_publisher_minor = excluded.min_fee_publisher_minor,
                    is_metadata_verified = excluded.is_metadata_verified,
                    is_fee_verified = excluded.is_fee_verified,
                    is_listing_min_verified = excluded.is_listing_min_verified,
                    is_price_step_verified = excluded.is_price_step_verified,
                    notes = excluded.notes,
                    updated_at = excluded.updated_at
            """, (
                currency.currency_id,
                currency.code,
                currency.symbol,
                currency.minor_unit_precision,
                currency.country,
                currency.min_listing_price_minor,
                currency.price_step_minor,
                currency.min_fee_steam_minor,
                currency.min_fee_publisher_minor,
                1 if currency.is_metadata_verified else 0,
                1 if currency.is_fee_verified else 0,
                1 if currency.is_listing_min_verified else 0,
                1 if currency.is_price_step_verified else 0,
                currency.notes,
                updated,
            ))

        return currency.model_copy(update={"updated_at": updated})

    def get_by_id(self, currency_id: int) -> CurrencyRecord | None:
        """Fetch currency configuration by ID."""
        row = self._db.fetch_one("SELECT * FROM currency_configs WHERE currency_id = ?", (currency_id,))
        if row is None:
            return None
        return self._row_to_record(row)

    def get_by_code(self, code: str) -> CurrencyRecord | None:
        """Fetch currency configuration by currency code (e.g. 'USD', 'EUR')."""
        row = self._db.fetch_one("SELECT * FROM currency_configs WHERE code = ?", (code,))
        if row is None:
            return None
        return self._row_to_record(row)

    def list_all(self) -> list[CurrencyRecord]:
        """List all configured currencies."""
        rows = self._db.fetch_all("SELECT * FROM currency_configs ORDER BY currency_id ASC")
        return [self._row_to_record(r) for r in rows]

    def seed_default_currencies(self) -> int:
        """Seed known currencies from core CurrencyConfig domain definitions."""
        default_configs = [
            CurrencyConfig.get_default_usd(),
            CurrencyConfig.get_eur(),
            CurrencyConfig.get_jpy(),
        ]
        # Also include any in KNOWN_CURRENCIES if registered
        for cfg in CurrencyConfig.KNOWN_CURRENCIES.values():
            if not any(c.currency_id == cfg.currency_id for c in default_configs):
                default_configs.append(cfg)

        count = 0
        for config in default_configs:
            rec = CurrencyRecord.from_currency_config(config)
            self.save(rec)
            count += 1
        return count

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> CurrencyRecord:
        return CurrencyRecord(
            currency_id=row["currency_id"],
            code=row["code"],
            symbol=row["symbol"],
            minor_unit_precision=row["minor_unit_precision"],
            country=row["country"],
            min_listing_price_minor=row["min_listing_price_minor"],
            price_step_minor=row["price_step_minor"],
            min_fee_steam_minor=row["min_fee_steam_minor"],
            min_fee_publisher_minor=row["min_fee_publisher_minor"],
            is_metadata_verified=bool(row["is_metadata_verified"]),
            is_fee_verified=bool(row["is_fee_verified"]),
            is_listing_min_verified=bool(row["is_listing_min_verified"]),
            is_price_step_verified=bool(row["is_price_step_verified"]),
            notes=row["notes"] or "",
            updated_at=row["updated_at"],
        )


class ListingRepository:
    """Repository for immutable listing observations."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def insert_observation(self, listing: ListingRecord) -> ListingRecord:
        """Insert an immutable listing observation."""
        now = listing.observed_at or current_utc_iso()
        with self._db.transaction() as cur:
            cur.execute("""
                INSERT INTO listings (
                    steam_listing_id, item_id, price_minor, fee_minor,
                    steam_fee_minor, publisher_fee_minor, currency_id,
                    observed_at, source, status, asset_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                listing.steam_listing_id,
                listing.item_id,
                listing.price_minor,
                listing.fee_minor,
                listing.steam_fee_minor,
                listing.publisher_fee_minor,
                listing.currency_id,
                now,
                listing.source,
                listing.status,
                listing.asset_id,
            ))
            listing_id = cur.lastrowid
            if listing_id is None:
                raise RuntimeError("Failed to obtain lastrowid for inserted listing.")

        return listing.model_copy(update={
            "listing_id": listing_id,
            "observed_at": now,
        })

    def insert_batch(self, listings: Sequence[ListingRecord]) -> int:
        """Batch insert multiple immutable listing observations in a single transaction."""
        if not listings:
            return 0
        now = current_utc_iso()
        with self._db.transaction() as cur:
            for l in listings:
                cur.execute("""
                    INSERT INTO listings (
                        steam_listing_id, item_id, price_minor, fee_minor,
                        steam_fee_minor, publisher_fee_minor, currency_id,
                        observed_at, source, status, asset_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    l.steam_listing_id,
                    l.item_id,
                    l.price_minor,
                    l.fee_minor,
                    l.steam_fee_minor,
                    l.publisher_fee_minor,
                    l.currency_id,
                    l.observed_at or now,
                    l.source,
                    l.status,
                    l.asset_id,
                ))
        return len(listings)

    def get_history(
        self,
        item_id: int,
        currency_id: int | None = None,
        limit: int = 100,
    ) -> list[ListingRecord]:
        """Retrieve listing observations ordered from newest to oldest."""
        query = "SELECT * FROM listings WHERE item_id = ?"
        params: list[Any] = [item_id]

        if currency_id is not None:
            query += " AND currency_id = ?"
            params.append(currency_id)

        query += " ORDER BY observed_at DESC, listing_id DESC LIMIT ?"
        params.append(limit)

        rows = self._db.fetch_all(query, params)
        return [self._row_to_record(r) for r in rows]

    def get_latest(self, item_id: int, currency_id: int) -> ListingRecord | None:
        """Retrieve the most recent listing observation for an item and currency."""
        row = self._db.fetch_one(
            "SELECT * FROM listings WHERE item_id = ? AND currency_id = ? ORDER BY observed_at DESC, listing_id DESC LIMIT 1",
            (item_id, currency_id),
        )
        if row is None:
            return None
        return self._row_to_record(row)

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> ListingRecord:
        return ListingRecord(
            listing_id=row["listing_id"],
            steam_listing_id=row["steam_listing_id"],
            item_id=row["item_id"],
            price_minor=row["price_minor"],
            fee_minor=row["fee_minor"],
            steam_fee_minor=row["steam_fee_minor"],
            publisher_fee_minor=row["publisher_fee_minor"],
            currency_id=row["currency_id"],
            observed_at=row["observed_at"],
            source=row["source"],
            status=row["status"],
            asset_id=row["asset_id"],
        )


class OrderbookRepository:
    """Repository for immutable orderbook depth and price snapshots."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def insert_snapshot(self, snapshot: OrderbookSnapshotRecord) -> OrderbookSnapshotRecord:
        """Insert an immutable orderbook snapshot."""
        now = snapshot.observed_at or current_utc_iso()
        with self._db.transaction() as cur:
            cur.execute("""
                INSERT INTO orderbook_snapshots (
                    item_id, currency_id, highest_buy_order_minor, lowest_sell_order_minor,
                    buy_order_count, sell_order_count, top_level_buy_quantity,
                    cumulative_buy_quantity, buy_graph_json, sell_graph_json,
                    observed_at, source_payload_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                snapshot.item_id,
                snapshot.currency_id,
                snapshot.highest_buy_order_minor,
                snapshot.lowest_sell_order_minor,
                snapshot.buy_order_count,
                snapshot.sell_order_count,
                snapshot.top_level_buy_quantity,
                snapshot.cumulative_buy_quantity,
                snapshot.buy_graph_json,
                snapshot.sell_graph_json,
                now,
                snapshot.source_payload_version,
            ))
            snapshot_id = cur.lastrowid
            if snapshot_id is None:
                raise RuntimeError("Failed to obtain lastrowid for inserted orderbook snapshot.")

        return snapshot.model_copy(update={
            "snapshot_id": snapshot_id,
            "observed_at": now,
        })

    def get_history(
        self,
        item_id: int,
        currency_id: int | None = None,
        limit: int = 100,
    ) -> list[OrderbookSnapshotRecord]:
        """Retrieve orderbook snapshots ordered from newest to oldest."""
        query = "SELECT * FROM orderbook_snapshots WHERE item_id = ?"
        params: list[Any] = [item_id]

        if currency_id is not None:
            query += " AND currency_id = ?"
            params.append(currency_id)

        query += " ORDER BY observed_at DESC, snapshot_id DESC LIMIT ?"
        params.append(limit)

        rows = self._db.fetch_all(query, params)
        return [self._row_to_record(r) for r in rows]

    def get_latest(self, item_id: int, currency_id: int) -> OrderbookSnapshotRecord | None:
        """Retrieve the most recent orderbook snapshot for an item and currency."""
        row = self._db.fetch_one(
            "SELECT * FROM orderbook_snapshots WHERE item_id = ? AND currency_id = ? ORDER BY observed_at DESC, snapshot_id DESC LIMIT 1",
            (item_id, currency_id),
        )
        if row is None:
            return None
        return self._row_to_record(row)

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> OrderbookSnapshotRecord:
        return OrderbookSnapshotRecord(
            snapshot_id=row["snapshot_id"],
            item_id=row["item_id"],
            currency_id=row["currency_id"],
            highest_buy_order_minor=row["highest_buy_order_minor"],
            lowest_sell_order_minor=row["lowest_sell_order_minor"],
            buy_order_count=row["buy_order_count"],
            sell_order_count=row["sell_order_count"],
            top_level_buy_quantity=row["top_level_buy_quantity"],
            cumulative_buy_quantity=row["cumulative_buy_quantity"],
            buy_graph_json=row["buy_graph_json"],
            sell_graph_json=row["sell_graph_json"],
            observed_at=row["observed_at"],
            source_payload_version=row["source_payload_version"],
        )


class InventoryRepository:
    """Repository for public inventory snapshots and item observations."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def import_snapshot(
        self,
        items: Sequence[InventoryItemRecord],
        owner_id: str | None = None,
        observed_at: str | None = None,
        source: str = "public_inventory",
    ) -> tuple[InventorySnapshotRecord, list[InventoryItemRecord]]:
        """Atomically import an inventory snapshot and all its items in a single transaction."""
        now = observed_at or current_utc_iso()
        inserted_items: list[InventoryItemRecord] = []

        with self._db.transaction() as cur:
            # 1. Insert snapshot header
            cur.execute("""
                INSERT INTO inventory_snapshots (owner_id, total_count, observed_at, source)
                VALUES (?, ?, ?, ?)
            """, (owner_id, len(items), now, source))
            snapshot_id = cur.lastrowid
            if snapshot_id is None:
                raise RuntimeError("Failed to obtain lastrowid for inventory snapshot.")

            # 2. Insert items with optional item_id linking
            for item in items:
                # Look up item_id if not explicitly provided
                linked_item_id = item.item_id
                if linked_item_id is None:
                    cur.execute("SELECT item_id FROM items WHERE market_hash_name = ?", (item.market_hash_name,))
                    match = cur.fetchone()
                    if match:
                        linked_item_id = int(match["item_id"])

                cur.execute("""
                    INSERT INTO inventory_items (
                        snapshot_id, asset_id, class_id, instance_id,
                        market_hash_name, amount, is_marketable, is_tradable,
                        market_restriction_days, item_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    snapshot_id,
                    item.asset_id,
                    item.class_id,
                    item.instance_id,
                    item.market_hash_name,
                    item.amount,
                    1 if item.is_marketable else 0,
                    1 if item.is_tradable else 0,
                    item.market_restriction_days,
                    linked_item_id,
                ))
                inv_id = cur.lastrowid
                inserted_items.append(item.model_copy(update={
                    "inventory_item_id": inv_id,
                    "snapshot_id": snapshot_id,
                    "item_id": linked_item_id,
                }))

        snapshot = InventorySnapshotRecord(
            snapshot_id=snapshot_id,
            owner_id=owner_id,
            total_count=len(items),
            observed_at=now,
            source=source,
        )
        return snapshot, inserted_items

    def get_snapshot_by_id(self, snapshot_id: int) -> InventorySnapshotRecord | None:
        """Fetch inventory snapshot header by ID."""
        row = self._db.fetch_one("SELECT * FROM inventory_snapshots WHERE snapshot_id = ?", (snapshot_id,))
        if row is None:
            return None
        return self._row_to_snapshot(row)

    def get_latest_snapshot(self, owner_id: str | None = None) -> InventorySnapshotRecord | None:
        """Fetch the most recent inventory snapshot, optionally filtered by owner."""
        if owner_id is not None:
            row = self._db.fetch_one(
                "SELECT * FROM inventory_snapshots WHERE owner_id = ? ORDER BY observed_at DESC, snapshot_id DESC LIMIT 1",
                (owner_id,),
            )
        else:
            row = self._db.fetch_one(
                "SELECT * FROM inventory_snapshots ORDER BY observed_at DESC, snapshot_id DESC LIMIT 1"
            )
        if row is None:
            return None
        return self._row_to_snapshot(row)

    def get_items_for_snapshot(self, snapshot_id: int) -> list[InventoryItemRecord]:
        """Fetch all items belonging to a given snapshot."""
        rows = self._db.fetch_all(
            "SELECT * FROM inventory_items WHERE snapshot_id = ? ORDER BY inventory_item_id ASC",
            (snapshot_id,),
        )
        return [self._row_to_item(r) for r in rows]

    def get_item_history(self, market_hash_name: str) -> list[InventoryItemRecord]:
        """Fetch all historical inventory item observations for a given market hash name."""
        rows = self._db.fetch_all(
            "SELECT * FROM inventory_items WHERE market_hash_name = ? ORDER BY inventory_item_id DESC",
            (market_hash_name,),
        )
        return [self._row_to_item(r) for r in rows]

    @staticmethod
    def _row_to_snapshot(row: sqlite3.Row) -> InventorySnapshotRecord:
        return InventorySnapshotRecord(
            snapshot_id=row["snapshot_id"],
            owner_id=row["owner_id"],
            total_count=row["total_count"],
            observed_at=row["observed_at"],
            source=row["source"],
        )

    @staticmethod
    def _row_to_item(row: sqlite3.Row) -> InventoryItemRecord:
        return InventoryItemRecord(
            inventory_item_id=row["inventory_item_id"],
            snapshot_id=row["snapshot_id"],
            asset_id=row["asset_id"],
            class_id=row["class_id"],
            instance_id=row["instance_id"],
            market_hash_name=row["market_hash_name"],
            amount=row["amount"],
            is_marketable=bool(row["is_marketable"]),
            is_tradable=bool(row["is_tradable"]),
            market_restriction_days=row["market_restriction_days"],
            item_id=row["item_id"],
        )


class PortfolioRepository:
    """Repository for manual portfolio transactions and exact integer cost accounting."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def record_transaction(self, tx: PortfolioTransactionRecord) -> PortfolioTransactionRecord:
        """Record a manual BUY or SELL transaction."""
        now = tx.timestamp or current_utc_iso()
        with self._db.transaction() as cur:
            cur.execute("""
                INSERT INTO portfolio_transactions (
                    item_id, transaction_type, quantity, unit_price_minor,
                    total_fees_minor, currency_id, timestamp,
                    cost_basis_metadata, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                tx.item_id,
                tx.transaction_type,
                tx.quantity,
                tx.unit_price_minor,
                tx.total_fees_minor,
                tx.currency_id,
                now,
                tx.cost_basis_metadata,
                tx.notes,
            ))
            tx_id = cur.lastrowid
            if tx_id is None:
                raise RuntimeError("Failed to obtain lastrowid for portfolio transaction.")

        return tx.model_copy(update={
            "transaction_id": tx_id,
            "timestamp": now,
        })

    def get_transactions(
        self,
        item_id: int | None = None,
        currency_id: int | None = None,
    ) -> list[PortfolioTransactionRecord]:
        """Fetch transactions with optional item_id and currency_id filters."""
        query = "SELECT * FROM portfolio_transactions WHERE 1=1"
        params: list[Any] = []

        if item_id is not None:
            query += " AND item_id = ?"
            params.append(item_id)
        if currency_id is not None:
            query += " AND currency_id = ?"
            params.append(currency_id)

        query += " ORDER BY timestamp ASC, transaction_id ASC"
        rows = self._db.fetch_all(query, params)
        return [self._row_to_record(r) for r in rows]

    def calculate_acquisition_cost(
        self,
        item_id: int | None = None,
        currency_id: int = 1,
    ) -> int:
        """Calculate total acquisition cost in minor units for BUY transactions using exact integer arithmetic.

        Total Cost = sum(quantity * unit_price_minor + total_fees_minor) for all BUYs.
        """
        query = "SELECT quantity, unit_price_minor, total_fees_minor FROM portfolio_transactions WHERE transaction_type = 'BUY' AND currency_id = ?"
        params: list[Any] = [currency_id]

        if item_id is not None:
            query += " AND item_id = ?"
            params.append(item_id)

        rows = self._db.fetch_all(query, params)
        total_minor = 0
        for r in rows:
            qty = int(r["quantity"])
            price = int(r["unit_price_minor"])
            fee = int(r["total_fees_minor"])
            total_minor += (qty * price) + fee

        return total_minor

    def calculate_portfolio_summary(
        self,
        currency_id: int,
        item_id: int | None = None,
    ) -> PortfolioSummary:
        """Calculate comprehensive portfolio accounting summary using exact integer arithmetic only."""
        txs = self.get_transactions(item_id=item_id, currency_id=currency_id)

        total_bought = 0
        total_sold = 0
        total_acquisition_cost = 0
        total_fees = 0
        total_realized_proceeds = 0

        for tx in txs:
            qty = tx.quantity
            price = tx.unit_price_minor
            fee = tx.total_fees_minor

            total_fees += fee

            if tx.transaction_type == "BUY":
                total_bought += qty
                total_acquisition_cost += (qty * price) + fee
            elif tx.transaction_type == "SELL":
                total_sold += qty
                total_realized_proceeds += (qty * price) - fee

        net_qty = total_bought - total_sold

        return PortfolioSummary(
            item_id=item_id,
            currency_id=currency_id,
            total_bought_quantity=total_bought,
            total_sold_quantity=total_sold,
            net_quantity=net_qty,
            total_acquisition_cost_minor=total_acquisition_cost,
            total_fees_paid_minor=total_fees,
            total_realized_proceeds_minor=total_realized_proceeds,
        )

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> PortfolioTransactionRecord:
        return PortfolioTransactionRecord(
            transaction_id=row["transaction_id"],
            item_id=row["item_id"],
            transaction_type=row["transaction_type"],
            quantity=row["quantity"],
            unit_price_minor=row["unit_price_minor"],
            total_fees_minor=row["total_fees_minor"],
            currency_id=row["currency_id"],
            timestamp=row["timestamp"],
            cost_basis_metadata=row["cost_basis_metadata"],
            notes=row["notes"],
        )
