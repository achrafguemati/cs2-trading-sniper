"""Application service integrating item identity resolution with persistent SQLite storage."""

from __future__ import annotations

from typing import Any, NamedTuple

from sniper.core.identity import (
    IdentityResolutionError,
    ItemIdentityResult,
    ItemNameIdDatabase,
    ResolutionSource,
    ResolutionStatus,
    SsrBucketResolutionError,
    SsrListingContext,
    SsrListingLoaderData,
    UrlType,
    resolve_item_identity,
)
from sniper.storage.database import Database
from sniper.storage.models import ItemRecord
from sniper.storage.repositories import ItemRepository


class IngestedItemResult(NamedTuple):
    """Pair of persisted catalog record and underlying identity resolution outcome."""
    record: ItemRecord
    identity: ItemIdentityResult


class ItemIdentityService:
    """Service layer connecting identity resolution to SQLite catalog persistence."""

    def __init__(
        self,
        repository: ItemRepository | Database,
        nameid_db: ItemNameIdDatabase | None = None,
    ) -> None:
        if isinstance(repository, Database):
            self._repo = ItemRepository(repository)
        elif isinstance(repository, ItemRepository):
            self._repo = repository
        else:
            raise TypeError(f"Expected ItemRepository or Database, got {type(repository).__name__}")
        self._nameid_db = nameid_db

    @property
    def repository(self) -> ItemRepository:
        """Underlying item repository."""
        return self._repo

    @property
    def nameid_db(self) -> ItemNameIdDatabase | None:
        """Configured offline item_nameid database, if any."""
        return self._nameid_db

    def resolve_and_persist(
        self,
        url: str,
        ssr_data: SsrListingContext | SsrListingLoaderData | dict[str, Any] | None = None,
    ) -> IngestedItemResult:
        """Resolve a Steam listing URL and persist the resulting canonical item.

        Workflow:
        1. Resolve identity via resolve_item_identity(url, ssr_data=ssr_data, db=self._nameid_db).
           - Enforces strict mapping trust (rejects synthetic / legacy / untrusted mappings).
           - Enforces strict SSR source_url context on grouped URLs.
        2. Reject persistence if identity is incomplete (status PARSED_URL_ONLY) or market_hash_name is empty.
        3. Convert resolved identity to ItemRecord via ItemRecord.from_identity_result(identity).
        4. Persist to SQLite catalog via ItemRepository.save(record).
        5. Return IngestedItemResult(record=saved_record, identity=identity).

        Returns:
            IngestedItemResult containing the persisted ItemRecord and ItemIdentityResult.

        Raises:
            IdentityResolutionError: If the URL or SSR data cannot be resolved to a canonical item,
                                     or if attempting to persist an incomplete grouped URL.
            ValueError: If input parameters or resulting record attributes are invalid.
        """
        identity = resolve_item_identity(
            url=url,
            ssr_data=ssr_data,
            db=self._nameid_db,
        )

        if identity.resolution_status == ResolutionStatus.PARSED_URL_ONLY:
            raise IdentityResolutionError(
                f"Cannot persist incomplete item identity for '{url}': status is PARSED_URL_ONLY. "
                "Grouped listing URLs require valid SSR context with matching exterior bucket."
            )

        if not identity.market_hash_name or not identity.market_hash_name.strip():
            raise IdentityResolutionError(
                f"Cannot persist item identity for '{url}': market_hash_name is empty."
            )

        record = ItemRecord.from_identity_result(identity)
        saved_record = self._repo.save(record)
        return IngestedItemResult(record=saved_record, identity=identity)

    # Convenience alias matching resolve_and_persist
    resolve_and_save = resolve_and_persist


def ingest_market_item(
    url: str,
    db: Database | ItemRepository,
    ssr_data: SsrListingContext | SsrListingLoaderData | dict[str, Any] | None = None,
    nameid_db: ItemNameIdDatabase | None = None,
) -> IngestedItemResult:
    """Convenience function to resolve and persist a market item in one call."""
    service = ItemIdentityService(db, nameid_db=nameid_db)
    return service.resolve_and_persist(url, ssr_data=ssr_data)
