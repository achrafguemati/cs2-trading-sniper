"""Typed repository implementations for SQLite persistence layer."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
import math
import sqlite3
from typing import Any, Sequence

from sniper.core.currency import CurrencyConfig
from sniper.core.identity import (
    VERIFIED_MAPPING_REPOSITORY,
    VERIFIED_MAPPING_REVISION,
)
from sniper.storage.database import Database
from sniper.storage.models import (
    AlertEventRecord,
    CurrencyRecord,
    InventoryItemRecord,
    InventorySnapshotRecord,
    ItemRecord,
    ListingRecord,
    NotificationDeliveryRecord,
    OrderbookSnapshotRecord,
    PortfolioSummary,
    PortfolioTransactionRecord,
    current_utc_iso,
)

logger = logging.getLogger(__name__)


def normalize_item_nameid_provenance(
    item_nameid: int | None,
    source: str | None,
    revision: str | None,
) -> tuple[int | None, str | None, str | None]:
    """Validate and normalize incoming item_nameid provenance group.

    Fails closed: returns (None, None, None) unless:
    - item_nameid is a positive integer (> 0).
    - source exactly equals VERIFIED_MAPPING_REPOSITORY.
    - revision exactly equals VERIFIED_MAPPING_REVISION.

    Never persists a partially trusted group.
    """
    if item_nameid is None or source is None or revision is None:
        return None, None, None

    if type(item_nameid) is not int or item_nameid <= 0:
        return None, None, None

    if source != VERIFIED_MAPPING_REPOSITORY or revision != VERIFIED_MAPPING_REVISION:
        return None, None, None

    return item_nameid, VERIFIED_MAPPING_REPOSITORY, VERIFIED_MAPPING_REVISION


def normalize_classid_provenance(
    classid: str | None,
    source: str | None,
    revision: str | None,
) -> tuple[str | None, str | None, str | None]:
    """Validate and normalize incoming classid provenance group.

    Fails closed: returns (None, None, None) unless:
    - classid is a non-empty string.
    - source exactly equals 'steam_ssr_bucket'.
    - revision is None.

    Never persists a partially trusted group.
    """
    if classid is None or source is None:
        return None, None, None

    clean_cid = str(classid).strip()
    if not clean_cid:
        return None, None, None

    if source != "steam_ssr_bucket":
        return None, None, None

    if revision is not None:
        return None, None, None

    return clean_cid, "steam_ssr_bucket", None


class ItemRepository:
    """Repository for CS2 item catalog records and identity provenance."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def save(self, item: ItemRecord) -> ItemRecord:
        """Insert or update an exact item identity record.

        Preserves original item_id and first_seen_at if record already exists.
        Validates incoming provenance on both direct INSERT and UPSERT paths,
        normalizing unverified or incomplete provenance groups to NULL.
        Protects trusted provenance against downgrade or conflicting lower-trust updates.
        """
        if not item.market_hash_name or not item.market_hash_name.strip():
            raise ValueError("Cannot persist an item record with an empty or blank market_hash_name.")
        if not item.base_name or not item.base_name.strip():
            raise ValueError("Cannot persist an item record with an empty or blank base_name.")

        now = current_utc_iso()
        first_seen = item.first_seen_at or now
        updated = item.updated_at or now

        # Validate incoming provenance groups before INSERT/UPSERT; normalize to NULL if unverified
        clean_nameid, clean_nameid_src, clean_nameid_rev = normalize_item_nameid_provenance(
            item.item_nameid,
            item.item_nameid_source,
            item.item_nameid_revision,
        )
        if item.item_nameid is not None and clean_nameid is None:
            logger.info(
                "Normalized unverified item_nameid %s (source=%s, rev=%s) for '%s' to NULL before persistence.",
                item.item_nameid,
                item.item_nameid_source,
                item.item_nameid_revision,
                item.market_hash_name,
            )

        clean_cid, clean_cid_src, clean_cid_rev = normalize_classid_provenance(
            item.classid,
            item.classid_source,
            item.classid_revision,
        )
        if item.classid is not None and clean_cid is None:
            logger.info(
                "Normalized unverified classid %s (source=%s, rev=%s) for '%s' to NULL before persistence.",
                item.classid,
                item.classid_source,
                item.classid_revision,
                item.market_hash_name,
            )

        with self._db.transaction() as cur:
            # Check existing item to detect and log downgrade or conflicting attempts
            cur.execute(
                "SELECT item_nameid, item_nameid_source, item_nameid_revision, "
                "classid, classid_source, classid_revision "
                "FROM items WHERE market_hash_name = ?",
                (item.market_hash_name,),
            )
            existing_row = cur.fetchone()
            if existing_row is not None:
                if (
                    existing_row["item_nameid_source"] == VERIFIED_MAPPING_REPOSITORY
                    and existing_row["item_nameid_revision"] == VERIFIED_MAPPING_REVISION
                ):
                    candidate_id = clean_nameid if clean_nameid is not None else item.item_nameid
                    if candidate_id is not None and candidate_id != existing_row["item_nameid"]:
                        logger.warning(
                            "Rejected attempt to overwrite trusted item_nameid %s for '%s' "
                            "(source=%s, rev=%s) with conflicting item_nameid %s (source=%s, rev=%s). "
                            "Preserved existing trusted value.",
                            existing_row["item_nameid"],
                            item.market_hash_name,
                            existing_row["item_nameid_source"],
                            existing_row["item_nameid_revision"],
                            item.item_nameid,
                            item.item_nameid_source,
                            item.item_nameid_revision,
                        )
                if (
                    existing_row["classid_source"] == "steam_ssr_bucket"
                    and existing_row["classid_revision"] is None
                ):
                    candidate_cid = clean_cid if clean_cid is not None else item.classid
                    if candidate_cid is not None and candidate_cid != existing_row["classid"]:
                        logger.warning(
                            "Rejected attempt to overwrite trusted SSR classid %s for '%s' "
                            "with classid %s (source=%s). Preserved existing trusted SSR classid.",
                            existing_row["classid"],
                            item.market_hash_name,
                            item.classid,
                            item.classid_source,
                        )

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
                        -- Protect trusted mapping provenance from synthetic, legacy, unknown, or conflicting downgrade
                        WHEN items.item_nameid_source = 'https://github.com/somespecialone/steam-market-ids'
                             AND items.item_nameid_revision = 'caac34bd8a28578e800501da5cc9f16c7cb3fa67'
                        THEN items.item_nameid
                        WHEN excluded.item_nameid IS NOT NULL THEN excluded.item_nameid
                        ELSE items.item_nameid
                    END,
                    item_nameid_source = CASE
                        WHEN items.item_nameid_source = 'https://github.com/somespecialone/steam-market-ids'
                             AND items.item_nameid_revision = 'caac34bd8a28578e800501da5cc9f16c7cb3fa67'
                        THEN items.item_nameid_source
                        WHEN excluded.item_nameid IS NOT NULL THEN excluded.item_nameid_source
                        ELSE items.item_nameid_source
                    END,
                    item_nameid_revision = CASE
                        WHEN items.item_nameid_source = 'https://github.com/somespecialone/steam-market-ids'
                             AND items.item_nameid_revision = 'caac34bd8a28578e800501da5cc9f16c7cb3fa67'
                        THEN items.item_nameid_revision
                        WHEN excluded.item_nameid IS NOT NULL THEN excluded.item_nameid_revision
                        ELSE items.item_nameid_revision
                    END,
                    classid = CASE
                        -- Protect verified SSR classid from synthetic, legacy, unknown, or missing downgrade
                        WHEN items.classid_source = 'steam_ssr_bucket'
                             AND items.classid_revision IS NULL
                        THEN items.classid
                        WHEN excluded.classid IS NOT NULL THEN excluded.classid
                        ELSE items.classid
                    END,
                    classid_source = CASE
                        WHEN items.classid_source = 'steam_ssr_bucket'
                             AND items.classid_revision IS NULL
                        THEN items.classid_source
                        WHEN excluded.classid IS NOT NULL THEN excluded.classid_source
                        ELSE items.classid_source
                    END,
                    classid_revision = CASE
                        WHEN items.classid_source = 'steam_ssr_bucket'
                             AND items.classid_revision IS NULL
                        THEN items.classid_revision
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
                clean_nameid,
                clean_nameid_src,
                clean_nameid_rev,
                clean_cid,
                clean_cid_src,
                clean_cid_rev,
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

    MAX_CLOCK_SKEW_SECONDS: float = 5.0

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

    def get_historical_buy_orders(
        self,
        item_id: int,
        currency_id: int,
        limit: int = 100,
        max_age_seconds: int | None = None,
        exclude_snapshot_id: int | None = None,
        now_dt: datetime | None = None,
    ) -> list[int]:
        """Retrieve historical highest_buy_order_minor values for an item and currency.

        Ordered from newest to oldest, optionally excluding a specific snapshot (e.g. current)
        and filtering out observations older than max_age_seconds.

        Timestamp validation and clock skew policy:
        - Malformed or missing timestamps are excluded (fail-closed).
        - Observations with timestamps beyond MAX_CLOCK_SKEW_SECONDS (5.0s) in the future are rejected.
        - Historical records are never deleted; invalid records are simply excluded from calculations.
        """
        query = (
            "SELECT snapshot_id, highest_buy_order_minor, observed_at "
            "FROM orderbook_snapshots "
            "WHERE item_id = ? AND currency_id = ? AND highest_buy_order_minor IS NOT NULL"
        )
        params: list[Any] = [item_id, currency_id]
        if exclude_snapshot_id is not None:
            query += " AND snapshot_id != ?"
            params.append(exclude_snapshot_id)

        query += " ORDER BY observed_at DESC, snapshot_id DESC LIMIT ?"
        params.append(limit)

        rows = self._db.fetch_all(query, params)
        if not rows:
            return []

        if now_dt is None:
            now_dt = datetime.now(timezone.utc)
        elif now_dt.tzinfo is None:
            now_dt = now_dt.replace(tzinfo=timezone.utc)

        valid_bids: list[int] = []
        for r in rows:
            obs_str = r["observed_at"]
            if not obs_str or not isinstance(obs_str, str) or not obs_str.strip():
                # Missing or empty timestamp: exclude
                continue

            obs_clean = obs_str.strip()
            if obs_clean.endswith("Z"):
                obs_clean = obs_clean[:-1] + "+00:00"

            try:
                obs_dt = datetime.fromisoformat(obs_clean)
            except (ValueError, TypeError):
                # Malformed timestamp: exclude
                continue

            if obs_dt.tzinfo is None:
                obs_dt = obs_dt.replace(tzinfo=timezone.utc)

            age = (now_dt - obs_dt).total_seconds()

            # Reject future timestamps beyond clock skew tolerance:
            if age < -self.MAX_CLOCK_SKEW_SECONDS:
                continue

            # Reject observations older than max_age_seconds if specified:
            if max_age_seconds is not None and age > max_age_seconds:
                continue

            valid_bids.append(int(r["highest_buy_order_minor"]))

        return valid_bids

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


class AlertRepository:
    """Repository for alert events and multi-channel notification deliveries."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def record_alert(self, alert: AlertEventRecord) -> AlertEventRecord:
        """Insert a newly detected alert event."""
        now = alert.created_at or current_utc_iso()
        with self._db.transaction() as cur:
            cur.execute("""
                INSERT INTO alert_events (
                    item_id, currency_id, alert_type, alert_fingerprint,
                    highest_buy_order_minor, lowest_sell_order_minor,
                    top_level_buy_quantity, cumulative_buy_quantity,
                    modeled_steam_fee_minor, modeled_publisher_fee_minor,
                    modeled_seller_receives_minor, historical_baseline_minor,
                    bid_multiple, opportunity_score, confidence_score,
                    warning_tags, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                alert.item_id,
                alert.currency_id,
                alert.alert_type,
                alert.alert_fingerprint,
                alert.highest_buy_order_minor,
                alert.lowest_sell_order_minor,
                alert.top_level_buy_quantity,
                alert.cumulative_buy_quantity,
                alert.modeled_steam_fee_minor,
                alert.modeled_publisher_fee_minor,
                alert.modeled_seller_receives_minor,
                alert.historical_baseline_minor,
                alert.bid_multiple,
                alert.opportunity_score,
                alert.confidence_score,
                alert.warning_tags,
                alert.status,
                now,
            ))
            alert_id = cur.lastrowid
            if alert_id is None:
                raise RuntimeError("Failed to obtain lastrowid for alert event.")

        return alert.model_copy(update={"alert_id": alert_id, "created_at": now})

    def get_latest_alert_for_item(
        self,
        item_id: int,
        currency_id: int,
        alert_type: str = "sell_target_bid",
    ) -> AlertEventRecord | None:
        """Retrieve the most recent alert for an item and currency."""
        row = self._db.fetch_one(
            """
            SELECT * FROM alert_events
            WHERE item_id = ? AND currency_id = ? AND alert_type = ?
            ORDER BY created_at DESC, alert_id DESC
            LIMIT 1
            """,
            (item_id, currency_id, alert_type),
        )
        if row is None:
            return None
        return self._row_to_alert(row)

    def get_alert_by_id(self, alert_id: int) -> AlertEventRecord | None:
        """Fetch alert event by ID."""
        row = self._db.fetch_one("SELECT * FROM alert_events WHERE alert_id = ?", (alert_id,))
        if row is None:
            return None
        return self._row_to_alert(row)

    def get_recent_alerts(self, limit: int = 50) -> list[AlertEventRecord]:
        """Fetch recent alerts ordered newest to oldest."""
        rows = self._db.fetch_all(
            "SELECT * FROM alert_events ORDER BY created_at DESC, alert_id DESC LIMIT ?",
            (limit,),
        )
        return [self._row_to_alert(r) for r in rows]

    def get_alert_by_fingerprint(self, fingerprint: str) -> AlertEventRecord | None:
        """Fetch alert event by SHA256 fingerprint."""
        row = self._db.fetch_one(
            "SELECT * FROM alert_events WHERE alert_fingerprint = ? ORDER BY alert_id DESC LIMIT 1",
            (fingerprint,),
        )
        if row is None:
            return None
        return self._row_to_alert(row)

    def get_last_successful_delivery_for_channel(
        self,
        item_id: int,
        currency_id: int,
        alert_type: str,
        channel: str,
    ) -> tuple[NotificationDeliveryRecord, AlertEventRecord] | None:
        """Fetch the latest successful ('delivered') delivery and its parent alert event for a channel."""
        row = self._db.fetch_one("""
            SELECT
                d.delivery_id, d.alert_id, d.channel, d.status, d.attempts, d.last_error, d.delivered_at, d.created_at AS deliv_created_at,
                a.item_id, a.currency_id, a.alert_type, a.alert_fingerprint, a.highest_buy_order_minor,
                a.lowest_sell_order_minor, a.top_level_buy_quantity, a.cumulative_buy_quantity,
                a.modeled_steam_fee_minor, a.modeled_publisher_fee_minor, a.modeled_seller_receives_minor,
                a.historical_baseline_minor, a.bid_multiple, a.opportunity_score, a.confidence_score,
                a.warning_tags, a.status AS alert_status, a.created_at AS alert_created_at
            FROM notification_deliveries d
            JOIN alert_events a ON d.alert_id = a.alert_id
            WHERE a.item_id = ? AND a.currency_id = ? AND a.alert_type = ?
              AND d.channel = ? AND d.status = 'delivered'
            ORDER BY d.delivery_id DESC
            LIMIT 1
        """, (item_id, currency_id, alert_type, channel))
        if row is None:
            return None

        delivery = NotificationDeliveryRecord(
            delivery_id=row["delivery_id"],
            alert_id=row["alert_id"],
            channel=row["channel"],
            status=row["status"],
            attempts=row["attempts"],
            last_error=row["last_error"],
            delivered_at=row["delivered_at"],
            created_at=row["deliv_created_at"],
        )
        alert = AlertEventRecord(
            alert_id=row["alert_id"],
            item_id=row["item_id"],
            currency_id=row["currency_id"],
            alert_type=row["alert_type"],
            alert_fingerprint=row["alert_fingerprint"],
            highest_buy_order_minor=row["highest_buy_order_minor"],
            lowest_sell_order_minor=row["lowest_sell_order_minor"],
            top_level_buy_quantity=row["top_level_buy_quantity"],
            cumulative_buy_quantity=row["cumulative_buy_quantity"],
            modeled_steam_fee_minor=row["modeled_steam_fee_minor"],
            modeled_publisher_fee_minor=row["modeled_publisher_fee_minor"],
            modeled_seller_receives_minor=row["modeled_seller_receives_minor"],
            historical_baseline_minor=row["historical_baseline_minor"],
            bid_multiple=row["bid_multiple"],
            opportunity_score=row["opportunity_score"],
            confidence_score=row["confidence_score"],
            warning_tags=row["warning_tags"],
            status=row["alert_status"],
            created_at=row["alert_created_at"],
        )
        return delivery, alert

    def get_latest_delivery_attempt(
        self,
        item_id: int,
        currency_id: int,
        alert_type: str,
        channel: str,
    ) -> tuple[NotificationDeliveryRecord, AlertEventRecord] | None:
        """Fetch the latest delivery attempt (any status) for a channel and item."""
        row = self._db.fetch_one("""
            SELECT
                d.delivery_id, d.alert_id, d.channel, d.status, d.attempts, d.last_error, d.delivered_at, d.created_at AS deliv_created_at,
                a.item_id, a.currency_id, a.alert_type, a.alert_fingerprint, a.highest_buy_order_minor,
                a.lowest_sell_order_minor, a.top_level_buy_quantity, a.cumulative_buy_quantity,
                a.modeled_steam_fee_minor, a.modeled_publisher_fee_minor, a.modeled_seller_receives_minor,
                a.historical_baseline_minor, a.bid_multiple, a.opportunity_score, a.confidence_score,
                a.warning_tags, a.status AS alert_status, a.created_at AS alert_created_at
            FROM notification_deliveries d
            JOIN alert_events a ON d.alert_id = a.alert_id
            WHERE a.item_id = ? AND a.currency_id = ? AND a.alert_type = ?
              AND d.channel = ?
            ORDER BY d.delivery_id DESC
            LIMIT 1
        """, (item_id, currency_id, alert_type, channel))
        if row is None:
            return None

        delivery = NotificationDeliveryRecord(
            delivery_id=row["delivery_id"],
            alert_id=row["alert_id"],
            channel=row["channel"],
            status=row["status"],
            attempts=row["attempts"],
            last_error=row["last_error"],
            delivered_at=row["delivered_at"],
            created_at=row["deliv_created_at"],
        )
        alert = AlertEventRecord(
            alert_id=row["alert_id"],
            item_id=row["item_id"],
            currency_id=row["currency_id"],
            alert_type=row["alert_type"],
            alert_fingerprint=row["alert_fingerprint"],
            highest_buy_order_minor=row["highest_buy_order_minor"],
            lowest_sell_order_minor=row["lowest_sell_order_minor"],
            top_level_buy_quantity=row["top_level_buy_quantity"],
            cumulative_buy_quantity=row["cumulative_buy_quantity"],
            modeled_steam_fee_minor=row["modeled_steam_fee_minor"],
            modeled_publisher_fee_minor=row["modeled_publisher_fee_minor"],
            modeled_seller_receives_minor=row["modeled_seller_receives_minor"],
            historical_baseline_minor=row["historical_baseline_minor"],
            bid_multiple=row["bid_multiple"],
            opportunity_score=row["opportunity_score"],
            confidence_score=row["confidence_score"],
            warning_tags=row["warning_tags"],
            status=row["alert_status"],
            created_at=row["alert_created_at"],
        )
        return delivery, alert

    def is_channel_in_cooldown(
        self,
        item_id: int,
        currency_id: int,
        alert_type: str,
        channel: str,
        cooldown_seconds: int,
        new_bid_minor: int,
        meaningful_increase_pct: float = 0.05,
        now_dt: datetime | None = None,
    ) -> bool:
        """Check whether a specific channel is in cooldown based on its last successful delivery."""
        res = self.get_last_successful_delivery_for_channel(item_id, currency_id, alert_type, channel)
        if res is None:
            return False
        delivery, alert = res

        if now_dt is None:
            now_dt = datetime.now(timezone.utc)

        try:
            ref_str = delivery.delivered_at or delivery.created_at
            if ref_str.endswith("Z"):
                ref_str = ref_str[:-1] + "+00:00"
            ref_dt = datetime.fromisoformat(ref_str)
            if ref_dt.tzinfo is None:
                ref_dt = ref_dt.replace(tzinfo=timezone.utc)
            elapsed = (now_dt - ref_dt).total_seconds()
        except Exception:
            return False

        if elapsed >= cooldown_seconds:
            return False

        # Within cooldown window. Compare new bid against the last successfully delivered bid:
        increase_threshold = int(math.ceil(alert.highest_buy_order_minor * (1.0 + meaningful_increase_pct)))
        if new_bid_minor >= increase_threshold:
            # Meaningful increase: allow delivery
            return False

        # In cooldown and not a meaningful increase: suppress!
        return True

    def get_delivery_history_for_fingerprint(
        self,
        item_id: int,
        currency_id: int,
        alert_type: str,
        channel: str,
        alert_fingerprint: str,
    ) -> list[NotificationDeliveryRecord]:
        """Fetch all delivery records for a specific fingerprint and channel, ordered newest to oldest."""
        rows = self._db.fetch_all("""
            SELECT
                d.delivery_id, d.alert_id, d.channel, d.status, d.attempts, d.last_error,
                d.delivered_at, d.created_at
            FROM notification_deliveries d
            JOIN alert_events a ON d.alert_id = a.alert_id
            WHERE a.item_id = ? AND a.currency_id = ? AND a.alert_type = ?
              AND d.channel = ? AND a.alert_fingerprint = ?
            ORDER BY d.delivery_id DESC
        """, (item_id, currency_id, alert_type, channel, alert_fingerprint))
        return [self._row_to_delivery(r) for r in rows]

    def is_channel_in_retry_backoff(
        self,
        item_id: int,
        currency_id: int,
        alert_type: str,
        channel: str,
        alert_fingerprint: str | None = None,
        backoff_seconds: int = 60,
        max_retries: int = 3,
        now_dt: datetime | None = None,
    ) -> bool:
        """Check whether a channel is currently in retry backoff or has exhausted retry attempts.

        Cumulative retry accounting semantics:
        - Accounting is tracked independently per (item_id, currency_id, alert_type, channel, alert_fingerprint).
        - A cycle is counted as a failure only when a delivery record has status = 'failed'.
        - Skipped or disabled deliveries (status = 'skipped') do NOT consume retry budget.
        - Cooldown-suppressed or non-actionable evaluations do NOT reset or alter failure counts.
        - Initial send is attempt #1 (1 failure). The configured `max_retries` (default 3) specifies
          the maximum number of retry attempts *after* the initial failure.
          Total allowed failures before permanent suppression for this fingerprint = 1 + max_retries (e.g. 1 + 3 = 4).
        - When cumulative failed attempts >= (1 + max_retries), further retries for this fingerprint
          are suppressed permanently (returns True).
        - A successful delivery (status = 'delivered') clears retry backoff (returns False).
        - A new or materially different bid generates a new fingerprint and receives a fresh retry budget.
        - If elapsed time since the most recent failure is < backoff_seconds (default 60s), returns True.
        - If elapsed time >= backoff_seconds and cumulative retries < max_retries, returns False (retry eligible).
        """
        if alert_fingerprint is None:
            latest = self.get_latest_alert_for_item(item_id, currency_id, alert_type)
            if latest is not None:
                alert_fingerprint = latest.alert_fingerprint

        if not alert_fingerprint:
            return False

        deliveries = self.get_delivery_history_for_fingerprint(
            item_id=item_id,
            currency_id=currency_id,
            alert_type=alert_type,
            channel=channel,
            alert_fingerprint=alert_fingerprint,
        )
        if not deliveries:
            return False

        # If this fingerprint was successfully delivered on this channel, retry state is cleared:
        if any(d.status == "delivered" for d in deliveries):
            return False

        failed_deliveries = [d for d in deliveries if d.status == "failed"]
        failed_count = len(failed_deliveries)

        if failed_count == 0:
            return False

        # Maximum cumulative attempts: 1 initial send + max_retries retries
        max_allowed_failures = 1 + max_retries
        if failed_count >= max_allowed_failures:
            # Retry limit exhausted for this fingerprint: suppress further attempts
            return True

        if now_dt is None:
            now_dt = datetime.now(timezone.utc)
        elif now_dt.tzinfo is None:
            now_dt = now_dt.replace(tzinfo=timezone.utc)

        # Check failure backoff window from the latest failed attempt:
        latest_failed = failed_deliveries[0]
        try:
            ref_str = latest_failed.created_at
            if ref_str.endswith("Z"):
                ref_str = ref_str[:-1] + "+00:00"
            ref_dt = datetime.fromisoformat(ref_str)
            if ref_dt.tzinfo is None:
                ref_dt = ref_dt.replace(tzinfo=timezone.utc)
            elapsed = (now_dt - ref_dt).total_seconds()
        except Exception:
            return False

        return elapsed < backoff_seconds

    def is_in_cooldown(
        self,
        item_id: int,
        currency_id: int,
        alert_type: str,
        cooldown_seconds: int,
        new_bid_minor: int,
        meaningful_increase_pct: float = 0.05,
        now_dt: datetime | None = None,
    ) -> bool:
        """Check whether an alert is in cooldown based on the latest successful delivery.

        Non-actionable observations (e.g. 'DATA INSUFFICIENT') and suppressed events do not
        reset or alter the cooldown reference.
        """
        row = self._db.fetch_one("""
            SELECT d.delivered_at, d.created_at AS deliv_created_at, a.highest_buy_order_minor
            FROM notification_deliveries d
            JOIN alert_events a ON d.alert_id = a.alert_id
            WHERE a.item_id = ? AND a.currency_id = ? AND a.alert_type = ? AND d.status = 'delivered'
            ORDER BY d.delivery_id DESC
            LIMIT 1
        """, (item_id, currency_id, alert_type))
        if row is None:
            return False

        if now_dt is None:
            now_dt = datetime.now(timezone.utc)

        try:
            ref_str = row["delivered_at"] or row["deliv_created_at"]
            if ref_str.endswith("Z"):
                ref_str = ref_str[:-1] + "+00:00"
            ref_dt = datetime.fromisoformat(ref_str)
            if ref_dt.tzinfo is None:
                ref_dt = ref_dt.replace(tzinfo=timezone.utc)
            elapsed = (now_dt - ref_dt).total_seconds()
        except Exception:
            return False

        if elapsed >= cooldown_seconds:
            return False

        increase_threshold = int(math.ceil(row["highest_buy_order_minor"] * (1.0 + meaningful_increase_pct)))
        if new_bid_minor >= increase_threshold:
            return False

        return True

    def record_delivery(self, delivery: NotificationDeliveryRecord) -> NotificationDeliveryRecord:
        """Insert or log a notification delivery record."""
        now = delivery.created_at or current_utc_iso()
        with self._db.transaction() as cur:
            cur.execute("""
                INSERT INTO notification_deliveries (
                    alert_id, channel, status, attempts, last_error, delivered_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                delivery.alert_id,
                delivery.channel,
                delivery.status,
                delivery.attempts,
                delivery.last_error,
                delivery.delivered_at,
                now,
            ))
            delivery_id = cur.lastrowid
            if delivery_id is None:
                raise RuntimeError("Failed to obtain lastrowid for notification delivery.")

        return delivery.model_copy(update={"delivery_id": delivery_id, "created_at": now})

    def get_deliveries_for_alert(self, alert_id: int) -> list[NotificationDeliveryRecord]:
        """Fetch all delivery records for a specific alert."""
        rows = self._db.fetch_all(
            "SELECT * FROM notification_deliveries WHERE alert_id = ? ORDER BY delivery_id ASC",
            (alert_id,),
        )
        return [self._row_to_delivery(r) for r in rows]

    @staticmethod
    def _row_to_alert(row: sqlite3.Row) -> AlertEventRecord:
        return AlertEventRecord(
            alert_id=row["alert_id"],
            item_id=row["item_id"],
            currency_id=row["currency_id"],
            alert_type=row["alert_type"],
            alert_fingerprint=row["alert_fingerprint"],
            highest_buy_order_minor=row["highest_buy_order_minor"],
            lowest_sell_order_minor=row["lowest_sell_order_minor"],
            top_level_buy_quantity=row["top_level_buy_quantity"],
            cumulative_buy_quantity=row["cumulative_buy_quantity"],
            modeled_steam_fee_minor=row["modeled_steam_fee_minor"],
            modeled_publisher_fee_minor=row["modeled_publisher_fee_minor"],
            modeled_seller_receives_minor=row["modeled_seller_receives_minor"],
            historical_baseline_minor=row["historical_baseline_minor"],
            bid_multiple=row["bid_multiple"],
            opportunity_score=row["opportunity_score"],
            confidence_score=row["confidence_score"],
            warning_tags=row["warning_tags"],
            status=row["status"],
            created_at=row["created_at"],
        )

    @staticmethod
    def _row_to_delivery(row: sqlite3.Row) -> NotificationDeliveryRecord:
        return NotificationDeliveryRecord(
            delivery_id=row["delivery_id"],
            alert_id=row["alert_id"],
            channel=row["channel"],
            status=row["status"],
            attempts=row["attempts"],
            last_error=row["last_error"],
            delivered_at=row["delivered_at"],
            created_at=row["created_at"],
        )
