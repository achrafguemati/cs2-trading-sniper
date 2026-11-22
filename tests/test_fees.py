"""Unit tests for Steam fee calculations, currency-specific minimums, and fail-closed safety."""

from __future__ import annotations

import unittest

from sniper.core.currency import CurrencyConfig
from sniper.core.fees import (
    calculate_buyer_pays,
    calculate_exact_breakeven_buyer_pays,
    calculate_seller_receives,
    validate_market_listing_price,
)


class TestFees(unittest.TestCase):
    def test_mathematical_fee_floor_forward(self):
        """The fee formula maps 1 cent proceeds to 3 cents gross; this is not listing-minimum evidence."""
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
        res = calculate_seller_receives(100, validate_market_listing=False)
        self.assertEqual(res.seller_receives, 88)
        self.assertEqual(res.actual_buyer_pays, 100)
        self.assertTrue(res.is_exact_match)

    def test_step_jump_gap(self):
        """114 cents buyer pays is in a fee step-jump gap (R=99 -> 112, R=100 -> 115).
        
        The function must return R=99, actual_buyer_pays=112, and is_exact_match=False.
        """
        res = calculate_seller_receives(114, validate_market_listing=False)
        self.assertEqual(res.seller_receives, 99)
        self.assertEqual(res.actual_buyer_pays, 112)
        self.assertFalse(res.is_exact_match)

    def test_high_tier_price(self):
        """Test high tier item: $500.00 (50000 cents)."""
        res = calculate_seller_receives(50000, validate_market_listing=False)
        forward = calculate_buyer_pays(res.seller_receives)
        self.assertEqual(forward.actual_buyer_pays, res.actual_buyer_pays)
        self.assertLessEqual(res.actual_buyer_pays, 50000)

    def test_breakeven_calculation(self):
        """Exact break-even buyer price must yield at least the cost basis."""
        cost = 2488  # $24.88
        breakeven_buyer = calculate_exact_breakeven_buyer_pays(cost)
        res = calculate_seller_receives(breakeven_buyer, validate_market_listing=False)
        self.assertGreaterEqual(res.seller_receives, cost)

    def test_usd_listing_minimum_is_unknown(self):
        """Fee floor is mathematical; Valve FAQ does not establish a USD listing minimum."""
        usd = CurrencyConfig.get_default_usd()
        self.assertTrue(usd.is_metadata_verified)
        self.assertTrue(usd.is_fee_verified)
        self.assertFalse(usd.is_listing_min_verified)
        self.assertIsNone(usd.min_listing_price_minor)
        with self.assertRaises(ValueError):
            calculate_seller_receives(100, currency=usd)

    def test_market_validation_requires_currency_and_pure_inverse_is_explicit(self):
        with self.assertRaises(ValueError):
            calculate_seller_receives(100)
        res = calculate_seller_receives(100, validate_market_listing=False)
        self.assertEqual(res.seller_receives, 88)
        self.assertEqual(res.actual_buyer_pays, 100)

    def test_fee_math_with_currency_requires_verified_metadata(self):
        cfg = CurrencyConfig(
            currency_id=96, code="TEST3", symbol="T", minor_unit_precision=2,
            is_metadata_verified=False, is_fee_verified=True,
        )
        with self.assertRaises(ValueError):
            calculate_buyer_pays(100, currency=cfg)
        with self.assertRaises(ValueError):
            calculate_seller_receives(100, currency=cfg, validate_market_listing=False)

    def test_eur_unverified_listing_rules_fails_closed_on_market_validation(self):
        """EUR fee rules are verified, but listing minimum is UNKNOWN without primary evidence.
        
        Market listing validation fails closed; pure fee calculation succeeds.
        """
        eur = CurrencyConfig.get_eur()
        self.assertTrue(eur.is_metadata_verified)
        self.assertTrue(eur.is_fee_verified)
        self.assertFalse(eur.is_listing_min_verified)
        self.assertIsNone(eur.min_listing_price_minor)
        self.assertFalse(eur.is_verified)

        # Default market listing validation fails closed
        with self.assertRaises(ValueError):
            calculate_seller_receives(100, currency=eur)

        with self.assertRaises(ValueError):
            validate_market_listing_price(100, eur)

        # Pure fee calculation succeeds
        pure_res = calculate_seller_receives(100, currency=eur, validate_market_listing=False)
        self.assertEqual(pure_res.seller_receives, 88)
        self.assertEqual(pure_res.actual_buyer_pays, 100)
        self.assertEqual(calculate_buyer_pays(88, currency=eur).actual_buyer_pays, 100)

    def test_fee_verified_true_but_listing_min_unknown(self):
        """Currency with verified fees but UNKNOWN listing minimum must fail market validation."""
        cfg = CurrencyConfig(
            currency_id=98,
            code="TEST",
            symbol="T",
            minor_unit_precision=2,
            min_listing_price_minor=None,  # UNKNOWN
            price_step_minor=1,
            is_metadata_verified=True,
            is_fee_verified=True,
            is_listing_min_verified=False, # UNKNOWN
            is_price_step_verified=True,
        )
        # Market listing validation fails closed
        with self.assertRaises(ValueError):
            calculate_seller_receives(100, currency=cfg, validate_market_listing=True)

        with self.assertRaises(ValueError):
            validate_market_listing_price(100, cfg)

        # Pure fee calculation succeeds
        res = calculate_seller_receives(100, currency=cfg, validate_market_listing=False)
        self.assertEqual(res.seller_receives, 88)
        self.assertEqual(res.actual_buyer_pays, 100)

    def test_price_step_verified_false_with_price_step_one(self):
        """Currency with unverified price step must not silently pass default price_step_minor=1."""
        cfg = CurrencyConfig(
            currency_id=97,
            code="TEST2",
            symbol="T2",
            minor_unit_precision=2,
            min_listing_price_minor=3,
            price_step_minor=1,             # default value 1
            is_metadata_verified=True,
            is_fee_verified=True,
            is_listing_min_verified=True,
            is_price_step_verified=False,   # UNKNOWN price step
        )
        # Market listing validation fails closed
        with self.assertRaises(ValueError):
            calculate_seller_receives(100, currency=cfg, validate_market_listing=True)

        with self.assertRaises(ValueError):
            validate_market_listing_price(100, cfg)

        # Pure fee calculation succeeds
        res = calculate_seller_receives(100, currency=cfg, validate_market_listing=False)
        self.assertEqual(res.seller_receives, 88)

    def test_unverified_currency_fails_closed(self):
        """Unverified currencies (like JPY) must strictly fail closed on financial fee calculations."""
        jpy = CurrencyConfig.get_jpy()
        self.assertFalse(jpy.is_fee_verified)
        self.assertFalse(jpy.is_listing_min_verified)
        self.assertFalse(jpy.is_verified)

        # calculate_buyer_pays on unverified currency must raise ValueError
        with self.assertRaises(ValueError):
            calculate_buyer_pays(100, currency=jpy)

        # calculate_seller_receives on unverified currency must raise ValueError (both modes)
        with self.assertRaises(ValueError):
            calculate_seller_receives(100, currency=jpy, validate_market_listing=True)
        with self.assertRaises(ValueError):
            calculate_seller_receives(100, currency=jpy, validate_market_listing=False)

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

    def test_tunisia_usd_metadata_and_unknown_listing_rules(self):
        """Steamworks USD_MENA metadata does not establish Community Market listing rules."""
        tn = CurrencyConfig.get_tunisia_context()
        self.assertEqual(tn.code, "USD")
        self.assertEqual(tn.country, "TN")
        self.assertIsNone(tn.min_listing_price_minor)
        self.assertFalse(tn.is_listing_min_verified)
        self.assertFalse(tn.is_price_step_verified)
        self.assertTrue(tn.is_metadata_verified)
        self.assertTrue(tn.is_fee_verified)

        # Market listing validation fails closed
        with self.assertRaises(ValueError):
            calculate_seller_receives(100, currency=tn, validate_market_listing=True)

        # Pure fee calculation succeeds
        res = calculate_seller_receives(100, currency=tn, validate_market_listing=False)
        self.assertEqual(res.seller_receives, 88)

    def test_invalid_arguments(self):
        with self.assertRaises(ValueError):
            calculate_buyer_pays(0)
        with self.assertRaises(ValueError):
            calculate_seller_receives(2, validate_market_listing=False)  # Below mathematical fee floor


if __name__ == "__main__":
    unittest.main()
