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

    async def test_public_inventory_exact_description_matching(self) -> None:
        """Verify descriptions are correlated exclusively by exact (classid, instanceid) tuple."""
        # Two descriptions sharing classid "799" but with different instanceids and names:
        payload = {
            "success": 1,
            "more_items": 0,
            "assets": [
                {"appid": 730, "contextid": "2", "assetid": "1001", "classid": "799", "instanceid": "1", "amount": "1"},
                {"appid": 730, "contextid": "2", "assetid": "1002", "classid": "799", "instanceid": "2", "amount": "1"},
                {"appid": 730, "contextid": "2", "assetid": "1003", "classid": "799", "instanceid": "3", "amount": "1"},
            ],
            "descriptions": [
                {
                    "appid": 730,
                    "classid": "799",
                    "instanceid": "1",
                    "name": "M4A4 | Howl",
                    "market_hash_name": "M4A4 | Howl (Field-Tested)",
                    "marketable": 1,
                    "tradable": 1,
                },
                {
                    "appid": 730,
                    "classid": "799",
                    "instanceid": "2",
                    "name": "M4A4 | Howl",
                    "market_hash_name": "M4A4 | Howl (Factory New)",
                    "marketable": 1,
                    "tradable": 1,
                },
            ],
        }
        mock_handler = httpx.MockTransport(lambda req: httpx.Response(200, json=payload))
        http_client = self.make_test_http_client(transport=mock_handler)
        client = PublicInventoryClient(http_client=http_client, db=self.db)

        snapshot, items = await client.fetch_and_import("76561198000000000")
        self.assertEqual(len(items), 3)

        # Asset 1001 matches instanceid 1
        self.assertEqual(items[0].market_hash_name, "M4A4 | Howl (Field-Tested)")
        self.assertTrue(items[0].is_marketable)

        # Asset 1002 matches instanceid 2
        self.assertEqual(items[1].market_hash_name, "M4A4 | Howl (Factory New)")
        self.assertTrue(items[1].is_marketable)

        # Asset 1003 has no matching instanceid: MUST NOT substitute another description sharing classid 799
        self.assertIn("Unknown Item (classid=799, instanceid=3)", items[2].market_hash_name)
        self.assertFalse(items[2].is_marketable)
        self.assertFalse(items[2].is_tradable)

    async def test_public_inventory_unsuccessful_response(self) -> None:
        """Verify fail-closed error when Steam returns success=0 (e.g. private profile)."""
        from sniper.adapters.inventory import PublicInventoryError

        mock_handler = httpx.MockTransport(
            lambda req: httpx.Response(200, json={"success": 0, "error": "Profile is private"})
        )
        http_client = self.make_test_http_client(transport=mock_handler)
        client = PublicInventoryClient(http_client=http_client, db=self.db)

        with self.assertRaises(PublicInventoryError) as ctx:
            await client.fetch_and_import("76561198000000000")
        self.assertIn("unsuccessful response", str(ctx.exception))

        # Verify no snapshot was saved:
        count = self.db.fetch_one("SELECT COUNT(*) FROM inventory_snapshots")[0]
        self.assertEqual(count, 0)

    async def test_public_inventory_failed_later_page_fails_closed(self) -> None:
        """Verify that a failure on page 2 aborts and does not persist partial page 1 items."""
        from sniper.adapters.inventory import PublicInventoryError

        call_count = 0

        def handler(req: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(200, json={
                    "success": 1,
                    "more_items": 1,
                    "last_assetid": "cur1",
                    "assets": [{"appid": 730, "contextid": "2", "assetid": "1", "classid": "10", "instanceid": "0", "amount": "1"}],
                    "descriptions": [{"appid": 730, "classid": "10", "instanceid": "0", "name": "Item 1", "market_hash_name": "Item 1", "marketable": 1, "tradable": 1}],
                })
            return httpx.Response(200, json={"success": 0})

        http_client = self.make_test_http_client(transport=httpx.MockTransport(handler))
        client = PublicInventoryClient(http_client=http_client, db=self.db)

        with self.assertRaises(PublicInventoryError) as ctx:
            await client.fetch_and_import("76561198000000000", max_pages=5)
        self.assertIn("page 2", str(ctx.exception))

        # Zero snapshots saved in database:
        count = self.db.fetch_one("SELECT COUNT(*) FROM inventory_snapshots")[0]
        self.assertEqual(count, 0)

    async def test_public_inventory_incomplete_limit_and_cursor_errors(self) -> None:
        """Verify incomplete pagination due to max_pages, max_items, or bad cursor fails closed."""
        from sniper.adapters.inventory import PublicInventoryError

        # 1. max_pages reached while more_items is true
        handler1 = httpx.MockTransport(lambda req: httpx.Response(200, json={
            "success": 1,
            "more_items": 1,
            "last_assetid": "cur1",
            "assets": [{"appid": 730, "contextid": "2", "assetid": "1", "classid": "10", "instanceid": "0", "amount": "1"}],
            "descriptions": [{"appid": 730, "classid": "10", "instanceid": "0", "name": "Item 1", "market_hash_name": "Item 1", "marketable": 1, "tradable": 1}],
        }))
        client1 = PublicInventoryClient(http_client=self.make_test_http_client(handler1), db=self.db)
        with self.assertRaises(PublicInventoryError) as ctx:
            await client1.fetch_and_import("76561198000000000", max_pages=1)
        self.assertIn("Pagination page limit reached", str(ctx.exception))

        # 2. Missing cursor while more_items is true
        handler2 = httpx.MockTransport(lambda req: httpx.Response(200, json={
            "success": 1,
            "more_items": 1,
            "last_assetid": None,
            "assets": [{"appid": 730, "contextid": "2", "assetid": "1", "classid": "10", "instanceid": "0", "amount": "1"}],
            "descriptions": [],
        }))
        client2 = PublicInventoryClient(http_client=self.make_test_http_client(handler2), db=self.db)
        with self.assertRaises(PublicInventoryError) as ctx:
            await client2.fetch_and_import("76561198000000000")
        self.assertIn("Missing last_assetid", str(ctx.exception))

        # 3. Repeated cursor while more_items is true
        calls = 0
        def repeat_handler(req: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(200, json={
                "success": 1,
                "more_items": 1,
                "last_assetid": "same_cursor",
                "assets": [{"appid": 730, "contextid": "2", "assetid": str(calls), "classid": "10", "instanceid": "0", "amount": "1"}],
                "descriptions": [],
            })
        client3 = PublicInventoryClient(http_client=self.make_test_http_client(httpx.MockTransport(repeat_handler)), db=self.db)
        with self.assertRaises(PublicInventoryError) as ctx:
            await client3.fetch_and_import("76561198000000000", max_pages=5)
        self.assertIn("Repeated last_assetid", str(ctx.exception))

    async def test_public_inventory_legitimately_empty(self) -> None:
        """Verify legitimately empty public inventory creates an empty snapshot."""
        mock_handler = httpx.MockTransport(lambda req: httpx.Response(200, json={
            "success": 1,
            "more_items": 0,
            "assets": [],
            "descriptions": [],
        }))
        client = PublicInventoryClient(http_client=self.make_test_http_client(mock_handler), db=self.db)
        snapshot, items = await client.fetch_and_import("76561198000000000")
        self.assertEqual(len(items), 0)
        self.assertEqual(snapshot.total_count, 0)
        self.assertIsNotNone(snapshot.snapshot_id)

    async def test_public_inventory_parameter_bounds(self) -> None:
        """Verify bounds validation on count, max_pages, and max_items."""
        client = PublicInventoryClient(http_client=self.make_test_http_client(httpx.MockTransport(lambda r: httpx.Response(200))), db=self.db)
        with self.assertRaises(ValueError):
            await client.fetch_and_import("76561198000000000", count=0)
        with self.assertRaises(ValueError):
            await client.fetch_and_import("76561198000000000", count=6000)
        with self.assertRaises(ValueError):
            await client.fetch_and_import("76561198000000000", max_pages=0)
        with self.assertRaises(ValueError):
            await client.fetch_and_import("76561198000000000", max_pages=100)
        with self.assertRaises(ValueError):
            await client.fetch_and_import("76561198000000000", max_items=0)

    async def test_public_inventory_existing_snapshots_unchanged_on_failure(self) -> None:
        """Verify database's existing snapshots are unchanged after failed import."""
        from sniper.adapters.inventory import PublicInventoryError
        from sniper.storage.models import InventoryItemRecord

        # Seed an existing snapshot
        snap, _ = self.inv_repo.import_snapshot(
            items=[InventoryItemRecord(snapshot_id=0, asset_id="123", class_id="7993038091", market_hash_name="AK-47 | Redline (Field-Tested)")],
            owner_id="76561198000000000",
            source="manual_csv",
        )
        orig_count = self.db.fetch_one("SELECT COUNT(*) FROM inventory_snapshots")[0]
        self.assertEqual(orig_count, 1)

        # Run failed import
        client = PublicInventoryClient(
            http_client=self.make_test_http_client(httpx.MockTransport(lambda r: httpx.Response(200, json={"success": 0}))),
            db=self.db,
        )
        with self.assertRaises(PublicInventoryError):
            await client.fetch_and_import("76561198000000000")

        # Confirm count and snapshot unchanged
        after_count = self.db.fetch_one("SELECT COUNT(*) FROM inventory_snapshots")[0]
        self.assertEqual(after_count, 1)

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


    def test_manual_csv_marketability_hardening(self) -> None:
        """Verify strict fail-closed marketability, tradability, and restriction days in CSV."""
        csv_data = """market_hash_name,marketable,tradable,restriction_days
Missing Flags Item,,,
Explicit True Item,true,yes,0
Explicit False Item,false,no,3
Invalid Marketable Maybe,maybe,true,0
Invalid Marketable Unknown,unknown,true,0
Invalid Marketable Two,2,true,0
Invalid Tradable Float,true,1.5,0
Invalid Negative Restriction,true,true,-1
Invalid String Restriction,true,true,foo
"""
        snapshot, items, errors = ManualInventoryImporter.import_csv(csv_content=csv_data, db=self.db)

        # First 3 items should succeed:
        # 1. Missing flags item: fails closed to non-marketable and non-tradable
        self.assertEqual(items[0].market_hash_name, "Missing Flags Item")
        self.assertFalse(items[0].is_marketable)
        self.assertFalse(items[0].is_tradable)
        self.assertIsNone(items[0].market_restriction_days)

        # 2. Explicit True item:
        self.assertEqual(items[1].market_hash_name, "Explicit True Item")
        self.assertTrue(items[1].is_marketable)
        self.assertTrue(items[1].is_tradable)
        self.assertEqual(items[1].market_restriction_days, 0)

        # 3. Explicit False item:
        self.assertEqual(items[2].market_hash_name, "Explicit False Item")
        self.assertFalse(items[2].is_marketable)
        self.assertFalse(items[2].is_tradable)
        self.assertEqual(items[2].market_restriction_days, 3)

        # Exactly 3 valid items imported:
        self.assertEqual(len(items), 3)

        # Remaining 6 rows must generate row-level validation errors:
        self.assertEqual(len(errors), 6)
        self.assertIn("Row 5: Invalid marketable value 'maybe'", errors[0])
        self.assertIn("Row 6: Invalid marketable value 'unknown'", errors[1])
        self.assertIn("Row 7: Invalid marketable value '2'", errors[2])
        self.assertIn("Row 8: Invalid tradable value '1.5'", errors[3])
        self.assertIn("Row 9: Invalid negative market_restriction_days '-1'", errors[4])
        self.assertIn("Row 10: Invalid non-integer market_restriction_days 'foo'", errors[5])

    def test_manual_json_marketability_hardening(self) -> None:
        """Verify strict fail-closed marketability, tradability, and restriction days in JSON."""
        json_payload = [
            {"market_hash_name": "Missing Flags"},
            {"market_hash_name": "Valid Explicit", "is_marketable": "1", "is_tradable": "y", "market_restriction_days": 5},
            {"market_hash_name": "Invalid Marketable", "is_marketable": "maybe"},
            {"market_hash_name": "Invalid Tradable", "is_tradable": 2},
            {"market_hash_name": "Negative Days", "market_restriction_days": -5},
        ]
        snapshot, items, errors = ManualInventoryImporter.import_json(
            json_content=json.dumps(json_payload),
            db=self.db,
        )
        self.assertEqual(len(items), 2)
        # Item 1: Missing flags fails closed to False
        self.assertEqual(items[0].market_hash_name, "Missing Flags")
        self.assertFalse(items[0].is_marketable)
        self.assertFalse(items[0].is_tradable)

        # Item 2: Valid explicit
        self.assertEqual(items[1].market_hash_name, "Valid Explicit")
        self.assertTrue(items[1].is_marketable)
        self.assertTrue(items[1].is_tradable)
        self.assertEqual(items[1].market_restriction_days, 5)

        # 3 errors generated for the invalid rows:
        self.assertEqual(len(errors), 3)
        self.assertIn("Item #3: Invalid marketable value 'maybe'", errors[0])
        self.assertIn("Item #4: Invalid tradable value '2'", errors[1])
        self.assertIn("Item #5: Invalid negative market_restriction_days '-5'", errors[2])

    async def test_public_inventory_final_page_exceeds_max_items_while_more_items_false(self) -> None:
        """Verify final page exceeding max_items raises PublicInventoryError and never truncates."""
        from sniper.adapters.inventory import PublicInventoryError

        # Steam returns 4 items on a single page (more_items=False), but max_items=2
        resp_data = {
            "success": 1,
            "more_items": 0,
            "assets": [
                {"assetid": "1", "classid": "100", "instanceid": "1", "amount": "1"},
                {"assetid": "2", "classid": "100", "instanceid": "1", "amount": "1"},
                {"assetid": "3", "classid": "100", "instanceid": "1", "amount": "1"},
                {"assetid": "4", "classid": "100", "instanceid": "1", "amount": "1"},
            ],
            "descriptions": [
                {"classid": "100", "instanceid": "1", "market_hash_name": "Item 100", "name": "Item 100", "marketable": 1, "tradable": 1}
            ],
        }
        client = PublicInventoryClient(
            http_client=self.make_test_http_client(httpx.MockTransport(lambda r: httpx.Response(200, json=resp_data))),
            db=self.db,
        )
        with self.assertRaises(PublicInventoryError) as ctx:
            await client.fetch_and_import("76561198000000000", max_items=2)
        self.assertIn("exceeded max_items (2)", str(ctx.exception))

        # Confirm 0 snapshots created
        count = self.db.fetch_one("SELECT COUNT(*) FROM inventory_snapshots")[0]
        self.assertEqual(count, 0)

    async def test_public_inventory_earlier_pages_consume_limit(self) -> None:
        """Verify reaching limit on earlier page while more_items=True aborts cleanly."""
        from sniper.adapters.inventory import PublicInventoryError

        resp_page1 = {
            "success": 1,
            "more_items": 1,
            "last_assetid": "asset_2",
            "assets": [
                {"assetid": "1", "classid": "100", "instanceid": "1", "amount": "1"},
                {"assetid": "2", "classid": "100", "instanceid": "1", "amount": "1"},
            ],
            "descriptions": [
                {"classid": "100", "instanceid": "1", "market_hash_name": "Item 100", "name": "Item 100", "marketable": 1, "tradable": 1}
            ],
        }
        client = PublicInventoryClient(
            http_client=self.make_test_http_client(httpx.MockTransport(lambda r: httpx.Response(200, json=resp_page1))),
            db=self.db,
        )
        # max_items=2: page 1 already has 2 items while more_items=True
        with self.assertRaises(PublicInventoryError) as ctx:
            await client.fetch_and_import("76561198000000000", max_items=2)
        self.assertIn("Inventory item limit reached", str(ctx.exception))

        count = self.db.fetch_one("SELECT COUNT(*) FROM inventory_snapshots")[0]
        self.assertEqual(count, 0)

    async def test_public_inventory_exactly_max_items_with_complete_pagination_succeeds(self) -> None:
        """Verify exactly max_items with complete pagination succeeds without error."""
        resp_data = {
            "success": 1,
            "more_items": 0,
            "assets": [
                {"assetid": "1", "classid": "100", "instanceid": "1", "amount": "1"},
                {"assetid": "2", "classid": "100", "instanceid": "1", "amount": "1"},
            ],
            "descriptions": [
                {"classid": "100", "instanceid": "1", "market_hash_name": "Item 100", "name": "Item 100", "marketable": 1, "tradable": 1}
            ],
        }
        client = PublicInventoryClient(
            http_client=self.make_test_http_client(httpx.MockTransport(lambda r: httpx.Response(200, json=resp_data))),
            db=self.db,
        )
        snap, items = await client.fetch_and_import("76561198000000000", max_items=2)
        self.assertEqual(snap.total_count, 2)
        self.assertEqual(len(items), 2)

    async def test_public_inventory_cursor_cycle_rejected(self) -> None:
        """Verify repeated cursor cycle across multiple pages is rejected promptly."""
        from sniper.adapters.inventory import PublicInventoryError

        page_calls = 0

        def mock_handler(request: httpx.Request) -> httpx.Response:
            nonlocal page_calls
            page_calls += 1
            if page_calls == 1:
                return httpx.Response(200, json={
                    "success": 1, "more_items": 1, "last_assetid": "cursor_A",
                    "assets": [{"assetid": "1", "classid": "100", "instanceid": "1", "amount": "1"}],
                    "descriptions": [{"classid": "100", "instanceid": "1", "market_hash_name": "Item 100", "name": "Item 100"}],
                })
            elif page_calls == 2:
                return httpx.Response(200, json={
                    "success": 1, "more_items": 1, "last_assetid": "cursor_B",
                    "assets": [{"assetid": "2", "classid": "100", "instanceid": "1", "amount": "1"}],
                    "descriptions": [{"classid": "100", "instanceid": "1", "market_hash_name": "Item 100", "name": "Item 100"}],
                })
            else:
                # Page 3 repeats cursor_A!
                return httpx.Response(200, json={
                    "success": 1, "more_items": 1, "last_assetid": "cursor_A",
                    "assets": [{"assetid": "3", "classid": "100", "instanceid": "1", "amount": "1"}],
                    "descriptions": [{"classid": "100", "instanceid": "1", "market_hash_name": "Item 100", "name": "Item 100"}],
                })

        client = PublicInventoryClient(
            http_client=self.make_test_http_client(httpx.MockTransport(mock_handler)),
            db=self.db,
        )
        with self.assertRaises(PublicInventoryError) as ctx:
            await client.fetch_and_import("76561198000000000", max_pages=10)
        self.assertIn("Repeated last_assetid pagination cursor ('cursor_A')", str(ctx.exception))

    def test_manual_json_strict_numeric_amount_validation(self) -> None:
        """Verify JSON amount accepts only positive integers, rejecting floats, booleans, and negatives."""
        payload = [
            {"market_hash_name": "Valid Int", "amount": 2},
            {"market_hash_name": "Valid Str Int", "amount": "3"},
            {"market_hash_name": "Invalid Bool", "amount": True},
            {"market_hash_name": "Invalid Float Integral", "amount": 1.0},
            {"market_hash_name": "Invalid Float Fractional", "amount": 1.9},
            {"market_hash_name": "Invalid Zero", "amount": 0},
            {"market_hash_name": "Invalid Negative", "amount": -2},
            {"market_hash_name": "Invalid Float Str", "amount": "1.0"},
            {"market_hash_name": "Invalid Malformed Str", "amount": "two"},
        ]
        snapshot, items, errors = ManualInventoryImporter.import_json(
            json_content=json.dumps(payload),
            db=self.db,
        )
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].market_hash_name, "Valid Int")
        self.assertEqual(items[0].amount, 2)
        self.assertEqual(items[1].market_hash_name, "Valid Str Int")
        self.assertEqual(items[1].amount, 3)

        self.assertEqual(len(errors), 7)
        self.assertIn("Item #3: Invalid boolean amount 'True'", errors[0])
        self.assertIn("Item #4: Invalid float amount '1.0'", errors[1])
        self.assertIn("Item #5: Invalid float amount '1.9'", errors[2])
        self.assertIn("Item #6: Amount must be a positive integer, got 0", errors[3])
        self.assertIn("Item #7: Amount must be a positive integer, got -2", errors[4])
        self.assertIn("Item #8: Invalid non-integer amount '1.0'", errors[5])
        self.assertIn("Item #9: Invalid non-integer amount 'two'", errors[6])

    def test_manual_json_strict_numeric_restriction_days_validation(self) -> None:
        """Verify JSON restriction_days accepts only non-negative integers or None, rejecting floats and booleans."""
        payload = [
            {"market_hash_name": "Valid None", "market_restriction_days": None},
            {"market_hash_name": "Valid Zero", "market_restriction_days": 0},
            {"market_hash_name": "Valid Positive Str", "market_restriction_days": "7"},
            {"market_hash_name": "Invalid Bool", "market_restriction_days": True},
            {"market_hash_name": "Invalid Float Zero", "market_restriction_days": 0.0},
            {"market_hash_name": "Invalid Float Positive", "market_restriction_days": 7.5},
            {"market_hash_name": "Invalid Negative Int", "market_restriction_days": -7},
            {"market_hash_name": "Invalid Negative Str", "market_restriction_days": "-3"},
            {"market_hash_name": "Invalid Malformed Str", "market_restriction_days": "seven"},
        ]
        snapshot, items, errors = ManualInventoryImporter.import_json(
            json_content=json.dumps(payload),
            db=self.db,
        )
        self.assertEqual(len(items), 3)
        self.assertIsNone(items[0].market_restriction_days)
        self.assertEqual(items[1].market_restriction_days, 0)
        self.assertEqual(items[2].market_restriction_days, 7)

        self.assertEqual(len(errors), 6)
        self.assertIn("Item #4: Invalid boolean market_restriction_days 'True'", errors[0])
        self.assertIn("Item #5: Invalid float market_restriction_days '0.0'", errors[1])
        self.assertIn("Item #6: Invalid float market_restriction_days '7.5'", errors[2])
        self.assertIn("Item #7: Invalid negative market_restriction_days '-7'", errors[3])
        self.assertIn("Item #8: Invalid negative market_restriction_days '-3'", errors[4])
        self.assertIn("Item #9: Invalid non-integer market_restriction_days 'seven'", errors[5])

    def test_manual_json_strict_boolean_marketability_validation(self) -> None:
        """Verify JSON marketable/tradable rejects float 1.0 and 0.0."""
        payload = [
            {"market_hash_name": "Float 1.0 Marketable", "is_marketable": 1.0},
            {"market_hash_name": "Float 0.0 Tradable", "is_tradable": 0.0},
        ]
        snapshot, items, errors = ManualInventoryImporter.import_json(
            json_content=json.dumps(payload),
            db=self.db,
        )
        self.assertEqual(len(items), 0)
        self.assertEqual(len(errors), 2)
        self.assertIn("Item #1: Invalid float marketable value '1.0'", errors[0])
        self.assertIn("Item #2: Invalid float tradable value '0.0'", errors[1])

    def test_manual_inventory_asset_id_namespace(self) -> None:
        """Verify manual imports preserve explicit manual-only asset ID namespace."""
        # 1. JSON import with explicit numeric ID and without ID
        payload = [
            {"market_hash_name": "Item A", "asset_id": "777788889999"},
            {"market_hash_name": "Item B"},
            {"market_hash_name": "Item C", "asset_id": "manual_existing_custom"},
        ]
        _, items, _ = ManualInventoryImporter.import_json(json_content=json.dumps(payload), db=self.db)
        self.assertEqual(len(items), 3)
        self.assertEqual(items[0].asset_id, "manual_777788889999")
        self.assertTrue(items[1].asset_id.startswith("manual_"))
        self.assertEqual(items[2].asset_id, "manual_existing_custom")

        # 2. CSV import with explicit numeric ID and without ID
        csv_data = "market_hash_name,asset_id\nItem D,123456\nItem E,\nItem F,manual:prefixed\n"
        _, csv_items, _ = ManualInventoryImporter.import_csv(csv_content=csv_data, db=self.db)
        self.assertEqual(len(csv_items), 3)
        self.assertEqual(csv_items[0].asset_id, "manual_123456")
        self.assertTrue(csv_items[1].asset_id.startswith("manual_"))
        self.assertEqual(csv_items[2].asset_id, "manual:prefixed")


if __name__ == "__main__":
    unittest.main()
