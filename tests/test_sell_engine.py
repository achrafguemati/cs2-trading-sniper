"""Deterministic unit tests for SELL Sniper Engine and opportunity scoring."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock
import unittest

from sniper.adapters.steam_market import SteamMarketAdapter
from sniper.core.identity import (
    VERIFIED_MAPPING_REPOSITORY,
    VERIFIED_MAPPING_REVISION,
)
from sniper.engine.sell_engine import (
    SellCandidateEvaluation,
    SellEvaluationConfig,
    SellSniperEngine,
    calculate_sell_confidence_score,
    calculate_sell_opportunity_score,
)
from sniper.models.schemas import OrderbookHistogramResponse
from sniper.notifications import DeliveryStatus, NotificationDispatcher, NotificationResult
from sniper.notifications.base import BaseNotifier
from sniper.storage.database import Database
from sniper.storage.models import InventoryItemRecord, ItemRecord, NotificationDeliveryRecord, OrderbookSnapshotRecord
from sniper.storage.repositories import (
    AlertRepository,
    CurrencyRepository,
    InventoryRepository,
    ItemRepository,
    OrderbookRepository,
)


def make_histogram(
    highest_buy_order_minor: int | None = 2500,
    lowest_sell_order_minor: int | None = 3000,
    top_level_buy_quantity: int = 5,
    cumulative_buy_quantity: int = 20,
) -> OrderbookHistogramResponse:
    """Helper constructing an OrderbookHistogramResponse with proper string and graph fields."""
    graph = []
    if highest_buy_order_minor is not None:
        graph = [[float(highest_buy_order_minor) / 100.0, top_level_buy_quantity, "test"]]
    return OrderbookHistogramResponse(
        success=1,
        highest_buy_order=str(highest_buy_order_minor) if highest_buy_order_minor is not None else None,
        lowest_sell_order=str(lowest_sell_order_minor) if lowest_sell_order_minor is not None else None,
        buy_order_count=str(cumulative_buy_quantity),
        buy_order_graph=graph,
    )


class TestSellEngine(unittest.IsolatedAsyncioTestCase):
    """Test suite for SELL Sniper Engine, quantitative scoring, and crossed book defense."""

    def setUp(self) -> None:
        self.db = Database(":memory:")
        self.db.initialize()
        self.curr_repo = CurrencyRepository(self.db)
        self.curr_repo.seed_default_currencies()
        self.item_repo = ItemRepository(self.db)
        self.inv_repo = InventoryRepository(self.db)
        self.ob_repo = OrderbookRepository(self.db)
        self.alert_repo = AlertRepository(self.db)

        # Seed valid verified catalog item
        self.item = self.item_repo.save(ItemRecord(
            market_hash_name="AK-47 | Redline (Field-Tested)",
            base_name="AK-47 | Redline",
            exterior="Field-Tested",
            item_nameid=7178002,
            item_nameid_source=VERIFIED_MAPPING_REPOSITORY,
            item_nameid_revision=VERIFIED_MAPPING_REVISION,
        ))

    def tearDown(self) -> None:
        self.db.close()

    def test_opportunity_scoring_formula(self) -> None:
        """Verify mathematical Opportunity Score calculation and penalties."""
        # Baseline multiple: 1.5x (gain = floor((1.5 - 1.0) * 100) = 50)
        # Depth: 5 items (depth = 100)
        # Fresh: 0s age (fresh = 100)
        # Expected raw: 0.50 * 50 + 0.30 * 100 + 0.20 * 100 = 25 + 30 + 20 = 75.0
        score = calculate_sell_opportunity_score(bid_multiple=1.5, top_level_buy_quantity=5, data_age_seconds=0.0)
        self.assertEqual(score, 75.0)

        # Crossed book penalty: -50 points
        score_crossed = calculate_sell_opportunity_score(
            bid_multiple=1.5, top_level_buy_quantity=5, data_age_seconds=0.0, is_crossed_book=True
        )
        self.assertEqual(score_crossed, 25.0)

        # Extreme high bid (>2.5x) with single quantity: -35 points penalty
        # multiple 3.0x -> gain=100 (capped). Depth=1 -> depth=35. Fresh=100.
        # raw: 0.5*100 + 0.3*35 + 0.2*100 = 50 + 10.5 + 20 = 80.5
        # penalty: 35 -> 80.5 - 35 = 45.5
        score_unverified = calculate_sell_opportunity_score(
            bid_multiple=3.0, top_level_buy_quantity=1, data_age_seconds=0.0
        )
        self.assertEqual(score_unverified, 45.5)

    def test_confidence_scoring_formula(self) -> None:
        """Verify Signal Confidence Score calculation and risk penalties."""
        # 10 historical samples (samples = 100)
        # Fresh: 0s age (fresh = 100)
        # Spread: bid=2500, ask=2800 -> spread_pct = (300/2800)*100 = 10.71 -> spread = 100 - 10 = 90
        # raw: 0.40 * 100 + 0.35 * 100 + 0.25 * 90 = 40 + 35 + 22.5 = 97.5
        conf = calculate_sell_confidence_score(
            historical_samples_count=10,
            highest_buy_order_minor=2500,
            lowest_sell_order_minor=2800,
            data_age_seconds=0.0,
            is_crossed_book=False,
        )
        self.assertEqual(conf, 97.5)

        # Insufficient sample count (<7 daily points): -50 points penalty
        conf_low_samples = calculate_sell_confidence_score(
            historical_samples_count=4,
            highest_buy_order_minor=2500,
            lowest_sell_order_minor=2800,
            data_age_seconds=0.0,
        )
        # samples=40 -> raw: 40 + 14 + 22.5 = 76.5 -> 76.5 - 50 = 26.5
        self.assertEqual(conf_low_samples, 26.5)

        # Crossed book: -60 points penalty
        conf_crossed = calculate_sell_confidence_score(
            historical_samples_count=10,
            highest_buy_order_minor=2800,
            lowest_sell_order_minor=2500,
            data_age_seconds=0.0,
            is_crossed_book=True,
        )
        # crossed book spread=0 -> raw: 40 + 35 + 0 = 75 -> 75 - 60 = 15.0
        self.assertEqual(conf_crossed, 15.0)

    async def test_strict_provenance_gate_blocks_unverified_items(self) -> None:
        """Verify items without trusted item_nameid provenance are rejected before network calls."""
        unverified_item = self.item_repo.save(ItemRecord(
            market_hash_name="AWP | Safari Mesh (Battle-Scarred)",
            base_name="AWP | Safari Mesh",
            item_nameid=None,  # Unverified / missing
        ))

        mock_adapter = MagicMock(spec=SteamMarketAdapter)
        mock_adapter.get_orderbook_histogram = AsyncMock()

        engine = SellSniperEngine(
            db=self.db,
            market_adapter=mock_adapter,
        )

        evaluation = await engine.evaluate_item(unverified_item)
        self.assertFalse(evaluation.is_actionable)
        self.assertEqual(evaluation.status, "Unverified item_nameid")
        self.assertIn("MISSING TRUSTED ITEM_NAMEID / UNVERIFIED", evaluation.warning_tags)
        # Assert network call was never made
        mock_adapter.get_orderbook_histogram.assert_not_called()

    async def test_crossed_book_defense_suppresses_actionability(self) -> None:
        """Verify crossed book (highest_bid >= lowest_ask) marks inconsistent snapshot and suppresses actionability."""
        # Seed historical observations
        for i in range(5):
            self.ob_repo.insert_snapshot(OrderbookSnapshotRecord(
                item_id=self.item.item_id,
                currency_id=1,
                highest_buy_order_minor=2000,
                lowest_sell_order_minor=2500,
                observed_at=(datetime.now(timezone.utc) - timedelta(days=i + 1)).isoformat(),
            ))

        mock_adapter = MagicMock(spec=SteamMarketAdapter)
        # Crossed book snapshot: bid 2600 >= ask 2500
        mock_adapter.get_orderbook_histogram = AsyncMock(return_value=make_histogram(
            highest_buy_order_minor=2600,
            lowest_sell_order_minor=2500,
            top_level_buy_quantity=2,
            cumulative_buy_quantity=10,
        ))

        engine = SellSniperEngine(
            db=self.db,
            market_adapter=mock_adapter,
        )

        evaluation = await engine.evaluate_item(self.item)
        self.assertFalse(evaluation.is_actionable)
        self.assertEqual(evaluation.status, "Crossed book")
        self.assertIn("INCONSISTENT SNAPSHOT / CROSSED BOOK", evaluation.warning_tags)

    async def test_insufficient_baseline_suppresses_actionability(self) -> None:
        """Verify items with fewer historical samples than min_historical_samples suppress profit alerts."""
        # Insert only 1 historical snapshot (less than default min_historical_samples=3)
        self.ob_repo.insert_snapshot(OrderbookSnapshotRecord(
            item_id=self.item.item_id,
            currency_id=1,
            highest_buy_order_minor=2000,
            lowest_sell_order_minor=2500,
            observed_at=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
        ))

        mock_adapter = MagicMock(spec=SteamMarketAdapter)
        mock_adapter.get_orderbook_histogram = AsyncMock(return_value=make_histogram(
            highest_buy_order_minor=2400,
            lowest_sell_order_minor=2800,
            top_level_buy_quantity=2,
            cumulative_buy_quantity=10,
        ))

        engine = SellSniperEngine(
            db=self.db,
            market_adapter=mock_adapter,
            config=SellEvaluationConfig(min_historical_samples=3),
        )

        evaluation = await engine.evaluate_item(self.item)
        self.assertFalse(evaluation.is_actionable)
        self.assertEqual(evaluation.status, "Data insufficient")
        self.assertIn("DATA INSUFFICIENT", evaluation.warning_tags)
        self.assertIsNone(evaluation.historical_baseline_minor)

    async def test_single_item_top_bid_tagging(self) -> None:
        """Verify top_level_buy_quantity == 1 tags Single-Item Top Bid."""
        # Seed 5 historical snapshots
        for i in range(5):
            self.ob_repo.insert_snapshot(OrderbookSnapshotRecord(
                item_id=self.item.item_id,
                currency_id=1,
                highest_buy_order_minor=2000,
                lowest_sell_order_minor=2500,
                observed_at=(datetime.now(timezone.utc) - timedelta(days=i + 1)).isoformat(),
            ))

        mock_adapter = MagicMock(spec=SteamMarketAdapter)
        # Top level quantity == 1
        mock_adapter.get_orderbook_histogram = AsyncMock(return_value=make_histogram(
            highest_buy_order_minor=2600,
            lowest_sell_order_minor=3000,
            top_level_buy_quantity=1,
            cumulative_buy_quantity=50,
        ))

        engine = SellSniperEngine(
            db=self.db,
            market_adapter=mock_adapter,
        )

        evaluation = await engine.evaluate_item(self.item)
        self.assertIn("Single-Item Top Bid", evaluation.warning_tags)
        self.assertEqual(evaluation.top_level_buy_quantity, 1)
        self.assertEqual(evaluation.cumulative_buy_quantity, 50)

    async def test_modeled_fee_arithmetic_exactness(self) -> None:
        """Verify modeled seller proceeds use exact M4 integer fee formulas."""
        # Seed historical snapshots
        for i in range(5):
            self.ob_repo.insert_snapshot(OrderbookSnapshotRecord(
                item_id=self.item.item_id,
                currency_id=1,
                highest_buy_order_minor=2000,
                lowest_sell_order_minor=2500,
                observed_at=(datetime.now(timezone.utc) - timedelta(days=i + 1)).isoformat(),
            ))

        mock_adapter = MagicMock(spec=SteamMarketAdapter)
        # Bid = $25.50 (2550 cents)
        mock_adapter.get_orderbook_histogram = AsyncMock(return_value=make_histogram(
            highest_buy_order_minor=2550,
            lowest_sell_order_minor=3000,
            top_level_buy_quantity=5,
            cumulative_buy_quantity=20,
        ))

        engine = SellSniperEngine(
            db=self.db,
            market_adapter=mock_adapter,
        )

        evaluation = await engine.evaluate_item(self.item)
        # For buyer pays 2550:
        # seller receives = 2218, steam fee = 110, pub fee = 221 (total = 2549 or exact match)
        # Invariant: proceeds + steam_fee + pub_fee <= highest_buy_order_minor
        sum_fees = evaluation.modeled_seller_receives_minor + evaluation.modeled_steam_fee_minor + evaluation.modeled_publisher_fee_minor
        self.assertLessEqual(sum_fees, 2550)
        self.assertTrue(evaluation.modeled_seller_receives_minor > 0)
        self.assertTrue(evaluation.modeled_steam_fee_minor > 0)
        self.assertTrue(evaluation.modeled_publisher_fee_minor > 0)

    async def test_cooldown_deduplication_and_meaningful_increase_bypass(self) -> None:
        """Verify cooldown suppresses repeated alerts unless bid increases by >= 5%."""
        # Seed 8 historical snapshots (>= 7 required to avoid low sample penalty)
        for i in range(8):
            self.ob_repo.insert_snapshot(OrderbookSnapshotRecord(
                item_id=self.item.item_id,
                currency_id=1,
                highest_buy_order_minor=2000,
                lowest_sell_order_minor=2500,
                observed_at=(datetime.now(timezone.utc) - timedelta(days=i + 1)).isoformat(),
            ))

        mock_ch = MagicMock(spec=BaseNotifier)
        mock_ch.channel_name = "discord"
        mock_ch.is_configured = True
        mock_ch.is_enabled = True
        mock_ch.send = AsyncMock(return_value=NotificationResult(channel="discord", status=DeliveryStatus.DELIVERED))

        dispatcher = NotificationDispatcher(channels=[mock_ch], alert_repo=self.alert_repo)

        mock_adapter = MagicMock(spec=SteamMarketAdapter)
        mock_adapter.get_orderbook_histogram = AsyncMock(return_value=make_histogram(
            highest_buy_order_minor=2500,
            lowest_sell_order_minor=3000,
            top_level_buy_quantity=5,
            cumulative_buy_quantity=20,
        ))

        engine = SellSniperEngine(
            db=self.db,
            market_adapter=mock_adapter,
            dispatcher=dispatcher,
            config=SellEvaluationConfig(cooldown_seconds=1800, meaningful_bid_increase_pct=0.05),
        )

        # 1. First evaluation: actionable, dispatches alert
        eval1 = await engine.evaluate_item(self.item)
        self.assertTrue(eval1.is_actionable)
        self.assertEqual(mock_ch.send.call_count, 1)

        # 2. Second evaluation with identical bid: in cooldown, suppressed, does not dispatch
        eval2 = await engine.evaluate_item(self.item)
        self.assertFalse(eval2.is_actionable)
        self.assertEqual(eval2.status, "Suppressed (Cooldown)")
        self.assertEqual(mock_ch.send.call_count, 1)  # not called again

        # 3. Third evaluation with slight 2% increase ($25.50 vs $25.00): still in cooldown
        mock_adapter.get_orderbook_histogram = AsyncMock(return_value=make_histogram(
            highest_buy_order_minor=2550,
            lowest_sell_order_minor=3000,
            top_level_buy_quantity=5,
            cumulative_buy_quantity=20,
        ))
        eval3 = await engine.evaluate_item(self.item)
        self.assertFalse(eval3.is_actionable)
        self.assertEqual(eval3.status, "Suppressed (Cooldown)")
        self.assertEqual(mock_ch.send.call_count, 1)

        # 4. Fourth evaluation with meaningful 8% increase ($27.00 vs $25.00): bypasses cooldown!
        mock_adapter.get_orderbook_histogram = AsyncMock(return_value=make_histogram(
            highest_buy_order_minor=2700,
            lowest_sell_order_minor=3000,
            top_level_buy_quantity=5,
            cumulative_buy_quantity=20,
        ))
        eval4 = await engine.evaluate_item(self.item)
        self.assertTrue(eval4.is_actionable)
        self.assertEqual(mock_ch.send.call_count, 2)  # Dispatched!

    async def test_scan_inventory_full_pipeline(self) -> None:
        """Verify scan_inventory full workflow end-to-end."""
        # 1. Import a snapshot with 2 marketable items
        snapshot, items = self.inv_repo.import_snapshot(
            items=[
                InventoryItemRecord(
                    snapshot_id=0,
                    asset_id="111",
                    class_id="7993038091",
                    instance_id="188530138",
                    market_hash_name="AK-47 | Redline (Field-Tested)",
                    amount=1,
                    is_marketable=True,
                ),
                InventoryItemRecord(
                    snapshot_id=0,
                    asset_id="222",
                    class_id="7993038090",
                    instance_id="188530138",
                    market_hash_name="AK-47 | Redline (Field-Tested)",  # duplicate skin in inventory
                    amount=1,
                    is_marketable=True,
                ),
                InventoryItemRecord(
                    snapshot_id=0,
                    asset_id="333",
                    class_id="999",
                    instance_id="0",
                    market_hash_name="Locked Case",
                    amount=1,
                    is_marketable=False,  # Restricted, should be skipped
                ),
            ],
            owner_id="76561198000000000",
        )

        # Seed historical baseline (8 samples)
        for i in range(8):
            self.ob_repo.insert_snapshot(OrderbookSnapshotRecord(
                item_id=self.item.item_id,
                currency_id=1,
                highest_buy_order_minor=2000,
                lowest_sell_order_minor=2500,
                observed_at=(datetime.now(timezone.utc) - timedelta(days=i + 1)).isoformat(),
            ))

        mock_adapter = MagicMock(spec=SteamMarketAdapter)
        mock_adapter.get_orderbook_histogram = AsyncMock(return_value=make_histogram(
            highest_buy_order_minor=2500,
            lowest_sell_order_minor=2800,
            top_level_buy_quantity=5,
            cumulative_buy_quantity=20,
        ))

        mock_ch = MagicMock(spec=BaseNotifier)
        mock_ch.channel_name = "discord"
        mock_ch.is_configured = True
        mock_ch.is_enabled = True
        mock_ch.send = AsyncMock(return_value=NotificationResult(channel="discord", status=DeliveryStatus.DELIVERED))

        dispatcher = NotificationDispatcher(channels=[mock_ch], alert_repo=self.alert_repo)

        engine = SellSniperEngine(
            db=self.db,
            market_adapter=mock_adapter,
            dispatcher=dispatcher,
        )

        evaluations = await engine.scan_inventory(snapshot_id=snapshot.snapshot_id)
        # Should evaluate 1 unique marketable item (deduplicated AK-47 Redline, locked case excluded)
        self.assertEqual(len(evaluations), 1)
        self.assertEqual(evaluations[0].market_hash_name, "AK-47 | Redline (Field-Tested)")
        self.assertTrue(evaluations[0].is_actionable)

    async def test_depth_missing_or_zero_adds_warning_and_suppresses_actionability(self) -> None:
        """Verify missing or zero buy depth adds warning tag and suppresses actionable notification."""
        # 1. Formula score check: top_level_buy_quantity=None and 0 yield s_depth = 0.0
        score_none = calculate_sell_opportunity_score(bid_multiple=1.5, top_level_buy_quantity=None, data_age_seconds=0.0)
        score_zero = calculate_sell_opportunity_score(bid_multiple=1.5, top_level_buy_quantity=0, data_age_seconds=0.0)
        score_pos = calculate_sell_opportunity_score(bid_multiple=1.5, top_level_buy_quantity=5, data_age_seconds=0.0)
        self.assertEqual(score_none, 45.0)
        self.assertEqual(score_zero, 45.0)
        self.assertEqual(score_pos, 75.0)

        # 2. Engine evaluation check: histogram with missing/zero depth
        for i in range(8):
            self.ob_repo.insert_snapshot(OrderbookSnapshotRecord(
                item_id=self.item.item_id,
                currency_id=1,
                highest_buy_order_minor=2000,
                lowest_sell_order_minor=2500,
                observed_at=(datetime.now(timezone.utc) - timedelta(days=i + 1)).isoformat(),
            ))

        mock_adapter = MagicMock(spec=SteamMarketAdapter)
        mock_adapter.get_orderbook_histogram = AsyncMock(return_value=make_histogram(
            highest_buy_order_minor=2500,
            lowest_sell_order_minor=3000,
            top_level_buy_quantity=0,
            cumulative_buy_quantity=0,
        ))

        engine = SellSniperEngine(
            db=self.db,
            market_adapter=mock_adapter,
        )

        evaluation = await engine.evaluate_item(self.item)
        self.assertFalse(evaluation.is_actionable)
        self.assertEqual(evaluation.status, "Buy depth unavailable")
        self.assertIn("BUY DEPTH UNAVAILABLE", evaluation.warning_tags)

    async def test_needs_verification_suppressed_by_default_and_allowed_when_configured(self) -> None:
        """Verify Needs verification suppresses actionability by default, allowed when configured."""
        # Seed only 4 snapshots (< 7 gives -50 penalty to confidence score -> Needs verification)
        for i in range(4):
            self.ob_repo.insert_snapshot(OrderbookSnapshotRecord(
                item_id=self.item.item_id,
                currency_id=1,
                highest_buy_order_minor=2000,
                lowest_sell_order_minor=2500,
                observed_at=(datetime.now(timezone.utc) - timedelta(days=i + 1)).isoformat(),
            ))

        mock_adapter = MagicMock(spec=SteamMarketAdapter)
        mock_adapter.get_orderbook_histogram = AsyncMock(return_value=make_histogram(
            highest_buy_order_minor=2500,
            lowest_sell_order_minor=3000,
            top_level_buy_quantity=5,
            cumulative_buy_quantity=20,
        ))

        # Default config: allow_needs_verification_dispatch=False
        engine_default = SellSniperEngine(
            db=self.db,
            market_adapter=mock_adapter,
        )
        eval_default = await engine_default.evaluate_item(self.item)
        self.assertEqual(eval_default.status, "Needs verification")
        self.assertFalse(eval_default.is_actionable)

        # Config allowing needs_verification dispatch:
        engine_allowed = SellSniperEngine(
            db=self.db,
            market_adapter=mock_adapter,
            config=SellEvaluationConfig(allow_needs_verification_dispatch=True),
        )
        eval_allowed = await engine_allowed.evaluate_item(self.item)
        self.assertEqual(eval_allowed.status, "Needs verification")
        self.assertTrue(eval_allowed.is_actionable)

    async def test_intervening_data_insufficient_does_not_reset_cooldown(self) -> None:
        """Verify intervening non-actionable or suppressed observations do not alter cooldown reference."""
        for i in range(8):
            self.ob_repo.insert_snapshot(OrderbookSnapshotRecord(
                item_id=self.item.item_id,
                currency_id=1,
                highest_buy_order_minor=2000,
                lowest_sell_order_minor=2500,
                observed_at=(datetime.now(timezone.utc) - timedelta(days=i + 1)).isoformat(),
            ))

        mock_ch = MagicMock(spec=BaseNotifier)
        mock_ch.channel_name = "discord"
        mock_ch.is_configured = True
        mock_ch.is_enabled = True
        mock_ch.send = AsyncMock(return_value=NotificationResult(channel="discord", status=DeliveryStatus.DELIVERED))
        dispatcher = NotificationDispatcher(channels=[mock_ch], alert_repo=self.alert_repo)

        mock_adapter = MagicMock(spec=SteamMarketAdapter)
        # 1. Actionable bid delivered at 2500
        mock_adapter.get_orderbook_histogram = AsyncMock(return_value=make_histogram(
            highest_buy_order_minor=2500,
            lowest_sell_order_minor=3000,
            top_level_buy_quantity=5,
            cumulative_buy_quantity=20,
        ))
        engine = SellSniperEngine(
            db=self.db,
            market_adapter=mock_adapter,
            dispatcher=dispatcher,
            config=SellEvaluationConfig(cooldown_seconds=1800, meaningful_bid_increase_pct=0.05),
        )

        eval1 = await engine.evaluate_item(self.item)
        self.assertTrue(eval1.is_actionable)
        self.assertEqual(mock_ch.send.call_count, 1)

        # 2. Intervening observation with depth 0 (Buy depth unavailable)
        mock_adapter.get_orderbook_histogram = AsyncMock(return_value=make_histogram(
            highest_buy_order_minor=2500,
            lowest_sell_order_minor=3000,
            top_level_buy_quantity=0,
            cumulative_buy_quantity=0,
        ))
        eval2 = await engine.evaluate_item(self.item)
        self.assertFalse(eval2.is_actionable)
        self.assertEqual(eval2.status, "Buy depth unavailable")

        # 3. Third observation: valid depth, bid 2500 again.
        # Must still be in cooldown referencing the delivered bid at eval1!
        mock_adapter.get_orderbook_histogram = AsyncMock(return_value=make_histogram(
            highest_buy_order_minor=2500,
            lowest_sell_order_minor=3000,
            top_level_buy_quantity=5,
            cumulative_buy_quantity=20,
        ))
        eval3 = await engine.evaluate_item(self.item)
        self.assertFalse(eval3.is_actionable)
        self.assertEqual(eval3.status, "Suppressed (Cooldown)")
        self.assertEqual(mock_ch.send.call_count, 1)

    async def test_bid_delivered_at_1000_compares_future_bids_against_1000_not_intervening(self) -> None:
        """Verify cooldown compares against delivered bid amount (1000), not intervening suppressed amounts."""
        for i in range(8):
            self.ob_repo.insert_snapshot(OrderbookSnapshotRecord(
                item_id=self.item.item_id,
                currency_id=1,
                highest_buy_order_minor=800,
                lowest_sell_order_minor=1200,
                observed_at=(datetime.now(timezone.utc) - timedelta(days=i + 1)).isoformat(),
            ))

        mock_ch = MagicMock(spec=BaseNotifier)
        mock_ch.channel_name = "discord"
        mock_ch.is_configured = True
        mock_ch.is_enabled = True
        mock_ch.send = AsyncMock(return_value=NotificationResult(channel="discord", status=DeliveryStatus.DELIVERED))
        dispatcher = NotificationDispatcher(channels=[mock_ch], alert_repo=self.alert_repo)

        mock_adapter = MagicMock(spec=SteamMarketAdapter)
        engine = SellSniperEngine(
            db=self.db,
            market_adapter=mock_adapter,
            dispatcher=dispatcher,
            config=SellEvaluationConfig(cooldown_seconds=1800, meaningful_bid_increase_pct=0.05),
        )

        # 1. Deliver at 1000
        mock_adapter.get_orderbook_histogram = AsyncMock(return_value=make_histogram(1000, 1500, 5, 20))
        eval1 = await engine.evaluate_item(self.item)
        self.assertTrue(eval1.is_actionable)
        self.assertEqual(mock_ch.send.call_count, 1)

        # 2. Intervening bid at 1020 (+2% over 1000, < 5% threshold of 1050) -> Suppressed
        mock_adapter.get_orderbook_histogram = AsyncMock(return_value=make_histogram(1020, 1500, 5, 20))
        eval2 = await engine.evaluate_item(self.item)
        self.assertFalse(eval2.is_actionable)
        self.assertEqual(eval2.status, "Suppressed (Cooldown)")

        # 3. Third bid at 1040 (+4% over delivered 1000, but +1.96% over intervening 1020).
        # Compared against delivered 1000, threshold is 1050 -> still suppressed!
        mock_adapter.get_orderbook_histogram = AsyncMock(return_value=make_histogram(1040, 1500, 5, 20))
        eval3 = await engine.evaluate_item(self.item)
        self.assertFalse(eval3.is_actionable)
        self.assertEqual(eval3.status, "Suppressed (Cooldown)")
        self.assertEqual(mock_ch.send.call_count, 1)

        # 4. Fourth bid at 1060 (+6% over delivered 1000, >= 1050) -> bypasses cooldown!
        mock_adapter.get_orderbook_histogram = AsyncMock(return_value=make_histogram(1060, 1500, 5, 20))
        eval4 = await engine.evaluate_item(self.item)
        self.assertTrue(eval4.is_actionable)
        self.assertEqual(mock_ch.send.call_count, 2)

    async def test_per_channel_independent_delivery_and_retry_backoff(self) -> None:
        """Verify Discord success + Telegram failure delivers independently and handles retry backoff."""
        for i in range(8):
            self.ob_repo.insert_snapshot(OrderbookSnapshotRecord(
                item_id=self.item.item_id,
                currency_id=1,
                highest_buy_order_minor=2000,
                lowest_sell_order_minor=2500,
                observed_at=(datetime.now(timezone.utc) - timedelta(days=i + 1)).isoformat(),
            ))

        mock_discord = MagicMock(spec=BaseNotifier)
        mock_discord.channel_name = "discord"
        mock_discord.is_configured = True
        mock_discord.is_enabled = True
        mock_discord.send = AsyncMock(return_value=NotificationResult(channel="discord", status=DeliveryStatus.DELIVERED))

        mock_tg = MagicMock(spec=BaseNotifier)
        mock_tg.channel_name = "telegram"
        mock_tg.is_configured = True
        mock_tg.is_enabled = True
        mock_tg.send = AsyncMock(return_value=NotificationResult(channel="telegram", status=DeliveryStatus.FAILED, error_message="Network error"))

        dispatcher = NotificationDispatcher(channels=[mock_discord, mock_tg], alert_repo=self.alert_repo)

        mock_adapter = MagicMock(spec=SteamMarketAdapter)
        mock_adapter.get_orderbook_histogram = AsyncMock(return_value=make_histogram(2500, 3000, 5, 20))

        engine = SellSniperEngine(
            db=self.db,
            market_adapter=mock_adapter,
            dispatcher=dispatcher,
            config=SellEvaluationConfig(cooldown_seconds=1800, failed_retry_backoff_seconds=60, max_delivery_retries=3),
        )

        # 1. First run: Discord delivers, Telegram fails
        eval1 = await engine.evaluate_item(self.item)
        self.assertTrue(eval1.is_actionable)
        self.assertEqual(mock_discord.send.call_count, 1)
        self.assertEqual(mock_tg.send.call_count, 1)

        # 2. Immediate second run (within 60s failure backoff):
        # Discord is in cooldown, Telegram is in retry backoff -> all channels in cooldown/backoff
        eval2 = await engine.evaluate_item(self.item)
        self.assertFalse(eval2.is_actionable)
        self.assertEqual(eval2.status, "Suppressed (Cooldown)")
        self.assertEqual(mock_discord.send.call_count, 1)
        self.assertEqual(mock_tg.send.call_count, 1)

        # 3. Simulate retry backoff expiry by updating Telegram's delivery attempt created_at to 70s ago
        self.db.execute("UPDATE notification_deliveries SET created_at = datetime('now', '-70 seconds') WHERE channel = 'telegram'")

        # Now Telegram retry backoff has expired, while Discord cooldown (1800s) is still active.
        # Now Telegram succeeds on retry:
        mock_tg.send = AsyncMock(return_value=NotificationResult(channel="telegram", status=DeliveryStatus.DELIVERED))

        eval3 = await engine.evaluate_item(self.item)
        self.assertTrue(eval3.is_actionable)
        # Discord was in cooldown, so NOT called again
        self.assertEqual(mock_discord.send.call_count, 1)
        # Telegram was retried and called!
        self.assertEqual(mock_tg.send.call_count, 1)

    async def test_unconfigured_channels_do_not_start_cooldown(self) -> None:
        """Verify unconfigured or disabled channels do not trigger cooldown."""
        unconfigured_ch = MagicMock(spec=BaseNotifier)
        unconfigured_ch.channel_name = "telegram"
        unconfigured_ch.is_configured = False
        unconfigured_ch.is_enabled = True

        dispatcher = NotificationDispatcher(channels=[unconfigured_ch], alert_repo=self.alert_repo)

        mock_adapter = MagicMock(spec=SteamMarketAdapter)
        mock_adapter.get_orderbook_histogram = AsyncMock(return_value=make_histogram(2500, 3000, 5, 20))

        engine = SellSniperEngine(
            db=self.db,
            market_adapter=mock_adapter,
            dispatcher=dispatcher,
        )

        # Neither cooldown nor delivered records should exist
        is_cd = self.alert_repo.is_channel_in_cooldown(
            item_id=self.item.item_id,
            currency_id=1,
            alert_type="sell_target_bid",
            channel="telegram",
            cooldown_seconds=1800,
            new_bid_minor=2500,
        )
        self.assertFalse(is_cd)


if __name__ == "__main__":
    unittest.main()
