"""Comprehensive deterministic tests for inventory ingestion and manual importers."""

from __future__ import annotations

import json
from pathlib import Path
import unittest

import httpx

from sniper.adapters.inventory import ManualInventoryImporter, PublicInventoryClient, validate_steamid64
from sniper.core.http_client import ReadOnlyHttpClient
from sniper.core.rate_limiter import RateLimiterConfig, SequentialRateLimiter
from sniper.storage.database import Database
from sniper.storage.models import ItemRecord
from sniper.storage.repositories import CurrencyRepository, InventoryRepository, ItemRepository


class TestInventoryIngestion(unittest.IsolatedAsyncioTestCase):
    """Test suite for public Steam inventory fetching and manual CSV/JSON importing."""

    def setUp(self) -> None:
        self.db = Database(":memory:")
        self.db.initialize()
        self.curr_repo = CurrencyRepository(self.db)
        self.curr_repo.seed_default_currencies()
        self.item_repo = ItemRepository(self.db)
        self.inv_repo = InventoryRepository(self.db)

        # Seed sample catalog items
        self.item_repo.save(ItemRecord(
            market_hash_name="AK-47 | Redline (Field-Tested)",
            base_name="AK-47 | Redline",
            exterior="Field-Tested",
        ))

        # Load fixture
        fixture_path = Path(__file__).parent / "fixtures" / "public_inventory.json"
        with open(fixture_path, "r", encoding="utf-8") as f:
            self.fixture_data = json.load(f)

    def tearDown(self) -> None:
        self.db.close()

    def make_test_http_client(self, transport: httpx.AsyncBaseTransport) -> ReadOnlyHttpClient:
        limiter = SequentialRateLimiter(
            config=RateLimiterConfig(
                requests_per_minute=60000.0,
                capacity=100.0,
                min_delay_seconds=0.0,
                max_delay_seconds=0.0,
                jitter_max_seconds=0.0,
            )
        )
        return ReadOnlyHttpClient(rate_limiter=limiter, transport=transport)

    def test_steamid64_validation(self) -> None:
        """Verify strict 17-digit SteamID64 validation."""
        valid_id = "76561198000000000"
        self.assertEqual(validate_steamid64(valid_id), valid_id)
        self.assertEqual(validate_steamid64(f"  {valid_id}  "), valid_id)

        # Rejections
        invalid_ids = [
            "",
            "12345",
            "7656119800000000",   # 16 digits
            "765611980000000000", # 18 digits
            "76561188000000000",  # wrong prefix (7656118)
            "steam_custom_url",
            "7656119abcdefghij",
        ]
        for bad_id in invalid_ids:
            with self.subTest(bad_id=bad_id):
                with self.assertRaises(ValueError):
                    validate_steamid64(bad_id)

    async def test_public_inventory_fetch_and_import(self) -> None:
        """Verify fetching and importing public inventory via mock HTTP transport."""
        mock_handler = httpx.MockTransport(
            lambda req: httpx.Response(200, json=self.fixture_data)
        )
        http_client = self.make_test_http_client(transport=mock_handler)
        client = PublicInventoryClient(http_client=http_client, db=self.db)

        snapshot, items = await client.fetch_and_import("76561198000000000")
        self.assertIsNotNone(snapshot.snapshot_id)
        self.assertEqual(snapshot.owner_id, "76561198000000000")
        self.assertEqual(snapshot.total_count, 2)
        self.assertEqual(len(items), 2)

        # Item 1: Marketable
        it1 = items[0]
        self.assertEqual(it1.asset_id, "39102948102")
        self.assertEqual(it1.class_id, "7993038091")
        self.assertEqual(it1.market_hash_name, "AK-47 | Redline (Field-Tested)")
        self.assertTrue(it1.is_marketable)
        self.assertTrue(it1.is_tradable)
        self.assertEqual(it1.market_restriction_days, 0)
        self.assertIsNotNone(it1.item_id, "AK-47 Redline should link to catalog item_id")

        # Item 2: Restricted
        it2 = items[1]
        self.assertEqual(it2.asset_id, "39102948103")
        self.assertEqual(it2.class_id, "7993038090")
        self.assertEqual(it2.market_hash_name, "AK-47 | Redline (Minimal Wear)")
        self.assertFalse(it2.is_marketable)
        self.assertFalse(it2.is_tradable)
        self.assertEqual(it2.market_restriction_days, 7)

    async def test_public_inventory_pagination(self) -> None:
        """Verify pagination handles more_items and last_assetid bounds."""
        call_count = 0

        def handler(req: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(200, json={
                    "success": 1,
                    "more_items": 1,
                    "last_assetid": "asset_page1",
                    "assets": [{"appid": 730, "contextid": "2", "assetid": "1", "classid": "100", "instanceid": "0", "amount": "1"}],
                    "descriptions": [{"appid": 730, "classid": "100", "instanceid": "0", "name": "Item A", "market_hash_name": "Item A", "marketable": 1, "tradable": 1}],
                })
            else:
                return httpx.Response(200, json={
                    "success": 1,
                    "more_items": 0,
                    "assets": [{"appid": 730, "contextid": "2", "assetid": "2", "classid": "200", "instanceid": "0", "amount": "1"}],
                    "descriptions": [{"appid": 730, "classid": "200", "instanceid": "0", "name": "Item B", "market_hash_name": "Item B", "marketable": 1, "tradable": 1}],
                })

        http_client = self.make_test_http_client(transport=httpx.MockTransport(handler))
        client = PublicInventoryClient(http_client=http_client, db=self.db)

        snapshot, items = await client.fetch_and_import("76561198000000000", max_pages=5)
        self.assertEqual(call_count, 2)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].market_hash_name, "Item A")
        self.assertEqual(items[1].market_hash_name, "Item B")

    async def test_public_inventory_unsuccessful_response(self) -> None:
        """Verify graceful handling when Steam returns success=0 (e.g. private profile)."""
        mock_handler = httpx.MockTransport(
            lambda req: httpx.Response(200, json={"success": 0, "error": "Profile is private"})
        )
        http_client = self.make_test_http_client(transport=mock_handler)
        client = PublicInventoryClient(http_client=http_client, db=self.db)

        snapshot, items = await client.fetch_and_import("76561198000000000")
        self.assertEqual(len(items), 0)
        self.assertEqual(snapshot.total_count, 0)

    def test_manual_csv_import_success_and_synonyms(self) -> None:
        """Verify manual CSV import supporting header synonyms."""
        csv_data = """item_name,quantity,marketable,tradable,restriction_days
AWP | Asiimov (Field-Tested),2,true,true,0
Desert Eagle | Printstream (Factory New),1,1,0,7
"""
        snapshot, items, errors = ManualInventoryImporter.import_csv(
            csv_content=csv_data,
            db=self.db,
            owner_id="test_user",
        )
        self.assertEqual(errors, [])
        self.assertEqual(snapshot.source, "manual_csv")
        self.assertEqual(len(items), 2)

        it1 = items[0]
        self.assertEqual(it1.market_hash_name, "AWP | Asiimov (Field-Tested)")
        self.assertEqual(it1.amount, 2)
        self.assertTrue(it1.is_marketable)
        self.assertTrue(it1.is_tradable)
        self.assertEqual(it1.market_restriction_days, 0)

        it2 = items[1]
        self.assertEqual(it2.market_hash_name, "Desert Eagle | Printstream (Factory New)")
        self.assertEqual(it2.amount, 1)
        self.assertTrue(it2.is_marketable)
        self.assertFalse(it2.is_tradable)
        self.assertEqual(it2.market_restriction_days, 7)

    def test_manual_csv_import_row_level_validation(self) -> None:
        """Verify row-level error reporting with exact line numbers in CSV import."""
        csv_data = """market_hash_name,amount
Valid Item A,1
,2
Valid Item B,not_an_int
Valid Item C,-5
"""
        snapshot, items, errors = ManualInventoryImporter.import_csv(
            csv_content=csv_data,
            db=self.db,
        )
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].market_hash_name, "Valid Item A")
        self.assertEqual(len(errors), 3)
        self.assertIn("Row 3", errors[0])  # Missing name
        self.assertIn("Row 4", errors[1])  # Non-integer amount
        self.assertIn("Row 5", errors[2])  # Negative amount

    def test_manual_json_import_array_format(self) -> None:
        """Verify manual JSON import from top-level array."""
        json_data = json.dumps([
            {"market_hash_name": "Glock-18 | Water Elemental (Field-Tested)", "amount": 1, "is_marketable": True},
            {"market_hash_name": "USP-S | Kill Confirmed (Minimal Wear)", "amount": 3, "is_marketable": False},
        ])
        snapshot, items, errors = ManualInventoryImporter.import_json(
            json_content=json_data,
            db=self.db,
            owner_id="json_user",
        )
        self.assertEqual(errors, [])
        self.assertEqual(snapshot.source, "manual_json")
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].market_hash_name, "Glock-18 | Water Elemental (Field-Tested)")
        self.assertEqual(items[1].amount, 3)

    def test_manual_json_import_dict_wrapper_and_validation(self) -> None:
        """Verify manual JSON import from dict wrapper with validation errors."""
        json_data = json.dumps({
            "items": [
                {"market_hash_name": "Valid Item", "amount": 1},
                {"market_hash_name": "", "amount": 1},
                {"market_hash_name": "Invalid Amount", "amount": 0},
                "not_a_dict",
            ]
        })
        snapshot, items, errors = ManualInventoryImporter.import_json(
            json_content=json_data,
            db=self.db,
        )
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].market_hash_name, "Valid Item")
        self.assertEqual(len(errors), 3)
        self.assertIn("Item #2", errors[0])
        self.assertIn("Item #3", errors[1])
        self.assertIn("Item #4", errors[2])

    def test_manual_json_malformed_input(self) -> None:
        """Verify malformed JSON input returns clean error without crashing."""
        snapshot, items, errors = ManualInventoryImporter.import_json(
            json_content="INVALID JSON CONTENT {{{",
            db=self.db,
        )
        self.assertEqual(len(items), 0)
        self.assertTrue(len(errors) > 0)
        self.assertIn("Malformed JSON", errors[0])


if __name__ == "__main__":
    unittest.main()
