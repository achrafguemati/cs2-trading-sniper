"""Deterministic offline unit tests for M6 Steam render listing parser & chart extractor."""

from __future__ import annotations

import json
from pathlib import Path
import unittest

from sniper.adapters.listing_parser import (
    compute_weighted_median_minor,
    extract_public_price_history,
    parse_listing_render_cards,
)
from sniper.core.currency import CurrencyConfig
from sniper.core.identity import WearCategory
from sniper.models.schemas import ListingRenderResponse

FIXTURES_DIR = Path(__file__).parent / "fixtures"


class TestListingParser(unittest.TestCase):
    """Tests for individual listing parsing, sold/currency guards, stickers/floats, and chart extraction."""

    def test_parse_standard_render_fixture(self) -> None:
        raw = json.loads((FIXTURES_DIR / "standard_render.json").read_text(encoding="utf-8"))
        render = ListingRenderResponse.model_validate(raw)

        valid, rejected = parse_listing_render_cards(
            render=render,
            expected_market_hash_name="AK-47 | Redline (Field-Tested)",
            expected_currency_id=1,
            expected_exterior=WearCategory.FIELD_TESTED,
        )
        self.assertEqual(len(rejected), 0)
        self.assertEqual(len(valid), 2)

        first = valid[0]
        self.assertEqual(first.steam_listing_id, "49827391028374")
        self.assertEqual(first.asset_id, "38291049281")
        self.assertEqual(first.seller_receives_minor, 2488)
        self.assertEqual(first.fee_minor, 324)
        self.assertEqual(first.buyer_pays_minor, 2812)
        self.assertEqual(first.stickers, ["FaZe Clan | Austin 2025"])
        self.assertFalse(first.has_notable_craft)
        self.assertIn("M49827391028374A38291049281D5083003", first.inspect_link or "")

    def test_rejects_sold_listings_and_currency_mismatch_and_variant_mismatch(self) -> None:
        payload = {
            "success": True,
            "start": 0,
            "pagesize": 4,
            "total_count": 4,
            "listinginfo": {
                "sold_1": {
                    "listingid": "sold_1",
                    "price": 0,
                    "fee": 0,
                    "steam_fee": 0,
                    "publisher_fee": 0,
                    "converted_price": 0,
                    "converted_fee": 0,
                    "converted_currencyid": 2001,
                    "asset": {"appid": 730, "contextid": "2", "id": "a1"},
                },
                "eur_mismatch": {
                    "listingid": "eur_mismatch",
                    "price": 1500,
                    "fee": 200,
                    "steam_fee": 65,
                    "publisher_fee": 135,
                    "converted_price": 1500,
                    "converted_fee": 200,
                    "converted_currencyid": 2003,
                    "asset": {"appid": 730, "contextid": "2", "id": "a2"},
                },
                "wrong_wear": {
                    "listingid": "wrong_wear",
                    "price": 1500,
                    "fee": 200,
                    "steam_fee": 65,
                    "publisher_fee": 135,
                    "converted_price": 1500,
                    "converted_fee": 200,
                    "converted_currencyid": 2001,
                    "asset": {"appid": 730, "contextid": "2", "id": "a3"},
                },
                "valid_one": {
                    "listingid": "valid_one",
                    "price": 9000,
                    "fee": 1200,
                    "steam_fee": 400,
                    "publisher_fee": 800,
                    "converted_price": 1800,
                    "converted_fee": 270,
                    "converted_currencyid": 2001,
                    "asset": {"appid": 730, "contextid": "2", "id": "a4"},
                },
            },
            "assets": {
                "730": {
                    "2": {
                        "a1": {"id": "a1", "classid": "1", "instanceid": "0", "name": "AK", "market_hash_name": "AK-47 | Redline (Field-Tested)"},
                        "a2": {"id": "a2", "classid": "1", "instanceid": "0", "name": "AK", "market_hash_name": "AK-47 | Redline (Field-Tested)"},
                        "a3": {"id": "a3", "classid": "2", "instanceid": "0", "name": "AK", "market_hash_name": "AK-47 | Redline (Battle-Scarred)"},
                        "a4": {
                            "id": "a4",
                            "classid": "1",
                            "instanceid": "0",
                            "name": "AK-47 | Redline (Field-Tested)",
                            "market_hash_name": "AK-47 | Redline (Field-Tested)",
                            "descriptions": [
                                {"value": "<div id='sticker_info'>Sticker: Titan (Holo) | Katowice 2014<br>Charm: Lil' Ava</div>"},
                            ],
                            "asset_properties": [
                                {"propertyid": 1, "name": "Wear Rating", "float_value": "0.154231"},
                                {"propertyid": 2, "name": "Pattern Template", "int_value": "661"},
                                {"propertyid": 3, "name": "Finish Catalog", "int_value": "282"},
                            ],
                            "market_actions": [
                                {"link": "steam://rungame/730/123/+csgo_econ_action_preview%20M%listingid%A%assetid%D99", "name": "Inspect in Game..."}
                            ],
                        },
                    }
                }
            },
        }
        render = ListingRenderResponse.model_validate(payload)
        valid, rejected = parse_listing_render_cards(
            render=render,
            expected_market_hash_name="AK-47 | Redline (Field-Tested)",
            expected_currency_id=1,
            expected_exterior=WearCategory.FIELD_TESTED,
        )

        self.assertEqual(len(valid), 1)
        self.assertEqual(len(rejected), 3)
        reasons = {r.steam_listing_id: r.rejection_reason for r in rejected}
        self.assertIn("LISTING_SOLD_OR_UNAVAILABLE", reasons["sold_1"] or "")
        self.assertIn("CURRENCY_MISMATCH", reasons["eur_mismatch"] or "")
        self.assertIn("VARIANT_MISMATCH", reasons["wrong_wear"] or "")

        card = valid[0]
        self.assertEqual(card.buyer_pays_minor, 2070)
        self.assertAlmostEqual(card.float_value or 0.0, 0.154231, places=6)
        self.assertTrue(card.is_low_float_tier)
        self.assertEqual(card.paint_seed, 661)
        self.assertEqual(card.paint_index, 282)
        self.assertEqual(card.stickers, ["Titan (Holo) | Katowice 2014"])
        self.assertEqual(card.charms, ["Lil' Ava"])
        self.assertTrue(card.has_notable_craft)
        self.assertEqual(
            card.inspect_link,
            "steam://rungame/730/123/+csgo_econ_action_preview%20Mvalid_oneAa4D99",
        )

    def test_extract_public_price_history_cookie_free(self) -> None:
        usd = CurrencyConfig.get_default_usd()
        html = """
        <html><body>
        <script type="text/javascript">
        var line1=[["Nov 10 2026 01: +0",24.00,"10"],["Nov 10 2026 05: +0",26.00,"30"],["Nov 11 2026 02: +0",25.50,"15"]];
        </script>
        </body></html>
        """
        pts = extract_public_price_history(html, usd)
        self.assertEqual(len(pts), 2)
        self.assertEqual(pts[0], ("2026-11-10", 2600, 40))
        self.assertEqual(pts[1], ("2026-11-11", 2550, 15))

        # Missing line1 returns [] cleanly without raising
        self.assertEqual(extract_public_price_history("<html><body>No chart</body></html>", usd), [])

    def test_compute_weighted_median_minor(self) -> None:
        self.assertEqual(compute_weighted_median_minor([(1000, 1), (2000, 5), (3000, 1)]), 2000)
        self.assertEqual(compute_weighted_median_minor([(1000, 10), (5000, 1)]), 1000)


if __name__ == "__main__":
    unittest.main()
