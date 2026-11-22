"""Unit tests for Steam fee calculations, currency-specific minimums, and fail-closed safety."""

import unittest
from sniper.core.currency import CurrencyConfig
from sniper.core.fees import (
    calculate_buyer_pays,
    calculate_seller_receives,
    calculate_exact_breakeven_buyer_pays,
)


class TestFees(unittest.TestCase):
    def test_minimum_price_forward(self):
        """At 1 cent seller proceeds, Steam fee is 1 cent and CS2 fee is 1 cent, buyer pays 3 cents."""
        res = calculate_buyer_pays(1)
        self.assertEqual(res.seller_receives, 1)
        self.assertEqual(res.steam_fee, 1)
        self.assertEqual(res.publisher_fee, 1)
        self.assertEqual(res.total_fee, 2)
        self.assertEqual(res.actual_buyer_pays, 3)
        self.assertTrue(res.is_exact_match)

    def test_one_dollar_forward(self):
        """At 88 cents seller proceeds, buyer pays 100 cents ($1.00)."""
        res = calculate_buyer_pays(88)
        self.assertEqual(res.seller_receives, 88)
        self.assertEqual(res.steam_fee, 4)       # floor(88 * 5 / 100) = 4
        self.assertEqual(res.publisher_fee, 8)   # floor(88 * 10 / 100) = 8
        self.assertEqual(res.total_fee, 12)
        self.assertEqual(res.actual_buyer_pays, 100)

    def test_one_dollar_inverse(self):
        """Inverting 100 cents buyer pays yields 88 cents seller proceeds with exact match."""
        res = calculate_seller_receives(100)
        self.assertEqual(res.seller_receives, 88)
        self.assertEqual(res.actual_buyer_pays, 100)
        self.assertTrue(res.is_exact_match)

    def test_step_jump_gap(self):
        """114 cents buyer pays is in a fee step-jump gap (R=99 -> 112, R=100 -> 115).
        
        The function must return R=99, actual_buyer_pays=112, and is_exact_match=False.
        """
        res = calculate_seller_receives(114)
        self.assertEqual(res.seller_receives, 99)
        self.assertEqual(res.actual_buyer_pays, 112)
        self.assertFalse(res.is_exact_match)

    def test_high_tier_price(self):
        """Test high tier item: $500.00 (50000 cents)."""
        res = calculate_seller_receives(50000)
        forward = calculate_buyer_pays(res.seller_receives)
        self.assertEqual(forward.actual_buyer_pays, res.actual_buyer_pays)
        self.assertLessEqual(res.actual_buyer_pays, 50000)

    def test_breakeven_calculation(self):
        """Exact break-even buyer price must yield at least the cost basis."""
        cost = 2488  # $24.88
        breakeven_buyer = calculate_exact_breakeven_buyer_pays(cost)
        res = calculate_seller_receives(breakeven_buyer)
        self.assertGreaterEqual(res.seller_receives, cost)

    def test_currency_specific_minimum_usd_and_eur(self):
        """USD and EUR have verified minimum buyer pays of 3 minor units ($0.03 / €0.03)."""
        usd = CurrencyConfig.get_default_usd()
        eur = CurrencyConfig.get_eur()
        self.assertTrue(usd.is_verified)
        self.assertTrue(eur.is_verified)
        self.assertEqual(usd.min_listing_price_minor, 3)
        self.assertEqual(eur.min_listing_price_minor, 3)

        # 3 minor units succeeds
        res_usd = calculate_seller_receives(3, currency=usd)
        self.assertEqual(res_usd.seller_receives, 1)
        self.assertEqual(res_usd.actual_buyer_pays, 3)

        # 2 minor units raises ValueError due to currency min listing price
        with self.assertRaises(ValueError):
            calculate_seller_receives(2, currency=usd)
        with self.assertRaises(ValueError):
            calculate_seller_receives(2, currency=eur)

    def test_unverified_currency_fails_closed(self):
        """Unverified currencies (like JPY) must strictly fail closed on financial fee calculations."""
        jpy = CurrencyConfig.get_jpy()
        self.assertFalse(jpy.is_fee_verified)
        self.assertFalse(jpy.is_listing_min_verified)
        self.assertFalse(jpy.is_verified)

        # calculate_buyer_pays on unverified currency must raise ValueError
        with self.assertRaises(ValueError):
            calculate_buyer_pays(100, currency=jpy)

        # calculate_seller_receives on unverified currency must raise ValueError
        with self.assertRaises(ValueError):
            calculate_seller_receives(100, currency=jpy)

    def test_custom_currency_with_price_step_and_higher_minimum(self):
        """Test custom currency configuration with verified non-standard price step and minimum."""
        custom = CurrencyConfig(
            currency_id=99,
            code="CUST",
            symbol="C",
            minor_unit_precision=2,
            min_listing_price_minor=10,
            price_step_minor=5,
            is_metadata_verified=True,
            is_fee_verified=True,
            is_listing_min_verified=True,
            is_price_step_verified=True,
        )
        # 10 is >= min_listing_price and divisible by price_step (5)
        res = calculate_seller_receives(10, currency=custom)
        self.assertLessEqual(res.actual_buyer_pays, 10)

        # 8 is below min_listing_price (10)
        with self.assertRaises(ValueError):
            calculate_seller_receives(8, currency=custom)

        # 12 is not divisible by price_step (5)
        with self.assertRaises(ValueError):
            calculate_seller_receives(12, currency=custom)

    def test_tunisia_context_billing(self):
        """Tunisia context must map to USD billing without inventing an unsupported TND currency."""
        tn = CurrencyConfig.get_tunisia_context()
        self.assertEqual(tn.code, "USD")
        self.assertEqual(tn.country, "TN")
        self.assertEqual(tn.min_listing_price_minor, 3)
        self.assertTrue(tn.is_verified)
        self.assertIn("TND is not a supported Steam wallet currency", tn.notes)

    def test_invalid_arguments(self):
        with self.assertRaises(ValueError):
            calculate_buyer_pays(0)
        with self.assertRaises(ValueError):
            calculate_seller_receives(2)  # Below universal fee floor of 3 cents


if __name__ == "__main__":
    unittest.main()
