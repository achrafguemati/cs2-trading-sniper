"""Unit tests for Pydantic response schemas, exact Decimal graph price normalization, and boundary conditions."""

from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path
import unittest

from sniper.core.currency import CurrencyConfig
from sniper.models.schemas import (
    ListingRenderResponse,
    NormalizedDepthEntry,
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
        
        listing_id = "49827391028374"
        self.assertIn(listing_id, parsed.listinginfo)
        item = parsed.listinginfo[listing_id]
        self.assertEqual(item.price, 2488)
        self.assertEqual(item.fee, 324)
        self.assertEqual(item.asset.id, "38291049281")
        
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
        
        ft_bucket = next(b for b in parsed.buckets if b.localized_name_inside_group == "Field-Tested")
        self.assertEqual(ft_bucket.min_price_minor, 2488)
        self.assertEqual(ft_bucket.filters[1], ["Exterior", "WearCategory2"])

    def test_orderbook_histogram_fixture(self):
        with open(FIXTURES_DIR / "orderbook_histogram.json", "r", encoding="utf-8") as f:
            data = json.load(f, parse_float=Decimal)
            
        parsed = OrderbookHistogramResponse.model_validate(data)
        self.assertTrue(parsed.success)
        self.assertEqual(parsed.highest_buy_order_minor, 2250)
        self.assertEqual(parsed.lowest_sell_order_minor, 2488)
        
        self.assertEqual(parsed.top_level_buy_quantity, 1)
        self.assertEqual(parsed.cumulative_buy_quantity, 211731)

    def test_graph_normalization_verified_currency(self):
        """Verify graph price normalization converts exact Decimals to minor units without float arithmetic."""
        with open(FIXTURES_DIR / "orderbook_histogram.json", "r", encoding="utf-8") as f:
            data = json.load(f, parse_float=Decimal)
            
        parsed = OrderbookHistogramResponse.model_validate(data)
        usd = CurrencyConfig.get_default_usd()

        normalized_buys = parsed.get_normalized_buy_graph(usd)
        self.assertEqual(len(normalized_buys), 4)

        # First entry: [Decimal('22.50'), 1, ...]
        top_bid = normalized_buys[0]
        self.assertEqual(top_bid.raw_price, Decimal("22.50"))
        self.assertEqual(top_bid.cumulative_quantity, 1)
        self.assertEqual(top_bid.price_minor, 2250)
        self.assertTrue(top_bid.is_currency_verified)
        self.assertEqual(top_bid.require_price_minor(), 2250)

        # Second entry: [Decimal('22.45'), 15, ...]
        second_bid = normalized_buys[1]
        self.assertEqual(second_bid.raw_price, Decimal("22.45"))
        self.assertEqual(second_bid.price_minor, 2245)

        # Sell graph normalization
        normalized_sells = parsed.get_normalized_sell_graph(usd)
        self.assertEqual(len(normalized_sells), 3)
        self.assertEqual(normalized_sells[0].price_minor, 2488)

    def test_exact_decimal_with_trailing_zeroes(self):
        """Accept exact values with trailing zeroes when they represent the same exact minor-unit amount."""
        usd = CurrencyConfig.get_default_usd()
        histogram = OrderbookHistogramResponse(success=1)

        # Decimal("22.500") -> 2250 cents
        entry1 = histogram.normalize_graph_entry([Decimal("22.500"), 1, "desc"], usd)
        self.assertEqual(entry1.price_minor, 2250)

        # Decimal("22.50000") -> 2250 cents
        entry2 = histogram.normalize_graph_entry([Decimal("22.50000"), 2, "desc"], usd)
        self.assertEqual(entry2.price_minor, 2250)

        # "22.500" string -> 2250 cents
        entry3 = histogram.normalize_graph_entry(["22.500", 3, "desc"], usd)
        self.assertEqual(entry3.price_minor, 2250)

        # Decimal("100.000") -> 10000 cents
        entry4 = histogram.normalize_graph_entry([Decimal("100.000"), 5, "desc"], usd)
        self.assertEqual(entry4.price_minor, 10000)

    def test_exact_decimal_orderbook_price_rejection(self):
        """Reject prices with excess decimal precision that cannot be represented exactly in minor units."""
        usd = CurrencyConfig.get_default_usd()
        histogram = OrderbookHistogramResponse(success=1)

        # Test excess decimals: 22.501, 22.505, long decimals
        with self.assertRaises(ValueError):
            histogram.normalize_graph_entry([Decimal("22.501"), 1, "desc"], usd)

        with self.assertRaises(ValueError):
            histogram.normalize_graph_entry([Decimal("22.505"), 1, "desc"], usd)

        with self.assertRaises(ValueError):
            histogram.normalize_graph_entry([Decimal("22.50001"), 1, "desc"], usd)

        with self.assertRaises(ValueError):
            histogram.normalize_graph_entry(["22.501", 1, "desc"], usd)

    def test_reject_negative_or_invalid_orderbook_prices(self):
        """Reject negative, zero, or non-numeric orderbook prices."""
        usd = CurrencyConfig.get_default_usd()
        histogram = OrderbookHistogramResponse(success=1)

        with self.assertRaises(ValueError):
            histogram.normalize_graph_entry([Decimal("-22.50"), 1, "desc"], usd)

        with self.assertRaises(ValueError):
            histogram.normalize_graph_entry([Decimal("0"), 1, "desc"], usd)

        with self.assertRaises(ValueError):
            histogram.normalize_graph_entry(["-5.00", 1, "desc"], usd)

        with self.assertRaises(ValueError):
            histogram.normalize_graph_entry([True, 1, "desc"], usd)

    def test_validate_graph_quantities_without_silent_truncation(self):
        """Validate quantities as non-negative integers without silently truncating fractional values."""
        usd = CurrencyConfig.get_default_usd()
        histogram = OrderbookHistogramResponse(success=1)

        # Valid non-negative integers
        e1 = histogram.normalize_graph_entry([Decimal("22.50"), 0, "desc"], usd)
        self.assertEqual(e1.cumulative_quantity, 0)

        e2 = histogram.normalize_graph_entry([Decimal("22.50"), 25, "desc"], usd)
        self.assertEqual(e2.cumulative_quantity, 25)

        e3 = histogram.normalize_graph_entry([Decimal("22.50"), "25", "desc"], usd)
        self.assertEqual(e3.cumulative_quantity, 25)

        e4 = histogram.normalize_graph_entry([Decimal("22.50"), Decimal("25"), "desc"], usd)
        self.assertEqual(e4.cumulative_quantity, 25)

        # Fractional quantities must NOT be silently truncated: must raise ValueError
        with self.assertRaises(ValueError):
            histogram.normalize_graph_entry([Decimal("22.50"), 1.5, "desc"], usd)

        with self.assertRaises(ValueError):
            histogram.normalize_graph_entry([Decimal("22.50"), "1.5", "desc"], usd)

        with self.assertRaises(ValueError):
            histogram.normalize_graph_entry([Decimal("22.50"), Decimal("1.5"), "desc"], usd)

        # Negative quantity must raise ValueError
        with self.assertRaises(ValueError):
            histogram.normalize_graph_entry([Decimal("22.50"), -1, "desc"], usd)

        # Boolean quantity must raise ValueError
        with self.assertRaises(ValueError):
            histogram.normalize_graph_entry([Decimal("22.50"), True, "desc"], usd)

    def test_decimal_price_parsing_boundaries(self):
        """Test boundary values for decimal conversion without floating-point inaccuracies."""
        usd = CurrencyConfig.get_default_usd()
        histogram = OrderbookHistogramResponse(success=1)

        test_cases = [
            ([Decimal("0.03"), 10, "desc"], 3),
            ([Decimal("1.15"), 5, "desc"], 115),
            ([Decimal("22.45"), 15, "desc"], 2245),
            ([Decimal("22.50"), 1, "desc"], 2250),
            ([Decimal("24.88"), 1, "desc"], 2488),
            ([Decimal("100.00"), 20, "desc"], 10000),
            ([Decimal("999.99"), 2, "desc"], 99999),
            ([Decimal("1240.50"), 4, "desc"], 124050),
        ]

        for raw_entry, expected_minor in test_cases:
            normalized = histogram.normalize_graph_entry(raw_entry, usd)
            self.assertEqual(normalized.price_minor, expected_minor)
            self.assertEqual(normalized.require_price_minor(), expected_minor)

    def test_graph_normalization_unverified_metadata_prohibits_comparison(self):
        """When currency metadata is unverified (or absent), price_minor is UNKNOWN."""
        with open(FIXTURES_DIR / "orderbook_histogram.json", "r", encoding="utf-8") as f:
            data = json.load(f, parse_float=Decimal)
            
        parsed = OrderbookHistogramResponse.model_validate(data)
        unknown = CurrencyConfig(
            currency_id=96, code="UNKNOWN", symbol="?", minor_unit_precision=2,
            is_metadata_verified=False,
        )

        normalized_buys = parsed.get_normalized_buy_graph(unknown)
        top_bid = normalized_buys[0]
        self.assertIsNone(top_bid.price_minor)
        self.assertFalse(top_bid.is_currency_verified)

        with self.assertRaises(ValueError):
            top_bid.require_price_minor()

        normalized_none = parsed.get_normalized_buy_graph(None)
        self.assertIsNone(normalized_none[0].price_minor)
        with self.assertRaises(ValueError):
            normalized_none[0].require_price_minor()

    def test_verified_jpy_metadata_normalizes_integer_minor_units(self):
        jpy = CurrencyConfig.get_jpy()
        histogram = OrderbookHistogramResponse(success=1)
        entry = histogram.normalize_graph_entry([Decimal("22"), 1, "desc"], jpy)
        self.assertEqual(entry.require_price_minor(), 22)
        self.assertEqual(entry.currency_code, "JPY")

    def test_eur_metadata_allows_exact_orderbook_normalization_without_listing_rules(self):
        eur = CurrencyConfig.get_eur()
        self.assertTrue(eur.is_metadata_verified)
        self.assertFalse(eur.is_listing_min_verified)
        self.assertFalse(eur.is_price_step_verified)
        histogram = OrderbookHistogramResponse(success=1)
        entry = histogram.normalize_graph_entry([Decimal("22.50"), 3, "desc"], eur)
        self.assertEqual(entry.price_minor, 2250)
        self.assertEqual(entry.require_price_minor(), 2250)
        self.assertTrue(entry.is_currency_verified)
        self.assertEqual(entry.currency_code, "EUR")

    def test_tunisia_usd_metadata_allows_orderbook_normalization_but_listing_unknown(self):
        tn = CurrencyConfig.get_tunisia_context()
        self.assertTrue(tn.is_metadata_verified)
        self.assertFalse(tn.is_listing_min_verified)
        histogram = OrderbookHistogramResponse(success=1)
        entry = histogram.normalize_graph_entry([Decimal("22.50"), 1, "desc"], tn)
        self.assertEqual(entry.require_price_minor(), 2250)

    def test_public_inventory_fixture(self):
        with open(FIXTURES_DIR / "public_inventory.json", "r", encoding="utf-8") as f:
            data = json.load(f)
            
        parsed = PublicInventoryResponse.model_validate(data)
        self.assertEqual(len(parsed.assets), 2)
        self.assertEqual(len(parsed.descriptions), 2)
        
        marketable_item = next(d for d in parsed.descriptions if d.market_hash_name == "AK-47 | Redline (Field-Tested)")
        self.assertEqual(marketable_item.marketable, 1)
        
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
