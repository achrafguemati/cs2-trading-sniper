"""Unit tests validating fixture integrity, authenticity manifest, and schema compliance."""

from decimal import Decimal
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


class TestFixtures(unittest.TestCase):
    def test_manifest_integrity_and_file_existence(self):
        manifest_path = FIXTURES_DIR / "manifest.json"
        self.assertTrue(manifest_path.exists(), "manifest.json must exist in tests/fixtures")

        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)

        self.assertIn("fixtures", manifest)
        fixtures_dict = manifest["fixtures"]

        for filename, metadata in fixtures_dict.items():
            fixture_file = FIXTURES_DIR / filename
            self.assertTrue(fixture_file.exists(), f"Fixture file {filename} referenced in manifest does not exist")
            self.assertIn("endpoint", metadata)
            self.assertIn("classification", metadata)
            self.assertIn("provenance", metadata)
            self.assertIn("currency", metadata)

    def test_all_fixtures_parse_successfully(self):
        manifest = json.loads((FIXTURES_DIR / "manifest.json").read_text(encoding="utf-8"))

        # 1. Orderbook Histogram
        with open(FIXTURES_DIR / "orderbook_histogram.json", "r", encoding="utf-8") as f:
            data = json.load(f, parse_float=Decimal)
            parsed = OrderbookHistogramResponse.model_validate(data)
            self.assertEqual(parsed.success, 1)

        # 2. Malformed Histogram
        with open(FIXTURES_DIR / "malformed_histogram.json", "r", encoding="utf-8") as f:
            data = json.load(f, parse_float=Decimal)
            parsed = OrderbookHistogramResponse.model_validate(data)
            self.assertEqual(parsed.success, 0)

        # 3. Standard Render
        with open(FIXTURES_DIR / "standard_render.json", "r", encoding="utf-8") as f:
            data = json.load(f)
            parsed = ListingRenderResponse.model_validate(data)
            self.assertTrue(parsed.success)

        # 4. Grouped SSR Data
        with open(FIXTURES_DIR / "grouped_ssr_data.json", "r", encoding="utf-8") as f:
            data = json.load(f)
            parsed = SsrListingLoaderData.model_validate(data)
            self.assertTrue(parsed.success)

        # 5. Public Inventory
        with open(FIXTURES_DIR / "public_inventory.json", "r", encoding="utf-8") as f:
            data = json.load(f)
            parsed = PublicInventoryResponse.model_validate(data)
            self.assertEqual(parsed.success, 1)

        # 6. Seed Market IDs
        with open(FIXTURES_DIR / "seed_market_ids.json", "r", encoding="utf-8") as f:
            mapping = json.load(f)
            self.assertIsInstance(mapping, dict)
            self.assertGreater(len(mapping), 0)
            self.assertTrue(all(type(value) is int and value > 0 for value in mapping.values()))
            self.assertEqual(manifest["fixtures"]["seed_market_ids.json"]["classification"], "SYNTHETIC_TEST_VECTOR")


if __name__ == "__main__":
    unittest.main()
