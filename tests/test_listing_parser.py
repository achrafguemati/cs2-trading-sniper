"""Deterministic offline unit tests for M6 Steam render listing parser & chart extractor."""

from __future__ import annotations

import json
from pathlib import Path
import unittest

from sniper.adapters.listing_parser import (
    compute_weighted_median_minor,
    extract_public_price_history,
    extract_ssr_listing_render_response,
    extract_ssr_orderbook_snapshot,
    extract_ssr_search_results,
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

    def test_extract_ssr_2026_listings_properties_inspect_and_pricehistory(self) -> None:
        """Verify 2026 SSR valve-ssr-data market_item_search and pricehistory parsing cleanly."""
        query_data = {
            "queries": [
                {
                    "queryKey": ["market_item_search", {"appid": 730, "strItemName": "G18820420D5083003"}],
                    "state": {
                        "data": {
                            "pages": [
                                {
                                    "start": 0,
                                    "total_count": 2,
                                    "listings": [
                                        {
                                            "listingid": "681604214558471823",
                                            "unPrice": 4340,
                                            "unFee": 651,
                                            "unSteamFee": 217,
                                            "unPublisherFee": 434,
                                            "eCurrency": 1,
                                            "asset": {
                                                "id": "52645377248",
                                                "classid": "7993039401",
                                                "instanceid": "188530139",
                                                "appid": 730,
                                                "contextid": "2",
                                                "asset_properties": [
                                                    {"propertyid": 1, "int_value": "356"},
                                                    {"propertyid": 2, "float_value": 0.27541118},
                                                    {"propertyid": 6, "string_value": "8E9E6E67"},
                                                ],
                                            },
                                            "description": {
                                                "appid": 730,
                                                "classid": "7993039401",
                                                "instanceid": "188530139",
                                                "name": "★ Bowie Knife | Black Laminate (Field-Tested)",
                                                "market_hash_name": "★ Bowie Knife | Black Laminate (Field-Tested)",
                                                "descriptions": [{"value": "Exterior: Field-Tested"}],
                                                "market_actions": [
                                                    {
                                                        "link": "steam://run/730//+csgo_econ_action_preview%20%propid:6%",
                                                        "name": "Inspect in Game...",
                                                    }
                                                ],
                                            },
                                        }
                                    ],
                                }
                            ]
                        }
                    },
                },
                {
                    "queryKey": ["market", "pricehistory", 730, "★ Bowie Knife | Black Laminate (Field-Tested)"],
                    "state": {
                        "data": {
                            "ecurrency": 1,
                            "prices": [
                                {"time": 1795532400, "price_median": 50.3364, "purchases": 3},
                                {"time": 1795536000, "price_median": 48.1248, "purchases": 2},
                            ],
                        }
                    },
                },
            ]
        }
        ssr_envelope = {
            "route": "/market/listings/:appid/:name/:bucket?",
            "loaderData": ["{}", "{}", "true", "{}"],
            "renderContext": {"queryData": json.dumps(query_data)},
        }
        html_doc = f'<html><script type="application/json" id="valve-ssr-data">{json.dumps(ssr_envelope)}</script></html>'

        render = extract_ssr_listing_render_response(html_doc)
        self.assertIsNotNone(render)
        valid, rejected = parse_listing_render_cards(
            render,
            expected_market_hash_name="★ Bowie Knife | Black Laminate (Field-Tested)",
            expected_currency_id=1,
            expected_exterior="Field-Tested",
        )
        self.assertEqual(len(rejected), 0)
        self.assertEqual(len(valid), 1)
        card = valid[0]
        self.assertEqual(card.buyer_pays_minor, 4991)
        self.assertEqual(card.seller_receives_minor, 4340)
        self.assertEqual(card.paint_seed, 356)
        self.assertAlmostEqual(card.float_value or 0.0, 0.27541118, places=6)
        self.assertEqual(card.inspect_link, "steam://run/730//+csgo_econ_action_preview%208E9E6E67")

        usd = CurrencyConfig(
            currency_id=1,
            code="USD",
            symbol="$",
            minor_unit_precision=2,
            is_metadata_verified=True,
            is_fee_verified=True,
        )
        pts = extract_public_price_history(
            html_doc,
            usd,
            expected_market_hash_name="★ Bowie Knife | Black Laminate (Field-Tested)",
        )
        self.assertEqual(len(pts), 1)
        self.assertEqual(pts[0][1], 5034)
        self.assertEqual(pts[0][2], 5)
        self.assertEqual(
            extract_public_price_history(html_doc, usd, expected_market_hash_name="Wrong Item"),
            [],
        )

    def test_extract_ssr_search_results_and_orderbook_snapshot(self) -> None:
        """Verify 2026 Steam /market/search SSR hydration and listing orderbook snapshot parsing."""
        usd = CurrencyConfig(
            currency_id=1,
            code="USD",
            symbol="$",
            minor_unit_precision=2,
            is_metadata_verified=True,
            is_fee_verified=True,
        )
        search_qd = {
            "queries": [
                {
                    "queryKey": ["market_search", {"appid": 730, "q": "Redline"}],
                    "state": {
                        "data": {
                            "pages": [
                                {
                                    "total_count": 42,
                                    "results": [
                                        {
                                            "strHash": "AK-47 | Redline (Field-Tested)",
                                            "cSellOrders": 1280,
                                            "cBuyOrders": 9400,
                                            "strMinSellSubtotal": "$28.45 USD",
                                            "eCurrency": 1,
                                            "asset_description": {
                                                "type": "Classified Rifle",
                                                "name_color": "D32CE6",
                                                "icon_url": "abc/icon",
                                            },
                                        }
                                    ],
                                }
                            ]
                        }
                    },
                }
            ]
        }
        search_html = (
            '<script type="application/json" id="valve-ssr-data">'
            + json.dumps({
                "route": "/market/search",
                "renderContext": {"queryData": json.dumps(search_qd)},
            })
            + "</script>"
        )
        parsed = extract_ssr_search_results(search_html, currency=usd)
        self.assertIsNotNone(parsed)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["market_hash_name"], "AK-47 | Redline (Field-Tested)")
        self.assertEqual(parsed[0]["min_sell_price_minor"], 2845)
        self.assertEqual(parsed[0]["sell_listings"], 1280)
        self.assertTrue(parsed[0]["icon_url"].endswith("abc/icon"))

        # Challenge HTML or listing route fails closed for market_search
        self.assertIsNone(extract_ssr_search_results("<html>Cloudflare check</html>", currency=usd))

        ob_qd = {
            "queries": [
                {
                    "queryKey": ["market", "orderbook", 730, "AK-47 | Redline (Field-Tested)"],
                    "state": {
                        "data": {
                            "eCurrency": 1,
                            "amtMaxBuyOrder": 2410,
                            "amtMinSellOrder": 2845,
                            "cBuyOrders": 9400,
                            "cSellOrders": 1280,
                            "rgCompactBuyOrders": [{"quantity": 4}],
                        }
                    },
                }
            ]
        }
        ob_html = (
            '<script type="application/json" id="valve-ssr-data">'
            + json.dumps({
                "route": "/market/listings/:appid/:name",
                "renderContext": {"queryData": json.dumps(ob_qd)},
            })
            + "</script>"
        )
        snap = extract_ssr_orderbook_snapshot(ob_html, "AK-47 | Redline (Field-Tested)", 1)
        self.assertIsNotNone(snap)
        self.assertEqual(snap["highest_buy_order_minor"], 2410)
        self.assertEqual(snap["lowest_sell_order_minor"], 2845)
        self.assertEqual(snap["top_level_buy_quantity"], 4)
        self.assertIsNone(extract_ssr_orderbook_snapshot(ob_html, "AK-47 | Redline (Field-Tested)", 3))


if __name__ == "__main__":
    unittest.main()
