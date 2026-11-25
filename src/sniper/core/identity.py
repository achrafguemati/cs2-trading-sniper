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


VERIFIED_MAPPING_REPOSITORY = "https://github.com/somespecialone/steam-market-ids"
VERIFIED_MAPPING_REVISION = "caac34bd8a28578e800501da5cc9f16c7cb3fa67"
VERIFIED_MAPPING_SOURCE_PATH = "data/CS2/item_names.json"
VERIFIED_MAPPING_LICENSE = "WTFPL"
VERIFIED_MAPPING_SHA256 = "9f34cee0a6f5479017d227568e1c8132c8b3c4f82b2d87123a80951e5adb3212"
VERIFIED_MAPPING_RECORD_COUNT = 26935


def is_trusted_mapping_source(source: str | None, revision: str | None) -> bool:
    """Validate whether an item_nameid mapping source and revision satisfy the pinned verified dataset policy."""
    if not source or not revision:
        return False
    return (
        source == VERIFIED_MAPPING_REPOSITORY
        and revision == VERIFIED_MAPPING_REVISION
    )


def is_trusted_classid_source(source: str | None) -> bool:
    """Validate whether a classid source is trusted for production identity results.

    Synthetic test data and legacy unverified sources are strictly rejected.
    """
    if not source:
        return False
    if source in ("SYNTHETIC_TEST_DATA", "legacy_unverified"):
        return False
    return source in ("steam_ssr_bucket",)


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
    retrieval_timestamp: str
    source_path: str
    license: str
    sha256: str
    record_count: int
    imported_at: str | None = None


class SsrListingContext(BaseModel):
    """SSR payload paired with the exact grouped URL from which it was obtained."""
    source_url: str
    loader_data: SsrListingLoaderData


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
    classid_source: str | None = None
    classid_revision: str | None = None
    item_nameid: int | None = None
    item_nameid_source: str | None = None
    item_nameid_revision: str | None = None
    resolution_status: ResolutionStatus
    resolution_source: ResolutionSource

    @property
    def canonical_url(self) -> str | None:
        """Return the canonical Steam Market listing URL if market_hash_name is known."""
        if self.market_hash_name:
            return f"https://steamcommunity.com/market/listings/730/{quote(self.market_hash_name, safe='')}"
        return None


def adapt_upstream_item_names(raw_mapping: Any) -> list[dict[str, Any]]:
    """Adapt upstream market-hash-name -> item_nameid JSON without normalizing keys.

    Names are copied verbatim. No classid is synthesized because the upstream file
    contains only item_nameid values.
    """
    if not isinstance(raw_mapping, dict):
        raise ValueError("Upstream item_names JSON must be a JSON object mapping names to IDs.")

    records: list[dict[str, Any]] = []
    for market_hash_name, item_nameid in raw_mapping.items():
        if not isinstance(market_hash_name, str) or not market_hash_name.strip():
            raise ValueError("Upstream mapping contains a blank or non-string market_hash_name.")
        if type(item_nameid) is not int or item_nameid <= 0:
            raise ValueError(
                f"Invalid item_nameid for {market_hash_name!r}: expected a positive integer."
            )
        records.append({"market_hash_name": market_hash_name, "item_nameid": item_nameid})
    return records


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

        q_val = query_params.get("category_Quality", "").strip().lower()
        is_st = q_val in ("tag_strange", "strange")
        is_souv = q_val in ("tag_tournament", "tournament")

        return ItemIdentityResult(
            appid=730,
            input_url=url,
            url_type=UrlType.GROUPED,
            group_id=decoded_token,
            base_name="",
            market_hash_name="",
            exterior=exterior,
            is_stattrak=is_st,
            is_souvenir=is_souv,
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

def _bucket_quality_tag(bucket: SsrBucketItem) -> str:
    """Return normalized quality key ('normal' | 'strange' | 'tournament') for SSR bucket disambiguation."""
    for f in bucket.filters:
        if len(f) >= 2 and str(f[0]).strip().lower() == "quality":
            return str(f[1]).strip().lower()
    _, _, is_st, is_souv = decompose_market_hash_name(bucket.bucket_id)
    if is_st:
        return "strange"
    if is_souv:
        return "tournament"
    return "normal"


def resolve_grouped_ssr_bucket(
    loader_data: SsrListingLoaderData | dict[str, Any],
    wear_category: WearCategory | str,
    *,
    initial_selected_bucket_id: str | None = None,
    is_stattrak: bool | None = None,
    is_souvenir: bool | None = None,
) -> SsrBucketItem:
    """Resolve the specific SSR bucket matching the requested wear filter.
    
    Fails closed if:
    - Loader data indicates success is false or appid != 730.
    - Requested wear category is missing from buckets.
    - Ambiguous matching buckets are found without unambiguous quality or initialSelectedBucketID selection.
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
        if is_stattrak is True:
            st_matches = [b for b in matching if _bucket_quality_tag(b) == "strange"]
            if len(st_matches) == 1:
                return st_matches[0]
        elif is_souvenir is True:
            souv_matches = [b for b in matching if _bucket_quality_tag(b) == "tournament"]
            if len(souv_matches) == 1:
                return souv_matches[0]

        if initial_selected_bucket_id:
            selected_all = [b for b in data.buckets if b.bucket_id == initial_selected_bucket_id]
            selected_matching = [b for b in matching if b.bucket_id == initial_selected_bucket_id]
            quality_tags = [_bucket_quality_tag(b) for b in matching]
            if (
                len(selected_all) == 1
                and len(selected_matching) == 1
                and len(quality_tags) == len(set(quality_tags))
            ):
                return selected_matching[0]

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
                    imported_at TEXT NOT NULL,
                    source_path TEXT NOT NULL DEFAULT 'legacy_unverified',
                    license TEXT NOT NULL DEFAULT 'UNVERIFIED'
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS item_mappings (
                    market_hash_name TEXT PRIMARY KEY,
                    item_nameid INTEGER NOT NULL,
                    item_nameid_source TEXT NOT NULL,
                    item_nameid_revision TEXT,
                    classid TEXT,
                    classid_source TEXT,
                    classid_revision TEXT,
                    source TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            # Migrate databases created by the earlier M2.1 prototype without
            # upgrading their existing mappings to verified provenance.
            metadata_columns = {row[1] for row in cur.execute("PRAGMA table_info(dataset_metadata)")}
            if "source_path" not in metadata_columns:
                cur.execute("ALTER TABLE dataset_metadata ADD COLUMN source_path TEXT NOT NULL DEFAULT 'legacy_unverified'")
            if "license" not in metadata_columns:
                cur.execute("ALTER TABLE dataset_metadata ADD COLUMN license TEXT NOT NULL DEFAULT 'UNVERIFIED'")

            mapping_columns = {row[1] for row in cur.execute("PRAGMA table_info(item_mappings)")}
            for column, sql_type in (
                ("item_nameid_source", "TEXT"),
                ("item_nameid_revision", "TEXT"),
                ("classid_source", "TEXT"),
                ("classid_revision", "TEXT"),
            ):
                if column not in mapping_columns:
                    cur.execute(f"ALTER TABLE item_mappings ADD COLUMN {column} {sql_type}")
            cur.execute("""
                UPDATE item_mappings
                SET item_nameid_source = 'legacy_unverified'
                WHERE item_nameid_source IS NULL
            """)
            cur.execute("""
                UPDATE item_mappings
                SET classid_source = 'legacy_unverified'
                WHERE classid IS NOT NULL AND classid_source IS NULL
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_item_mappings_nameid ON item_mappings(item_nameid)
            """)
            self._conn.commit()

    def import_verified_dataset(
        self,
        source_file: Path | str,
        metadata: DatasetMetadata | dict[str, Any],
    ) -> int:
        """Import only the pinned upstream JSON file after byte and metadata checks.
        
        Args:
            source_file: Real local copy of the immutable upstream source file.
            metadata: Provenance fields which must match the pinned source release.
            
        Returns:
            Actual number of imported source mappings.
        """
        if not isinstance(source_file, (Path, str)):
            raise TypeError("Verified import requires a real source file path; use import_synthetic_records() for tests.")
        if isinstance(metadata, dict):
            meta = DatasetMetadata.model_validate(metadata)
        elif isinstance(metadata, DatasetMetadata):
            meta = metadata
        else:
            raise TypeError(f"Invalid metadata type: {type(metadata).__name__}")

        expected_metadata = {
            "upstream_repo": VERIFIED_MAPPING_REPOSITORY,
            "revision": VERIFIED_MAPPING_REVISION,
            "source_path": VERIFIED_MAPPING_SOURCE_PATH,
            "license": VERIFIED_MAPPING_LICENSE,
        }
        for field, expected in expected_metadata.items():
            if getattr(meta, field) != expected:
                raise ValueError(f"Unverified dataset {field}: expected {expected!r}.")
        if not re.fullmatch(r"[0-9a-fA-F]{64}", meta.sha256 or ""):
            raise ValueError("Verified dataset SHA-256 must contain exactly 64 hexadecimal characters.")
        if meta.sha256.lower() != VERIFIED_MAPPING_SHA256:
            raise ValueError("Dataset SHA-256 does not match the pinned upstream source digest.")
        try:
            retrieved_at = datetime.fromisoformat(meta.retrieval_timestamp.replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise ValueError("Verified dataset retrieval_timestamp must be an ISO-8601 timestamp.") from exc
        if retrieved_at.tzinfo is None:
            raise ValueError("Verified dataset retrieval_timestamp must include a timezone.")

        p = Path(source_file)
        if not p.is_file():
            raise FileNotFoundError(f"Dataset source file not found: {p}")
        content_bytes = p.read_bytes()
        computed_sha256 = hashlib.sha256(content_bytes).hexdigest()
        if computed_sha256 != VERIFIED_MAPPING_SHA256 or computed_sha256 != meta.sha256.lower():
            raise ValueError(
                f"SHA-256 mismatch for {p}: expected pinned {VERIFIED_MAPPING_SHA256}, computed {computed_sha256}"
            )
        try:
            raw_mapping = json.loads(content_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Upstream dataset must be valid UTF-8 JSON.") from exc
        raw_records = adapt_upstream_item_names(raw_mapping)
        imported_count = len(raw_records)
        if imported_count != VERIFIED_MAPPING_RECORD_COUNT or meta.record_count != imported_count:
            raise ValueError(
                f"Dataset record-count mismatch: metadata={meta.record_count}, actual={imported_count}, "
                f"pinned={VERIFIED_MAPPING_RECORD_COUNT}."
            )

        now_iso = datetime.now(timezone.utc).isoformat()
        with self._lock, self._conn:
            cur = self._conn.cursor()
            for rec in raw_records:
                cur.execute("""
                    INSERT OR REPLACE INTO item_mappings
                    (market_hash_name, item_nameid, item_nameid_source, item_nameid_revision,
                     classid, classid_source, classid_revision, source, updated_at)
                    VALUES (?, ?, ?, ?, NULL, NULL, NULL, ?, ?)
                """, (
                    rec["market_hash_name"], rec["item_nameid"], meta.upstream_repo,
                    meta.revision, meta.upstream_repo, now_iso,
                ))

            cur.execute("""
                INSERT INTO dataset_metadata
                (upstream_repo, revision, retrieval_date, sha256, record_count, imported_at, source_path, license)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                meta.upstream_repo, meta.revision, meta.retrieval_timestamp, computed_sha256,
                imported_count, now_iso, meta.source_path, meta.license,
            ))

        return imported_count

    def import_synthetic_records(self, records: Iterable[dict[str, Any]]) -> int:
        """Import explicitly synthetic records for tests; never writes verified metadata."""
        now_iso = datetime.now(timezone.utc).isoformat()
        normalized: list[tuple[str, int, str | None]] = []
        for rec in records:
            name = rec.get("market_hash_name")
            nameid = rec.get("item_nameid")
            classid = rec.get("classid")
            if not isinstance(name, str) or not name.strip():
                raise ValueError("Synthetic record requires a nonblank market_hash_name.")
            if type(nameid) is not int or nameid <= 0:
                raise ValueError("Synthetic record item_nameid must be a positive integer.")
            normalized.append((name, nameid, str(classid) if classid is not None else None))

        with self._lock, self._conn:
            for name, nameid, classid in normalized:
                self._conn.execute("""
                    INSERT OR REPLACE INTO item_mappings
                    (market_hash_name, item_nameid, item_nameid_source, item_nameid_revision,
                     classid, classid_source, classid_revision, source, updated_at)
                    VALUES (?, ?, 'SYNTHETIC_TEST_DATA', NULL, ?, ?, NULL, 'SYNTHETIC_TEST_DATA', ?)
                """, (name, nameid, classid, "SYNTHETIC_TEST_DATA" if classid else None, now_iso))
        return len(normalized)

    def lookup_verified_mapping(self, market_hash_name: str) -> dict[str, Any] | None:
        """Look up verified item mapping by market_hash_name.

        Enforces strict mapping trust and metadata policy:
        - Fails closed (returns None) if database lacks verified dataset metadata.
        - Fails closed if source and revision do not match pinned verified policy.
        - Rejects SYNTHETIC_TEST_DATA and legacy_unverified.
        - Filters out unverified classid values.
        - Never invents, fabricates, or infers IDs across wear categories.

        Returns a dictionary with verified item_nameid and provenance fields if verified,
        or None if unverified, missing, or untrusted.
        """
        if not market_hash_name:
            return None

        if not self.has_verified_metadata():
            return None

        provenance = self.lookup_item_provenance(market_hash_name)
        if provenance is None:
            return None

        nameid_source = provenance.get("item_nameid_source")
        nameid_rev = provenance.get("item_nameid_revision")
        if not is_trusted_mapping_source(nameid_source, nameid_rev):
            return None

        trusted_nameid = int(provenance["item_nameid"])

        classid_source = provenance.get("classid_source")
        if is_trusted_classid_source(classid_source):
            trusted_classid = provenance.get("classid")
            trusted_classid_source = classid_source
            trusted_classid_rev = provenance.get("classid_revision")
        else:
            trusted_classid = None
            trusted_classid_source = None
            trusted_classid_rev = None

        return {
            "item_nameid": trusted_nameid,
            "item_nameid_source": nameid_source,
            "item_nameid_revision": nameid_rev,
            "classid": trusted_classid,
            "classid_source": trusted_classid_source,
            "classid_revision": trusted_classid_rev,
        }

    def lookup_item_nameid(self, market_hash_name: str) -> tuple[int | None, str | None]:
        """Look up verified item_nameid and classid by market_hash_name.

        Enforces strict mapping trust policy via lookup_verified_mapping:
        - Rejects SYNTHETIC_TEST_DATA.
        - Rejects legacy_unverified mappings.
        - Fails closed on unknown, unrecognized, or unverified metadata.
        - Only returns item_nameid when source and revision match the pinned verified dataset policy.
        - Never leaks synthetic or unverified classid values.

        Returns (item_nameid, classid) if verified, else (None, None).
        Never invents or fabricates IDs.
        """
        verified = self.lookup_verified_mapping(market_hash_name)
        if verified is None:
            return None, None
        return verified["item_nameid"], verified["classid"]

    def search_mappings(
        self,
        query: str = "",
        category: str | None = None,
        exterior: str | None = None,
        stattrak_only: bool = False,
        limit: int = 40,
    ) -> list[dict[str, Any]]:
        """Search verified CS2 market mappings locally without touching Steam network."""
        if not self.has_verified_metadata():
            return []

        clauses = [
            "item_nameid_source = ?",
            "item_nameid_revision = ?",
        ]
        params: list[Any] = [VERIFIED_MAPPING_REPOSITORY, VERIFIED_MAPPING_REVISION]

        tokens = [t.strip() for t in (query or "").split() if t.strip()]
        for tok in tokens[:6]:
            clauses.append("market_hash_name LIKE ?")
            params.append(f"%{tok}%")

        if exterior and exterior.strip():
            clauses.append("market_hash_name LIKE ?")
            params.append(f"%({exterior.strip()})%")

        if stattrak_only:
            clauses.append("market_hash_name LIKE '%StatTrak%'")

        cat = (category or "").strip().lower()
        if cat == "knives":
            clauses.append("market_hash_name LIKE '★ %'")
            clauses.append("market_hash_name NOT LIKE '%Gloves%' AND market_hash_name NOT LIKE '%Wraps%'")
        elif cat == "gloves":
            clauses.append("(market_hash_name LIKE '%Gloves%' OR market_hash_name LIKE '%Wraps%')")
        elif cat == "rifles":
            clauses.append(
                "(market_hash_name LIKE 'AK-47 | %' OR market_hash_name LIKE 'M4A1-S | %' "
                "OR market_hash_name LIKE 'M4A4 | %' OR market_hash_name LIKE 'AWP | %' "
                "OR market_hash_name LIKE 'FAMAS | %' OR market_hash_name LIKE 'Galil AR | %' "
                "OR market_hash_name LIKE 'AUG | %' OR market_hash_name LIKE 'SG 553 | %' "
                "OR market_hash_name LIKE 'SSG 08 | %')"
            )
        elif cat == "pistols":
            clauses.append(
                "(market_hash_name LIKE 'USP-S | %' OR market_hash_name LIKE 'Glock-18 | %' "
                "OR market_hash_name LIKE 'Desert Eagle | %' OR market_hash_name LIKE 'P250 | %' "
                "OR market_hash_name LIKE 'Five-SeveN | %' OR market_hash_name LIKE 'Tec-9 | %' "
                "OR market_hash_name LIKE 'CZ75-Auto | %' OR market_hash_name LIKE 'Dual Berettas | %')"
            )
        elif cat == "cases":
            clauses.append("(market_hash_name LIKE '%Case' OR market_hash_name LIKE '%Capsule%')")
        elif cat == "stickers":
            clauses.append("market_hash_name LIKE 'Sticker | %'")
        elif cat == "agents":
            clauses.append("(market_hash_name LIKE '% | %SEAL%' OR market_hash_name LIKE '% | %Sabre%' OR market_hash_name LIKE '% | %SWAT%' OR market_hash_name LIKE '% | %FBI%' OR market_hash_name LIKE '% | %Gendarmerie%' OR market_hash_name LIKE '% | %Guerrilla%')")

        safe_limit = max(1, min(100, int(limit)))
        sql = (
            "SELECT market_hash_name, item_nameid FROM item_mappings "
            f"WHERE {' AND '.join(clauses)} "
            "ORDER BY LENGTH(market_hash_name) ASC, market_hash_name ASC LIMIT ?"
        )
        params.append(safe_limit)

        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()

        results: list[dict[str, Any]] = []
        for row in rows:
            mname = str(row["market_hash_name"])
            _, ext, is_st, is_souv = decompose_market_hash_name(mname)
            results.append({
                "market_hash_name": mname,
                "item_nameid": int(row["item_nameid"]),
                "exterior": ext.value if ext else None,
                "is_stattrak": is_st,
                "is_souvenir": is_souv,
                "steam_listing_url": f"https://steamcommunity.com/market/listings/730/{quote(mname, safe='')}",
            })
        return results

    def lookup_item_provenance(self, market_hash_name: str) -> dict[str, Any] | None:
        """Return separate source/revision metadata for item_nameid and classid."""
        if not market_hash_name:
            return None
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("""
                SELECT item_nameid, item_nameid_source, item_nameid_revision,
                       classid, classid_source, classid_revision
                FROM item_mappings WHERE market_hash_name = ?
            """, (market_hash_name,))
            row = cur.fetchone()
            if row is None:
                return None
            return {
                "item_nameid": int(row["item_nameid"]),
                "item_nameid_source": row["item_nameid_source"],
                "item_nameid_revision": row["item_nameid_revision"],
                "classid": row["classid"],
                "classid_source": row["classid_source"],
                "classid_revision": row["classid_revision"],
            }

    def get_metadata(self) -> list[DatasetMetadata]:
        """Return list of imported dataset provenance records."""
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("""
                SELECT upstream_repo, revision, retrieval_date, source_path, license,
                       sha256, record_count, imported_at
                FROM dataset_metadata ORDER BY id ASC
            """)
            rows = cur.fetchall()
            return [
                DatasetMetadata(
                    upstream_repo=r["upstream_repo"],
                    revision=r["revision"],
                    retrieval_timestamp=r["retrieval_date"],
                    source_path=r["source_path"],
                    license=r["license"],
                    sha256=r["sha256"],
                    record_count=int(r["record_count"]),
                    imported_at=r["imported_at"],
                )
                for r in rows
            ]

    def has_verified_metadata(self) -> bool:
        """Check if database contains verified dataset metadata matching all pinned constants."""
        for meta in self.get_metadata():
            if (
                meta.upstream_repo == VERIFIED_MAPPING_REPOSITORY
                and meta.revision == VERIFIED_MAPPING_REVISION
                and meta.source_path == VERIFIED_MAPPING_SOURCE_PATH
                and meta.license == VERIFIED_MAPPING_LICENSE
                and (meta.sha256 or "").lower() == VERIFIED_MAPPING_SHA256.lower()
                and meta.record_count == VERIFIED_MAPPING_RECORD_COUNT
            ):
                return True
        return False

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
    ssr_data: SsrListingContext | SsrListingLoaderData | dict[str, Any] | None = None,
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

    # 1. SSR bucket resolution/enrichment. Validate every supplied loader before
    # reading any bucket, including canonical enrichment and selected-bucket paths.
    if ssr_data is not None:
        context: SsrListingContext | None = None
        if isinstance(ssr_data, SsrListingContext):
            context = ssr_data
            loader = context.loader_data
        elif isinstance(ssr_data, dict) and {"source_url", "loader_data"}.issubset(ssr_data):
            context = SsrListingContext.model_validate(ssr_data)
            loader = context.loader_data
        elif isinstance(ssr_data, dict):
            loader = SsrListingLoaderData.model_validate(ssr_data)
        elif isinstance(ssr_data, SsrListingLoaderData):
            loader = ssr_data
        else:
            raise TypeError(f"Invalid ssr_data type: {type(ssr_data).__name__}")

        if loader.success is not True:
            raise SsrBucketResolutionError("SSR loader data is unsuccessful; refusing SSR identity use.")
        if loader.appid != 730:
            raise SsrBucketResolutionError(f"SSR loader appid {loader.appid} does not match CS2 appid 730.")

        if result.url_type == UrlType.GROUPED:
            if context is None:
                raise SsrBucketResolutionError(
                    "Grouped SSR data requires a source_url context tied to the requested group URL."
                )
            try:
                source_identity = parse_steam_market_url(context.source_url)
            except (IdentityResolutionError, ValueError) as exc:
                raise SsrBucketResolutionError("SSR source_url context is invalid.") from exc
            if source_identity.url_type != UrlType.GROUPED or source_identity.group_id != result.group_id:
                raise SsrBucketResolutionError(
                    f"SSR source group {source_identity.group_id!r} does not match requested group {result.group_id!r}."
                )

            if result.exterior is not None:
                bucket = resolve_grouped_ssr_bucket(
                    loader,
                    result.exterior,
                    initial_selected_bucket_id=loader.initialSelectedBucketID,
                    is_stattrak=True if result.is_stattrak else None,
                    is_souvenir=True if result.is_souvenir else None,
                )
            elif loader.initialSelectedBucketID:
                selected = [b for b in loader.buckets if b.bucket_id == loader.initialSelectedBucketID]
                if not selected:
                    raise SsrBucketResolutionError(
                        f"initialSelectedBucketID '{loader.initialSelectedBucketID}' not found in SSR buckets."
                    )
                if len(selected) != 1:
                    raise SsrBucketResolutionError(
                        f"initialSelectedBucketID '{loader.initialSelectedBucketID}' is ambiguous ({len(selected)} buckets)."
                    )
                bucket = selected[0]
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
            result.classid_source = "steam_ssr_bucket"
            result.resolution_status = ResolutionStatus.CANONICAL_RESOLVED
            result.resolution_source = ResolutionSource.SSR_BUCKET
        elif result.url_type == UrlType.CANONICAL:
            # Optionally enrich classid from SSR buckets if matching
            if context is not None:
                source_identity = parse_steam_market_url(context.source_url)
                if source_identity.url_type != UrlType.CANONICAL or source_identity.market_hash_name != result.market_hash_name:
                    raise SsrBucketResolutionError("SSR source_url does not match the canonical item being enriched.")
            matches = [b for b in loader.buckets if b.bucket_id == result.market_hash_name and b.classid]
            if len(matches) > 1:
                raise SsrBucketResolutionError("Canonical SSR enrichment has duplicate matching buckets.")
            if matches:
                result.classid = matches[0].classid
                result.classid_source = "steam_ssr_bucket"

    # 2. Offline item_nameid resolution
    if db is not None and result.market_hash_name:
        verified = db.lookup_verified_mapping(result.market_hash_name)
        if verified is not None:
            result.item_nameid = int(verified["item_nameid"])
            result.item_nameid_source = verified["item_nameid_source"]
            result.item_nameid_revision = verified["item_nameid_revision"]

            # classid from offline DB is only accepted if trusted and not already set by SSR
            if not result.classid and verified.get("classid"):
                result.classid = verified["classid"]
                result.classid_source = verified.get("classid_source")
                result.classid_revision = verified.get("classid_revision")

            result.resolution_status = ResolutionStatus.ITEM_NAMEID_RESOLVED
            if result.resolution_source == ResolutionSource.SSR_BUCKET:
                result.resolution_source = ResolutionSource.COMBINED
            else:
                result.resolution_source = ResolutionSource.OFFLINE_DB
        else:
            # Unmapped, synthetic, legacy, untrusted, or missing verified metadata in DB:
            # fails closed, returning item_nameid=None while preserving CANONICAL_RESOLVED
            result.item_nameid = None
            result.item_nameid_source = None
            result.item_nameid_revision = None

    return result
