"""Unit tests for Steam fee calculations, currency-specific minimums, and fail-closed safety (M4)."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
import unittest

from sniper.core.currency import CurrencyConfig
from sniper.core.fees import (
    FeeBreakdown,
    calculate_buyer_pays,
    calculate_exact_breakeven_buyer_pays,
    calculate_seller_receives,
    validate_market_listing_price,
)


class TestFees(unittest.TestCase):
    # -------------------------------------------------------------------------
    # 1. Forward and Inverse Boundary Cases
    # -------------------------------------------------------------------------

    def test_mathematical_fee_floor_forward(self) -> None:
        """The fee formula maps 1 cent proceeds to 3 cents gross; this is not listing-minimum evidence."""
        res = calculate_buyer_pays(1)
        self.assertEqual(res.seller_receives, 1)
        self.assertEqual(res.steam_fee, 1)
        self.assertEqual(res.publisher_fee, 1)
        self.assertEqual(res.total_fee, 2)
        self.assertEqual(res.actual_buyer_pays, 3)
        self.assertTrue(res.is_exact_match)

    def test_mathematical_fee_floor_inverse(self) -> None:
        """Inverting 3 cents buyer pays yields 1 cent seller proceeds with exact match."""
        res = calculate_seller_receives(3, validate_market_listing=False)
        self.assertEqual(res.seller_receives, 1)
        self.assertEqual(res.steam_fee, 1)
        self.assertEqual(res.publisher_fee, 1)
        self.assertEqual(res.total_fee, 2)
        self.assertEqual(res.actual_buyer_pays, 3)
        self.assertTrue(res.is_exact_match)

    def test_one_dollar_forward(self) -> None:
        """At 88 cents seller proceeds, buyer pays 100 cents ($1.00)."""
        res = calculate_buyer_pays(88)
        self.assertEqual(res.seller_receives, 88)
        self.assertEqual(res.steam_fee, 4)       # floor(88 * 5 / 100) = 4
        self.assertEqual(res.publisher_fee, 8)   # floor(88 * 10 / 100) = 8
        self.assertEqual(res.total_fee, 12)
        self.assertEqual(res.actual_buyer_pays, 100)
        self.assertTrue(res.is_exact_match)

    def test_one_dollar_inverse(self) -> None:
        """Inverting 100 cents buyer pays yields 88 cents seller proceeds with exact match."""
        res = calculate_seller_receives(100, validate_market_listing=False)
        self.assertEqual(res.seller_receives, 88)
        self.assertEqual(res.steam_fee, 4)
        self.assertEqual(res.publisher_fee, 8)
        self.assertEqual(res.total_fee, 12)
        self.assertEqual(res.actual_buyer_pays, 100)
        self.assertTrue(res.is_exact_match)

    def test_ten_dollar_boundary(self) -> None:
        """$10.00 (1,000 cents): buyer pays 1,000 cents; seller receives 870 cents."""
        # Forward
        fwd = calculate_buyer_pays(870)
        self.assertEqual(fwd.seller_receives, 870)
        self.assertEqual(fwd.steam_fee, 43)        # floor(870 * 5 / 100) = 43
        self.assertEqual(fwd.publisher_fee, 87)    # floor(870 * 10 / 100) = 87
        self.assertEqual(fwd.total_fee, 130)
        self.assertEqual(fwd.actual_buyer_pays, 1000)
        self.assertTrue(fwd.is_exact_match)

        # Inverse
        inv = calculate_seller_receives(1000, validate_market_listing=False)
        self.assertEqual(inv.seller_receives, 870)
        self.assertEqual(inv.steam_fee, 43)
        self.assertEqual(inv.publisher_fee, 87)
        self.assertEqual(inv.total_fee, 130)
        self.assertEqual(inv.actual_buyer_pays, 1000)
        self.assertTrue(inv.is_exact_match)

        # Verify next seller proceeds unit (871) exceeds 1000 cents
        next_fwd = calculate_buyer_pays(871)
        self.assertEqual(next_fwd.actual_buyer_pays, 1001)
        self.assertGreater(next_fwd.actual_buyer_pays, 1000)

    def test_one_thousand_dollar_boundary(self) -> None:
        """$1,000.00 (100,000 cents): buyer pays 100,000 cents; seller receives 86,958 cents."""
        # Forward
        fwd = calculate_buyer_pays(86958)
        self.assertEqual(fwd.seller_receives, 86958)
        self.assertEqual(fwd.steam_fee, 4347)       # floor(86958 * 5 / 100) = 4347
        self.assertEqual(fwd.publisher_fee, 8695)   # floor(86958 * 10 / 100) = 8695
        self.assertEqual(fwd.total_fee, 13042)
        self.assertEqual(fwd.actual_buyer_pays, 100000)
        self.assertTrue(fwd.is_exact_match)

        # Inverse
        inv = calculate_seller_receives(100000, validate_market_listing=False)
        self.assertEqual(inv.seller_receives, 86958)
        self.assertEqual(inv.steam_fee, 4347)
        self.assertEqual(inv.publisher_fee, 8695)
        self.assertEqual(inv.total_fee, 13042)
        self.assertEqual(inv.actual_buyer_pays, 100000)
        self.assertTrue(inv.is_exact_match)

        # Verify next seller proceeds unit (86959) exceeds 100,000 cents
        next_fwd = calculate_buyer_pays(86959)
        self.assertEqual(next_fwd.actual_buyer_pays, 100001)
        self.assertGreater(next_fwd.actual_buyer_pays, 100000)

    # -------------------------------------------------------------------------
    # 2. Fee Step-Jump Cases
    # -------------------------------------------------------------------------

    def test_step_jump_gap(self) -> None:
        """R=99 -> 112, R=100 -> 115. Buyer requests 113 and 114 are unreachable.

        Inversion must return R=99, actual_buyer_pays=112, and is_exact_match=False.
        """
        # Step jump points
        fwd_99 = calculate_buyer_pays(99)
        self.assertEqual(fwd_99.seller_receives, 99)
        self.assertEqual(fwd_99.steam_fee, 4)
        self.assertEqual(fwd_99.publisher_fee, 9)
        self.assertEqual(fwd_99.actual_buyer_pays, 112)

        fwd_100 = calculate_buyer_pays(100)
        self.assertEqual(fwd_100.seller_receives, 100)
        self.assertEqual(fwd_100.steam_fee, 5)
        self.assertEqual(fwd_100.publisher_fee, 10)
        self.assertEqual(fwd_100.actual_buyer_pays, 115)

        # Unreachable 113
        res_113 = calculate_seller_receives(113, validate_market_listing=False)
        self.assertEqual(res_113.seller_receives, 99)
        self.assertEqual(res_113.actual_buyer_pays, 112)
        self.assertFalse(res_113.is_exact_match)

        # Unreachable 114
        res_114 = calculate_seller_receives(114, validate_market_listing=False)
        self.assertEqual(res_114.seller_receives, 99)
        self.assertEqual(res_114.actual_buyer_pays, 112)
        self.assertFalse(res_114.is_exact_match)

    # -------------------------------------------------------------------------
    # 3. High-Tier Case ($500.00)
    # -------------------------------------------------------------------------

    def test_high_tier_price(self) -> None:
        """Requested buyer-paid of $500.00 (50,000 cents) returns seller proceeds $434.79 (43,479 cents),
        actual buyer-paid $499.99 (49,999 cents), and is_exact_match=False.
        """
        res = calculate_seller_receives(50000, validate_market_listing=False)
        self.assertEqual(res.seller_receives, 43479)
        self.assertEqual(res.steam_fee, 2173)       # floor(43479 * 5 / 100) = 2173
        self.assertEqual(res.publisher_fee, 4347)   # floor(43479 * 10 / 100) = 4347
        self.assertEqual(res.total_fee, 6520)
        self.assertEqual(res.actual_buyer_pays, 49999)
        self.assertFalse(res.is_exact_match)

        # Prove next seller proceeds unit (43480) strictly exceeds 50000
        next_fwd = calculate_buyer_pays(43480)
        self.assertEqual(next_fwd.actual_buyer_pays, 50002)
        self.assertGreater(next_fwd.actual_buyer_pays, 50000)

    # -------------------------------------------------------------------------
    # 4. Inversion Properties (Deterministic Range Invariant Test)
    # -------------------------------------------------------------------------

    def test_inversion_monotonic_properties_over_range(self) -> None:
        """Exhaustively verify mathematical inversion invariants over 3 to 5,000 cents.

        Guarantees:
        1. actual_buyer_pays <= requested_buyer_pays
        2. calculate_buyer_pays(seller_receives).actual_buyer_pays == actual_buyer_pays
        3. calculate_buyer_pays(seller_receives + 1).actual_buyer_pays > requested_buyer_pays (maximal R)
        4. is_exact_match is consistent with equality (actual == requested)
        5. Monotonicity: seller_receives(B + 1) >= seller_receives(B)
        """
        prev_r = 1
        for b_req in range(3, 5001):
            inv = calculate_seller_receives(b_req, validate_market_listing=False)
            # 1. Never exceeds requested
            self.assertLessEqual(inv.actual_buyer_pays, b_req)
            # 2. Forward consistency
            fwd = calculate_buyer_pays(inv.seller_receives)
            self.assertEqual(fwd.actual_buyer_pays, inv.actual_buyer_pays)
            self.assertEqual(fwd.steam_fee, inv.steam_fee)
            self.assertEqual(fwd.publisher_fee, inv.publisher_fee)
            self.assertEqual(fwd.total_fee, inv.total_fee)
            # 3. Maximal R: next R must exceed b_req
            next_fwd = calculate_buyer_pays(inv.seller_receives + 1)
            self.assertGreater(next_fwd.actual_buyer_pays, b_req)
            # 4. Exact match consistency
            self.assertEqual(inv.is_exact_match, (inv.actual_buyer_pays == b_req))
            # 5. Monotonicity
            self.assertGreaterEqual(inv.seller_receives, prev_r)
            prev_r = inv.seller_receives

    # -------------------------------------------------------------------------
    # 5. Strict Type Validation & Boolean Rejection
    # -------------------------------------------------------------------------

    def test_strict_type_validation_rejects_booleans(self) -> None:
        """Python booleans subclass int, but must be strictly rejected from all monetary and fee args."""
        usd = CurrencyConfig.get_default_usd()

        # calculate_buyer_pays
        with self.assertRaises(TypeError):
            calculate_buyer_pays(True)
        with self.assertRaises(TypeError):
            calculate_buyer_pays(False)
        with self.assertRaises(TypeError):
            calculate_buyer_pays(88, publisher_fee_pct=True)
        with self.assertRaises(TypeError):
            calculate_buyer_pays(88, publisher_fee_pct=False)

        # calculate_seller_receives
        with self.assertRaises(TypeError):
            calculate_seller_receives(True, validate_market_listing=False)
        with self.assertRaises(TypeError):
            calculate_seller_receives(False, validate_market_listing=False)
        with self.assertRaises(TypeError):
            calculate_seller_receives(100, publisher_fee_pct=True, validate_market_listing=False)
        with self.assertRaises(TypeError):
            calculate_seller_receives(100, validate_market_listing=123)

        # calculate_exact_breakeven_buyer_pays
        with self.assertRaises(TypeError):
            calculate_exact_breakeven_buyer_pays(True)
        with self.assertRaises(TypeError):
            calculate_exact_breakeven_buyer_pays(False)
        with self.assertRaises(TypeError):
            calculate_exact_breakeven_buyer_pays(100, publisher_fee_pct=True)

        # validate_market_listing_price
        with self.assertRaises(TypeError):
            validate_market_listing_price(True, usd)
        with self.assertRaises(TypeError):
            validate_market_listing_price(False, usd)

    def test_strict_type_validation_rejects_floats_and_strings(self) -> None:
        """Floats, strings, and other non-integers must raise TypeError without silent coercion."""
        usd = CurrencyConfig.get_default_usd()

        # Floats
        for bad_val in (100.5, 1.0, 88.0, 0.03):
            with self.assertRaises(TypeError):
                calculate_buyer_pays(bad_val)
            with self.assertRaises(TypeError):
                calculate_seller_receives(bad_val, validate_market_listing=False)
            with self.assertRaises(TypeError):
                calculate_exact_breakeven_buyer_pays(bad_val)
            with self.assertRaises(TypeError):
                validate_market_listing_price(bad_val, usd)

        # Strings
        for bad_str in ("100", "0.03", "88", "True", "abc"):
            with self.assertRaises(TypeError):
                calculate_buyer_pays(bad_str)
            with self.assertRaises(TypeError):
                calculate_seller_receives(bad_str, validate_market_listing=False)
            with self.assertRaises(TypeError):
                calculate_exact_breakeven_buyer_pays(bad_str)
            with self.assertRaises(TypeError):
                validate_market_listing_price(bad_str, usd)

    def test_strict_type_validation_rejects_invalid_currency_type(self) -> None:
        """Non-CurrencyConfig instances passed as currency argument must raise TypeError."""
        with self.assertRaises(TypeError):
            calculate_buyer_pays(100, currency="USD")
        with self.assertRaises(TypeError):
            calculate_seller_receives(100, currency={"code": "USD"}, validate_market_listing=False)
        with self.assertRaises(TypeError):
            validate_market_listing_price(100, currency="USD")

    # -------------------------------------------------------------------------
    # 6. Value Validation & Negative / Zero Amounts
    # -------------------------------------------------------------------------

    def test_value_validation_rejects_non_positive_and_negative_amounts(self) -> None:
        """Zero and negative monetary amounts or percentages must raise ValueError."""
        # Zero
        with self.assertRaises(ValueError):
            calculate_buyer_pays(0)
        with self.assertRaises(ValueError):
            calculate_seller_receives(0, validate_market_listing=False)
        with self.assertRaises(ValueError):
            calculate_exact_breakeven_buyer_pays(0)

        # Negative proceeds / prices
        with self.assertRaises(ValueError):
            calculate_buyer_pays(-1)
        with self.assertRaises(ValueError):
            calculate_buyer_pays(-100)
        with self.assertRaises(ValueError):
            calculate_seller_receives(-1, validate_market_listing=False)
        with self.assertRaises(ValueError):
            calculate_seller_receives(-100, validate_market_listing=False)
        with self.assertRaises(ValueError):
            calculate_exact_breakeven_buyer_pays(-1)

        # Negative publisher fee percentage
        with self.assertRaises(ValueError):
            calculate_buyer_pays(100, publisher_fee_pct=-1)
        with self.assertRaises(ValueError):
            calculate_seller_receives(100, publisher_fee_pct=-5, validate_market_listing=False)
        with self.assertRaises(ValueError):
            calculate_exact_breakeven_buyer_pays(100, publisher_fee_pct=-10)

        # Below mathematical fee floor (minimum is 3 cents for 1+1+1)
        with self.assertRaises(ValueError):
            calculate_seller_receives(1, validate_market_listing=False)
        with self.assertRaises(ValueError):
            calculate_seller_receives(2, validate_market_listing=False)

    def test_publisher_fee_percentage_bounds_rejection(self) -> None:
        """Verify all three public fee functions reject publisher_fee_pct outside 0-100 range."""
        invalid_percentages = [-1, 101, 200]

        for pct in invalid_percentages:
            with self.assertRaises(ValueError, msg=f"calculate_buyer_pays failed to reject pct={pct}"):
                calculate_buyer_pays(100, publisher_fee_pct=pct)

            with self.assertRaises(ValueError, msg=f"calculate_seller_receives failed to reject pct={pct}"):
                calculate_seller_receives(100, publisher_fee_pct=pct, validate_market_listing=False)

            with self.assertRaises(ValueError, msg=f"calculate_exact_breakeven_buyer_pays failed to reject pct={pct}"):
                calculate_exact_breakeven_buyer_pays(100, publisher_fee_pct=pct)

    def test_publisher_fee_percentage_boundary_validity(self) -> None:
        """Verify 0% and 100% publisher fee percentages are valid boundaries."""
        # 0% publisher fee (enforces minimum publisher fee component floor of 1 minor unit)
        fwd_0 = calculate_buyer_pays(100, publisher_fee_pct=0)
        self.assertEqual(fwd_0.seller_receives, 100)
        self.assertEqual(fwd_0.steam_fee, 5)
        self.assertEqual(fwd_0.publisher_fee, 1)  # max(1, (100 * 0) // 100) = 1
        self.assertEqual(fwd_0.actual_buyer_pays, 106)

        inv_0 = calculate_seller_receives(106, publisher_fee_pct=0, validate_market_listing=False)
        self.assertEqual(inv_0.seller_receives, 100)
        self.assertEqual(inv_0.actual_buyer_pays, 106)
        self.assertTrue(inv_0.is_exact_match)

        be_0 = calculate_exact_breakeven_buyer_pays(100, publisher_fee_pct=0)
        self.assertEqual(be_0, 106)

        # 100% publisher fee
        fwd_100 = calculate_buyer_pays(100, publisher_fee_pct=100)
        self.assertEqual(fwd_100.seller_receives, 100)
        self.assertEqual(fwd_100.steam_fee, 5)
        self.assertEqual(fwd_100.publisher_fee, 100)
        self.assertEqual(fwd_100.actual_buyer_pays, 205)

        inv_100 = calculate_seller_receives(205, publisher_fee_pct=100, validate_market_listing=False)
        self.assertEqual(inv_100.seller_receives, 100)
        self.assertEqual(inv_100.actual_buyer_pays, 205)
        self.assertTrue(inv_100.is_exact_match)

        be_100 = calculate_exact_breakeven_buyer_pays(100, publisher_fee_pct=100)
        self.assertEqual(be_100, 205)

    def test_regression_invalid_pct_200_at_fee_floor_rejected(self) -> None:
        """Regression test demonstrating calculate_seller_receives(3, publisher_fee_pct=200, validate_market_listing=False)
        is rejected rather than returning a buyer-paid amount exceeding 3.
        """
        with self.assertRaises(ValueError) as ctx:
            calculate_seller_receives(3, publisher_fee_pct=200, validate_market_listing=False)
        self.assertIn("publisher_fee_pct must be between 0 and 100 inclusive", str(ctx.exception))

    # -------------------------------------------------------------------------
    # 7. Currency Configuration Hardening & Fee Minimums
    # -------------------------------------------------------------------------

    def test_currency_config_validation_rejects_invalid_values(self) -> None:
        """CurrencyConfig __post_init__ must validate integer types and non-negative constraints."""
        # Non-integer currency_id
        with self.assertRaises(TypeError):
            CurrencyConfig(currency_id="1", code="USD", symbol="$", minor_unit_precision=2)
        with self.assertRaises(TypeError):
            CurrencyConfig(currency_id=True, code="USD", symbol="$", minor_unit_precision=2)

        # Negative or non-int precision
        with self.assertRaises(ValueError):
            CurrencyConfig(currency_id=1, code="USD", symbol="$", minor_unit_precision=-1)
        with self.assertRaises(TypeError):
            CurrencyConfig(currency_id=1, code="USD", symbol="$", minor_unit_precision="2")

        # Invalid price_step_minor
        with self.assertRaises(ValueError):
            CurrencyConfig(currency_id=1, code="USD", symbol="$", minor_unit_precision=2, price_step_minor=0)
        with self.assertRaises(TypeError):
            CurrencyConfig(currency_id=1, code="USD", symbol="$", minor_unit_precision=2, price_step_minor=True)

        # Invalid min_fee_steam_minor
        with self.assertRaises(ValueError):
            CurrencyConfig(currency_id=1, code="USD", symbol="$", minor_unit_precision=2, min_fee_steam_minor=0)
        with self.assertRaises(TypeError):
            CurrencyConfig(currency_id=1, code="USD", symbol="$", minor_unit_precision=2, min_fee_steam_minor=1.5)

        # Invalid min_fee_publisher_minor
        with self.assertRaises(ValueError):
            CurrencyConfig(currency_id=1, code="USD", symbol="$", minor_unit_precision=2, min_fee_publisher_minor=-1)
        with self.assertRaises(TypeError):
            CurrencyConfig(currency_id=1, code="USD", symbol="$", minor_unit_precision=2, min_fee_publisher_minor=False)

        # Invalid min_listing_price_minor
        with self.assertRaises(ValueError):
            CurrencyConfig(currency_id=1, code="USD", symbol="$", minor_unit_precision=2, min_listing_price_minor=0)
        with self.assertRaises(TypeError):
            CurrencyConfig(currency_id=1, code="USD", symbol="$", minor_unit_precision=2, min_listing_price_minor=True)

    def test_fee_breakdown_immutability_and_validation(self) -> None:
        """FeeBreakdown is frozen and validates field types upon construction."""
        breakdown = FeeBreakdown(
            seller_receives=88,
            steam_fee=4,
            publisher_fee=8,
            total_fee=12,
            actual_buyer_pays=100,
            is_exact_match=True,
        )
        with self.assertRaises(FrozenInstanceError):
            breakdown.seller_receives = 90

        # Type validation
        with self.assertRaises(TypeError):
            FeeBreakdown(
                seller_receives=True,
                steam_fee=4,
                publisher_fee=8,
                total_fee=12,
                actual_buyer_pays=100,
                is_exact_match=True,
            )
        with self.assertRaises(TypeError):
            FeeBreakdown(
                seller_receives=88,
                steam_fee=4,
                publisher_fee=8,
                total_fee=12,
                actual_buyer_pays=100,
                is_exact_match="True",
            )

    # -------------------------------------------------------------------------
    # 8. Break-even Calculation Tests
    # -------------------------------------------------------------------------

    def test_breakeven_calculation(self) -> None:
        """Exact break-even buyer price must yield at least the cost basis."""
        cost = 2488  # $24.88
        breakeven_buyer = calculate_exact_breakeven_buyer_pays(cost)
        res = calculate_seller_receives(breakeven_buyer, validate_market_listing=False)
        self.assertGreaterEqual(res.seller_receives, cost)

    def test_breakeven_calculation_various_tiers(self) -> None:
        """Verify break-even calculation across diverse cost basis amounts."""
        for cost in (1, 3, 10, 50, 88, 100, 870, 1000, 2488, 43479, 86958):
            breakeven_buyer = calculate_exact_breakeven_buyer_pays(cost)
            # Forward must produce this buyer price directly
            fwd = calculate_buyer_pays(cost)
            self.assertEqual(breakeven_buyer, fwd.actual_buyer_pays)
            # Inverting breakeven buyer price must yield exactly cost basis
            inv = calculate_seller_receives(breakeven_buyer, validate_market_listing=False)
            self.assertEqual(inv.seller_receives, cost)
            self.assertEqual(inv.actual_buyer_pays, breakeven_buyer)
            self.assertTrue(inv.is_exact_match)

    # -------------------------------------------------------------------------
    # 9. Market Listing Validation vs Pure Mathematical Fee Calculation
    # -------------------------------------------------------------------------

    def test_usd_listing_minimum_is_unknown(self) -> None:
        """Fee floor is mathematical; Valve FAQ does not establish a USD listing minimum."""
        usd = CurrencyConfig.get_default_usd()
        self.assertTrue(usd.is_metadata_verified)
        self.assertTrue(usd.is_fee_verified)
        self.assertFalse(usd.is_listing_min_verified)
        self.assertIsNone(usd.min_listing_price_minor)
        with self.assertRaises(ValueError):
            calculate_seller_receives(100, currency=usd)

    def test_market_validation_requires_currency_and_pure_inverse_is_explicit(self) -> None:
        """calculate_seller_receives requires CurrencyConfig when validate_market_listing=True."""
        with self.assertRaises(ValueError):
            calculate_seller_receives(100)
        res = calculate_seller_receives(100, validate_market_listing=False)
        self.assertEqual(res.seller_receives, 88)
        self.assertEqual(res.actual_buyer_pays, 100)

    def test_fee_math_with_currency_requires_verified_metadata(self) -> None:
        """Currency metadata must be verified to perform financial calculations."""
        cfg = CurrencyConfig(
            currency_id=96, code="TEST3", symbol="T", minor_unit_precision=2,
            is_metadata_verified=False, is_fee_verified=True,
        )
        with self.assertRaises(ValueError):
            calculate_buyer_pays(100, currency=cfg)
        with self.assertRaises(ValueError):
            calculate_seller_receives(100, currency=cfg, validate_market_listing=False)

    def test_eur_unverified_listing_rules_fails_closed_on_market_validation(self) -> None:
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

    def test_fee_verified_true_but_listing_min_unknown(self) -> None:
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

    def test_price_step_verified_false_with_price_step_one(self) -> None:
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

    def test_unverified_currency_fails_closed(self) -> None:
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

    def test_custom_currency_with_price_step_and_higher_minimum(self) -> None:
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
        self.assertEqual(res.actual_buyer_pays, 10)
        self.assertTrue(res.is_exact_match)

        # 8 is below min_listing_price (10)
        with self.assertRaises(ValueError):
            calculate_seller_receives(8, currency=custom)

        # 12 is not divisible by price_step (5)
        with self.assertRaises(ValueError):
            calculate_seller_receives(12, currency=custom)

    def test_tunisia_usd_metadata_and_unknown_listing_rules(self) -> None:
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

    def test_adjusted_unreachable_price_cannot_bypass_listing_constraints(self) -> None:
        """Ensure any adjusted buyer-paid amount returned by inversion cannot silently bypass applicable listing constraints."""
        custom = CurrencyConfig(
            currency_id=99,
            code="CUST2",
            symbol="C2",
            minor_unit_precision=2,
            min_listing_price_minor=110,
            price_step_minor=1,
            is_metadata_verified=True,
            is_fee_verified=True,
            is_listing_min_verified=True,
            is_price_step_verified=True,
        )
        # 115 is reachable (R=100) -> exact match at 115 >= 110
        res_115 = calculate_seller_receives(115, currency=custom, validate_market_listing=True)
        self.assertEqual(res_115.actual_buyer_pays, 115)
        self.assertTrue(res_115.is_exact_match)

        # 114 is unreachable; lower reachable price is 112 (R=99).
        # Since 112 >= 110 and 112 % 1 == 0, 112 satisfies market listing constraints.
        res_114 = calculate_seller_receives(114, currency=custom, validate_market_listing=True)
        self.assertEqual(res_114.actual_buyer_pays, 112)
        self.assertFalse(res_114.is_exact_match)

        # Now test where the adjusted price would violate min_listing_price_minor:
        # min_listing_price_minor = 113. Requested is 114 (valid >= 113).
        # But adjusted reachable price is 112 (< 113)!
        custom_strict = CurrencyConfig(
            currency_id=99,
            code="CUST3",
            symbol="C3",
            minor_unit_precision=2,
            min_listing_price_minor=113,
            price_step_minor=1,
            is_metadata_verified=True,
            is_fee_verified=True,
            is_listing_min_verified=True,
            is_price_step_verified=True,
        )
        with self.assertRaises(ValueError) as ctx:
            calculate_seller_receives(114, currency=custom_strict, validate_market_listing=True)
        self.assertIn("below minimum market listing price", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
