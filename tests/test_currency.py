"""Unit tests for explicit currency configuration, negative values, and non-float precision."""

import unittest
from sniper.core.currency import CurrencyConfig, assert_same_currency


class TestCurrency(unittest.TestCase):
    def test_usd_formatting(self):
        usd = CurrencyConfig.get_default_usd()
        self.assertEqual(usd.format_amount(2488), "$24.88")
        self.assertEqual(usd.format_amount(3), "$0.03")
        self.assertEqual(usd.format_amount(100), "$1.00")
        self.assertEqual(usd.format_amount(0), "$0.00")

    def test_usd_negative_formatting(self):
        usd = CurrencyConfig.get_default_usd()
        self.assertEqual(usd.format_amount(-2488), "-$24.88")
        self.assertEqual(usd.format_amount(-1), "-$0.01")
        self.assertEqual(usd.format_amount(-100), "-$1.00")

    def test_usd_parsing(self):
        usd = CurrencyConfig.get_default_usd()
        self.assertEqual(usd.parse_to_minor_units("$24.88"), 2488)
        self.assertEqual(usd.parse_to_minor_units("100"), 10000)
        self.assertEqual(usd.parse_to_minor_units("0.03"), 3)
        self.assertEqual(usd.parse_to_minor_units("24.8"), 2480)

    def test_usd_negative_parsing(self):
        usd = CurrencyConfig.get_default_usd()
        self.assertEqual(usd.parse_to_minor_units("-$24.88"), -2488)
        self.assertEqual(usd.parse_to_minor_units("-24.88"), -2488)
        self.assertEqual(usd.parse_to_minor_units("-$0.01"), -1)
        self.assertEqual(usd.parse_to_minor_units("-100"), -10000)

    def test_strict_minus_grammar(self):
        """Reject misplaced, repeated, or post-symbol minus signs."""
        usd = CurrencyConfig.get_default_usd()
        # Misplaced in middle
        with self.assertRaises(ValueError):
            usd.parse_to_minor_units("2-4.88")
        # Misplaced after symbol
        with self.assertRaises(ValueError):
            usd.parse_to_minor_units("$-24.88")
        # Trailing minus
        with self.assertRaises(ValueError):
            usd.parse_to_minor_units("24.88-")
        # Repeated minus
        with self.assertRaises(ValueError):
            usd.parse_to_minor_units("--24.88")

    def test_strict_comma_grouping_grammar(self):
        """Verify standard 3-digit comma grouping and reject malformed separators."""
        usd = CurrencyConfig.get_default_usd()
        # Valid groupings
        self.assertEqual(usd.parse_to_minor_units("1,240.50"), 124050)
        self.assertEqual(usd.parse_to_minor_units("$1,240.50"), 124050)
        self.assertEqual(usd.parse_to_minor_units("1,000,000.00"), 100000000)
        self.assertEqual(usd.parse_to_minor_units("2,488"), 248800)

        # Malformed groupings
        with self.assertRaises(ValueError):
            usd.parse_to_minor_units("2,,4.88")
        with self.assertRaises(ValueError):
            usd.parse_to_minor_units(",24.88")
        with self.assertRaises(ValueError):
            usd.parse_to_minor_units("24,88.00")
        with self.assertRaises(ValueError):
            usd.parse_to_minor_units("24,")
        with self.assertRaises(ValueError):
            usd.parse_to_minor_units("1,2.50")

    def test_strict_currency_symbol_grammar(self):
        """Reject multiple or misplaced currency symbols."""
        usd = CurrencyConfig.get_default_usd()
        with self.assertRaises(ValueError):
            usd.parse_to_minor_units("$$24.88")
        with self.assertRaises(ValueError):
            usd.parse_to_minor_units("$24.88$")
        with self.assertRaises(ValueError):
            usd.parse_to_minor_units("24.88$")

    def test_usd_excess_decimal_rejection(self):
        usd = CurrencyConfig.get_default_usd()
        with self.assertRaises(ValueError):
            usd.parse_to_minor_units("$24.885")
        with self.assertRaises(ValueError):
            usd.parse_to_minor_units("0.001")

    def test_jpy_zero_decimals(self):
        jpy = CurrencyConfig.get_jpy()
        self.assertEqual(jpy.format_amount(1500), "¥1500")
        self.assertEqual(jpy.format_amount(-1500), "-¥1500")
        self.assertEqual(jpy.parse_to_minor_units("¥1500"), 1500)
        self.assertEqual(jpy.parse_to_minor_units("-1500"), -1500)

    def test_jpy_decimal_rejection(self):
        jpy = CurrencyConfig.get_jpy()
        with self.assertRaises(ValueError):
            jpy.parse_to_minor_units("¥1500.00")
        with self.assertRaises(ValueError):
            jpy.parse_to_minor_units("1500.5")

    def test_invalid_monetary_formats(self):
        usd = CurrencyConfig.get_default_usd()
        with self.assertRaises(ValueError):
            usd.parse_to_minor_units("")
        with self.assertRaises(ValueError):
            usd.parse_to_minor_units("  ")
        with self.assertRaises(ValueError):
            usd.parse_to_minor_units("$abc")
        with self.assertRaises(ValueError):
            usd.parse_to_minor_units("24..88")

    def test_currency_mismatch_assertion(self):
        usd = CurrencyConfig.get_default_usd()
        eur = CurrencyConfig.get_eur()
        with self.assertRaises(ValueError):
            assert_same_currency(usd, eur)


if __name__ == "__main__":
    unittest.main()
