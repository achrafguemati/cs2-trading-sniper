"""Deterministic offline unit tests for ExternalMarketFeedAdapter, wear percentiles, craft valuation & venue payouts."""

from __future__ import annotations

import unittest

import httpx

from sniper.adapters.external_markets import ExternalMarketFeedAdapter, _validate_external_url
from sniper.core.currency import CurrencyConfig
from sniper.core.identity import ItemNameIdDatabase
from sniper.engine.discovery import (
    compute_exit_venues_comparison,
    compute_wear_float_metrics,
    evaluate_craft_alpha,
)
from tests.test_identity import VERIFIED_SEED_PATH, verified_seed_metadata


def _usd_cfg() -> CurrencyConfig:
    return CurrencyConfig(
        currency_id=1,
        code="USD",
        symbol="$",
        minor_unit_precision=2,
        is_metadata_verified=True,
        is_fee_verified=True,
    )


class TestExternalMarketsAndAnalytics(unittest.IsolatedAsyncioTestCase):
    """Offline tests verifying cross-market feeds, Decimal cents parsing, allowlists, and integer payouts."""

    def test_allowlist_blocks_unapproved_hosts(self) -> None:
        _validate_external_url("https://api.skinport.com/v1/items")
        _validate_external_url("https://market.csgo.com/api/v2/prices/USD.json")
        with self.assertRaises(PermissionError):
            _validate_external_url("https://evil.example.com/steal")
        with self.assertRaises(PermissionError):
            _validate_external_url("http://api.skinport.com/v1/items")

    async def test_skinport_and_csmarket_cross_market_ranking_uses_decimal_integer_cents(self) -> None:
        def handler(req: httpx.Request) -> httpx.Response:
            host = req.url.host
            if host == "api.skinport.com":
                return httpx.Response(200, json=[
                    {
                        "market_hash_name": "AK-47 | Redline (Field-Tested)",
                        "min_price": "21.25",
                        "suggested_price": "31.55",
                        "median_price": "32.00",
                        "quantity": 120,
                        "item_page": "https://skinport.com/item/ak-47-redline-field-tested",
                    },
                    {
                        "market_hash_name": "Souvenir P250 | Sand Dune (Field-Tested)",
                        "min_price": "0.10",
                        "suggested_price": "5.00",
                        "quantity": 50,
                    },
                    {
                        "market_hash_name": "Sticker | Crown (Foil)",
                        "min_price": "363.10",
                        "suggested_price": "343.04",
                        "quantity": 24,
                    },
                ])
            if host == "market.csgo.com":
                return httpx.Response(200, json={
                    "success": True,
                    "items": [
                        {"market_hash_name": "AK-47 | Redline (Field-Tested)", "price": "22.10"},
                    ],
                })
            return httpx.Response(404)

        adapter = ExternalMarketFeedAdapter(transport=httpx.MockTransport(handler))
        with ItemNameIdDatabase(":memory:") as nid_db:
            nid_db.import_verified_dataset(VERIFIED_SEED_PATH, verified_seed_metadata())
            ranked = await adapter.find_cross_market_candidates(
                currency_cfg=_usd_cfg(),
                nameid_db=nid_db,
                min_price_minor=300,
                min_supply=5,
                min_roi_pct=5.0,
            )

        self.assertEqual(len(ranked), 1)
        top = ranked[0]
        self.assertEqual(top["market_hash_name"], "AK-47 | Redline (Field-Tested)")
        self.assertEqual(top["skinport_min_minor"], 2125)
        self.assertEqual(top["skinport_suggested_minor"], 3155)
        self.assertEqual(top["csmarket_min_minor"], 2210)
        self.assertEqual(top["est_steam_net_minor"], 2744)
        self.assertEqual(top["est_wallet_profit_minor"], 619)
        self.assertGreater(top["est_wallet_roi_pct"], 25.0)
        self.assertEqual(top["item_nameid"], 7178002)

    async def test_graceful_degradation_when_external_feed_fails(self) -> None:
        adapter = ExternalMarketFeedAdapter(
            transport=httpx.MockTransport(lambda req: httpx.Response(503))
        )
        sp = await adapter.get_skinport_index()
        cm = await adapter.get_csmarket_prices()
        self.assertEqual(sp, {})
        self.assertEqual(cm, {})

    def test_wear_percentile_craft_valuation_and_exit_venue_math(self) -> None:
        usd = _usd_cfg()
        wear = compute_wear_float_metrics(0.1569, "Field-Tested")
        self.assertIsNotNone(wear["wear_percentile"])
        self.assertEqual(wear["wear_percentile"], 3.0)
        self.assertAlmostEqual(wear["distance_to_better_wear"], 0.0069, places=4)
        self.assertEqual(wear["next_better_wear"], "Minimal Wear")
        self.assertIn("Top 3% FT Float", wear["float_alpha_badge"])

        ext_idx = {
            "Sticker | Crown (Foil)": {"skinport_min_minor": 35000, "skinport_suggested_minor": 34000}
        }
        craft = evaluate_craft_alpha(
            base_price_minor=2500,
            floor_ask_minor=2800,
            stickers=["Crown (Foil)"],
            charms=[],
            external_index=ext_idx,
        )
        self.assertEqual(craft["sticker_total_value_minor"], 35000)
        self.assertEqual(craft["retained_craft_alpha_minor"], 1400)  # 4% on $25 skin
        self.assertLess(craft["craft_sp_pct"], 2.0)

        exits = compute_exit_venues_comparison(3155, usd)
        by_venue = {e["venue"]: e for e in exits}
        self.assertEqual(by_venue["Steam Market"]["net_minor"], 2744)
        self.assertEqual(by_venue["Steam Market"]["wallet_type"], "Steam Wallet (Non-Withdrawable)")
        self.assertEqual(by_venue["CSFloat"]["fee_minor"], 63)
        self.assertEqual(by_venue["CSFloat"]["net_minor"], 3092)
        self.assertEqual(by_venue["Skinport"]["fee_label"], "9%")
        self.assertEqual(by_venue["Skinport"]["net_minor"], 2871)


if __name__ == "__main__":
    unittest.main()
