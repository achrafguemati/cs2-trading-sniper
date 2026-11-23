"""Comprehensive offline unit tests for M2.3 Identity Trust & SQLite Integration.

Covers:
1. Mapping trust tests (strict verified vs synthetic / legacy / unknown provenance).
2. Identity parsing and catalog persistence tests.
3. Repository integrity, idempotence, close/reopen, and provenance protection tests.
"""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest

from sniper.core.identity import (
    VERIFIED_MAPPING_RECORD_COUNT,
    VERIFIED_MAPPING_REPOSITORY,
    VERIFIED_MAPPING_REVISION,
    IdentityResolutionError,
    ItemIdentityResult,
    ItemNameIdDatabase,
    ResolutionSource,
    ResolutionStatus,
    SsrBucketResolutionError,
    SsrListingContext,
    UrlType,
    WearCategory,
    is_trusted_classid_source,
    is_trusted_mapping_source,
    resolve_item_identity,
)
from sniper.models.schemas import SsrListingLoaderData
from sniper.storage.database import Database
from sniper.storage.models import ItemRecord
from sniper.storage.repositories import ItemRepository
from sniper.storage.service import IngestedItemResult, ItemIdentityService, ingest_market_item


DATA_DIR = Path(__file__).parents[1] / "src" / "sniper" / "data"
VERIFIED_SEED_PATH = DATA_DIR / "seed_market_ids.json"
VERIFIED_METADATA_PATH = DATA_DIR / "seed_market_ids.metadata.json"
FIXTURES_DIR = Path(__file__).parent / "fixtures"
GROUPED_SSR_PATH = FIXTURES_DIR / "grouped_ssr_data.json"


def load_verified_seed_metadata() -> dict:
    return json.loads(VERIFIED_METADATA_PATH.read_text(encoding="utf-8"))


def load_grouped_ssr_fixture() -> dict:
    return json.loads(GROUPED_SSR_PATH.read_text(encoding="utf-8"))


class TestMappingTrustPolicy(unittest.TestCase):
    """Offline unit tests verifying strict mapping trust boundaries."""

    def test_verified_pinned_mapping_accepted(self) -> None:
        """Verified pinned dataset mappings are accepted with exact provenance."""
        with ItemNameIdDatabase(":memory:") as db:
            db.import_verified_dataset(VERIFIED_SEED_PATH, load_verified_seed_metadata())
            self.assertTrue(db.has_verified_metadata())

            nameid, classid = db.lookup_item_nameid("AK-47 | Redline (Field-Tested)")
            self.assertEqual(nameid, 7178002)
            self.assertIsNone(classid)

            url = "https://steamcommunity.com/market/listings/730/AK-47%20%7C%20Redline%20%28Field-Tested%29"
            res = resolve_item_identity(url, db=db)
            self.assertEqual(res.resolution_status, ResolutionStatus.ITEM_NAMEID_RESOLVED)
            self.assertEqual(res.item_nameid, 7178002)
            self.assertEqual(res.item_nameid_source, VERIFIED_MAPPING_REPOSITORY)
            self.assertEqual(res.item_nameid_revision, VERIFIED_MAPPING_REVISION)

    def test_synthetic_mapping_rejected_as_trusted(self) -> None:
        """Synthetic mappings must never produce production item_nameid resolutions."""
        with ItemNameIdDatabase(":memory:") as db:
            db.import_synthetic_records([
                {"market_hash_name": "AK-47 | Redline (Field-Tested)", "item_nameid": 99999999, "classid": "88888888"}
            ])

            # lookup_item_nameid rejects synthetic mappings
            nameid, classid = db.lookup_item_nameid("AK-47 | Redline (Field-Tested)")
            self.assertIsNone(nameid)
            self.assertIsNone(classid)

            # resolve_item_identity fails closed to CANONICAL_RESOLVED with item_nameid=None
            url = "https://steamcommunity.com/market/listings/730/AK-47%20%7C%20Redline%20%28Field-Tested%29"
            res = resolve_item_identity(url, db=db)
            self.assertEqual(res.resolution_status, ResolutionStatus.CANONICAL_RESOLVED)
            self.assertIsNone(res.item_nameid)
            self.assertIsNone(res.item_nameid_source)
            self.assertIsNone(res.classid)

    def test_legacy_unverified_mapping_rejected(self) -> None:
        """Mappings marked legacy_unverified must fail closed."""
        with ItemNameIdDatabase(":memory:") as db:
            # Directly insert a legacy_unverified row
            with db._lock, db._conn:
                db._conn.execute("""
                    INSERT INTO item_mappings
                    (market_hash_name, item_nameid, item_nameid_source, item_nameid_revision,
                     classid, classid_source, classid_revision, source, updated_at)
                    VALUES ('AK-47 | Redline (Field-Tested)', 55555, 'legacy_unverified', NULL,
                            NULL, NULL, NULL, 'legacy_unverified', '2026-01-01T00:00:00Z')
                """)

            nameid, classid = db.lookup_item_nameid("AK-47 | Redline (Field-Tested)")
            self.assertIsNone(nameid)
            self.assertIsNone(classid)

            url = "https://steamcommunity.com/market/listings/730/AK-47%20%7C%20Redline%20%28Field-Tested%29"
            res = resolve_item_identity(url, db=db)
            self.assertEqual(res.resolution_status, ResolutionStatus.CANONICAL_RESOLVED)
            self.assertIsNone(res.item_nameid)

    def test_unknown_provenance_rejected(self) -> None:
        """Unrecognized source or revision must fail closed."""
        with ItemNameIdDatabase(":memory:") as db:
            with db._lock, db._conn:
                db._conn.execute("""
                    INSERT INTO item_mappings
                    (market_hash_name, item_nameid, item_nameid_source, item_nameid_revision,
                     classid, classid_source, classid_revision, source, updated_at)
                    VALUES ('AK-47 | Redline (Field-Tested)', 12345, 'https://unknown-repo.com', 'v1.0',
                            NULL, NULL, NULL, 'custom', '2026-01-01T00:00:00Z')
                """)

            nameid, classid = db.lookup_item_nameid("AK-47 | Redline (Field-Tested)")
            self.assertIsNone(nameid)
            self.assertIsNone(classid)

            url = "https://steamcommunity.com/market/listings/730/AK-47%20%7C%20Redline%20%28Field-Tested%29"
            res = resolve_item_identity(url, db=db)
            self.assertEqual(res.resolution_status, ResolutionStatus.CANONICAL_RESOLVED)
            self.assertIsNone(res.item_nameid)

    def test_synthetic_classid_not_exposed_as_trusted(self) -> None:
        """Synthetic classid must never leak into production identity results."""
        with ItemNameIdDatabase(":memory:") as db:
            db.import_synthetic_records([
                {"market_hash_name": "AWP | Asiimov (Field-Tested)", "item_nameid": 777777, "classid": "9999999999"}
            ])

            url = "https://steamcommunity.com/market/listings/730/AWP%20%7C%20Asiimov%20%28Field-Tested%29"
            res = resolve_item_identity(url, db=db)
            self.assertIsNone(res.classid, "Synthetic classid must never leak into resolution result")
            self.assertIsNone(res.classid_source)

    def test_missing_mapping_returns_none_without_inference(self) -> None:
        """Unmapped items return None without inferring or substituting IDs from other wear variants."""
        with ItemNameIdDatabase(":memory:") as db:
            # Seed verified database
            db.import_verified_dataset(VERIFIED_SEED_PATH, load_verified_seed_metadata())

            # AK-47 | Redline (Factory New) does NOT exist in CS2
            nameid, classid = db.lookup_item_nameid("AK-47 | Redline (Factory New)")
            self.assertIsNone(nameid)
            self.assertIsNone(classid)

            url = "https://steamcommunity.com/market/listings/730/AK-47%20%7C%20Redline%20%28Factory%20New%29"
            res = resolve_item_identity(url, db=db)
            self.assertIsNone(res.item_nameid, "Must not infer ID from Field-Tested or Minimal Wear")
            self.assertEqual(res.resolution_status, ResolutionStatus.CANONICAL_RESOLVED)

    def test_diagnostic_lookup_item_provenance_preserves_synthetic_records(self) -> None:
        """lookup_item_provenance remains available for diagnostic inspection of synthetic records."""
        with ItemNameIdDatabase(":memory:") as db:
            db.import_synthetic_records([
                {"market_hash_name": "M4A4 | Howl (Factory New)", "item_nameid": 88888, "classid": "11111"}
            ])
            provenance = db.lookup_item_provenance("M4A4 | Howl (Factory New)")
            self.assertIsNotNone(provenance)
            self.assertEqual(provenance["item_nameid"], 88888)
            self.assertEqual(provenance["item_nameid_source"], "SYNTHETIC_TEST_DATA")
            self.assertEqual(provenance["classid"], "11111")
            self.assertEqual(provenance["classid_source"], "SYNTHETIC_TEST_DATA")


class TestIdentityParsingAndPersistence(unittest.TestCase):
    """Offline unit tests for identity resolution and SQLite catalog persistence."""

    def setUp(self) -> None:
        self.db = Database(":memory:")
        self.db.initialize()
        self.service = ItemIdentityService(self.db)

    def tearDown(self) -> None:
        self.db.close()

    def test_standard_canonical_url_resolves_and_persists(self) -> None:
        """Standard canonical listing URL resolves and persists to SQLite catalog."""
        url = "https://steamcommunity.com/market/listings/730/AK-47%20%7C%20Redline%20%28Field-Tested%29"
        result = self.service.resolve_and_persist(url)

        self.assertIsInstance(result, IngestedItemResult)
        self.assertIsNotNone(result.record.item_id)
        self.assertEqual(result.record.market_hash_name, "AK-47 | Redline (Field-Tested)")
        self.assertEqual(result.record.base_name, "AK-47 | Redline")
        self.assertEqual(result.record.exterior, "Field-Tested")
        self.assertFalse(result.record.is_stattrak)
        self.assertFalse(result.record.is_souvenir)
        self.assertIsNone(result.record.group_id)

        # Database lookup verifies persistence
        saved = self.service.repository.get_by_market_hash_name("AK-47 | Redline (Field-Tested)")
        self.assertIsNotNone(saved)
        self.assertEqual(saved.item_id, result.record.item_id)

    def test_grouped_url_with_matching_ssr_context_resolves_and_persists(self) -> None:
        """Grouped listing URL paired with matching SSR context resolves and persists."""
        grouped_url = "https://steamcommunity.com/market/listings/730/G1807209A023004?category_Exterior=WearCategory2&appid=730"
        ssr_data = load_grouped_ssr_fixture()
        context = SsrListingContext(source_url=grouped_url, loader_data=ssr_data)

        result = self.service.resolve_and_persist(grouped_url, ssr_data=context)
        self.assertEqual(result.record.market_hash_name, "AK-47 | Redline (Field-Tested)")
        self.assertEqual(result.record.group_id, "G1807209A023004")
        self.assertEqual(result.record.exterior, "Field-Tested")
        self.assertEqual(result.record.classid, "7993038091")
        self.assertEqual(result.record.classid_source, "steam_ssr_bucket")

        # Identity metadata matches
        self.assertEqual(result.identity.resolution_status, ResolutionStatus.CANONICAL_RESOLVED)
        self.assertEqual(result.identity.resolution_source, ResolutionSource.SSR_BUCKET)

    def test_grouped_url_without_ssr_context_not_persisted(self) -> None:
        """Grouped URL without SSR context remains PARSED_URL_ONLY and is blocked from persistence."""
        grouped_url = "https://steamcommunity.com/market/listings/730/G1807209A023004?category_Exterior=WearCategory2&appid=730"

        with self.assertRaises(IdentityResolutionError) as ctx:
            self.service.resolve_and_persist(grouped_url)

        self.assertIn("PARSED_URL_ONLY", str(ctx.exception))
        # Ensure database remains completely empty
        self.assertEqual(self.service.repository.count(), 0)

    def test_grouped_url_with_mismatched_source_context_rejected(self) -> None:
        """Mismatched grouped URL and SSR source_url must fail closed and never persist."""
        requested_url = "https://steamcommunity.com/market/listings/730/G1807209A023004?category_Exterior=WearCategory2&appid=730"
        mismatched_url = "https://steamcommunity.com/market/listings/730/G99999999999999?category_Exterior=WearCategory2&appid=730"
        ssr_data = load_grouped_ssr_fixture()
        context = SsrListingContext(source_url=mismatched_url, loader_data=ssr_data)

        with self.assertRaises(SsrBucketResolutionError):
            self.service.resolve_and_persist(requested_url, ssr_data=context)

        self.assertEqual(self.service.repository.count(), 0)

    def test_missing_or_ambiguous_ssr_wear_bucket_fails_closed(self) -> None:
        """Missing requested wear bucket fails closed without writing to database."""
        grouped_url = "https://steamcommunity.com/market/listings/730/G1807209A023004?category_Exterior=WearCategory4&appid=730"
        ssr_data = {
            "success": True,
            "appid": 730,
            "buckets": [
                {
                    "bucket_id": "Test | Skin (Factory New)",
                    "localized_name": "Test | Skin (Factory New)",
                    "min_price": "1000",
                    "classid": "111",
                    "localized_name_inside_group": "Factory New",
                    "filters": [["Exterior", "WearCategory0"]],
                }
            ],
        }
        context = SsrListingContext(source_url=grouped_url, loader_data=ssr_data)

        with self.assertRaises(SsrBucketResolutionError):
            self.service.resolve_and_persist(grouped_url, ssr_data=context)

        self.assertEqual(self.service.repository.count(), 0)

    def test_all_five_wear_categories_remain_distinct(self) -> None:
        """All five wear categories persist as distinct items without collision."""
        wears = [
            ("Factory New", "WearCategory0"),
            ("Minimal Wear", "WearCategory1"),
            ("Field-Tested", "WearCategory2"),
            ("Well-Worn", "WearCategory3"),
            ("Battle-Scarred", "WearCategory4"),
        ]
        item_ids = set()

        for wear_name, param in wears:
            url = f"https://steamcommunity.com/market/listings/730/AK-47%20%7C%20Cartel%20%28{wear_name.replace(' ', '%20')}%29"
            result = self.service.resolve_and_persist(url)
            self.assertEqual(result.record.exterior, wear_name)
            self.assertEqual(result.record.base_name, "AK-47 | Cartel")
            item_ids.add(result.record.item_id)

        self.assertEqual(len(item_ids), 5, "All 5 wear variants must have unique item_id primary keys")
        self.assertEqual(self.service.repository.count(), 5)

    def test_stattrak_and_ordinary_variants_remain_distinct(self) -> None:
        """StatTrak and non-StatTrak counterparts persist as separate items."""
        reg_url = "https://steamcommunity.com/market/listings/730/AK-47%20%7C%20Redline%20%28Field-Tested%29"
        st_url = "https://steamcommunity.com/market/listings/730/StatTrak%E2%84%A2%20AK-47%20%7C%20Redline%20%28Field-Tested%29"

        reg_res = self.service.resolve_and_persist(reg_url)
        st_res = self.service.resolve_and_persist(st_url)

        self.assertNotEqual(reg_res.record.item_id, st_res.record.item_id)
        self.assertFalse(reg_res.record.is_stattrak)
        self.assertTrue(st_res.record.is_stattrak)
        self.assertEqual(reg_res.record.base_name, "AK-47 | Redline")
        self.assertEqual(st_res.record.base_name, "AK-47 | Redline")
        self.assertEqual(self.service.repository.count(), 2)

    def test_souvenir_and_ordinary_variants_remain_distinct(self) -> None:
        """Souvenir and ordinary variants persist as separate items."""
        reg_url = "https://steamcommunity.com/market/listings/730/AWP%20%7C%20Desert%20Hydra%20%28Factory%20New%29"
        souv_url = "https://steamcommunity.com/market/listings/730/Souvenir%20AWP%20%7C%20Desert%20Hydra%20%28Factory%20New%29"

        reg_res = self.service.resolve_and_persist(reg_url)
        souv_res = self.service.resolve_and_persist(souv_url)

        self.assertNotEqual(reg_res.record.item_id, souv_res.record.item_id)
        self.assertFalse(reg_res.record.is_souvenir)
        self.assertTrue(souv_res.record.is_souvenir)
        self.assertEqual(self.service.repository.count(), 2)

    def test_unknown_item_nameid_preserves_valid_canonical_item_identity(self) -> None:
        """Canonical identity persists cleanly even when item_nameid is unknown."""
        url = "https://steamcommunity.com/market/listings/730/M4A4%20%7C%20Spider%20Lily%20%28Factory%20New%29"
        result = self.service.resolve_and_persist(url)

        self.assertIsNone(result.record.item_nameid)
        self.assertIsNone(result.record.item_nameid_source)
        self.assertEqual(result.record.market_hash_name, "M4A4 | Spider Lily (Factory New)")
        self.assertEqual(result.identity.resolution_status, ResolutionStatus.CANONICAL_RESOLVED)

    def test_ssr_classid_provenance_remains_independent_from_item_nameid_provenance(self) -> None:
        """SSR classid provenance and item_nameid mapping provenance are tracked independently."""
        with ItemNameIdDatabase(":memory:") as nameid_db:
            nameid_db.import_verified_dataset(VERIFIED_SEED_PATH, load_verified_seed_metadata())
            service = ItemIdentityService(self.db, nameid_db=nameid_db)

            grouped_url = "https://steamcommunity.com/market/listings/730/G1807209A023004?category_Exterior=WearCategory2&appid=730"
            ssr_data = load_grouped_ssr_fixture()
            context = SsrListingContext(source_url=grouped_url, loader_data=ssr_data)

            result = service.resolve_and_persist(grouped_url, ssr_data=context)

            # classid comes from SSR bucket
            self.assertEqual(result.record.classid, "7993038091")
            self.assertEqual(result.record.classid_source, "steam_ssr_bucket")

            # item_nameid comes from verified mapping DB
            self.assertEqual(result.record.item_nameid, 7178002)
            self.assertEqual(result.record.item_nameid_source, VERIFIED_MAPPING_REPOSITORY)
            self.assertEqual(result.record.item_nameid_revision, VERIFIED_MAPPING_REVISION)


class TestRepositoryIntegrityAndProvenance(unittest.TestCase):
    """Offline unit tests for repository round-trips, idempotence, and provenance downgrade protection."""

    def test_database_close_and_reopen_preserves_identity_and_provenance(self) -> None:
        """Persisted item identity and provenance remain intact across database close and reopen."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "reopen_test.db"

            # 1. Initialize and persist with first connection
            with ItemNameIdDatabase(":memory:") as nameid_db:
                nameid_db.import_verified_dataset(VERIFIED_SEED_PATH, load_verified_seed_metadata())
                with Database(db_path) as db1:
                    db1.initialize()
                    service1 = ItemIdentityService(db1, nameid_db=nameid_db)

                    grouped_url = "https://steamcommunity.com/market/listings/730/G1807209A023004?category_Exterior=WearCategory2&appid=730"
                    context = SsrListingContext(source_url=grouped_url, loader_data=load_grouped_ssr_fixture())
                    res1 = service1.resolve_and_persist(grouped_url, ssr_data=context)
                    item_id = res1.record.item_id
                    first_seen = res1.record.first_seen_at

            # 2. Reopen with new independent Database connection
            with Database(db_path) as db2:
                repo2 = ItemRepository(db2)
                reloaded = repo2.get_by_id(item_id)
                self.assertIsNotNone(reloaded)
                self.assertEqual(reloaded.market_hash_name, "AK-47 | Redline (Field-Tested)")
                self.assertEqual(reloaded.base_name, "AK-47 | Redline")
                self.assertEqual(reloaded.exterior, "Field-Tested")
                self.assertEqual(reloaded.group_id, "G1807209A023004")
                self.assertEqual(reloaded.item_nameid, 7178002)
                self.assertEqual(reloaded.item_nameid_source, VERIFIED_MAPPING_REPOSITORY)
                self.assertEqual(reloaded.item_nameid_revision, VERIFIED_MAPPING_REVISION)
                self.assertEqual(reloaded.classid, "7993038091")
                self.assertEqual(reloaded.classid_source, "steam_ssr_bucket")
                self.assertEqual(reloaded.first_seen_at, first_seen)

    def test_repeated_save_idempotent_no_duplicates(self) -> None:
        """Repeated persistence of the same canonical identity is idempotent."""
        db = Database(":memory:")
        db.initialize()
        service = ItemIdentityService(db)

        url = "https://steamcommunity.com/market/listings/730/AK-47%20%7C%20Redline%20%28Field-Tested%29"

        # First ingestion
        res1 = service.resolve_and_persist(url)
        item_id_1 = res1.record.item_id
        first_seen_1 = res1.record.first_seen_at

        time.sleep(0.005)

        # Second ingestion
        res2 = service.resolve_and_persist(url)
        item_id_2 = res2.record.item_id
        first_seen_2 = res2.record.first_seen_at

        self.assertEqual(item_id_1, item_id_2, "Repeated save must return the same item_id primary key")
        self.assertEqual(first_seen_1, first_seen_2, "Original first_seen_at must be preserved on update")
        self.assertEqual(service.repository.count(), 1, "Catalog count must not increase")
        db.close()

    def test_lower_trust_data_cannot_replace_trusted_mapping_provenance(self) -> None:
        """Incoming synthetic or legacy data cannot overwrite trusted mapping provenance."""
        db = Database(":memory:")
        db.initialize()
        repo = ItemRepository(db)

        # 1. Save item with verified trusted provenance
        trusted = repo.save(ItemRecord(
            market_hash_name="AK-47 | Redline (Field-Tested)",
            base_name="AK-47 | Redline",
            item_nameid=7178002,
            item_nameid_source=VERIFIED_MAPPING_REPOSITORY,
            item_nameid_revision=VERIFIED_MAPPING_REVISION,
            classid="7993038091",
            classid_source="steam_ssr_bucket",
        ))
        self.assertEqual(trusted.item_nameid, 7178002)

        # 2. Attempt upsert with SYNTHETIC_TEST_DATA
        synthetic_attempt = repo.save(ItemRecord(
            market_hash_name="AK-47 | Redline (Field-Tested)",
            base_name="AK-47 | Redline",
            item_nameid=99999999,
            item_nameid_source="SYNTHETIC_TEST_DATA",
            classid="8888888888",
            classid_source="SYNTHETIC_TEST_DATA",
        ))
        self.assertEqual(synthetic_attempt.item_nameid, 7178002, "Trusted item_nameid must not be overwritten by synthetic")
        self.assertEqual(synthetic_attempt.item_nameid_source, VERIFIED_MAPPING_REPOSITORY)
        self.assertEqual(synthetic_attempt.classid, "7993038091", "Trusted classid must not be overwritten by synthetic")
        self.assertEqual(synthetic_attempt.classid_source, "steam_ssr_bucket")

        # 3. Attempt upsert with legacy_unverified
        legacy_attempt = repo.save(ItemRecord(
            market_hash_name="AK-47 | Redline (Field-Tested)",
            base_name="AK-47 | Redline",
            item_nameid=11111111,
            item_nameid_source="legacy_unverified",
        ))
        self.assertEqual(legacy_attempt.item_nameid, 7178002, "Trusted item_nameid must not be overwritten by legacy")
        self.assertEqual(legacy_attempt.item_nameid_source, VERIFIED_MAPPING_REPOSITORY)

        db.close()

    def test_missing_provenance_does_not_erase_existing_trusted_provenance(self) -> None:
        """Upsert without item_nameid (e.g. from plain URL resolver) preserves existing trusted provenance."""
        db = Database(":memory:")
        db.initialize()
        repo = ItemRepository(db)

        # Save with verified item_nameid
        repo.save(ItemRecord(
            market_hash_name="AK-47 | Redline (Field-Tested)",
            base_name="AK-47 | Redline",
            item_nameid=7178002,
            item_nameid_source=VERIFIED_MAPPING_REPOSITORY,
            item_nameid_revision=VERIFIED_MAPPING_REVISION,
        ))

        # Re-save with item_nameid=None (as produced by plain canonical URL parser without DB)
        updated = repo.save(ItemRecord(
            market_hash_name="AK-47 | Redline (Field-Tested)",
            base_name="AK-47 | Redline",
            exterior="Field-Tested",
            item_nameid=None,
            item_nameid_source=None,
            item_nameid_revision=None,
        ))

        self.assertEqual(updated.item_nameid, 7178002, "Existing item_nameid must not be erased")
        self.assertEqual(updated.item_nameid_source, VERIFIED_MAPPING_REPOSITORY)
        self.assertEqual(updated.item_nameid_revision, VERIFIED_MAPPING_REVISION)
        db.close()

    def test_invalid_or_incomplete_identity_cannot_create_blank_canonical_records(self) -> None:
        """Blank market_hash_name or PARSED_URL_ONLY results raise errors and cannot create records."""
        db = Database(":memory:")
        db.initialize()
        repo = ItemRepository(db)

        # Blank market_hash_name in ItemRecord raises validation error
        with self.assertRaises(ValueError):
            ItemRecord(market_hash_name="", base_name="AK-47")

        with self.assertRaises(ValueError):
            ItemRecord(market_hash_name="   ", base_name="AK-47")

        # Blank base_name in ItemRecord raises validation error
        with self.assertRaises(ValueError):
            ItemRecord(market_hash_name="AK-47 | Redline (Field-Tested)", base_name="")

        # PARSED_URL_ONLY identity result cannot create ItemRecord
        incomplete_identity = ItemIdentityResult(
            appid=730,
            input_url="https://steamcommunity.com/market/listings/730/G1807209A023004",
            url_type=UrlType.GROUPED,
            group_id="G1807209A023004",
            market_hash_name="",
            base_name="",
            resolution_status=ResolutionStatus.PARSED_URL_ONLY,
            resolution_source=ResolutionSource.URL,
        )
        with self.assertRaises(ValueError):
            ItemRecord.from_identity_result(incomplete_identity)

        self.assertEqual(repo.count(), 0)
        db.close()


if __name__ == "__main__":
    unittest.main()
