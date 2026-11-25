"""SELL Sniper Engine: Opportunity detection, baseline modeling, fee calculation, and alert dispatch."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import logging
import math
import statistics
from typing import Any, Sequence
from urllib.parse import quote

from pydantic import BaseModel, Field

from sniper.adapters.steam_market import SteamMarketAdapter
from sniper.core.currency import CurrencyConfig
from sniper.core.fees import FeeBreakdown, calculate_seller_receives
from sniper.core.identity import (
    ItemIdentityResult,
    ResolutionSource,
    ResolutionStatus,
    UrlType,
    WearCategory,
    is_trusted_mapping_source,
)
from sniper.models.schemas import OrderbookHistogramResponse
from sniper.notifications.dispatcher import NotificationDispatcher
from sniper.notifications.models import AlertPayload, NotificationResult
from sniper.storage.database import Database
from sniper.storage.models import (
    AlertEventRecord,
    CurrencyRecord,
    InventoryItemRecord,
    ItemRecord,
    OrderbookSnapshotRecord,
)
from sniper.storage.repositories import (
    AlertRepository,
    CurrencyRepository,
    InventoryRepository,
    ItemRepository,
    OrderbookRepository,
)
from sniper.storage.service import ItemIdentityService

logger = logging.getLogger(__name__)


def calculate_sell_opportunity_score(
    bid_multiple: float | None,
    top_level_buy_quantity: int | None = None,
    data_age_seconds: float = 0.0,
    is_crossed_book: bool = False,
) -> float:
    """Calculate quantitative Opportunity Score (0 to 100) for a SELL candidate."""
    if bid_multiple is None:
        s_gain = 0.0
    else:
        s_gain = max(0.0, min(100.0, math.floor((bid_multiple - 1.0) * 100.0)))

    qty = top_level_buy_quantity if (top_level_buy_quantity is not None and top_level_buy_quantity > 0) else 0

    if qty >= 5:
        s_depth = 100.0
    elif qty >= 2:
        s_depth = 70.0
    elif qty == 1:
        s_depth = 35.0
    else:
        s_depth = 0.0

    s_fresh = max(0.0, 100.0 - (data_age_seconds * 2.0))
    raw_score = (0.50 * s_gain) + (0.30 * s_depth) + (0.20 * s_fresh)

    penalties = 0.0
    if is_crossed_book:
        penalties += 50.0

    if bid_multiple is not None and bid_multiple > 2.5 and qty == 1:
        penalties += 35.0

    final_score = max(0.0, min(100.0, raw_score - penalties))
    return round(final_score, 1)


def calculate_sell_confidence_score(
    historical_samples_count: int,
    highest_buy_order_minor: int,
    lowest_sell_order_minor: int | None,
    data_age_seconds: float = 0.0,
    is_crossed_book: bool = False,
) -> float:
    """Calculate Signal Confidence Score (0 to 100) for a SELL candidate."""
    c_fresh = max(0.0, 100.0 - (data_age_seconds * 2.0))
    c_samples = min(100.0, historical_samples_count * 10.0)

    if lowest_sell_order_minor is not None and lowest_sell_order_minor > 0:
        if highest_buy_order_minor < lowest_sell_order_minor:
            spread_pct = ((lowest_sell_order_minor - highest_buy_order_minor) / lowest_sell_order_minor) * 100.0
            c_spread = max(0.0, min(100.0, 100.0 - math.floor(spread_pct)))
        else:
            c_spread = 0.0
    else:
        c_spread = 50.0

    raw_conf = (0.40 * c_fresh) + (0.35 * c_samples) + (0.25 * c_spread)

    risk_penalties = 0.0
    if is_crossed_book:
        risk_penalties += 60.0
    if historical_samples_count < 7:
        risk_penalties += 50.0
    if data_age_seconds > 120.0:
        risk_penalties += 35.0

    final_conf = max(0.0, min(100.0, raw_conf - risk_penalties))
    return round(final_conf, 1)


@dataclass
class SellEvaluationConfig:
    """Configuration parameters for SELL opportunity scanning and alerting."""
    min_bid_minor: int = 100
    min_bid_multiple: float = 1.0
    min_historical_samples: int = 3
    max_data_age_seconds: int = 86400 * 30
    cooldown_seconds: int = 1800
    meaningful_bid_increase_pct: float = 0.05
    failed_retry_backoff_seconds: int = 60
    max_delivery_retries: int = 3
    min_candidate_opp_score: float = 50.0
    min_candidate_conf_score: float = 60.0
    min_high_confidence_opp_score: float = 75.0
    min_high_confidence_conf_score: float = 80.0
    allow_needs_verification_dispatch: bool = False
    currency_id: int = 1


class SellCandidateEvaluation(BaseModel):
    """Result of evaluating a single inventory item's sell potential."""
    item_id: int | None = None
    market_hash_name: str
    currency_id: int = 1
    currency_code: str = "USD"
    currency_symbol: str = "$"
    highest_buy_order_minor: int = 0
    lowest_sell_order_minor: int | None = None
    top_level_buy_quantity: int = 0
    cumulative_buy_quantity: int = 0
    modeled_steam_fee_minor: int = 0
    modeled_publisher_fee_minor: int = 0
    modeled_seller_receives_minor: int = 0
    historical_baseline_minor: int | None = None
    bid_multiple: float | None = None
    opportunity_score: float = 0.0
    confidence_score: float = 0.0
    warning_tags: list[str] = Field(default_factory=list)
    status: str = "Candidate"
    is_actionable: bool = False
    alert_id: int | None = None
    suppression_reason: str | None = None
    delivery_results: dict[str, NotificationResult] = Field(default_factory=dict)


class SellSniperEngine:
    """Core evaluation engine analyzing CS2 inventory items for profitable buy orders."""

    def __init__(
        self,
        db: Database,
        market_adapter: SteamMarketAdapter,
        identity_service: ItemIdentityService | None = None,
        dispatcher: NotificationDispatcher | None = None,
        config: SellEvaluationConfig | None = None,
    ) -> None:
        self._db = db
        self._adapter = market_adapter
        self._identity_service = identity_service or ItemIdentityService(db)
        self._dispatcher = dispatcher or NotificationDispatcher.from_database(db)
        self._config = config or SellEvaluationConfig()

        self._item_repo = ItemRepository(db)
        self._orderbook_repo = OrderbookRepository(db)
        self._inventory_repo = InventoryRepository(db)
        self._alert_repo = AlertRepository(db)
        self._currency_repo = CurrencyRepository(db)

    def _get_currency_config(self, currency_id: int) -> tuple[CurrencyConfig, CurrencyRecord]:
        curr_rec = self._currency_repo.get_by_id(currency_id)
        if curr_rec is None:
            # Seed default if missing
            self._currency_repo.seed_default_currencies()
            curr_rec = self._currency_repo.get_by_id(currency_id)
            if curr_rec is None:
                raise ValueError(f"Currency ID {currency_id} is not configured or seeded.")

        domain_cfg = CurrencyConfig(
            currency_id=curr_rec.currency_id,
            code=curr_rec.code,
            symbol=curr_rec.symbol,
            minor_unit_precision=curr_rec.minor_unit_precision,
            country=curr_rec.country,
            min_listing_price_minor=curr_rec.min_listing_price_minor,
            price_step_minor=curr_rec.price_step_minor,
            min_fee_steam_minor=curr_rec.min_fee_steam_minor,
            min_fee_publisher_minor=curr_rec.min_fee_publisher_minor,
            is_metadata_verified=curr_rec.is_metadata_verified,
            is_fee_verified=curr_rec.is_fee_verified,
            is_listing_min_verified=curr_rec.is_listing_min_verified,
            is_price_step_verified=curr_rec.is_price_step_verified,
            notes=curr_rec.notes,
        )
        return domain_cfg, curr_rec

    async def evaluate_item(
        self,
        item_record: ItemRecord,
        dispatch_notifications: bool = True,
    ) -> SellCandidateEvaluation:
        """Evaluate a single catalog item for SELL opportunity."""
        currency_cfg, curr_rec = self._get_currency_config(self._config.currency_id)
        name = item_record.market_hash_name
        item_id = item_record.item_id

        # 1. Strict provenance gate: do not make network requests without trusted item_nameid
        if (
            item_record.item_nameid is None
            or type(item_record.item_nameid) is not int
            or item_record.item_nameid <= 0
            or not is_trusted_mapping_source(item_record.item_nameid_source, item_record.item_nameid_revision)
        ):
            logger.info("Skipping orderbook request for '%s': missing trusted item_nameid.", name)
            return SellCandidateEvaluation(
                item_id=item_id,
                market_hash_name=name,
                currency_id=self._config.currency_id,
                currency_code=curr_rec.code,
                currency_symbol=curr_rec.symbol,
                warning_tags=["MISSING TRUSTED ITEM_NAMEID / UNVERIFIED"],
                status="Unverified item_nameid",
                is_actionable=False,
                suppression_reason="Item lacks trusted, verified item_nameid provenance.",
            )

        # 2. Build ItemIdentityResult
        ext_enum = None
        if item_record.exterior:
            for w in WearCategory:
                if w.value == item_record.exterior:
                    ext_enum = w
                    break

        identity = ItemIdentityResult(
            appid=730,
            input_url=f"https://steamcommunity.com/market/listings/730/{quote(name)}",
            url_type=UrlType.CANONICAL,
            base_name=item_record.base_name,
            market_hash_name=name,
            exterior=ext_enum,
            is_stattrak=bool(item_record.is_stattrak),
            is_souvenir=bool(item_record.is_souvenir),
            group_id=item_record.group_id,
            item_nameid=item_record.item_nameid,
            item_nameid_source=item_record.item_nameid_source,
            item_nameid_revision=item_record.item_nameid_revision,
            classid=item_record.classid,
            classid_source=item_record.classid_source,
            classid_revision=item_record.classid_revision,
            resolution_status=ResolutionStatus.ITEM_NAMEID_RESOLVED,
            resolution_source=ResolutionSource.OFFLINE_DB,
        )

        # 3. Query orderbook histogram
        histogram = await self._adapter.get_orderbook_histogram(
            identity=identity,
            currency=self._config.currency_id,
            country=currency_cfg.country,
        )

        # 4. Save orderbook snapshot
        now_iso = datetime.now(timezone.utc).isoformat()
        saved_snapshot = self._orderbook_repo.insert_snapshot(
            OrderbookSnapshotRecord(
                item_id=item_id if item_id is not None else 0,
                currency_id=self._config.currency_id,
                highest_buy_order_minor=histogram.highest_buy_order_minor,
                lowest_sell_order_minor=histogram.lowest_sell_order_minor,
                buy_order_count=histogram.buy_order_count,
                sell_order_count=histogram.sell_order_count,
                top_level_buy_quantity=histogram.top_level_buy_quantity,
                cumulative_buy_quantity=histogram.cumulative_buy_quantity,
                observed_at=now_iso,
                source_payload_version="histogram_v1",
            )
        )

        highest_bid = histogram.highest_buy_order_minor
        lowest_ask = histogram.lowest_sell_order_minor
        top_qty = histogram.top_level_buy_quantity
        cum_qty = histogram.cumulative_buy_quantity

        if highest_bid is None:
            return SellCandidateEvaluation(
                item_id=item_id,
                market_hash_name=name,
                currency_id=self._config.currency_id,
                currency_code=curr_rec.code,
                currency_symbol=curr_rec.symbol,
                warning_tags=["NO ACTIVE BUY ORDERS"],
                status="No buy orders",
                is_actionable=False,
                suppression_reason="Orderbook contains no active buy orders.",
            )

        # 5. Check historical baseline
        historical_bids = self._orderbook_repo.get_historical_buy_orders(
            item_id=item_id if item_id is not None else 0,
            currency_id=self._config.currency_id,
            max_age_seconds=self._config.max_data_age_seconds,
            exclude_snapshot_id=saved_snapshot.snapshot_id,
        )

        warning_tags: list[str] = []
        is_actionable = True
        baseline: int | None = None
        bid_multiple: float | None = None
        suppression_reason: str | None = None

        if len(historical_bids) < self._config.min_historical_samples:
            warning_tags.append("DATA INSUFFICIENT")
            status = "Data insufficient"
            is_actionable = False
            suppression_reason = "Insufficient historical orderbook observations for baseline."
        else:
            baseline = int(statistics.median_low(historical_bids))
            if baseline > 0:
                bid_multiple = float(highest_bid) / float(baseline)
            else:
                bid_multiple = 1.0

        # 6. Crossed Book Detection
        is_crossed = False
        if lowest_ask is not None and highest_bid >= lowest_ask:
            is_crossed = True
            warning_tags.append("INCONSISTENT SNAPSHOT / CROSSED BOOK")
            status = "Crossed book"
            is_actionable = False
            suppression_reason = "Orderbook book is crossed (highest bid >= lowest ask)."

        # 7. Buy Depth Verification (no fabrication)
        if top_qty is None or top_qty <= 0:
            warning_tags.append("BUY DEPTH UNAVAILABLE")
            if is_actionable:
                status = "Buy depth unavailable"
                is_actionable = False
                suppression_reason = "Orderbook buy depth is zero or unavailable."
        elif top_qty == 1:
            warning_tags.append("Single-Item Top Bid")

        # 8. Unverified High Bid
        if bid_multiple is not None and bid_multiple > 2.5 and top_qty == 1:
            warning_tags.append("Unverified High Bid")

        # 9. Modeled Fee & Proceeds Calculation (exact integer arithmetic, validate_market_listing=False)
        fee_result = calculate_seller_receives(
            requested_buyer_pays_minor=highest_bid,
            currency=currency_cfg,
            validate_market_listing=False,
        )
        steam_fee = fee_result.steam_fee
        pub_fee = fee_result.publisher_fee
        seller_receives = fee_result.seller_receives

        # 10. Quantitative Scoring
        opp_score = calculate_sell_opportunity_score(
            bid_multiple=bid_multiple,
            top_level_buy_quantity=top_qty,
            data_age_seconds=0.0,
            is_crossed_book=is_crossed,
        )
        conf_score = calculate_sell_confidence_score(
            historical_samples_count=len(historical_bids),
            highest_buy_order_minor=highest_bid,
            lowest_sell_order_minor=lowest_ask,
            data_age_seconds=0.0,
            is_crossed_book=is_crossed,
        )

        # 11. Status Label assignment and notification eligibility gating
        if is_actionable:
            if highest_bid < self._config.min_bid_minor:
                status = "Below minimum bid"
                is_actionable = False
                suppression_reason = "Highest buy order is below minimum configured bid."
            elif bid_multiple is not None and bid_multiple < self._config.min_bid_multiple:
                status = "Below target multiple"
                is_actionable = False
                suppression_reason = "Bid multiple is below target multiple over baseline."
            elif (
                opp_score >= self._config.min_high_confidence_opp_score
                and conf_score >= self._config.min_high_confidence_conf_score
            ):
                status = "High-confidence signal"
            elif (
                opp_score >= self._config.min_candidate_opp_score
                and conf_score >= self._config.min_candidate_conf_score
            ):
                status = "Candidate"
            else:
                status = "Needs verification"
                if not self._config.allow_needs_verification_dispatch:
                    is_actionable = False
                    suppression_reason = (
                        f"Opportunity score ({opp_score:.1f}) or confidence score ({conf_score:.1f}) "
                        "below candidate thresholds; requires manual verification."
                    )

        # 12. Alert Fingerprinting & Cooldown Check
        fingerprint = hashlib.sha256(
            f"{item_id}:{self._config.currency_id}:sell_target_bid:{highest_bid}".encode()
        ).hexdigest()

        delivery_results: dict[str, NotificationResult] = {}
        alert_id: int | None = None

        if is_actionable and self._dispatcher is not None and self._alert_repo is not None and item_id is not None:
            configured_enabled = [
                ch for ch in self._dispatcher.channels
                if ch.is_configured and getattr(ch, "is_enabled", True)
            ]
            if configured_enabled:
                all_in_cooldown = True
                for ch in configured_enabled:
                    ch_cd = self._alert_repo.is_channel_in_cooldown(
                        item_id=item_id,
                        currency_id=self._config.currency_id,
                        alert_type="sell_target_bid",
                        channel=ch.channel_name,
                        cooldown_seconds=self._config.cooldown_seconds,
                        new_bid_minor=highest_bid,
                        meaningful_increase_pct=self._config.meaningful_bid_increase_pct,
                    )
                    ch_backoff = self._alert_repo.is_channel_in_retry_backoff(
                        item_id=item_id,
                        currency_id=self._config.currency_id,
                        alert_type="sell_target_bid",
                        channel=ch.channel_name,
                        alert_fingerprint=fingerprint,
                        backoff_seconds=self._config.failed_retry_backoff_seconds,
                        max_retries=self._config.max_delivery_retries,
                    )
                    if not ch_cd and not ch_backoff:
                        all_in_cooldown = False
                        break

                if all_in_cooldown:
                    status = "Suppressed (Cooldown)"
                    is_actionable = False
                    suppression_reason = (
                        "All configured notification channels are within cooldown "
                        "or retry backoff without meaningful bid increase."
                    )

        # Record alert event in SQLite
        recorded_alert = self._alert_repo.record_alert(
            AlertEventRecord(
                item_id=item_id if item_id is not None else 0,
                currency_id=self._config.currency_id,
                alert_type="sell_target_bid",
                alert_fingerprint=fingerprint,
                highest_buy_order_minor=highest_bid,
                lowest_sell_order_minor=lowest_ask,
                top_level_buy_quantity=top_qty or 0,
                cumulative_buy_quantity=cum_qty or 0,
                modeled_steam_fee_minor=steam_fee,
                modeled_publisher_fee_minor=pub_fee,
                modeled_seller_receives_minor=seller_receives,
                historical_baseline_minor=baseline,
                bid_multiple=bid_multiple,
                opportunity_score=opp_score,
                confidence_score=conf_score,
                warning_tags=json.dumps(warning_tags),
                status=status,
                created_at=now_iso,
            )
        )
        alert_id = recorded_alert.alert_id

        # 13. Dispatch Notifications if actionable and requested
        if is_actionable and dispatch_notifications and self._dispatcher is not None:
            listing_url = f"https://steamcommunity.com/market/listings/730/{quote(name)}"
            payload = AlertPayload(
                alert_id=alert_id,
                alert_fingerprint=fingerprint,
                item_id=item_id,
                currency_id=self._config.currency_id,
                market_hash_name=name,
                exterior=item_record.exterior,
                currency_code=curr_rec.code,
                currency_symbol=curr_rec.symbol,
                highest_buy_order_minor=highest_bid,
                lowest_sell_order_minor=lowest_ask,
                top_level_buy_quantity=top_qty or 0,
                cumulative_buy_quantity=cum_qty or 0,
                modeled_steam_fee_minor=steam_fee,
                modeled_publisher_fee_minor=pub_fee,
                modeled_seller_receives_minor=seller_receives,
                historical_baseline_minor=baseline,
                bid_multiple=bid_multiple,
                opportunity_score=opp_score,
                confidence_score=conf_score,
                warning_tags=warning_tags,
                status=status,
                created_at=now_iso,
                steam_listing_url=listing_url,
            )
            delivery_results = await self._dispatcher.dispatch(
                payload,
                alert_id=alert_id,
                check_cooldown=True,
                cooldown_seconds=self._config.cooldown_seconds,
                meaningful_increase_pct=self._config.meaningful_bid_increase_pct,
                failed_retry_backoff_seconds=self._config.failed_retry_backoff_seconds,
                max_delivery_retries=self._config.max_delivery_retries,
            )

        return SellCandidateEvaluation(
            item_id=item_id,
            market_hash_name=name,
            currency_id=self._config.currency_id,
            currency_code=curr_rec.code,
            currency_symbol=curr_rec.symbol,
            highest_buy_order_minor=highest_bid,
            lowest_sell_order_minor=lowest_ask,
            top_level_buy_quantity=top_qty,
            cumulative_buy_quantity=cum_qty,
            modeled_steam_fee_minor=steam_fee,
            modeled_publisher_fee_minor=pub_fee,
            modeled_seller_receives_minor=seller_receives,
            historical_baseline_minor=baseline,
            bid_multiple=bid_multiple,
            opportunity_score=opp_score,
            confidence_score=conf_score,
            warning_tags=warning_tags,
            status=status,
            is_actionable=is_actionable,
            alert_id=alert_id,
            suppression_reason=suppression_reason,
            delivery_results=delivery_results,
        )

    async def scan_inventory(
        self,
        snapshot_id: int | None = None,
        owner_id: str | None = None,
        dispatch_notifications: bool = True,
        max_unique_items: int | None = None,
        market_hash_names: list[str] | None = None,
    ) -> list[SellCandidateEvaluation]:
        """Scan all or selected marketable items in the specified or latest inventory snapshot."""
        if snapshot_id is not None:
            items = self._inventory_repo.get_items_for_snapshot(snapshot_id)
        else:
            latest_snap = self._inventory_repo.get_latest_snapshot(owner_id)
            if latest_snap is None or latest_snap.snapshot_id is None:
                logger.info("No inventory snapshots found for scan.")
                return []
            items = self._inventory_repo.get_items_for_snapshot(latest_snap.snapshot_id)

        # Filter for marketable items only
        marketable_items = [i for i in items if i.is_marketable]
        if not marketable_items:
            logger.info("No marketable items found in inventory snapshot.")
            return []

        allowed_names = {n.strip() for n in market_hash_names if n.strip()} if market_hash_names else None
        if allowed_names is not None:
            marketable_items = [i for i in marketable_items if i.market_hash_name in allowed_names]
            if not marketable_items:
                logger.info("No matching marketable items found for requested market_hash_names filter.")
                return []

        # Deduplicate by market_hash_name
        seen_names: set[str] = set()
        unique_items: list[InventoryItemRecord] = []
        for it in marketable_items:
            if it.market_hash_name not in seen_names:
                seen_names.add(it.market_hash_name)
                unique_items.append(it)
                if max_unique_items is not None and len(unique_items) >= max_unique_items:
                    break

        logger.info("Evaluating %d unique marketable items for SELL opportunities.", len(unique_items))
        evaluations: list[SellCandidateEvaluation] = []

        for inv_item in unique_items:
            # Look up or resolve item in catalog
            catalog_item = self._item_repo.get_by_market_hash_name(inv_item.market_hash_name)
            if catalog_item is None:
                try:
                    ingested = self._identity_service.resolve_and_persist(
                        f"https://steamcommunity.com/market/listings/730/{quote(inv_item.market_hash_name)}"
                    )
                    catalog_item = ingested.record
                except Exception as exc:
                    logger.warning("Could not resolve identity for '%s': %s", inv_item.market_hash_name, exc)
                    catalog_item = ItemRecord(
                        market_hash_name=inv_item.market_hash_name,
                        base_name=inv_item.market_hash_name,
                    )
                    catalog_item = self._item_repo.save(catalog_item)

            eval_result = await self.evaluate_item(
                item_record=catalog_item,
                dispatch_notifications=dispatch_notifications,
            )
            evaluations.append(eval_result)

        return evaluations
