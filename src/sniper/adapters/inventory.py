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


class PublicInventoryError(RuntimeError):
    """Raised when public Steam inventory fetching fails or returns incomplete data."""


class PublicInventoryClient:
    """Fetches public CS2 inventories via Steam's read-only inventory endpoint."""

    def __init__(self, http_client: ReadOnlyHttpClient, db: Database) -> None:
        self._http = http_client
        self._db = db

    async def fetch_and_import(
        self,
        steamid64: str,
        language: str = "english",
        count: int = 1000,
        max_pages: int = 25,
        max_items: int = 25000,
    ) -> tuple[InventorySnapshotRecord, list[InventoryItemRecord]]:
        """Fetch a public CS2 inventory and import the snapshot into the local database.

        Fails closed on unsuccessful, partial, or truncated pagination sequences.
        Correlates assets and descriptions strictly via exact (classid, instanceid) key.
        Distinguishes marketable (marketable == 1) vs restricted (marketable == 0) items.
        """
        valid_steamid = validate_steamid64(steamid64)
        if count <= 0 or count > 5000:
            raise ValueError(f"Invalid count {count}; must be between 1 and 5000.")
        if max_pages <= 0 or max_pages > 50:
            raise ValueError(f"Invalid max_pages {max_pages}; must be between 1 and 50.")
        if max_items <= 0 or max_items > 50000:
            raise ValueError(f"Invalid max_items {max_items}; must be between 1 and 50000.")

        repo = InventoryRepository(self._db)

        all_assets: list[Any] = []
        desc_map: dict[tuple[str, str], Any] = {}

        start_assetid: str | None = None
        seen_cursors: set[str] = set()
        pages_fetched = 0
        pagination_completed = False

        while pages_fetched < max_pages:
            pages_fetched += 1
            url = f"https://steamcommunity.com/inventory/{valid_steamid}/730/2?l={language}&count={count}"
            if start_assetid:
                url += f"&start_assetid={start_assetid}"

            logger.info("Fetching public inventory page %d for %s", pages_fetched, valid_steamid)
            resp = await self._http.get_json(url, schema=PublicInventoryResponse)
            if resp is None or not resp.success:
                if pages_fetched == 1:
                    logger.warning("Steam returned unsuccessful response for inventory %s", valid_steamid)
                    raise PublicInventoryError(
                        f"Steam returned unsuccessful response for inventory {valid_steamid}. "
                        "Profile may be private, rate limited, or inventory unavailable."
                    )
                raise PublicInventoryError(
                    f"Steam returned unsuccessful response on page {pages_fetched} for {valid_steamid}. "
                    "Incomplete inventory import aborted."
                )

            # Index descriptions strictly by exact (classid, instanceid) tuple:
            for desc in resp.descriptions:
                key = (str(desc.classid), str(desc.instanceid))
                desc_map[key] = desc

            all_assets.extend(resp.assets)

            # Check if total accumulated assets exceeded max_items:
            if len(all_assets) > max_items:
                raise PublicInventoryError(
                    f"Inventory asset count ({len(all_assets)}) exceeded max_items ({max_items}). "
                    "Incomplete inventory import aborted."
                )

            # Check pagination completion:
            has_more = bool(resp.more_items)
            if not has_more:
                pagination_completed = True
                break

            # If Steam reports more items, validate pagination bounds and cursor:
            if not resp.last_assetid:
                raise PublicInventoryError(
                    f"Missing last_assetid pagination cursor from Steam while more_items=True on page {pages_fetched}. "
                    "Incomplete inventory import aborted."
                )
            cursor_str = str(resp.last_assetid)
            if cursor_str in seen_cursors or cursor_str == start_assetid:
                raise PublicInventoryError(
                    f"Repeated last_assetid pagination cursor ('{cursor_str}') detected from Steam on page {pages_fetched}. "
                    "Incomplete inventory import aborted."
                )
            seen_cursors.add(cursor_str)

            if len(all_assets) >= max_items:
                raise PublicInventoryError(
                    f"Inventory item limit reached ({len(all_assets)} >= max_items={max_items}) while Steam reports more items. "
                    "Incomplete inventory import aborted."
                )
            if pages_fetched >= max_pages:
                raise PublicInventoryError(
                    f"Pagination page limit reached ({pages_fetched} >= max_pages={max_pages}) while Steam reports more items. "
                    "Incomplete inventory import aborted."
                )

            start_assetid = cursor_str

        if not pagination_completed:
            raise PublicInventoryError(
                f"Public inventory pagination did not complete successfully for {valid_steamid}. "
                "Incomplete inventory import aborted."
            )

        if len(all_assets) > max_items:
            raise PublicInventoryError(
                f"Inventory asset count ({len(all_assets)}) exceeded max_items ({max_items}). "
                "Incomplete inventory import aborted."
            )

        # Build inventory item records
        item_records: list[InventoryItemRecord] = []
        for asset in all_assets:
            cid = str(asset.classid)
            iid = str(asset.instanceid)
            # Match descriptions exclusively by exact (classid, instanceid) key:
            desc = desc_map.get((cid, iid))

            if desc is not None:
                market_hash_name = desc.market_hash_name
                is_marketable = bool(desc.marketable == 1 or desc.marketable is True)
                is_tradable = bool(desc.tradable == 1 or desc.tradable is True)
                restriction_days = desc.market_marketable_restriction
            else:
                # Missing exact description: preserve asset as unknown, mark non-marketable & non-tradable
                market_hash_name = f"Unknown Item (classid={cid}, instanceid={iid})"
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
    """Parses and imports manual CSV and JSON inventories for private Steam profiles.

    Accepted boolean representations for marketable / tradable flags:
      - True values:  True (bool), 'true', '1', 'yes', 'y' (case-insensitive)
      - False values: False (bool), 'false', '0', 'no', 'n' (case-insensitive)

    Fail-closed semantics:
      - Missing or empty marketable / tradable fields fail closed to False (non-marketable / non-tradable).
      - Invalid explicit values (e.g. 'maybe', 'unknown', '2') produce row-level validation errors.
      - Negative or non-integer restriction days produce row-level validation errors.
    """

    ACCEPTED_BOOLEAN_TRUE = frozenset({"true", "1", "yes", "y"})
    ACCEPTED_BOOLEAN_FALSE = frozenset({"false", "0", "no", "n"})

    @classmethod
    def _parse_amount(
        cls,
        raw_amount: Any,
        row_label: str,
    ) -> tuple[int | None, str | None]:
        """Parse inventory amount strictly as a positive integer.

        Accepts only positive actual integers and positive integer strings.
        Rejects booleans, floats (e.g. 1.0, 1.9), non-positive numbers, and malformed strings.
        Never silently truncates values.
        """
        if raw_amount is None or raw_amount == "":
            return 1, None
        if type(raw_amount) is bool:
            return None, (
                f"{row_label}: Invalid boolean amount '{raw_amount}'. "
                "Amount must be a positive integer."
            )
        if isinstance(raw_amount, float):
            return None, (
                f"{row_label}: Invalid float amount '{raw_amount}'. "
                "Amount must be a positive integer."
            )
        if type(raw_amount) is int:
            if raw_amount <= 0:
                return None, f"{row_label}: Amount must be a positive integer, got {raw_amount}."
            return raw_amount, None
        if isinstance(raw_amount, str):
            s = raw_amount.strip()
            if not s:
                return 1, None
            if s.isdigit():
                val = int(s)
                if val <= 0:
                    return None, f"{row_label}: Amount must be a positive integer, got {val}."
                return val, None
            return None, (
                f"{row_label}: Invalid non-integer amount '{raw_amount}'. "
                "Amount must be a positive integer."
            )
        return None, (
            f"{row_label}: Invalid amount type '{type(raw_amount).__name__}'. "
            "Amount must be a positive integer."
        )

    @classmethod
    def _parse_strict_bool(
        cls,
        val: Any,
        field_name: str,
        row_label: str,
    ) -> tuple[bool, str | None]:
        """Parse boolean flag strictly with fail-closed semantics."""
        if val is None or val == "":
            return False, None
        if type(val) is bool:
            return val, None
        if isinstance(val, float):
            return False, (
                f"{row_label}: Invalid float {field_name} value '{val}'. "
                "Floats cannot be used as boolean values."
            )
        if type(val) is int:
            if val == 1:
                return True, None
            if val == 0:
                return False, None
            return False, (
                f"{row_label}: Invalid {field_name} value '{val}'. "
                "Accepted boolean values are: true, false, 1, 0, yes, no, y, n."
            )
        if isinstance(val, str):
            s = val.strip().lower()
            if s in cls.ACCEPTED_BOOLEAN_TRUE:
                return True, None
            if s in cls.ACCEPTED_BOOLEAN_FALSE:
                return False, None
            return False, (
                f"{row_label}: Invalid {field_name} value '{val}'. "
                "Accepted boolean values are: true, false, 1, 0, yes, no, y, n."
            )
        return False, (
            f"{row_label}: Invalid {field_name} type '{type(val).__name__}'. "
            "Accepted boolean values are: true, false, 1, 0, yes, no, y, n."
        )

    @classmethod
    def _parse_restriction_days(
        cls,
        raw_restr: Any,
        row_label: str,
    ) -> tuple[int | None, str | None]:
        """Parse market restriction days, requiring non-negative integer or None."""
        if raw_restr is None or raw_restr == "":
            return None, None
        if type(raw_restr) is bool:
            return None, (
                f"{row_label}: Invalid boolean market_restriction_days '{raw_restr}'. "
                "Must be a non-negative integer."
            )
        if isinstance(raw_restr, float):
            return None, (
                f"{row_label}: Invalid float market_restriction_days '{raw_restr}'. "
                "Must be a non-negative integer."
            )
        if type(raw_restr) is int:
            if raw_restr < 0:
                return None, (
                    f"{row_label}: Invalid negative market_restriction_days '{raw_restr}'. "
                    "Must be a non-negative integer."
                )
            return raw_restr, None
        if isinstance(raw_restr, str):
            s = raw_restr.strip()
            if not s:
                return None, None
            if s.startswith("-"):
                try:
                    int(s)
                    return None, (
                        f"{row_label}: Invalid negative market_restriction_days '{raw_restr}'. "
                        "Must be a non-negative integer."
                    )
                except ValueError:
                    pass
            if s.isdigit():
                return int(s), None
            return None, (
                f"{row_label}: Invalid non-integer market_restriction_days '{raw_restr}'. "
                "Must be a non-negative integer."
            )
        return None, (
            f"{row_label}: Invalid market_restriction_days type '{type(raw_restr).__name__}'. "
            "Must be a non-negative integer."
        )

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
            row_label = f"Row {row_num}"
            raw_name = get_val(row, "market_hash_name", "name", "item", "item_name")
            if not raw_name:
                errors.append(f"{row_label}: Missing or empty market_hash_name.")
                continue

            raw_amount = get_val(row, "amount", "quantity", "count", "qty")
            amount, a_err = cls._parse_amount(raw_amount, row_label)
            if a_err is not None:
                errors.append(a_err)
                continue

            raw_asset_id = get_val(row, "asset_id", "id", "assetid")
            if raw_asset_id:
                clean_aid = str(raw_asset_id).strip()
                asset_id = clean_aid if clean_aid.startswith(("manual_", "manual:")) else f"manual_{clean_aid}"
            else:
                asset_id = f"manual_{uuid.uuid4().hex[:12]}"

            raw_marketable = get_val(row, "is_marketable", "marketable")
            is_marketable, m_err = cls._parse_strict_bool(raw_marketable, "marketable", row_label)
            if m_err is not None:
                errors.append(m_err)
                continue

            raw_tradable = get_val(row, "is_tradable", "tradable")
            is_tradable, t_err = cls._parse_strict_bool(raw_tradable, "tradable", row_label)
            if t_err is not None:
                errors.append(t_err)
                continue

            raw_restr = get_val(row, "market_restriction_days", "restriction_days")
            restr_days, r_err = cls._parse_restriction_days(raw_restr, row_label)
            if r_err is not None:
                errors.append(r_err)
                continue

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
            row_label = f"Item #{idx}"
            if not isinstance(item, dict):
                errors.append(f"{row_label}: Expected a JSON object, got {type(item).__name__}.")
                continue

            name = item.get("market_hash_name") or item.get("name") or item.get("item")
            if not name or not str(name).strip():
                errors.append(f"{row_label}: Missing or empty market_hash_name.")
                continue

            raw_amount = item.get("amount")
            if raw_amount is None:
                raw_amount = item.get("quantity")
            if raw_amount is None:
                raw_amount = item.get("count")
            if raw_amount is None:
                raw_amount = item.get("qty")
            amount, a_err = cls._parse_amount(raw_amount, row_label)
            if a_err is not None:
                errors.append(a_err)
                continue

            raw_asset_id = item.get("asset_id") or item.get("id") or item.get("assetid")
            if raw_asset_id:
                clean_aid = str(raw_asset_id).strip()
                asset_id = clean_aid if clean_aid.startswith(("manual_", "manual:")) else f"manual_{clean_aid}"
            else:
                asset_id = f"manual_{uuid.uuid4().hex[:12]}"

            raw_marketable = item.get("is_marketable")
            if raw_marketable is None:
                raw_marketable = item.get("marketable")
            is_marketable, m_err = cls._parse_strict_bool(raw_marketable, "marketable", row_label)
            if m_err is not None:
                errors.append(m_err)
                continue

            raw_tradable = item.get("is_tradable")
            if raw_tradable is None:
                raw_tradable = item.get("tradable")
            is_tradable, t_err = cls._parse_strict_bool(raw_tradable, "tradable", row_label)
            if t_err is not None:
                errors.append(t_err)
                continue

            raw_restr = item.get("market_restriction_days")
            if raw_restr is None:
                raw_restr = item.get("restriction_days")
            restr_days, r_err = cls._parse_restriction_days(raw_restr, row_label)
            if r_err is not None:
                errors.append(r_err)
                continue

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
