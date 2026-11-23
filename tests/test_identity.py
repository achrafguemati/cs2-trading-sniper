"""Unit tests for Steam item identity resolver, URL/group parser, SSR bucket resolution, and offline SQLite mapping."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from urllib.parse import quote

from sniper.core.identity import (
    ItemNameIdDatabase,
    ResolutionSource,
    ResolutionStatus,
    SsrListingContext,
    SsrBucketResolutionError,
    UrlType,
    WearCategory,
    adapt_upstream_item_names,
    compose_market_hash_name,
    decompose_market_hash_name,
    parse_steam_market_url,
    resolve_grouped_ssr_bucket,
    resolve_item_identity,
)
from sniper.models.schemas import SsrBucketItem, SsrListingLoaderData

FIXTURES_DIR = Path(__file__).parent / "fixtures"
DATA_DIR = Path(__file__).parents[1] / "src" / "sniper" / "data"
VERIFIED_SEED_PATH = DATA_DIR / "seed_market_ids.json"
VERIFIED_METADATA_PATH = DATA_DIR / "seed_market_ids.metadata.json"


def verified_seed_metadata():
    return json.loads(VERIFIED_METADATA_PATH.read_text(encoding="utf-8"))


class TestItemIdentityResolver(unittest.TestCase):
    """Deterministic, zero-network unit tests for item identity resolution."""

    # -------------------------------------------------------------------------
    # 1. Canonical URL Parsing
    # -------------------------------------------------------------------------

    def test_canonical_url_parsing_plain(self):
        url = "https://steamcommunity.com/market/listings/730/AK-47 | Redline (Field-Tested)"
        res = parse_steam_market_url(url)

        self.assertEqual(res.appid, 730)
        self.assertEqual(res.url_type, UrlType.CANONICAL)
        self.assertIsNone(res.group_id)
        self.assertEqual(res.market_hash_name, "AK-47 | Redline (Field-Tested)")
        self.assertEqual(res.base_name, "AK-47 | Redline")
        self.assertEqual(res.exterior, WearCategory.FIELD_TESTED)
        self.assertFalse(res.is_stattrak)
        self.assertFalse(res.is_souvenir)
        self.assertIsNone(res.item_nameid)
        self.assertEqual(res.resolution_status, ResolutionStatus.CANONICAL_RESOLVED)
        self.assertEqual(res.resolution_source, ResolutionSource.URL)

    def test_canonical_url_parsing_percent_encoded(self):
        url = "https://steamcommunity.com/market/listings/730/AK-47%20%7C%20Redline%20%28Field-Tested%29"
        res = parse_steam_market_url(url)

        self.assertEqual(res.market_hash_name, "AK-47 | Redline (Field-Tested)")
        self.assertEqual(res.base_name, "AK-47 | Redline")
        self.assertEqual(res.exterior, WearCategory.FIELD_TESTED)
        self.assertEqual(res.resolution_status, ResolutionStatus.CANONICAL_RESOLVED)

    def test_canonical_url_percent_encoding_round_trip(self):
        original_name = "AK-47 | Redline (Field-Tested)"
        url = "https://steamcommunity.com/market/listings/730/AK-47%20%7C%20Redline%20%28Field-Tested%29"
        res = parse_steam_market_url(url)
        reconstructed_url = res.canonical_url

        self.assertIsNotNone(reconstructed_url)
        re_parsed = parse_steam_market_url(reconstructed_url)
        self.assertEqual(re_parsed.market_hash_name, original_name)
        self.assertEqual(re_parsed.exterior, WearCategory.FIELD_TESTED)

    # -------------------------------------------------------------------------
    # 2. Grouped URL Parsing (All 5 Wear Categories)
    # -------------------------------------------------------------------------

    def test_grouped_url_parsing_all_wear_categories(self):
        wear_expectations = [
            ("WearCategory0", WearCategory.FACTORY_NEW),
            ("WearCategory1", WearCategory.MINIMAL_WEAR),
            ("WearCategory2", WearCategory.FIELD_TESTED),
            ("WearCategory3", WearCategory.WELL_WORN),
            ("WearCategory4", WearCategory.BATTLE_SCARRED),
        ]

        group_token = "G1807209A023004"
        for param, expected_wear in wear_expectations:
            url = f"https://steamcommunity.com/market/listings/730/{group_token}?category_Exterior={param}&appid=730"
            res = parse_steam_market_url(url)

            self.assertEqual(res.appid, 730)
            self.assertEqual(res.url_type, UrlType.GROUPED)
            self.assertEqual(res.group_id, group_token)
            self.assertEqual(res.exterior, expected_wear)
            self.assertEqual(res.market_hash_name, "")
            self.assertEqual(res.base_name, "")
            self.assertEqual(res.resolution_status, ResolutionStatus.PARSED_URL_ONLY)
            self.assertEqual(res.resolution_source, ResolutionSource.URL)

    # -------------------------------------------------------------------------
    # 3. Missing Wear Filter on Grouped URL
    # -------------------------------------------------------------------------

    def test_grouped_url_missing_wear_filter(self):
        url = "https://steamcommunity.com/market/listings/730/G1807209A023004"
        res = parse_steam_market_url(url)

        self.assertEqual(res.url_type, UrlType.GROUPED)
        self.assertEqual(res.group_id, "G1807209A023004")
        self.assertIsNone(res.exterior)
        self.assertEqual(res.market_hash_name, "")
        self.assertEqual(res.resolution_status, ResolutionStatus.PARSED_URL_ONLY)

    def test_grouped_url_missing_wear_filter_with_appid_only(self):
        url = "https://steamcommunity.com/market/listings/730/G1807209A023004?appid=730"
        res = parse_steam_market_url(url)

        self.assertEqual(res.url_type, UrlType.GROUPED)
        self.assertIsNone(res.exterior)
        self.assertEqual(res.resolution_status, ResolutionStatus.PARSED_URL_ONLY)

    # -------------------------------------------------------------------------
    # 4. Invalid Wear Filter Rejection
    # -------------------------------------------------------------------------

    def test_invalid_wear_filter_rejection(self):
        invalid_urls = [
            "https://steamcommunity.com/market/listings/730/G1807209A023004?category_Exterior=WearCategory5&appid=730",
            "https://steamcommunity.com/market/listings/730/G1807209A023004?category_Exterior=WearCategory99&appid=730",
            "https://steamcommunity.com/market/listings/730/G1807209A023004?category_Exterior=InvalidWear&appid=730",
            "https://steamcommunity.com/market/listings/730/G1807209A023004?category_Exterior=&appid=730",
        ]
        for url in invalid_urls:
            with self.assertRaises(ValueError):
                parse_steam_market_url(url)

    # -------------------------------------------------------------------------
    # 5. Non-730 App ID Rejection
    # -------------------------------------------------------------------------

    def test_non_730_appid_in_path_rejection(self):
        url = "https://steamcommunity.com/market/listings/731/AK-47%20%7C%20Redline%20%28Field-Tested%29"
        with self.assertRaises(ValueError):
            parse_steam_market_url(url)

    def test_non_730_appid_in_query_rejection(self):
        url = "https://steamcommunity.com/market/listings/730/G1807209A023004?category_Exterior=WearCategory2&appid=570"
        with self.assertRaises(ValueError):
            parse_steam_market_url(url)

    # -------------------------------------------------------------------------
    # 6. Malformed Group Identifier Rejection
    # -------------------------------------------------------------------------

    def test_malformed_group_identifier_rejection(self):
        malformed_urls = [
            "https://steamcommunity.com/market/listings/730/G",
            "https://steamcommunity.com/market/listings/730/G!@#",
            "https://steamcommunity.com/market/listings/730/G_invalid",
            "https://steamcommunity.com/market/listings/730/G%20invalid?category_Exterior=WearCategory2&appid=730",
            "https://steamcommunity.com/market/listings/730/G1234?category_Exterior=WearCategory2&appid=730/extra",
        ]
        for url in malformed_urls:
            with self.assertRaises(ValueError):
                parse_steam_market_url(url)

    # -------------------------------------------------------------------------
    # 7. Duplicate Query Parameters Rejection
    # -------------------------------------------------------------------------

    def test_duplicate_query_parameters_rejection(self):
        dup_urls = [
            "https://steamcommunity.com/market/listings/730/G1807209A023004?category_Exterior=WearCategory2&category_Exterior=WearCategory2",
            "https://steamcommunity.com/market/listings/730/G1807209A023004?appid=730&appid=730",
            "https://steamcommunity.com/market/listings/730/G1807209A023004?category_Exterior=WearCategory1&category_Exterior=WearCategory2",
        ]
        for url in dup_urls:
            with self.assertRaises(ValueError):
                parse_steam_market_url(url)

    # -------------------------------------------------------------------------
    # 8. Strict URL Security Constraints
    # -------------------------------------------------------------------------

    def test_reject_non_https(self):
        url = "http://steamcommunity.com/market/listings/730/AK-47%20%7C%20Redline%20%28Field-Tested%29"
        with self.assertRaises(ValueError):
            parse_steam_market_url(url)

    def test_reject_wrong_host(self):
        wrong_hosts = [
            "https://evil.com/market/listings/730/AK-47%20%7C%20Redline",
            "https://steamcommunity.com.phishing.org/market/listings/730/AK-47",
            "https://steamcommunity.evil.com/market/listings/730/AK-47",
        ]
        for url in wrong_hosts:
            with self.assertRaises(ValueError):
                parse_steam_market_url(url)

    def test_reject_userinfo(self):
        url = "https://user:pass@steamcommunity.com/market/listings/730/AK-47%20%7C%20Redline"
        with self.assertRaises(ValueError):
            parse_steam_market_url(url)

    def test_reject_non_default_port(self):
        url = "https://steamcommunity.com:8443/market/listings/730/AK-47%20%7C%20Redline"
        with self.assertRaises(ValueError):
            parse_steam_market_url(url)

    # -------------------------------------------------------------------------
    # 9. StatTrak and Souvenir Item Name Parsing & Knife Stars
    # -------------------------------------------------------------------------

    def test_stattrak_item_name_parsing(self):
        name = "StatTrak™ AK-47 | Redline (Field-Tested)"
        base_name, exterior, is_st, is_souv = decompose_market_hash_name(name)

        self.assertEqual(base_name, "AK-47 | Redline")
        self.assertEqual(exterior, WearCategory.FIELD_TESTED)
        self.assertTrue(is_st)
        self.assertFalse(is_souv)

        recomposed = compose_market_hash_name(base_name, exterior, is_stattrak=True)
        self.assertEqual(recomposed, name)

    def test_souvenir_item_name_parsing(self):
        name = "Souvenir AWP | Desert Hydra (Factory New)"
        base_name, exterior, is_st, is_souv = decompose_market_hash_name(name)

        self.assertEqual(base_name, "AWP | Desert Hydra")
        self.assertEqual(exterior, WearCategory.FACTORY_NEW)
        self.assertFalse(is_st)
        self.assertTrue(is_souv)

        recomposed = compose_market_hash_name(base_name, exterior, is_souvenir=True)
        self.assertEqual(recomposed, name)

    def test_knife_with_star_prefix(self):
        name = "★ Karambit | Doppler (Factory New)"
        base_name, exterior, is_st, is_souv = decompose_market_hash_name(name)

        self.assertEqual(base_name, "★ Karambit | Doppler")
        self.assertEqual(exterior, WearCategory.FACTORY_NEW)
        self.assertFalse(is_st)
        self.assertFalse(is_souv)

        recomposed = compose_market_hash_name(base_name, exterior)
        self.assertEqual(recomposed, name)

    def test_knife_with_star_and_stattrak(self):
        name = "★ StatTrak™ M9 Bayonet | Fade (Minimal Wear)"
        base_name, exterior, is_st, is_souv = decompose_market_hash_name(name)

        self.assertEqual(base_name, "★ M9 Bayonet | Fade")
        self.assertEqual(exterior, WearCategory.MINIMAL_WEAR)
        self.assertTrue(is_st)
        self.assertFalse(is_souv)

        recomposed = compose_market_hash_name(base_name, exterior, is_stattrak=True)
        self.assertEqual(recomposed, name)

    def test_plain_vanilla_knife(self):
        name = "★ Karambit"
        base_name, exterior, is_st, is_souv = decompose_market_hash_name(name)

        self.assertEqual(base_name, "★ Karambit")
        self.assertIsNone(exterior)
        self.assertFalse(is_st)
        self.assertFalse(is_souv)

    def test_non_skin_item_no_wear(self):
        name = "Operation Phoenix Weapon Case"
        base_name, exterior, is_st, is_souv = decompose_market_hash_name(name)

        self.assertEqual(base_name, "Operation Phoenix Weapon Case")
        self.assertIsNone(exterior)
        self.assertFalse(is_st)
        self.assertFalse(is_souv)

    # -------------------------------------------------------------------------
    # 10. Collision Safety: Similar Skins Must Remain Distinct
    # -------------------------------------------------------------------------

    def test_collision_safety(self):
        item1 = parse_steam_market_url("https://steamcommunity.com/market/listings/730/AK-47%20%7C%20Redline%20%28Field-Tested%29")
        item2 = parse_steam_market_url("https://steamcommunity.com/market/listings/730/AK-47%20%7C%20Redline%20%28Well-Worn%29")
        item3 = parse_steam_market_url("https://steamcommunity.com/market/listings/730/StatTrak%E2%84%A2%20AK-47%20%7C%20Redline%20%28Field-Tested%29")
        item4 = parse_steam_market_url("https://steamcommunity.com/market/listings/730/M4A4%20%7C%20The%20Battlestar%20%28Field-Tested%29")
        item5 = parse_steam_market_url("https://steamcommunity.com/market/listings/730/M4A4%20%7C%20The%20Emperor%20%28Field-Tested%29")

        # Distinct market hash names
        names = {item.market_hash_name for item in [item1, item2, item3, item4, item5]}
        self.assertEqual(len(names), 5)

        # Different exteriors
        self.assertNotEqual(item1.exterior, item2.exterior)
        self.assertEqual(item1.base_name, item2.base_name)

        # StatTrak vs non-StatTrak
        self.assertNotEqual(item1.is_stattrak, item3.is_stattrak)
        self.assertEqual(item1.base_name, item3.base_name)

        # Distinct skin finishes
        self.assertNotEqual(item4.base_name, item5.base_name)

    # -------------------------------------------------------------------------
    # 11. Grouped SSR Bucket Resolution
    # -------------------------------------------------------------------------

    def test_ssr_bucket_resolution_from_fixture(self):
        fixture_path = FIXTURES_DIR / "grouped_ssr_data.json"
        with open(fixture_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Field-Tested resolution
        ft_bucket = resolve_grouped_ssr_bucket(data, WearCategory.FIELD_TESTED)
        self.assertEqual(ft_bucket.bucket_id, "AK-47 | Redline (Field-Tested)")
        self.assertEqual(ft_bucket.classid, "7993038091")
        self.assertEqual(ft_bucket.localized_name_inside_group, "Field-Tested")
        self.assertEqual(ft_bucket.min_price_minor, 2488)

        # Factory New resolution
        fn_bucket = resolve_grouped_ssr_bucket(data, "WearCategory0")
        self.assertEqual(fn_bucket.bucket_id, "AK-47 | Redline (Factory New)")
        self.assertEqual(fn_bucket.classid, "7993038089")
        self.assertEqual(fn_bucket.min_price_minor, 145000)

    def test_ssr_bucket_resolution_failure_missing_bucket(self):
        # Create loader data missing Battle-Scarred
        data = {
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
        with self.assertRaises(SsrBucketResolutionError):
            resolve_grouped_ssr_bucket(data, WearCategory.BATTLE_SCARRED)

    def test_ssr_bucket_resolution_failure_ambiguous_buckets(self):
        # Create loader data with duplicate buckets for same wear
        data = {
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
                },
                {
                    "bucket_id": "Test | Skin Dup (Factory New)",
                    "localized_name": "Test | Skin Dup (Factory New)",
                    "min_price": "1100",
                    "classid": "112",
                    "localized_name_inside_group": "Factory New",
                    "filters": [["Exterior", "WearCategory0"]],
                },
            ],
        }
        with self.assertRaises(SsrBucketResolutionError):
            resolve_grouped_ssr_bucket(data, WearCategory.FACTORY_NEW)

    def test_ssr_bucket_resolution_failure_unsuccessful_loader(self):
        data = {
            "success": False,
            "appid": 730,
            "buckets": [],
        }
        with self.assertRaises(ValueError):
            resolve_grouped_ssr_bucket(data, WearCategory.FACTORY_NEW)

    def test_ssr_bucket_resolution_failure_wrong_appid(self):
        data = {"success": True, "appid": 440, "buckets": []}
        with self.assertRaises(ValueError):
            resolve_grouped_ssr_bucket(data, WearCategory.FACTORY_NEW)

    def test_top_level_grouped_ssr_rejects_unsuccessful_and_wrong_appid(self):
        url = "https://steamcommunity.com/market/listings/730/G1807209A023004?category_Exterior=WearCategory2&appid=730"
        base = json.loads((FIXTURES_DIR / "grouped_ssr_data.json").read_text(encoding="utf-8"))
        for change in ({"success": False}, {"appid": 440}):
            with self.subTest(change=change):
                invalid_data = {**base, **change}
                with self.assertRaises(SsrBucketResolutionError):
                    resolve_item_identity(url, ssr_data={"source_url": url, "loader_data": invalid_data})

    def test_top_level_grouped_ssr_requires_matching_verified_context(self):
        requested = "https://steamcommunity.com/market/listings/730/G1807209A023004?category_Exterior=WearCategory2&appid=730"
        mismatched = "https://steamcommunity.com/market/listings/730/G999999?category_Exterior=WearCategory2&appid=730"
        loader = json.loads((FIXTURES_DIR / "grouped_ssr_data.json").read_text(encoding="utf-8"))

        with self.assertRaises(SsrBucketResolutionError):
            resolve_item_identity(requested, ssr_data=loader)
        with self.assertRaises(SsrBucketResolutionError):
            resolve_item_identity(requested, ssr_data={"source_url": mismatched, "loader_data": loader})

        valid = resolve_item_identity(requested, ssr_data={"source_url": requested, "loader_data": loader})
        self.assertEqual(valid.group_id, "G1807209A023004")
        self.assertEqual(valid.market_hash_name, "AK-47 | Redline (Field-Tested)")
        self.assertEqual(valid.classid_source, "steam_ssr_bucket")

    def test_initial_selected_bucket_missing_or_duplicated_fails_closed(self):
        url = "https://steamcommunity.com/market/listings/730/G1807209A023004?appid=730"
        base = json.loads((FIXTURES_DIR / "grouped_ssr_data.json").read_text(encoding="utf-8"))
        missing = {**base, "initialSelectedBucketID": "AK-47 | Missing (Field-Tested)"}
        with self.assertRaises(SsrBucketResolutionError):
            resolve_item_identity(url, ssr_data={"source_url": url, "loader_data": missing})

        selected = next(bucket for bucket in base["buckets"] if bucket["bucket_id"] == base["initialSelectedBucketID"])
        duplicate = {**base, "buckets": [*base["buckets"], dict(selected)]}
        with self.assertRaises(SsrBucketResolutionError):
            resolve_item_identity(url, ssr_data={"source_url": url, "loader_data": duplicate})

    def test_canonical_ssr_enrichment_rejects_invalid_context(self):
        url = "https://steamcommunity.com/market/listings/730/AK-47%20%7C%20Redline%20%28Field-Tested%29"
        base = json.loads((FIXTURES_DIR / "grouped_ssr_data.json").read_text(encoding="utf-8"))
        for change in ({"success": False}, {"appid": 440}):
            with self.subTest(change=change), self.assertRaises(SsrBucketResolutionError):
                resolve_item_identity(url, ssr_data={**base, **change})

        resolved = resolve_item_identity(url, ssr_data=base)
        self.assertEqual(resolved.classid, "7993038091")
        self.assertEqual(resolved.classid_source, "steam_ssr_bucket")

    # -------------------------------------------------------------------------
    # 12. Offline SQLite Database Import & Item NameID Resolution
    # -------------------------------------------------------------------------

    def test_synthetic_import_and_lookup_is_never_verified(self):
        with ItemNameIdDatabase(":memory:") as db:
            records = [
                {
                    "market_hash_name": "AK-47 | Redline (Field-Tested)",
                    "item_nameid": 900000001,
                    "classid": "7993038091",
                },
                {
                    "market_hash_name": "AK-47 | Redline (Minimal Wear)",
                    "item_nameid": 900000002,
                    "classid": "7993038090",
                },
            ]

            count = db.import_synthetic_records(records)
            self.assertEqual(count, 2)

            # Strict trust policy: lookup_item_nameid rejects SYNTHETIC_TEST_DATA as production ID
            nameid, classid = db.lookup_item_nameid("AK-47 | Redline (Field-Tested)")
            self.assertIsNone(nameid, "Synthetic records must never be returned as verified item_nameid")
            self.assertIsNone(classid, "Synthetic classid must never be returned as verified classid")

            # Lookup unknown item also returns None
            unknown_nameid, unknown_classid = db.lookup_item_nameid("AK-47 | Redline (Factory New)")
            self.assertIsNone(unknown_nameid)
            self.assertIsNone(unknown_classid)

            # Diagnostic inspection via lookup_item_provenance is preserved
            provenance = db.lookup_item_provenance("AK-47 | Redline (Field-Tested)")
            self.assertIsNotNone(provenance)
            self.assertEqual(provenance["item_nameid"], 900000001)
            self.assertEqual(provenance["item_nameid_source"], "SYNTHETIC_TEST_DATA")
            self.assertEqual(provenance["classid_source"], "SYNTHETIC_TEST_DATA")
            self.assertEqual(db.get_metadata(), [])

            # resolve_item_identity must fail closed on synthetic records
            url = "https://steamcommunity.com/market/listings/730/AK-47%20%7C%20Redline%20%28Field-Tested%29"
            res = resolve_item_identity(url, db=db)
            self.assertIsNone(res.item_nameid, "Synthetic ID must not produce ITEM_NAMEID_RESOLVED")
            self.assertIsNone(res.classid, "Synthetic classid must not leak into identity result")
            self.assertEqual(res.resolution_status, ResolutionStatus.CANONICAL_RESOLVED)

    def test_verified_upstream_dataset_import_ids_and_provenance(self):
        with ItemNameIdDatabase(":memory:") as db:
            count = db.import_verified_dataset(VERIFIED_SEED_PATH, verified_seed_metadata())
            self.assertEqual(count, 26935)

            expected = {
                "AK-47 | Redline (Field-Tested)": 7178002,
                "AK-47 | Redline (Minimal Wear)": 7179644,
                "AK-47 | Redline (Well-Worn)": 7181849,
                "AK-47 | Redline (Battle-Scarred)": 7179463,
                "StatTrak™ AK-47 | Redline (Field-Tested)": 7180207,
            }
            for name, expected_id in expected.items():
                nameid, classid = db.lookup_item_nameid(name)
                self.assertEqual(nameid, expected_id, name)
                self.assertIsNone(classid, "The item_names upstream does not provide classid.")
                url = f"https://steamcommunity.com/market/listings/730/{quote(name, safe='')}"
                resolved = resolve_item_identity(url, db=db)
                self.assertEqual(resolved.item_nameid, expected_id, name)
                self.assertEqual(resolved.item_nameid_source, "https://github.com/somespecialone/steam-market-ids")

            missing_nameid, missing_classid = db.lookup_item_nameid("AK-47 | Redline (Factory New)")
            self.assertIsNone(missing_nameid)
            self.assertIsNone(missing_classid)
            unresolved = resolve_item_identity(
                "https://steamcommunity.com/market/listings/730/AK-47%20%7C%20Redline%20%28Factory%20New%29",
                db=db,
            )
            self.assertIsNone(unresolved.item_nameid)

            provenance = db.lookup_item_provenance("AK-47 | Redline (Field-Tested)")
            self.assertEqual(provenance["item_nameid_source"], "https://github.com/somespecialone/steam-market-ids")
            self.assertEqual(provenance["item_nameid_revision"], "caac34bd8a28578e800501da5cc9f16c7cb3fa67")
            self.assertIsNone(provenance["classid_source"])
            self.assertIsNone(provenance["classid_revision"])

            metadata = db.get_metadata()
            self.assertEqual(len(metadata), 1)
            self.assertEqual(metadata[0].record_count, count)
            self.assertEqual(metadata[0].source_path, "data/CS2/item_names.json")
            self.assertEqual(metadata[0].license, "WTFPL")
            self.assertEqual(metadata[0].retrieval_timestamp, verified_seed_metadata()["retrieval_timestamp"])

    def test_upstream_object_adapter_preserves_exact_keys_without_classid(self):
        mapping = json.loads((FIXTURES_DIR / "seed_market_ids.json").read_text(encoding="utf-8"))
        records = adapt_upstream_item_names(mapping)
        self.assertEqual([record["market_hash_name"] for record in records], list(mapping))
        self.assertEqual(records[0]["market_hash_name"], "ak-47 | test item (Field-Tested)")
        self.assertEqual(records[2]["market_hash_name"], "StatTrak™ AK-47 | Test Item (Minimal Wear)")
        self.assertEqual(records[4]["market_hash_name"], "★ Karambit | Test Item (Well-Worn)")
        self.assertTrue(all("classid" not in record for record in records))

    def test_upstream_adapter_rejects_invalid_ids_and_non_object_payloads(self):
        for invalid_id in (None, 0, -1, True, 1.5, "123"):
            with self.subTest(invalid_id=invalid_id):
                with self.assertRaises(ValueError):
                    adapt_upstream_item_names({"AK-47 | Test": invalid_id})
        with self.assertRaises(ValueError):
            adapt_upstream_item_names([{"market_hash_name": "AK-47 | Test", "item_nameid": 1}])

    def test_verified_import_rejects_synthetic_in_memory_records(self):
        with ItemNameIdDatabase(":memory:") as db:
            with self.assertRaises(TypeError):
                db.import_verified_dataset(
                    [{"market_hash_name": "AK-47 | Test", "item_nameid": 123}],
                    verified_seed_metadata(),
                )

    def test_verified_import_rejects_fake_metadata_and_count_mismatches(self):
        valid = verified_seed_metadata()
        invalid_updates = (
            {"sha256": ""},
            {"sha256": "a" * 64},
            {"revision": "main"},
            {"source_path": "data/other.json"},
            {"license": "MIT"},
            {"record_count": valid["record_count"] - 1},
            {"retrieval_timestamp": "2026-11-22"},
        )
        for update in invalid_updates:
            with self.subTest(update=update), ItemNameIdDatabase(":memory:") as db:
                invalid = {**valid, **update}
                with self.assertRaises(ValueError):
                    db.import_verified_dataset(VERIFIED_SEED_PATH, invalid)

    def test_verified_import_rejects_file_byte_hash_mismatch(self):
        valid = verified_seed_metadata()
        with tempfile.TemporaryDirectory() as tmpdir:
            tampered_path = Path(tmpdir) / "item_names.json"
            tampered_path.write_bytes(VERIFIED_SEED_PATH.read_bytes() + b" ")
            with ItemNameIdDatabase(":memory:") as db:
                with self.assertRaises(ValueError):
                    db.import_verified_dataset(tampered_path, valid)

    # -------------------------------------------------------------------------
    # 13. Top-Level End-to-End Identity Resolution
    # -------------------------------------------------------------------------

    def test_top_level_resolution_canonical_with_db(self):
        with ItemNameIdDatabase(":memory:") as db:
            db.import_verified_dataset(VERIFIED_SEED_PATH, verified_seed_metadata())

            url = "https://steamcommunity.com/market/listings/730/AK-47%20%7C%20Redline%20%28Field-Tested%29"
            res = resolve_item_identity(url, db=db)

            self.assertEqual(res.resolution_status, ResolutionStatus.ITEM_NAMEID_RESOLVED)
            self.assertEqual(res.resolution_source, ResolutionSource.OFFLINE_DB)
            self.assertEqual(res.item_nameid, 7178002)
            self.assertEqual(res.item_nameid_source, "https://github.com/somespecialone/steam-market-ids")
            self.assertEqual(res.item_nameid_revision, "caac34bd8a28578e800501da5cc9f16c7cb3fa67")
            self.assertIsNone(res.classid)

    def test_top_level_resolution_grouped_with_ssr_and_db(self):
        with open(FIXTURES_DIR / "grouped_ssr_data.json", "r", encoding="utf-8") as f:
            ssr_data = json.load(f)

        with ItemNameIdDatabase(":memory:") as db:
            db.import_verified_dataset(VERIFIED_SEED_PATH, verified_seed_metadata())

            grouped_url = "https://steamcommunity.com/market/listings/730/G1807209A023004?category_Exterior=WearCategory2&appid=730"
            context = SsrListingContext(source_url=grouped_url, loader_data=ssr_data)
            res = resolve_item_identity(grouped_url, ssr_data=context, db=db)

            self.assertEqual(res.resolution_status, ResolutionStatus.ITEM_NAMEID_RESOLVED)
            self.assertEqual(res.resolution_source, ResolutionSource.COMBINED)
            self.assertEqual(res.group_id, "G1807209A023004")
            self.assertEqual(res.market_hash_name, "AK-47 | Redline (Field-Tested)")
            self.assertEqual(res.base_name, "AK-47 | Redline")
            self.assertEqual(res.exterior, WearCategory.FIELD_TESTED)
            self.assertEqual(res.item_nameid, 7178002)
            self.assertEqual(res.item_nameid_source, "https://github.com/somespecialone/steam-market-ids")
            self.assertEqual(res.item_nameid_revision, "caac34bd8a28578e800501da5cc9f16c7cb3fa67")
            self.assertEqual(res.classid, "7993038091")
            self.assertEqual(res.classid_source, "steam_ssr_bucket")

    def test_unknown_item_nameid_returns_none_with_canonical_resolved(self):
        with ItemNameIdDatabase(":memory:") as db:
            # DB is empty
            url = "https://steamcommunity.com/market/listings/730/AK-47%20%7C%20Redline%20%28Field-Tested%29"
            res = resolve_item_identity(url, db=db)

            # Must return None for item_nameid and retain CANONICAL_RESOLVED status
            self.assertIsNone(res.item_nameid)
            self.assertEqual(res.resolution_status, ResolutionStatus.CANONICAL_RESOLVED)
            self.assertEqual(res.market_hash_name, "AK-47 | Redline (Field-Tested)")


if __name__ == "__main__":
    unittest.main()
