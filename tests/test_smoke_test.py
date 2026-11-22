"""Regression tests for the single-request read-only smoke-test calculation."""

from __future__ import annotations

import unittest

from scripts.smoke_test import calculate_probe_fee_estimate
from sniper.core.currency import CurrencyConfig
from sniper.models.schemas import OrderbookHistogramResponse


class TestSmokeTest(unittest.TestCase):
    def test_successful_payload_uses_mathematical_fee_estimate_with_unknown_usd_minimum(self):
        usd = CurrencyConfig.get_default_usd()
        payload = OrderbookHistogramResponse(
            success=1,
            highest_buy_order="100",
            lowest_sell_order="120",
        )

        self.assertIsNone(usd.min_listing_price_minor)
        self.assertFalse(usd.is_listing_min_verified)
        estimate = calculate_probe_fee_estimate(payload, usd)

        self.assertIsNotNone(estimate)
        assert estimate is not None
        self.assertEqual(estimate.seller_receives, 88)
        self.assertEqual(estimate.actual_buyer_pays, 100)
        self.assertTrue(estimate.is_exact_match)

    def test_unsuccessful_payload_has_no_fee_estimate(self):
        payload = OrderbookHistogramResponse(success=0, highest_buy_order="100")
        self.assertIsNone(calculate_probe_fee_estimate(payload, CurrencyConfig.get_default_usd()))

    def test_successful_payload_without_positive_bid_has_no_fee_estimate(self):
        payload = OrderbookHistogramResponse(success=1, highest_buy_order="0")
        self.assertIsNone(calculate_probe_fee_estimate(payload, CurrencyConfig.get_default_usd()))


if __name__ == "__main__":
    unittest.main()
