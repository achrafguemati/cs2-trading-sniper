"""Steam Community Market item identity resolver.

Provides parsing, normalization, and resolution across:
- Canonical Steam listing URLs (/market/listings/730/{market_hash_name})
- Modern 2026 grouped listing URLs (/market/listings/730/G{token}?category_Exterior={WearCategory}&appid=730)
- Grouped SSR hydration loader data (loaderData[3] / SsrListingLoaderData)
- Offline versioned SQLite item_nameid mappings with dataset provenance tracking.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import threading
from typing import Any, Iterable
from urllib.parse import parse_qsl, quote, unquote, urlsplit

from pydantic import BaseModel, Field

from sniper.models.schemas import SsrBucketItem, SsrListingLoaderData


# -----------------------------------------------------------------------------
# Enums
# -----------------------------------------------------------------------------

class WearCategory(str, Enum):
    """CS2 official wear categories."""
    FACTORY_NEW = "Factory New"
    MINIMAL_WEAR = "Minimal Wear"
    FIELD_TESTED = "Field-Tested"
    WELL_WORN = "Well-Worn"
    BATTLE_SCARRED = "Battle-Scarred"


class UrlType(str, Enum):
    """Identifies URL structure type."""
    CANONICAL = "canonical"
    GROUPED = "grouped"


class ResolutionStatus(str, Enum):
    """Resolution lifecycle stages.
    
    PARSED_URL_ONLY: URL parsed, but canonical market_hash_name is incomplete
                     (e.g. grouped URL before SSR bucket resolution).
    CANONICAL_RESOLVED: Canonical market_hash_name and wear are resolved,
                        item_nameid is UNKNOWN (None).
    ITEM_NAMEID_RESOLVED: Fully resolved with exact item_nameid for orderbook polling.
    UNRESOLVED_MAPPING: Canonical identity known, but offline mapping was explicitly
                        attempted and no mapping entry exists.
    """
    PARSED_URL_ONLY = "PARSED_URL_ONLY"
    CANONICAL_RESOLVED = "CANONICAL_RESOLVED"
    ITEM_NAMEID_RESOLVED = "ITEM_NAMEID_RESOLVED"
    UNRESOLVED_MAPPING = "UNRESOLVED_MAPPING"


class ResolutionSource(str, Enum):
    """Source that resolved the current identity state."""
    URL = "url"
    SSR_BUCKET = "ssr_bucket"
    OFFLINE_DB = "offline_db"
    COMBINED = "combined"


# -----------------------------------------------------------------------------
# Wear Category Mappings
# -----------------------------------------------------------------------------

WEAR_CATEGORY_TO_PARAM: dict[WearCategory, str] = {
    WearCategory.FACTORY_NEW: "WearCategory0",
    WearCategory.MINIMAL_WEAR: "WearCategory1",
    WearCategory.FIELD_TESTED: "WearCategory2",
    WearCategory.WELL_WORN: "WearCategory3",
    WearCategory.BATTLE_SCARRED: "WearCategory4",
}

PARAM_TO_WEAR_CATEGORY: dict[str, WearCategory] = {
    "WearCategory0": WearCategory.FACTORY_NEW,
    "WearCategory1": WearCategory.MINIMAL_WEAR,
    "WearCategory2": WearCategory.FIELD_TESTED,
    "WearCategory3": WearCategory.WELL_WORN,
    "WearCategory4": WearCategory.BATTLE_SCARRED,
    # Steam tag variants
    "tag_WearCategory0": WearCategory.FACTORY_NEW,
    "tag_WearCategory1": WearCategory.MINIMAL_WEAR,
    "tag_WearCategory2": WearCategory.FIELD_TESTED,
    "tag_WearCategory3": WearCategory.WELL_WORN,
    "tag_WearCategory4": WearCategory.BATTLE_SCARRED,
    # Direct wear names
    "Factory New": WearCategory.FACTORY_NEW,
    "Minimal Wear": WearCategory.MINIMAL_WEAR,
    "Field-Tested": WearCategory.FIELD_TESTED,
    "Well-Worn": WearCategory.WELL_WORN,
    "Battle-Scarred": WearCategory.BATTLE_SCARRED,
}


# -----------------------------------------------------------------------------
# Exceptions
# -----------------------------------------------------------------------------

class IdentityResolutionError(Exception):
    """Base error for identity resolution failures."""
    pass


class SsrBucketResolutionError(IdentityResolutionError, ValueError):
    """Raised when SSR bucket resolution fails due to missing, malformed, or ambiguous buckets."""
    pass


# -----------------------------------------------------------------------------
# Data Models
# -----------------------------------------------------------------------------

class DatasetMetadata(BaseModel):
    """Provenance metadata for an imported community mapping dataset."""
    upstream_repo: str
    revision: str
    retrieval_date: str
    sha256: str
    record_count: int
    imported_at: str | None = None


class ItemIdentityResult(BaseModel):
    """Typed identity resolution result bridging URLs, SSR loader data, and item_nameid."""
    appid: int = 730
    input_url: str
    url_type: UrlType
    group_id: str | None = None
    base_name: str = ""
    market_hash_name: str = ""
    exterior: WearCategory | None = None
    is_stattrak: bool = False
    is_souvenir: bool = False
    classid: str | None = None
    item_nameid: int | None = None
    resolution_status: ResolutionStatus
    resolution_source: ResolutionSource

    @property
    def canonical_url(self) -> str | None:
        """Return the canonical Steam Market listing URL if market_hash_name is known."""
        if self.market_hash_name:
            return f"https://steamcommunity.com/market/listings/730/{quote(self.market_hash_name, safe='')}"
        return None


# -----------------------------------------------------------------------------
# Item Name Decomposition & Composition
# -----------------------------------------------------------------------------

def decompose_market_hash_name(market_hash_name: str) -> tuple[str, WearCategory | None, bool, bool]:
    """Decompose a CS2 market hash name into (base_name, exterior, is_stattrak, is_souvenir).
    
    Correctly preserves weapon skin names, knife star prefixes (★), StatTrak™ and Souvenir flags,
    and exact wear categories.
    """
    name = market_hash_name.strip()
    is_stattrak = False
    is_souvenir = False

    # Check prefix flags
    if name.startswith("★ StatTrak™ "):
        is_stattrak = True
        remainder = "★ " + name[len("★ StatTrak™ "):]
    elif name.startswith("StatTrak™ "):
        is_stattrak = True
        remainder = name[len("StatTrak™ "):]
    elif name.startswith("★ Souvenir "):
        is_souvenir = True
        remainder = "★ " + name[len("★ Souvenir "):]
    elif name.startswith("Souvenir "):
        is_souvenir = True
        remainder = name[len("Souvenir "):]
    else:
        remainder = name

    # Check exterior suffix
    exterior: WearCategory | None = None
    for wear in WearCategory:
        suffix = f" ({wear.value})"
        if remainder.endswith(suffix):
            exterior = wear
            remainder = remainder[:-len(suffix)].strip()
            break

    base_name = remainder.strip()
    return base_name, exterior, is_stattrak, is_souvenir


def compose_market_hash_name(
    base_name: str,
    exterior: WearCategory | None = None,
    is_stattrak: bool = False,
    is_souvenir: bool = False,
) -> str:
    """Compose a canonical CS2 market hash name from components."""
    if is_stattrak and is_souvenir:
        raise ValueError("An item cannot be simultaneously StatTrak and Souvenir.")

    base = base_name.strip()
    has_star = base.startswith("★ ")

    if has_star:
        core_name = base[len("★ "):]
        if is_stattrak:
            prefix = "★ StatTrak™ "
        elif is_souvenir:
            prefix = "★ Souvenir "
        else:
            prefix = "★ "
    else:
        core_name = base
        if is_stattrak:
            prefix = "StatTrak™ "
        elif is_souvenir:
            prefix = "Souvenir "
        else:
            prefix = ""

    suffix = f" ({exterior.value})" if exterior is not None else ""
    return f"{prefix}{core_name}{suffix}"


# -----------------------------------------------------------------------------
# URL & Group Parser
# -----------------------------------------------------------------------------

_GROUP_TOKEN_REGEX = re.compile(r"^G[A-Za-z0-9]+$")


def parse_steam_market_url(url: str) -> ItemIdentityResult:
    """Parse and strictly validate a Steam Community Market listing URL.
    
    Supports:
    - Canonical URLs: https://steamcommunity.com/market/listings/730/{market_hash_name}
    - Grouped URLs: https://steamcommunity.com/market/listings/730/G{token}?category_Exterior={WearCategory}&appid=730
    
    Strict validation rules:
    - Scheme must be strictly HTTPS.
    - Host must be strictly steamcommunity.com (lowercase).
    - Userinfo (username/password) is strictly prohibited.
    - Non-default ports are strictly prohibited.
    - Appid must be strictly 730 (both in path and query).
    - Ambiguous duplicate query parameters are strictly prohibited.
    - Malformed group tokens are rejected.
    """
    if not isinstance(url, str) or not url.strip():
        raise ValueError("URL must be a non-empty string.")

    split_url = urlsplit(url.strip())

    # 1. Scheme check
    if split_url.scheme != "https":
        raise ValueError(f"Invalid URL scheme '{split_url.scheme}'. Only HTTPS is permitted.")

    # 2. Userinfo check
    if split_url.username or split_url.password or "@" in split_url.netloc:
        raise ValueError("URL contains embedded userinfo which is prohibited.")

    # 3. Port check
    if split_url.port is not None and split_url.port != 443:
        raise ValueError(f"Non-default port {split_url.port} is prohibited.")

    # 4. Host check
    if not split_url.hostname or split_url.hostname.lower() != "steamcommunity.com":
        raise ValueError(f"Invalid host '{split_url.hostname}'. Only steamcommunity.com is permitted.")

    # 5. Path check
    path = split_url.path
    prefix = "/market/listings/730/"
    if not path.startswith(prefix):
        raise ValueError(f"Invalid path '{path}'. Must start with '{prefix}'.")

    raw_token = path[len(prefix):].strip("/")
    if not raw_token:
        raise ValueError("Empty item or group identifier in path.")
    if "/" in raw_token:
        raise ValueError(f"Unexpected path segments in listing URL: '{raw_token}'.")

    decoded_token = unquote(raw_token)

    # 6. Query parameters check
    raw_query_pairs = parse_qsl(split_url.query, keep_blank_values=True)
    param_keys = [k for k, _ in raw_query_pairs]
    if len(param_keys) != len(set(param_keys)):
        raise ValueError(f"Ambiguous duplicate query parameters in URL: '{split_url.query}'.")

    query_params = dict(raw_query_pairs)

    # Validate appid if present in query
    if "appid" in query_params and query_params["appid"] != "730":
        raise ValueError(f"Invalid query appid '{query_params['appid']}'. Only 730 is permitted.")

    # 7. Determine URL structure: Grouped vs Canonical
    has_exterior_param = "category_Exterior" in query_params

    # Check if path token is an attempted group token
    is_group_candidate = (
        decoded_token.startswith("G")
        and " " not in decoded_token
        and " | " not in decoded_token
    )

    if has_exterior_param:
        # Grouped URL with exterior filter
        ext_val = query_params["category_Exterior"]
        exterior = PARAM_TO_WEAR_CATEGORY.get(ext_val)
        if exterior is None:
            raise ValueError(f"Invalid wear filter parameter: '{ext_val}'.")

        if not _GROUP_TOKEN_REGEX.match(decoded_token) or len(decoded_token) <= 1:
            raise ValueError(f"Malformed group identifier: '{decoded_token}'.")

        return ItemIdentityResult(
            appid=730,
            input_url=url,
            url_type=UrlType.GROUPED,
            group_id=decoded_token,
            base_name="",
            market_hash_name="",
            exterior=exterior,
            is_stattrak=False,
            is_souvenir=False,
            classid=None,
            item_nameid=None,
            resolution_status=ResolutionStatus.PARSED_URL_ONLY,
            resolution_source=ResolutionSource.URL,
        )

    if is_group_candidate:
        # Group token without exterior filter
        if not _GROUP_TOKEN_REGEX.match(decoded_token) or len(decoded_token) <= 1:
            raise ValueError(f"Malformed group identifier: '{decoded_token}'.")

        return ItemIdentityResult(
            appid=730,
            input_url=url,
            url_type=UrlType.GROUPED,
            group_id=decoded_token,
            base_name="",
            market_hash_name="",
            exterior=None,
            is_stattrak=False,
            is_souvenir=False,
            classid=None,
            item_nameid=None,
            resolution_status=ResolutionStatus.PARSED_URL_ONLY,
            resolution_source=ResolutionSource.URL,
        )

    # Canonical URL
    market_hash_name = decoded_token
    base_name, exterior, is_st, is_souv = decompose_market_hash_name(market_hash_name)

    return ItemIdentityResult(
        appid=730,
        input_url=url,
        url_type=UrlType.CANONICAL,
        group_id=None,
        base_name=base_name,
        market_hash_name=market_hash_name,
        exterior=exterior,
        is_stattrak=is_st,
        is_souvenir=is_souv,
        classid=None,
        item_nameid=None,
        resolution_status=ResolutionStatus.CANONICAL_RESOLVED,
        resolution_source=ResolutionSource.URL,
    )


# -----------------------------------------------------------------------------
# Grouped SSR Bucket Resolution
# -----------------------------------------------------------------------------

def resolve_grouped_ssr_bucket(
    loader_data: SsrListingLoaderData | dict[str, Any],
    wear_category: WearCategory | str,
) -> SsrBucketItem:
    """Resolve the specific SSR bucket matching the requested wear filter.
    
    Fails closed if:
    - Loader data indicates success is false or appid != 730.
    - Requested wear category is missing from buckets.
    - Ambiguous matching buckets are found.
    """
    if isinstance(loader_data, dict):
        data = SsrListingLoaderData.model_validate(loader_data)
    elif isinstance(loader_data, SsrListingLoaderData):
        data = loader_data
    else:
        raise TypeError(f"Invalid loader_data type: {type(loader_data).__name__}")

    if not data.success:
        raise ValueError("SSR loader data indicates unsuccessful response (success=False).")
    if data.appid != 730:
        raise ValueError(f"SSR loader data appid {data.appid} != 730.")

    if isinstance(wear_category, WearCategory):
        target_wear = wear_category
    elif isinstance(wear_category, str):
        target_wear = PARAM_TO_WEAR_CATEGORY.get(wear_category)
        if target_wear is None:
            try:
                target_wear = WearCategory(wear_category)
            except ValueError:
                raise ValueError(f"Invalid wear category: '{wear_category}'.")
    else:
        raise TypeError(f"Invalid wear_category type: {type(wear_category).__name__}")

    target_param = WEAR_CATEGORY_TO_PARAM[target_wear]
    target_name = target_wear.value

    matching: list[SsrBucketItem] = []
    for bucket in data.buckets:
        matched = False
        # 1. Match against filters array e.g. [["Exterior", "WearCategory2"]]
        for f in bucket.filters:
            if len(f) >= 2 and str(f[0]).strip().lower() == "exterior":
                if str(f[1]).strip() == target_param:
                    matched = True
                    break
        # 2. Match localized_name_inside_group e.g. "Field-Tested"
        if not matched and bucket.localized_name_inside_group == target_name:
            matched = True
        # 3. Match bucket_id or localized_name suffix e.g. "... (Field-Tested)"
        if not matched and bucket.bucket_id.endswith(f" ({target_name})"):
            matched = True

        if matched:
            matching.append(bucket)

    if len(matching) == 0:
        raise SsrBucketResolutionError(
            f"Requested wear bucket '{target_name}' ({target_param}) not found in SSR loader data."
        )
    if len(matching) > 1:
        raise SsrBucketResolutionError(
            f"Ambiguous SSR buckets matching wear '{target_name}': found {len(matching)} candidates."
        )

    return matching[0]


# -----------------------------------------------------------------------------
# Offline Item NameID Mapping Database
# -----------------------------------------------------------------------------

class ItemNameIdDatabase:
    """Thread-safe SQLite database for offline market_hash_name -> item_nameid mappings.
    
    Guarantees:
    - Never fabricates or infers item_nameids (returns None if unknown).
    - Records provenance metadata (upstream repo, revision, retrieval date, sha256).
    - Enforces integer IDs > 0.
    """

    def __init__(self, db_path: str | Path = ":memory:"):
        self._db_path = str(db_path)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS dataset_metadata (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    upstream_repo TEXT NOT NULL,
                    revision TEXT NOT NULL,
                    retrieval_date TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    record_count INTEGER NOT NULL,
                    imported_at TEXT NOT NULL
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS item_mappings (
                    market_hash_name TEXT PRIMARY KEY,
                    item_nameid INTEGER NOT NULL,
                    classid TEXT,
                    source TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_item_mappings_nameid ON item_mappings(item_nameid)
            """)
            self._conn.commit()

    def import_verified_dataset(
        self,
        records: Iterable[dict[str, Any]] | Path | str,
        metadata: DatasetMetadata | dict[str, Any],
    ) -> int:
        """Import verified mapping records with provenance tracking.
        
        Args:
            records: Iterable of dicts or path to JSON file.
            metadata: Dataset provenance metadata.
            
        Returns:
            Number of imported records.
        """
        if isinstance(metadata, dict):
            meta = DatasetMetadata.model_validate(metadata)
        elif isinstance(metadata, DatasetMetadata):
            meta = metadata
        else:
            raise TypeError(f"Invalid metadata type: {type(metadata).__name__}")

        if isinstance(records, (Path, str)):
            p = Path(records)
            if not p.is_file():
                raise FileNotFoundError(f"Dataset file not found: {p}")
            content_bytes = p.read_bytes()
            computed_sha256 = hashlib.sha256(content_bytes).hexdigest()
            if meta.sha256 and meta.sha256.lower() != computed_sha256.lower():
                raise ValueError(
                    f"SHA256 mismatch for {p}: expected {meta.sha256}, computed {computed_sha256}"
                )
            raw_records = json.loads(content_bytes.decode("utf-8"))
            if isinstance(raw_records, dict) and "records" in raw_records:
                raw_records = raw_records["records"]
        else:
            raw_records = list(records)

        now_iso = datetime.now(timezone.utc).isoformat()
        imported_count = 0

        with self._lock:
            cur = self._conn.cursor()
            for rec in raw_records:
                name = rec.get("market_hash_name")
                nameid = rec.get("item_nameid")
                classid = rec.get("classid")

                if not name or not isinstance(name, str):
                    raise ValueError(f"Record missing valid market_hash_name: {rec}")
                if nameid is None or not isinstance(nameid, int) or nameid <= 0:
                    raise ValueError(f"Record has invalid item_nameid: {rec}")

                source = rec.get("source", meta.upstream_repo)
                cur.execute("""
                    INSERT OR REPLACE INTO item_mappings 
                    (market_hash_name, item_nameid, classid, source, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                """, (name, nameid, str(classid) if classid else None, source, now_iso))
                imported_count += 1

            cur.execute("""
                INSERT INTO dataset_metadata
                (upstream_repo, revision, retrieval_date, sha256, record_count, imported_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                meta.upstream_repo,
                meta.revision,
                meta.retrieval_date,
                meta.sha256,
                imported_count,
                now_iso,
            ))
            self._conn.commit()

        return imported_count

    def lookup_item_nameid(self, market_hash_name: str) -> tuple[int | None, str | None]:
        """Look up item_nameid and classid by market_hash_name.
        
        Returns (item_nameid, classid) if found, else (None, None).
        Never invents or fabricates IDs.
        """
        if not market_hash_name:
            return None, None

        with self._lock:
            cur = self._conn.cursor()
            cur.execute("""
                SELECT item_nameid, classid FROM item_mappings WHERE market_hash_name = ?
            """, (market_hash_name,))
            row = cur.fetchone()
            if row is not None:
                return int(row["item_nameid"]), row["classid"]
            return None, None

    def get_metadata(self) -> list[DatasetMetadata]:
        """Return list of imported dataset provenance records."""
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("""
                SELECT upstream_repo, revision, retrieval_date, sha256, record_count, imported_at
                FROM dataset_metadata ORDER BY id ASC
            """)
            rows = cur.fetchall()
            return [
                DatasetMetadata(
                    upstream_repo=r["upstream_repo"],
                    revision=r["revision"],
                    retrieval_date=r["retrieval_date"],
                    sha256=r["sha256"],
                    record_count=int(r["record_count"]),
                    imported_at=r["imported_at"],
                )
                for r in rows
            ]

    def close(self) -> None:
        """Close SQLite connection."""
        with self._lock:
            self._conn.close()

    def __enter__(self) -> ItemNameIdDatabase:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()


# -----------------------------------------------------------------------------
# Top-Level Identity Resolver
# -----------------------------------------------------------------------------

def resolve_item_identity(
    url: str,
    ssr_data: SsrListingLoaderData | dict[str, Any] | None = None,
    db: ItemNameIdDatabase | None = None,
) -> ItemIdentityResult:
    """Resolve item identity across URL, SSR hydration data, and offline mapping database.
    
    Lifecycle transitions:
    1. parse_steam_market_url -> PARSED_URL_ONLY (grouped) or CANONICAL_RESOLVED (canonical).
    2. If SSR data is provided for grouped URL -> resolves matching bucket to CANONICAL_RESOLVED.
    3. If db is provided and item_nameid is mapped -> ITEM_NAMEID_RESOLVED.
    4. If db is provided but item_nameid is unmapped -> remains CANONICAL_RESOLVED with item_nameid=None.
    """
    result = parse_steam_market_url(url)

    # 1. SSR bucket resolution for grouped listings
    if ssr_data is not None:
        if result.url_type == UrlType.GROUPED:
            if isinstance(ssr_data, dict):
                loader = SsrListingLoaderData.model_validate(ssr_data)
            else:
                loader = ssr_data

            if result.exterior is not None:
                bucket = resolve_grouped_ssr_bucket(loader, result.exterior)
            elif loader.initialSelectedBucketID:
                bucket = next((b for b in loader.buckets if b.bucket_id == loader.initialSelectedBucketID), None)
                if bucket is None:
                    raise SsrBucketResolutionError(
                        f"initialSelectedBucketID '{loader.initialSelectedBucketID}' not found in SSR buckets."
                    )
            else:
                raise SsrBucketResolutionError(
                    "Cannot resolve grouped listing without exterior filter or initialSelectedBucketID."
                )

            base_name, exterior, is_st, is_souv = decompose_market_hash_name(bucket.bucket_id)
            result.base_name = base_name
            result.market_hash_name = bucket.bucket_id
            result.exterior = exterior
            result.is_stattrak = is_st
            result.is_souvenir = is_souv
            result.classid = bucket.classid
            result.resolution_status = ResolutionStatus.CANONICAL_RESOLVED
            result.resolution_source = ResolutionSource.SSR_BUCKET
        elif result.url_type == UrlType.CANONICAL:
            # Optionally enrich classid from SSR buckets if matching
            if isinstance(ssr_data, dict):
                loader = SsrListingLoaderData.model_validate(ssr_data)
            else:
                loader = ssr_data
            for b in loader.buckets:
                if b.bucket_id == result.market_hash_name and b.classid:
                    result.classid = b.classid
                    break

    # 2. Offline item_nameid resolution
    if db is not None and result.market_hash_name:
        nameid, db_classid = db.lookup_item_nameid(result.market_hash_name)
        if nameid is not None:
            result.item_nameid = nameid
            if not result.classid and db_classid:
                result.classid = db_classid
            result.resolution_status = ResolutionStatus.ITEM_NAMEID_RESOLVED
            if result.resolution_source == ResolutionSource.SSR_BUCKET:
                result.resolution_source = ResolutionSource.COMBINED
            else:
                result.resolution_source = ResolutionSource.OFFLINE_DB
        else:
            # Unknown in DB: remains CANONICAL_RESOLVED with item_nameid=None
            result.item_nameid = None

    return result
