"""Unit tests for Steam fee calculations and integer cents precision."""

import unittest
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
        # Forward calculation of seller_receives must equal actual_buyer_pays
        forward = calculate_buyer_pays(res.seller_receives)
        self.assertEqual(forward.actual_buyer_pays, res.actual_buyer_pays)
        self.assertLessEqual(res.actual_buyer_pays, 50000)

    def test_breakeven_calculation(self):
        """Exact break-even buyer price must yield at least the cost basis."""
        cost = 2488  # $24.88
        breakeven_buyer = calculate_exact_breakeven_buyer_pays(cost)
        res = calculate_seller_receives(breakeven_buyer)
        self.assertGreaterEqual(res.seller_receives, cost)

    def test_invalid_arguments(self):
        with self.assertRaises(ValueError):
            calculate_buyer_pays(0)
        with self.assertRaises(ValueError):
            calculate_seller_receives(2)  # Less than minimum 3 cents


if __name__ == "__main__":
    unittest.main()
