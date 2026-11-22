"""Unit tests for Pydantic response schemas against real recorded fixtures."""

import json
from pathlib import Path
import unittest

from sniper.models.schemas import (
    ListingRenderResponse,
    OrderbookHistogramResponse,
    PublicInventoryResponse,
    SsrListingLoaderData,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


class TestSchemas(unittest.TestCase):
    def test_standard_render_fixture(self):
        with open(FIXTURES_DIR / "standard_render.json", "r", encoding="utf-8") as f:
            data = json.load(f)
            
        parsed = ListingRenderResponse.model_validate(data)
        self.assertTrue(parsed.success)
        self.assertEqual(parsed.total_count, 254)
        self.assertEqual(len(parsed.listinginfo), 2)
        
        # Verify first listing
        listing_id = "49827391028374"
        self.assertIn(listing_id, parsed.listinginfo)
        item = parsed.listinginfo[listing_id]
        self.assertEqual(item.price, 2488)
        self.assertEqual(item.fee, 324)
        self.assertEqual(item.asset.id, "38291049281")
        
        # Verify asset data
        asset = parsed.assets["730"]["2"]["38291049281"]
        self.assertEqual(asset.market_hash_name, "AK-47 | Redline (Field-Tested)")
        self.assertIn("Inspect in Game...", asset.market_actions[0].name)

    def test_grouped_ssr_fixture(self):
        with open(FIXTURES_DIR / "grouped_ssr_data.json", "r", encoding="utf-8") as f:
            data = json.load(f)
            
        parsed = SsrListingLoaderData.model_validate(data)
        self.assertTrue(parsed.success)
        self.assertEqual(parsed.appid, 730)
        self.assertEqual(len(parsed.buckets), 5)
        
        # Verify FT bucket
        ft_bucket = next(b for b in parsed.buckets if b.localized_name_inside_group == "Field-Tested")
        self.assertEqual(ft_bucket.min_price_minor, 2488)
        self.assertEqual(ft_bucket.filters[1], ["Exterior", "WearCategory2"])

    def test_orderbook_histogram_fixture(self):
        with open(FIXTURES_DIR / "orderbook_histogram.json", "r", encoding="utf-8") as f:
            data = json.load(f)
            
        parsed = OrderbookHistogramResponse.model_validate(data)
        self.assertTrue(parsed.success)
        self.assertEqual(parsed.highest_buy_order_minor, 2250)
        self.assertEqual(parsed.lowest_sell_order_minor, 2488)
        
        # Verify critical quantity distinction
        self.assertEqual(parsed.top_level_buy_quantity, 1)
        self.assertEqual(parsed.cumulative_buy_quantity, 211731)

    def test_public_inventory_fixture(self):
        with open(FIXTURES_DIR / "public_inventory.json", "r", encoding="utf-8") as f:
            data = json.load(f)
            
        parsed = PublicInventoryResponse.model_validate(data)
        self.assertEqual(len(parsed.assets), 2)
        self.assertEqual(len(parsed.descriptions), 2)
        
        # Check marketable item
        marketable_item = next(d for d in parsed.descriptions if d.market_hash_name == "AK-47 | Redline (Field-Tested)")
        self.assertEqual(marketable_item.marketable, 1)
        
        # Check restricted item
        restricted_item = next(d for d in parsed.descriptions if d.market_hash_name == "AK-47 | Redline (Minimal Wear)")
        self.assertEqual(restricted_item.marketable, 0)
        self.assertEqual(restricted_item.market_marketable_restriction, 7)

    def test_malformed_histogram_resilience(self):
        with open(FIXTURES_DIR / "malformed_histogram.json", "r", encoding="utf-8") as f:
            data = json.load(f)
            
        parsed = OrderbookHistogramResponse.model_validate(data)
        self.assertEqual(parsed.success, 0)
        self.assertIsNone(parsed.highest_buy_order_minor)
        self.assertEqual(parsed.top_level_buy_quantity, 0)
        self.assertEqual(parsed.cumulative_buy_quantity, 0)


if __name__ == "__main__":
    unittest.main()
