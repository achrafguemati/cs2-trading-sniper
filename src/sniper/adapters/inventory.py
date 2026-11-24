"""Public Steam inventory client and manual inventory importers (CSV / JSON)."""

from __future__ import annotations

import csv
import io
import json
import logging
import re
from typing import Any, TextIO
import uuid

from sniper.core.http_client import ReadOnlyHttpClient
from sniper.models.schemas import PublicInventoryResponse
from sniper.storage.database import Database
from sniper.storage.models import (
    InventoryItemRecord,
    InventorySnapshotRecord,
)
from sniper.storage.repositories import InventoryRepository

logger = logging.getLogger(__name__)

STEAMID64_REGEX = re.compile(r"^7656119\d{10}$")


def validate_steamid64(steamid64: str) -> str:
    """Validate that the provided string is a valid 17-digit SteamID64.

    Must be exactly 17 digits and begin with the standard individual account prefix '7656119'.
    """
    clean_id = str(steamid64).strip()
    if not STEAMID64_REGEX.match(clean_id):
        raise ValueError(
            f"Invalid SteamID64 '{steamid64}': must be a 17-digit numeric string starting with 7656119."
        )
    return clean_id


class PublicInventoryClient:
    """Fetches public CS2 inventories via Steam's read-only inventory endpoint."""

    def __init__(self, http_client: ReadOnlyHttpClient, db: Database) -> None:
        self._http = http_client
        self._db = db

    async def fetch_and_import(
        self,
        steamid64: str,
        language: str = "english",
        count: int = 5000,
        max_pages: int = 10,
        max_items: int = 25000,
    ) -> tuple[InventorySnapshotRecord, list[InventoryItemRecord]]:
        """Fetch a public CS2 inventory and import the snapshot into the local database.

        Handles pagination via more_items and last_assetid bounds.
        Correlates assets and descriptions via exact (classid, instanceid) key.
        Distinguishes marketable (marketable == 1) vs restricted (marketable == 0) items.
        """
        valid_steamid = validate_steamid64(steamid64)
        repo = InventoryRepository(self._db)

        all_assets: list[Any] = []
        desc_map: dict[tuple[str, str], Any] = {}
        desc_by_classid: dict[str, Any] = {}

        start_assetid: str | None = None
        pages_fetched = 0

        while pages_fetched < max_pages and len(all_assets) < max_items:
            pages_fetched += 1
            url = f"https://steamcommunity.com/inventory/{valid_steamid}/730/2?l={language}&count={count}"
            if start_assetid:
                url += f"&start_assetid={start_assetid}"

            logger.info("Fetching public inventory page %d for %s", pages_fetched, valid_steamid)
            resp = await self._http.get_json(url, schema=PublicInventoryResponse)
            if not resp or not resp.success:
                logger.warning("Steam returned unsuccessful response for inventory %s", valid_steamid)
                break

            # Index descriptions
            for desc in resp.descriptions:
                key = (str(desc.classid), str(desc.instanceid))
                desc_map[key] = desc
                desc_by_classid[str(desc.classid)] = desc

            all_assets.extend(resp.assets)

            # Check pagination
            has_more = bool(resp.more_items)
            if not has_more or not resp.last_assetid or resp.last_assetid == start_assetid:
                break
            start_assetid = str(resp.last_assetid)

        # Build inventory item records
        item_records: list[InventoryItemRecord] = []
        for asset in all_assets[:max_items]:
            cid = str(asset.classid)
            iid = str(asset.instanceid)
            desc = desc_map.get((cid, iid)) or desc_by_classid.get(cid)

            if desc is not None:
                market_hash_name = desc.market_hash_name
                is_marketable = bool(desc.marketable == 1 or desc.marketable is True)
                is_tradable = bool(desc.tradable == 1 or desc.tradable is True)
                restriction_days = desc.market_marketable_restriction
            else:
                market_hash_name = f"Unknown Item (classid={cid})"
                is_marketable = False
                is_tradable = False
                restriction_days = None

            amount = 1
            try:
                amount = int(asset.amount)
            except (ValueError, TypeError):
                amount = 1

            record = InventoryItemRecord(
                snapshot_id=0,
                asset_id=str(asset.assetid),
                class_id=cid,
                instance_id=iid,
                market_hash_name=market_hash_name,
                amount=amount,
                is_marketable=is_marketable,
                is_tradable=is_tradable,
                market_restriction_days=restriction_days,
            )
            item_records.append(record)

        snapshot, inserted_items = repo.import_snapshot(
            items=item_records,
            owner_id=valid_steamid,
            source="public_inventory",
        )
        logger.info(
            "Imported snapshot #%s with %d items for owner %s",
            snapshot.snapshot_id,
            len(inserted_items),
            valid_steamid,
        )
        return snapshot, inserted_items


class ManualInventoryImporter:
    """Parses and imports manual CSV and JSON inventories for private Steam profiles."""

    @staticmethod
    def _parse_bool(val: Any, default: bool = True) -> bool:
        if val is None or val == "":
            return default
        if isinstance(val, bool):
            return val
        s = str(val).strip().lower()
        if s in ("1", "true", "yes", "y"):
            return True
        if s in ("0", "false", "no", "n"):
            return False
        return default

    @classmethod
    def import_csv(
        cls,
        csv_content: str | TextIO,
        db: Database,
        owner_id: str | None = None,
    ) -> tuple[InventorySnapshotRecord, list[InventoryItemRecord], list[str]]:
        """Import inventory items from CSV content.

        Supports header synonyms:
        - Item name: 'market_hash_name', 'name', 'item', 'item_name'
        - Amount: 'amount', 'quantity', 'count', 'qty'
        - Asset ID: 'asset_id', 'id', 'assetid'
        - Marketable: 'is_marketable', 'marketable'
        - Tradable: 'is_tradable', 'tradable'
        - Restriction days: 'market_restriction_days', 'restriction_days'

        Performs row-level validation with line numbers.
        Returns (snapshot, inserted_items, errors).
        """
        if isinstance(csv_content, str):
            stream = io.StringIO(csv_content)
        else:
            stream = csv_content

        reader = csv.DictReader(stream)
        if reader.fieldnames is None:
            return (
                InventorySnapshotRecord(total_count=0, source="manual_csv"),
                [],
                ["Empty CSV content: no headers found."],
            )

        # Normalize header map
        header_map: dict[str, str] = {}
        for h in reader.fieldnames:
            clean = h.strip().lower().replace(" ", "_")
            header_map[clean] = h

        def get_val(row: dict[str, Any], *synonyms: str) -> Any:
            for syn in synonyms:
                orig = header_map.get(syn)
                if orig is not None and orig in row and row[orig] is not None:
                    val = str(row[orig]).strip()
                    if val != "":
                        return val
            return None

        records: list[InventoryItemRecord] = []
        errors: list[str] = []

        row_num = 1  # 1 is header row
        for row in reader:
            row_num += 1
            raw_name = get_val(row, "market_hash_name", "name", "item", "item_name")
            if not raw_name:
                errors.append(f"Row {row_num}: Missing or empty market_hash_name.")
                continue

            raw_amount = get_val(row, "amount", "quantity", "count", "qty")
            amount = 1
            if raw_amount is not None:
                try:
                    amount = int(raw_amount)
                    if amount <= 0:
                        errors.append(f"Row {row_num}: Amount must be a positive integer, got {amount}.")
                        continue
                except ValueError:
                    errors.append(f"Row {row_num}: Invalid non-integer amount '{raw_amount}'.")
                    continue

            raw_asset_id = get_val(row, "asset_id", "id", "assetid")
            asset_id = raw_asset_id if raw_asset_id else f"manual_{uuid.uuid4().hex[:12]}"

            raw_marketable = get_val(row, "is_marketable", "marketable")
            is_marketable = cls._parse_bool(raw_marketable, default=True)

            raw_tradable = get_val(row, "is_tradable", "tradable")
            is_tradable = cls._parse_bool(raw_tradable, default=True)

            raw_restr = get_val(row, "market_restriction_days", "restriction_days")
            restr_days = None
            if raw_restr is not None:
                try:
                    restr_days = int(raw_restr)
                except ValueError:
                    restr_days = None

            record = InventoryItemRecord(
                snapshot_id=0,
                asset_id=asset_id,
                class_id=get_val(row, "class_id", "classid") or "0",
                instance_id=get_val(row, "instance_id", "instanceid") or "0",
                market_hash_name=raw_name,
                amount=amount,
                is_marketable=is_marketable,
                is_tradable=is_tradable,
                market_restriction_days=restr_days,
            )
            records.append(record)

        repo = InventoryRepository(db)
        snapshot, inserted_items = repo.import_snapshot(
            items=records,
            owner_id=owner_id,
            source="manual_csv",
        )
        return snapshot, inserted_items, errors

    @classmethod
    def import_json(
        cls,
        json_content: str | bytes,
        db: Database,
        owner_id: str | None = None,
    ) -> tuple[InventorySnapshotRecord, list[InventoryItemRecord], list[str]]:
        """Import inventory items from JSON content.

        Accepts:
        - List of items: [{"market_hash_name": "...", "amount": 1, ...}]
        - Root dictionary with 'items' or 'inventory' key: {"items": [...]}

        Performs item-level validation with index positions.
        Returns (snapshot, inserted_items, errors).
        """
        try:
            parsed = json.loads(json_content)
        except Exception as exc:
            return (
                InventorySnapshotRecord(total_count=0, source="manual_json"),
                [],
                [f"Malformed JSON: {exc}"],
            )

        if isinstance(parsed, dict):
            raw_items = parsed.get("items") or parsed.get("inventory") or []
        elif isinstance(parsed, list):
            raw_items = parsed
        else:
            return (
                InventorySnapshotRecord(total_count=0, source="manual_json"),
                [],
                ["Invalid JSON: expected a list of items or an object containing 'items'/'inventory' array."],
            )

        if not isinstance(raw_items, list):
            return (
                InventorySnapshotRecord(total_count=0, source="manual_json"),
                [],
                ["Invalid JSON: items must be an array."],
            )

        records: list[InventoryItemRecord] = []
        errors: list[str] = []

        for idx, item in enumerate(raw_items, start=1):
            if not isinstance(item, dict):
                errors.append(f"Item #{idx}: Expected a JSON object, got {type(item).__name__}.")
                continue

            name = item.get("market_hash_name") or item.get("name") or item.get("item")
            if not name or not str(name).strip():
                errors.append(f"Item #{idx}: Missing or empty market_hash_name.")
                continue

            raw_amount = item.get("amount")
            if raw_amount is None:
                raw_amount = item.get("quantity")
            if raw_amount is None:
                raw_amount = item.get("count")
            if raw_amount is None:
                raw_amount = 1
            try:
                amount = int(raw_amount)
                if amount <= 0:
                    errors.append(f"Item #{idx}: Amount must be a positive integer, got {amount}.")
                    continue
            except (ValueError, TypeError):
                errors.append(f"Item #{idx}: Invalid non-integer amount '{raw_amount}'.")
                continue

            asset_id = str(item.get("asset_id") or item.get("id") or f"manual_{uuid.uuid4().hex[:12]}")
            is_marketable = cls._parse_bool(item.get("is_marketable", item.get("marketable")), default=True)
            is_tradable = cls._parse_bool(item.get("is_tradable", item.get("tradable")), default=True)

            raw_restr = item.get("market_restriction_days", item.get("restriction_days"))
            restr_days = None
            if raw_restr is not None:
                try:
                    restr_days = int(raw_restr)
                except (ValueError, TypeError):
                    restr_days = None

            record = InventoryItemRecord(
                snapshot_id=0,
                asset_id=asset_id,
                class_id=str(item.get("class_id", item.get("classid", "0"))),
                instance_id=str(item.get("instance_id", item.get("instanceid", "0"))),
                market_hash_name=str(name).strip(),
                amount=amount,
                is_marketable=is_marketable,
                is_tradable=is_tradable,
                market_restriction_days=restr_days,
            )
            records.append(record)

        repo = InventoryRepository(db)
        snapshot, inserted_items = repo.import_snapshot(
            items=records,
            owner_id=owner_id,
            source="manual_json",
        )
        return snapshot, inserted_items, errors
