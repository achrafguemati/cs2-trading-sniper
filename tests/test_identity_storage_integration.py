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

from pydantic import ValidationError

from sniper.core.identity import (
    VERIFIED_MAPPING_LICENSE,
    VERIFIED_MAPPING_RECORD_COUNT,
    VERIFIED_MAPPING_REPOSITORY,
    VERIFIED_MAPPING_REVISION,
    VERIFIED_MAPPING_SHA256,
    VERIFIED_MAPPING_SOURCE_PATH,
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
from sniper.storage.repositories import ItemRepository, normalize_item_nameid_provenance
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

    def test_missing_metadata_blocks_trusted_resolution(self) -> None:
        """A row claiming trusted source/revision fails closed if dataset_metadata is absent."""
        with ItemNameIdDatabase(":memory:") as db:
            with db._lock, db._conn:
                db._conn.execute("""
                    INSERT INTO item_mappings
                    (market_hash_name, item_nameid, item_nameid_source, item_nameid_revision,
                     classid, classid_source, classid_revision, source, updated_at)
                    VALUES ('AK-47 | Redline (Field-Tested)', 7178002, ?, ?,
                            NULL, NULL, NULL, 'manual', '2026-01-01T00:00:00Z')
                """, (VERIFIED_MAPPING_REPOSITORY, VERIFIED_MAPPING_REVISION))

            # dataset_metadata table is empty
            self.assertFalse(db.has_verified_metadata())

            # Both lookup paths must fail closed
            nameid, classid = db.lookup_item_nameid("AK-47 | Redline (Field-Tested)")
            self.assertIsNone(nameid)
            self.assertIsNone(classid)

            verified = db.lookup_verified_mapping("AK-47 | Redline (Field-Tested)")
            self.assertIsNone(verified)

            url = "https://steamcommunity.com/market/listings/730/AK-47%20%7C%20Redline%20%28Field-Tested%29"
            res = resolve_item_identity(url, db=db)
            self.assertEqual(res.resolution_status, ResolutionStatus.CANONICAL_RESOLVED)
            self.assertIsNone(res.item_nameid)

            # Diagnostic provenance inspection still returns raw record
            prov = db.lookup_item_provenance("AK-47 | Redline (Field-Tested)")
            self.assertIsNotNone(prov)
            self.assertEqual(prov["item_nameid"], 7178002)

    def test_tampered_metadata_blocks_trusted_resolution(self) -> None:
        """Any discrepancy in pinned metadata fields causes fail-closed lookup and resolution."""
        tampered_cases = [
            ("sha256", "0" * 64),
            ("record_count", 9999),
            ("upstream_repo", "https://github.com/evil/steam-market-ids"),
            ("revision", "wrong_revision"),
            ("source_path", "data/CS2/wrong_path.json"),
            ("license", "GPL-3.0"),
        ]

        valid_base = {
            "upstream_repo": VERIFIED_MAPPING_REPOSITORY,
            "revision": VERIFIED_MAPPING_REVISION,
            "retrieval_date": "2026-01-01T00:00:00Z",
            "source_path": VERIFIED_MAPPING_SOURCE_PATH,
            "license": VERIFIED_MAPPING_LICENSE,
            "sha256": VERIFIED_MAPPING_SHA256,
            "record_count": VERIFIED_MAPPING_RECORD_COUNT,
            "imported_at": "2026-01-01T00:00:00Z",
        }

        url = "https://steamcommunity.com/market/listings/730/AK-47%20%7C%20Redline%20%28Field-Tested%29"

        for field_name, bad_value in tampered_cases:
            with self.subTest(tampered_field=field_name):
                with ItemNameIdDatabase(":memory:") as db:
                    # Insert mapping row claiming trusted provenance
                    with db._lock, db._conn:
                        db._conn.execute("""
                            INSERT INTO item_mappings
                            (market_hash_name, item_nameid, item_nameid_source, item_nameid_revision,
                             classid, classid_source, classid_revision, source, updated_at)
                            VALUES ('AK-47 | Redline (Field-Tested)', 7178002, ?, ?,
                                    NULL, NULL, NULL, 'test', '2026-01-01T00:00:00Z')
                        """, (VERIFIED_MAPPING_REPOSITORY, VERIFIED_MAPPING_REVISION))

                        meta = dict(valid_base)
                        meta[field_name] = bad_value

                        db._conn.execute("""
                            INSERT INTO dataset_metadata
                            (upstream_repo, revision, retrieval_date, source_path, license, sha256, record_count, imported_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            meta["upstream_repo"],
                            meta["revision"],
                            meta["retrieval_date"],
                            meta["source_path"],
                            meta["license"],
                            meta["sha256"],
                            meta["record_count"],
                            meta["imported_at"],
                        ))

                    self.assertFalse(db.has_verified_metadata(), f"Metadata with tampered {field_name} must not be verified")

                    nameid, classid = db.lookup_item_nameid("AK-47 | Redline (Field-Tested)")
                    self.assertIsNone(nameid, f"lookup_item_nameid must return None when {field_name} is tampered")
                    self.assertIsNone(classid)

                    verified = db.lookup_verified_mapping("AK-47 | Redline (Field-Tested)")
                    self.assertIsNone(verified)

                    res = resolve_item_identity(url, db=db)
                    self.assertIsNone(res.item_nameid, f"resolve_item_identity must return item_nameid=None when {field_name} is tampered")
                    self.assertEqual(res.resolution_status, ResolutionStatus.CANONICAL_RESOLVED)


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

    def test_unknown_incoming_source_cannot_overwrite_trusted(self) -> None:
        """Incoming unknown repository source cannot overwrite an existing trusted item_nameid."""
        db = Database(":memory:")
        db.initialize()
        repo = ItemRepository(db)

        repo.save(ItemRecord(
            market_hash_name="AK-47 | Redline (Field-Tested)",
            base_name="AK-47 | Redline",
            item_nameid=7178002,
            item_nameid_source=VERIFIED_MAPPING_REPOSITORY,
            item_nameid_revision=VERIFIED_MAPPING_REVISION,
        ))

        attempt = repo.save(ItemRecord(
            market_hash_name="AK-47 | Redline (Field-Tested)",
            base_name="AK-47 | Redline",
            item_nameid=99999999,
            item_nameid_source="https://unknown-domain.com/repo",
            item_nameid_revision=VERIFIED_MAPPING_REVISION,
        ))

        self.assertEqual(attempt.item_nameid, 7178002)
        self.assertEqual(attempt.item_nameid_source, VERIFIED_MAPPING_REPOSITORY)
        self.assertEqual(attempt.item_nameid_revision, VERIFIED_MAPPING_REVISION)

        stored = repo.get_by_market_hash_name("AK-47 | Redline (Field-Tested)")
        self.assertIsNotNone(stored)
        self.assertEqual(stored.item_nameid, 7178002)
        db.close()

    def test_correct_repo_with_incorrect_or_missing_revision_cannot_overwrite_trusted(self) -> None:
        """Correct repository URL with wrong or missing revision cannot overwrite trusted item_nameid."""
        db = Database(":memory:")
        db.initialize()
        repo = ItemRepository(db)

        repo.save(ItemRecord(
            market_hash_name="AK-47 | Redline (Field-Tested)",
            base_name="AK-47 | Redline",
            item_nameid=7178002,
            item_nameid_source=VERIFIED_MAPPING_REPOSITORY,
            item_nameid_revision=VERIFIED_MAPPING_REVISION,
        ))

        # Attempt with wrong revision
        wrong_rev = repo.save(ItemRecord(
            market_hash_name="AK-47 | Redline (Field-Tested)",
            base_name="AK-47 | Redline",
            item_nameid=88888888,
            item_nameid_source=VERIFIED_MAPPING_REPOSITORY,
            item_nameid_revision="wrong_revision_hash_value",
        ))
        self.assertEqual(wrong_rev.item_nameid, 7178002)
        self.assertEqual(wrong_rev.item_nameid_revision, VERIFIED_MAPPING_REVISION)

        # Attempt with missing revision
        missing_rev = repo.save(ItemRecord(
            market_hash_name="AK-47 | Redline (Field-Tested)",
            base_name="AK-47 | Redline",
            item_nameid=77777777,
            item_nameid_source=VERIFIED_MAPPING_REPOSITORY,
            item_nameid_revision=None,
        ))
        self.assertEqual(missing_rev.item_nameid, 7178002)
        self.assertEqual(missing_rev.item_nameid_revision, VERIFIED_MAPPING_REVISION)
        db.close()

    def test_trusted_item_survives_every_untrusted_update_category(self) -> None:
        """Existing trusted mapping survives every category of untrusted or degraded update."""
        db = Database(":memory:")
        db.initialize()
        repo = ItemRepository(db)

        repo.save(ItemRecord(
            market_hash_name="AK-47 | Redline (Field-Tested)",
            base_name="AK-47 | Redline",
            item_nameid=7178002,
            item_nameid_source=VERIFIED_MAPPING_REPOSITORY,
            item_nameid_revision=VERIFIED_MAPPING_REVISION,
            classid="7993038091",
            classid_source="steam_ssr_bucket",
        ))

        untrusted_attempts = [
            (None, None, None),
            (12345, "SYNTHETIC_TEST_DATA", None),
            (23456, "legacy_unverified", None),
            (34567, "https://unknown.com", "rev1"),
            (45678, VERIFIED_MAPPING_REPOSITORY, "wrong_rev"),
            (56789, VERIFIED_MAPPING_REPOSITORY, None),
            (67890, None, VERIFIED_MAPPING_REVISION),
            (78901, "", ""),
        ]

        for bad_id, bad_source, bad_rev in untrusted_attempts:
            res = repo.save(ItemRecord(
                market_hash_name="AK-47 | Redline (Field-Tested)",
                base_name="AK-47 | Redline",
                item_nameid=bad_id,
                item_nameid_source=bad_source,
                item_nameid_revision=bad_rev,
            ))
            self.assertEqual(res.item_nameid, 7178002, f"Failed preserving for ({bad_id}, {bad_source}, {bad_rev})")
            self.assertEqual(res.item_nameid_source, VERIFIED_MAPPING_REPOSITORY)
            self.assertEqual(res.item_nameid_revision, VERIFIED_MAPPING_REVISION)

        db.close()

    def test_unknown_incoming_classid_source_cannot_overwrite_trusted_ssr(self) -> None:
        """Unknown incoming classid source cannot overwrite existing trusted SSR classid."""
        db = Database(":memory:")
        db.initialize()
        repo = ItemRepository(db)

        repo.save(ItemRecord(
            market_hash_name="AK-47 | Redline (Field-Tested)",
            base_name="AK-47 | Redline",
            classid="7993038091",
            classid_source="steam_ssr_bucket",
        ))

        attempt = repo.save(ItemRecord(
            market_hash_name="AK-47 | Redline (Field-Tested)",
            base_name="AK-47 | Redline",
            classid="9999999999",
            classid_source="unknown_custom_source",
        ))
        self.assertEqual(attempt.classid, "7993038091")
        self.assertEqual(attempt.classid_source, "steam_ssr_bucket")
        self.assertIsNone(attempt.classid_revision)
        db.close()

    def test_wrong_or_missing_classid_provenance_cannot_replace_trusted_ssr(self) -> None:
        """Wrong, synthetic, legacy, or missing classid provenance cannot replace trusted SSR provenance."""
        db = Database(":memory:")
        db.initialize()
        repo = ItemRepository(db)

        repo.save(ItemRecord(
            market_hash_name="AK-47 | Redline (Field-Tested)",
            base_name="AK-47 | Redline",
            classid="7993038091",
            classid_source="steam_ssr_bucket",
        ))

        bad_classid_attempts = [
            ("8888888888", "steam_ssr_bucket", "unexpected_rev"),
            ("7777777777", None, None),
            (None, None, None),
            ("6666666666", "SYNTHETIC_TEST_DATA", None),
            ("5555555555", "legacy_unverified", None),
        ]

        for bad_cid, bad_src, bad_rev in bad_classid_attempts:
            res = repo.save(ItemRecord(
                market_hash_name="AK-47 | Redline (Field-Tested)",
                base_name="AK-47 | Redline",
                classid=bad_cid,
                classid_source=bad_src,
                classid_revision=bad_rev,
            ))
            self.assertEqual(res.classid, "7993038091", f"Failed protecting SSR classid against ({bad_cid}, {bad_src}, {bad_rev})")
            self.assertEqual(res.classid_source, "steam_ssr_bucket")
            self.assertIsNone(res.classid_revision)

        db.close()

    def test_trusted_item_nameid_and_classid_provenance_remain_atomic(self) -> None:
        """Provenance triples (id, source, revision) for item_nameid and classid remain atomic."""
        db = Database(":memory:")
        db.initialize()
        repo = ItemRepository(db)

        repo.save(ItemRecord(
            market_hash_name="AK-47 | Redline (Field-Tested)",
            base_name="AK-47 | Redline",
            item_nameid=7178002,
            item_nameid_source=VERIFIED_MAPPING_REPOSITORY,
            item_nameid_revision=VERIFIED_MAPPING_REVISION,
            classid="7993038091",
            classid_source="steam_ssr_bucket",
        ))

        # Attempt partial change of item_nameid group
        repo.save(ItemRecord(
            market_hash_name="AK-47 | Redline (Field-Tested)",
            base_name="AK-47 | Redline",
            item_nameid=9999999,
            item_nameid_source=VERIFIED_MAPPING_REPOSITORY,
            item_nameid_revision="tampered_rev",
        ))

        stored = repo.get_by_market_hash_name("AK-47 | Redline (Field-Tested)")
        self.assertIsNotNone(stored)
        self.assertEqual(stored.item_nameid, 7178002)
        self.assertEqual(stored.item_nameid_source, VERIFIED_MAPPING_REPOSITORY)
        self.assertEqual(stored.item_nameid_revision, VERIFIED_MAPPING_REVISION)

        # Attempt partial change of classid group
        repo.save(ItemRecord(
            market_hash_name="AK-47 | Redline (Field-Tested)",
            base_name="AK-47 | Redline",
            classid="12345678",
            classid_source="steam_ssr_bucket",
            classid_revision="unwanted_rev",
        ))

        stored2 = repo.get_by_market_hash_name("AK-47 | Redline (Field-Tested)")
        self.assertIsNotNone(stored2)
        self.assertEqual(stored2.classid, "7993038091")
        self.assertEqual(stored2.classid_source, "steam_ssr_bucket")
        self.assertIsNone(stored2.classid_revision)
        db.close()

    def test_conflicting_id_claiming_pinned_source_and_revision_preserved_safely(self) -> None:
        """Conflicting IDs claiming pinned source/revision are preserved safely and log a warning."""
        db = Database(":memory:")
        db.initialize()
        repo = ItemRepository(db)

        repo.save(ItemRecord(
            market_hash_name="AK-47 | Redline (Field-Tested)",
            base_name="AK-47 | Redline",
            item_nameid=7178002,
            item_nameid_source=VERIFIED_MAPPING_REPOSITORY,
            item_nameid_revision=VERIFIED_MAPPING_REVISION,
        ))

        # Attempt to save a conflicting item_nameid with the same pinned repository and revision
        with self.assertLogs("sniper.storage.repositories", level="WARNING") as cm:
            res = repo.save(ItemRecord(
                market_hash_name="AK-47 | Redline (Field-Tested)",
                base_name="AK-47 | Redline",
                item_nameid=99999999,
                item_nameid_source=VERIFIED_MAPPING_REPOSITORY,
                item_nameid_revision=VERIFIED_MAPPING_REVISION,
            ))

        self.assertEqual(res.item_nameid, 7178002, "Existing trusted item_nameid must be preserved on conflict")
        self.assertTrue(any("conflicting item_nameid 99999999" in msg for msg in cm.output))

        stored = repo.get_by_market_hash_name("AK-47 | Redline (Field-Tested)")
        self.assertIsNotNone(stored)
        self.assertEqual(stored.item_nameid, 7178002)
        db.close()

    def test_valid_trusted_mapping_remains_available_after_all_rejected_updates(self) -> None:
        """After sequential rejected downgrade attempts, the trusted item remains fully queryable."""
        db = Database(":memory:")
        db.initialize()
        repo = ItemRepository(db)

        initial = repo.save(ItemRecord(
            market_hash_name="AK-47 | Redline (Field-Tested)",
            base_name="AK-47 | Redline",
            exterior="Field-Tested",
            is_stattrak=False,
            is_souvenir=False,
            item_nameid=7178002,
            item_nameid_source=VERIFIED_MAPPING_REPOSITORY,
            item_nameid_revision=VERIFIED_MAPPING_REVISION,
            classid="7993038091",
            classid_source="steam_ssr_bucket",
        ))

        # Sequential invalid updates
        for bad_id in (None, 999, 888, 777):
            repo.save(ItemRecord(
                market_hash_name="AK-47 | Redline (Field-Tested)",
                base_name="AK-47 | Redline",
                item_nameid=bad_id,
                item_nameid_source="untrusted",
                item_nameid_revision=None,
            ))

        # Query by ID
        by_id = repo.get_by_id(initial.item_id)
        self.assertIsNotNone(by_id)
        self.assertEqual(by_id.item_nameid, 7178002)
        self.assertEqual(by_id.classid, "7993038091")
        self.assertEqual(by_id.item_nameid_source, VERIFIED_MAPPING_REPOSITORY)
        self.assertEqual(by_id.classid_source, "steam_ssr_bucket")

        # Query by market_hash_name
        by_name = repo.get_by_market_hash_name("AK-47 | Redline (Field-Tested)")
        self.assertIsNotNone(by_name)
        self.assertEqual(by_name.item_nameid, 7178002)
        self.assertEqual(by_name.classid, "7993038091")
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


class TestNewRecordProvenanceValidation(unittest.TestCase):
    """Offline unit tests verifying strict provenance validation on new-record INSERT."""

    def setUp(self) -> None:
        self.db = Database(":memory:")
        self.db.initialize()
        self.repo = ItemRepository(self.db)

    def tearDown(self) -> None:
        self.db.close()

    def test_new_item_with_synthetic_item_nameid_normalized_to_null(self) -> None:
        """New item with synthetic item_nameid provenance is normalized to NULL."""
        saved = self.repo.save(ItemRecord(
            market_hash_name="AK-47 | Redline (Field-Tested)",
            base_name="AK-47 | Redline",
            item_nameid=99999999,
            item_nameid_source="SYNTHETIC_TEST_DATA",
            item_nameid_revision=None,
        ))
        self.assertIsNone(saved.item_nameid)
        self.assertIsNone(saved.item_nameid_source)
        self.assertIsNone(saved.item_nameid_revision)

        stored = self.repo.get_by_market_hash_name("AK-47 | Redline (Field-Tested)")
        self.assertIsNotNone(stored)
        self.assertIsNone(stored.item_nameid)

    def test_new_item_with_legacy_item_nameid_normalized_to_null(self) -> None:
        """New item with legacy item_nameid provenance is normalized to NULL."""
        saved = self.repo.save(ItemRecord(
            market_hash_name="AK-47 | Redline (Field-Tested)",
            base_name="AK-47 | Redline",
            item_nameid=11111111,
            item_nameid_source="legacy_unverified",
            item_nameid_revision=None,
        ))
        self.assertIsNone(saved.item_nameid)
        self.assertIsNone(saved.item_nameid_source)
        self.assertIsNone(saved.item_nameid_revision)

    def test_new_item_with_unknown_item_nameid_source_normalized_to_null(self) -> None:
        """New item with unknown item_nameid source is normalized to NULL."""
        saved = self.repo.save(ItemRecord(
            market_hash_name="AK-47 | Redline (Field-Tested)",
            base_name="AK-47 | Redline",
            item_nameid=12345,
            item_nameid_source="https://unknown-domain.com/repo",
            item_nameid_revision=VERIFIED_MAPPING_REVISION,
        ))
        self.assertIsNone(saved.item_nameid)
        self.assertIsNone(saved.item_nameid_source)
        self.assertIsNone(saved.item_nameid_revision)

    def test_new_item_with_correct_repo_wrong_revision_normalized_to_null(self) -> None:
        """New item with correct repository but wrong revision is normalized to NULL."""
        saved = self.repo.save(ItemRecord(
            market_hash_name="AK-47 | Redline (Field-Tested)",
            base_name="AK-47 | Redline",
            item_nameid=7178002,
            item_nameid_source=VERIFIED_MAPPING_REPOSITORY,
            item_nameid_revision="wrong_revision_hash_value",
        ))
        self.assertIsNone(saved.item_nameid)
        self.assertIsNone(saved.item_nameid_source)
        self.assertIsNone(saved.item_nameid_revision)

    def test_new_item_with_correct_repo_missing_revision_normalized_to_null(self) -> None:
        """New item with correct repository but missing revision is normalized to NULL."""
        saved = self.repo.save(ItemRecord(
            market_hash_name="AK-47 | Redline (Field-Tested)",
            base_name="AK-47 | Redline",
            item_nameid=7178002,
            item_nameid_source=VERIFIED_MAPPING_REPOSITORY,
            item_nameid_revision=None,
        ))
        self.assertIsNone(saved.item_nameid)
        self.assertIsNone(saved.item_nameid_source)
        self.assertIsNone(saved.item_nameid_revision)

    def test_new_item_with_item_nameid_missing_source_normalized_to_null(self) -> None:
        """New item with item_nameid but missing source is normalized to NULL."""
        saved = self.repo.save(ItemRecord(
            market_hash_name="AK-47 | Redline (Field-Tested)",
            base_name="AK-47 | Redline",
            item_nameid=7178002,
            item_nameid_source=None,
            item_nameid_revision=VERIFIED_MAPPING_REVISION,
        ))
        self.assertIsNone(saved.item_nameid)
        self.assertIsNone(saved.item_nameid_source)
        self.assertIsNone(saved.item_nameid_revision)

    def test_new_item_with_unknown_classid_source_normalized_to_null(self) -> None:
        """New item with unknown classid source is normalized to NULL."""
        saved = self.repo.save(ItemRecord(
            market_hash_name="AK-47 | Redline (Field-Tested)",
            base_name="AK-47 | Redline",
            classid="7993038091",
            classid_source="unknown_custom_source",
            classid_revision=None,
        ))
        self.assertIsNone(saved.classid)
        self.assertIsNone(saved.classid_source)
        self.assertIsNone(saved.classid_revision)

    def test_new_item_with_synthetic_classid_source_normalized_to_null(self) -> None:
        """New item with synthetic classid source is normalized to NULL."""
        saved = self.repo.save(ItemRecord(
            market_hash_name="AK-47 | Redline (Field-Tested)",
            base_name="AK-47 | Redline",
            classid="7993038091",
            classid_source="SYNTHETIC_TEST_DATA",
            classid_revision=None,
        ))
        self.assertIsNone(saved.classid)
        self.assertIsNone(saved.classid_source)
        self.assertIsNone(saved.classid_revision)

    def test_new_item_with_classid_missing_source_normalized_to_null(self) -> None:
        """New item with classid but missing source is normalized to NULL."""
        saved = self.repo.save(ItemRecord(
            market_hash_name="AK-47 | Redline (Field-Tested)",
            base_name="AK-47 | Redline",
            classid="7993038091",
            classid_source=None,
            classid_revision=None,
        ))
        self.assertIsNone(saved.classid)
        self.assertIsNone(saved.classid_source)
        self.assertIsNone(saved.classid_revision)

    def test_valid_pinned_item_nameid_provenance_accepted(self) -> None:
        """New item with valid pinned item_nameid provenance is accepted and persisted."""
        saved = self.repo.save(ItemRecord(
            market_hash_name="AK-47 | Redline (Field-Tested)",
            base_name="AK-47 | Redline",
            item_nameid=7178002,
            item_nameid_source=VERIFIED_MAPPING_REPOSITORY,
            item_nameid_revision=VERIFIED_MAPPING_REVISION,
        ))
        self.assertEqual(saved.item_nameid, 7178002)
        self.assertEqual(saved.item_nameid_source, VERIFIED_MAPPING_REPOSITORY)
        self.assertEqual(saved.item_nameid_revision, VERIFIED_MAPPING_REVISION)

        stored = self.repo.get_by_id(saved.item_id)
        self.assertIsNotNone(stored)
        self.assertEqual(stored.item_nameid, 7178002)
        self.assertEqual(stored.item_nameid_source, VERIFIED_MAPPING_REPOSITORY)
        self.assertEqual(stored.item_nameid_revision, VERIFIED_MAPPING_REVISION)

    def test_valid_ssr_classid_provenance_accepted(self) -> None:
        """New item with valid SSR classid provenance is accepted and persisted."""
        saved = self.repo.save(ItemRecord(
            market_hash_name="AK-47 | Redline (Field-Tested)",
            base_name="AK-47 | Redline",
            classid="7993038091",
            classid_source="steam_ssr_bucket",
            classid_revision=None,
        ))
        self.assertEqual(saved.classid, "7993038091")
        self.assertEqual(saved.classid_source, "steam_ssr_bucket")
        self.assertIsNone(saved.classid_revision)

        stored = self.repo.get_by_id(saved.item_id)
        self.assertIsNotNone(stored)
        self.assertEqual(stored.classid, "7993038091")
        self.assertEqual(stored.classid_source, "steam_ssr_bucket")
        self.assertIsNone(stored.classid_revision)

    def test_canonical_item_with_no_nameid_and_no_classid_remains_valid(self) -> None:
        """A canonical item with no item_nameid and no classid remains completely valid."""
        saved = self.repo.save(ItemRecord(
            market_hash_name="AK-47 | Redline (Field-Tested)",
            base_name="AK-47 | Redline",
            exterior="Field-Tested",
        ))
        self.assertIsNotNone(saved.item_id)
        self.assertEqual(saved.market_hash_name, "AK-47 | Redline (Field-Tested)")
        self.assertIsNone(saved.item_nameid)
        self.assertIsNone(saved.classid)

    def test_rejected_or_normalized_input_cannot_later_be_returned_as_trusted(self) -> None:
        """Normalized inputs are never stored and cannot be returned as trusted in queries."""
        self.repo.save(ItemRecord(
            market_hash_name="AK-47 | Redline (Field-Tested)",
            base_name="AK-47 | Redline",
            item_nameid=88888888,
            item_nameid_source="SYNTHETIC_TEST_DATA",
            classid="77777777",
            classid_source="unknown",
        ))
        by_name = self.repo.get_by_market_hash_name("AK-47 | Redline (Field-Tested)")
        self.assertIsNotNone(by_name)
        self.assertIsNone(by_name.item_nameid)
        self.assertIsNone(by_name.classid)
        self.assertIsNone(by_name.item_nameid_source)
        self.assertIsNone(by_name.classid_source)

    def test_service_ingestion_path_with_valid_and_unverified_provenance(self) -> None:
        """Test ItemIdentityService ingestion with verified vs unverified/synthetic DB."""
        # 1. With verified database: item_nameid is persisted
        with ItemNameIdDatabase(":memory:") as verified_db:
            verified_db.import_verified_dataset(VERIFIED_SEED_PATH, load_verified_seed_metadata())
            service1 = ItemIdentityService(self.db, nameid_db=verified_db)
            res1 = service1.resolve_and_persist(
                "https://steamcommunity.com/market/listings/730/AK-47%20%7C%20Redline%20%28Field-Tested%29"
            )
            self.assertEqual(res1.record.item_nameid, 7178002)
            self.assertEqual(res1.record.item_nameid_source, VERIFIED_MAPPING_REPOSITORY)

        # 2. With unverified database: canonical item is persisted with item_nameid=None
        db2 = Database(":memory:")
        db2.initialize()
        with ItemNameIdDatabase(":memory:") as unverified_db:
            unverified_db.import_synthetic_records([
                {"market_hash_name": "AK-47 | Redline (Field-Tested)", "item_nameid": 99999999}
            ])
            service2 = ItemIdentityService(db2, nameid_db=unverified_db)
            res2 = service2.resolve_and_persist(
                "https://steamcommunity.com/market/listings/730/AK-47%20%7C%20Redline%20%28Field-Tested%29"
            )
            self.assertIsNone(res2.record.item_nameid)
            self.assertIsNone(res2.record.item_nameid_source)
            self.assertEqual(res2.record.market_hash_name, "AK-47 | Redline (Field-Tested)")
        db2.close()


class TestItemNameIdStrictIntegerValidation(unittest.TestCase):
    """Offline unit tests proving strict integer validation for ItemRecord.item_nameid (M2.3.1.2)."""

    def setUp(self) -> None:
        self.db = Database(":memory:")
        self.db.initialize()
        self.repo = ItemRepository(self.db)

    def tearDown(self) -> None:
        self.db.close()

    def test_item_record_rejects_bool_true(self) -> None:
        """Prove that boolean True is strictly rejected by ItemRecord.item_nameid."""
        with self.assertRaises((ValidationError, ValueError)):
            ItemRecord(
                market_hash_name="AK-47 | Redline (Field-Tested)",
                base_name="AK-47 | Redline",
                item_nameid=True,  # type: ignore[arg-type]
                item_nameid_source=VERIFIED_MAPPING_REPOSITORY,
                item_nameid_revision=VERIFIED_MAPPING_REVISION,
            )

    def test_item_record_rejects_bool_false(self) -> None:
        """Prove that boolean False is strictly rejected by ItemRecord.item_nameid."""
        with self.assertRaises((ValidationError, ValueError)):
            ItemRecord(
                market_hash_name="AK-47 | Redline (Field-Tested)",
                base_name="AK-47 | Redline",
                item_nameid=False,  # type: ignore[arg-type]
            )

    def test_item_record_rejects_float_values(self) -> None:
        """Prove that integral and non-integral floats are strictly rejected."""
        with self.assertRaises((ValidationError, ValueError)):
            ItemRecord(
                market_hash_name="AK-47 | Redline (Field-Tested)",
                base_name="AK-47 | Redline",
                item_nameid=1.0,  # type: ignore[arg-type]
            )
        with self.assertRaises((ValidationError, ValueError)):
            ItemRecord(
                market_hash_name="AK-47 | Redline (Field-Tested)",
                base_name="AK-47 | Redline",
                item_nameid=7178002.5,  # type: ignore[arg-type]
            )

    def test_item_record_rejects_string_digits(self) -> None:
        """Prove that strings containing numeric digits are strictly rejected without coercion."""
        with self.assertRaises((ValidationError, ValueError)):
            ItemRecord(
                market_hash_name="AK-47 | Redline (Field-Tested)",
                base_name="AK-47 | Redline",
                item_nameid="7178002",  # type: ignore[arg-type]
            )

    def test_item_record_rejects_non_integer_composite_types(self) -> None:
        """Prove that arbitrary non-integer objects and sequences are strictly rejected."""
        for invalid_val in ([1], {"id": 1}, (1,), object()):
            with self.subTest(invalid_val=invalid_val):
                with self.assertRaises((ValidationError, ValueError)):
                    ItemRecord(
                        market_hash_name="AK-47 | Redline (Field-Tested)",
                        base_name="AK-47 | Redline",
                        item_nameid=invalid_val,  # type: ignore[arg-type]
                    )

    def test_item_record_model_validate_rejects_non_integers(self) -> None:
        """Prove that model_validate input path enforces identical strict integer rejection."""
        for bad in (True, False, 1.0, "7178002", [7178002]):
            with self.subTest(bad=bad):
                with self.assertRaises((ValidationError, ValueError)):
                    ItemRecord.model_validate({
                        "market_hash_name": "AK-47 | Redline (Field-Tested)",
                        "base_name": "AK-47 | Redline",
                        "item_nameid": bad,
                    })

    def test_repository_normalizes_zero_and_negative_to_null(self) -> None:
        """Prove that 0 and negative integers are normalized to NULL by the repository provenance policy."""
        for non_positive_id in (0, -1, -7178002):
            with self.subTest(non_positive_id=non_positive_id):
                item = ItemRecord(
                    market_hash_name=f"Test Item ({non_positive_id})",
                    base_name="Test Item",
                    item_nameid=non_positive_id,
                    item_nameid_source=VERIFIED_MAPPING_REPOSITORY,
                    item_nameid_revision=VERIFIED_MAPPING_REVISION,
                )
                saved = self.repo.save(item)
                self.assertIsNone(saved.item_nameid)
                self.assertIsNone(saved.item_nameid_source)
                self.assertIsNone(saved.item_nameid_revision)

                retrieved = self.repo.get_by_market_hash_name(f"Test Item ({non_positive_id})")
                self.assertIsNotNone(retrieved)
                self.assertIsNone(retrieved.item_nameid)
                self.assertIsNone(retrieved.item_nameid_source)
                self.assertIsNone(retrieved.item_nameid_revision)

    def test_repository_accepts_valid_positive_integer_with_pinned_provenance(self) -> None:
        """Prove that an actual positive integer with pinned repository and revision is accepted and persisted."""
        item = ItemRecord(
            market_hash_name="AK-47 | Redline (Field-Tested)",
            base_name="AK-47 | Redline",
            item_nameid=7178002,
            item_nameid_source=VERIFIED_MAPPING_REPOSITORY,
            item_nameid_revision=VERIFIED_MAPPING_REVISION,
        )
        saved = self.repo.save(item)
        self.assertEqual(saved.item_nameid, 7178002)
        self.assertEqual(saved.item_nameid_source, VERIFIED_MAPPING_REPOSITORY)
        self.assertEqual(saved.item_nameid_revision, VERIFIED_MAPPING_REVISION)

        retrieved = self.repo.get_by_market_hash_name("AK-47 | Redline (Field-Tested)")
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved.item_nameid, 7178002)
        self.assertEqual(retrieved.item_nameid_source, VERIFIED_MAPPING_REPOSITORY)
        self.assertEqual(retrieved.item_nameid_revision, VERIFIED_MAPPING_REVISION)

    def test_repository_persists_canonical_identity_with_none_item_nameid(self) -> None:
        """Prove that canonical identities with item_nameid=None still persist normally."""
        item = ItemRecord(
            market_hash_name="AWP | Asiimov (Field-Tested)",
            base_name="AWP | Asiimov",
            item_nameid=None,
        )
        saved = self.repo.save(item)
        self.assertIsNone(saved.item_nameid)
        self.assertIsNone(saved.item_nameid_source)
        self.assertIsNone(saved.item_nameid_revision)

        retrieved = self.repo.get_by_market_hash_name("AWP | Asiimov (Field-Tested)")
        self.assertIsNotNone(retrieved)
        self.assertIsNone(retrieved.item_nameid)
        self.assertEqual(retrieved.market_hash_name, "AWP | Asiimov (Field-Tested)")

    def test_normalize_item_nameid_provenance_rejects_bools_and_non_positives(self) -> None:
        """Prove normalize_item_nameid_provenance helper strictly rejects bools, 0, and negatives."""
        # Bools must fail closed even if pinned metadata is supplied
        self.assertEqual(
            normalize_item_nameid_provenance(True, VERIFIED_MAPPING_REPOSITORY, VERIFIED_MAPPING_REVISION),
            (None, None, None),
        )
        self.assertEqual(
            normalize_item_nameid_provenance(False, VERIFIED_MAPPING_REPOSITORY, VERIFIED_MAPPING_REVISION),
            (None, None, None),
        )
        # Non-positives must normalize to NULL
        self.assertEqual(
            normalize_item_nameid_provenance(0, VERIFIED_MAPPING_REPOSITORY, VERIFIED_MAPPING_REVISION),
            (None, None, None),
        )
        self.assertEqual(
            normalize_item_nameid_provenance(-100, VERIFIED_MAPPING_REPOSITORY, VERIFIED_MAPPING_REVISION),
            (None, None, None),
        )
        # Valid positive integer with pinned metadata succeeds
        self.assertEqual(
            normalize_item_nameid_provenance(7178002, VERIFIED_MAPPING_REPOSITORY, VERIFIED_MAPPING_REVISION),
            (7178002, VERIFIED_MAPPING_REPOSITORY, VERIFIED_MAPPING_REVISION),
        )


if __name__ == "__main__":
    unittest.main()
