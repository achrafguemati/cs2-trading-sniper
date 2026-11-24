"""BUY Sniper Engine: Individual listing evaluation, VWM baseline, 7-day conservative economics & alerts (M6)."""

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

from sniper.adapters.listing_parser import (
    ParsedListingCard,
    compute_weighted_median_minor,
    parse_listing_render_cards,
)
from sniper.adapters.steam_market import SteamMarketAdapter
from sniper.core.currency import CurrencyConfig
from sniper.core.fees import calculate_seller_receives
from sniper.core.identity import (
    ItemIdentityResult,
    ResolutionSource,
    ResolutionStatus,
    UrlType,
    WearCategory,
    decompose_market_hash_name,
)
from sniper.notifications.dispatcher import NotificationDispatcher
from sniper.notifications.models import BuyAlertPayload, NotificationResult
from sniper.storage.database import Database
from sniper.storage.models import (
    BuyListingEvaluationRecord,
    CurrencyRecord,
    ItemRecord,
    ListingRecord,
    PriceHistoryPointRecord,
    StrategyAlertRecord,
    WatchlistEntryRecord,
)
from sniper.storage.repositories import (
    BuyEvaluationRepository,
    CurrencyRepository,
    ItemRepository,
    ListingRepository,
    OrderbookRepository,
    PriceHistoryRepository,
    StrategyAlertRepository,
    WatchlistRepository,
)
from sniper.storage.service import ItemIdentityService

logger = logging.getLogger(__name__)


def calculate_buy_opportunity_score(
    conservative_roi_pct: float,
    sales_7d: int,
    buy_orders_count: int,
    std_dev_14d_minor: float,
    baseline_minor: int | None,
    discount_pct: float = 0.0,
    listing_age_seconds: float = 0.0,
    low_liquidity_threshold: int = 10,
) -> float:
    """Calculate bounded 0-100 BUY Opportunity Score per opportunity-scoring.md."""
    if conservative_roi_pct < 5.0:
        s_roi = 0.0
    else:
        s_roi = max(0.0, min(100.0, float(math.floor(conservative_roi_pct * 4.0))))

    s_liq = min(
        100.0,
        min(60.0, float(max(0, sales_7d) * 2))
        + min(40.0, float(max(0, buy_orders_count) // 5)),
    )

    if baseline_minor is None or baseline_minor <= 0:
        s_vol = 0.0
    else:
        vol_pct = math.floor((max(0.0, std_dev_14d_minor) / float(baseline_minor)) * 100.0)
        s_vol = max(0.0, min(100.0, float(100 - vol_pct)))

    raw_score = (0.45 * s_roi) + (0.35 * s_liq) + (0.20 * s_vol)

    penalties = 0.0
    if sales_7d < low_liquidity_threshold:
        penalties += 40.0
    if listing_age_seconds > 120.0:
        penalties += 25.0
    if discount_pct > 60.0:
        penalties += 30.0

    return round(max(0.0, min(100.0, raw_score - penalties)), 1)


def calculate_buy_confidence_score(
    daily_points_count: int,
    highest_buy_order_minor: int | None = None,
    lowest_sell_order_minor: int | None = None,
    data_age_seconds: float = 0.0,
    is_crossed_book: bool = False,
    is_manipulated_spike: bool = False,
    min_daily_points: int = 7,
) -> float:
    """Calculate bounded 0-100 BUY Confidence Score per opportunity-scoring.md."""
    c_fresh = max(0.0, 100.0 - (max(0.0, data_age_seconds) * 2.0))
    c_samples = min(100.0, float(max(0, daily_points_count) * 10))

    if (
        highest_buy_order_minor is not None
        and lowest_sell_order_minor is not None
        and lowest_sell_order_minor > 0
    ):
        if highest_buy_order_minor < lowest_sell_order_minor:
            spread_pct = (
                (lowest_sell_order_minor - highest_buy_order_minor)
                / float(lowest_sell_order_minor)
            ) * 100.0
            c_spread = max(0.0, min(100.0, float(100 - math.floor(spread_pct))))
        else:
            c_spread = 0.0
    else:
        c_spread = 60.0

    raw_conf = (0.40 * c_fresh) + (0.35 * c_samples) + (0.25 * c_spread)

    risk_penalties = 0.0
    if is_crossed_book:
        risk_penalties += 60.0
    if daily_points_count < min_daily_points:
        risk_penalties += 50.0
    if data_age_seconds > 120.0:
        risk_penalties += 35.0
    if is_manipulated_spike:
        risk_penalties += 25.0

    return round(max(0.0, min(100.0, raw_conf - risk_penalties)), 1)


@dataclass
class BuyEvaluationConfig:
    """Configuration for BUY opportunity detection and risk gates."""
    currency_id: int = 1
    min_daily_history_points: int = 7
    min_cumulative_volume: int = 10
    min_sales_7d: int = 10
    min_buy_orders_count: int = 0
    risk_buffer_pct: int = 5
    min_roi_pct: float = 5.0
    max_buy_price_minor: int | None = None
    min_discount_pct: float | None = None
    min_net_profit_minor: int | None = None
    render_count: int = 20
    fetch_public_history_fallback: bool = True
    fetch_orderbook_histogram: bool = False
    cooldown_seconds: int = 1800
    meaningful_drop_pct: float = 0.05
    failed_retry_backoff_seconds: int = 60
    max_delivery_retries: int = 3
    min_candidate_opp_score: float = 50.0
    min_candidate_conf_score: float = 60.0
    min_high_confidence_opp_score: float = 75.0
    min_high_confidence_conf_score: float = 80.0
    allow_needs_verification_dispatch: bool = False


class BuyCandidateEvaluation(BaseModel):
    """Evaluation output for an individual listing on the BUY engine."""
    evaluation_id: int | None = None
    item_id: int | None = None
    market_hash_name: str
    steam_listing_id: str
    asset_id: str | None = None
    currency_id: int = 1
    currency_code: str = "USD"
    currency_symbol: str = "$"
    purchase_buyer_pays_minor: int = 0
    listing_seller_receives_minor: int = 0
    listing_fee_minor: int = 0
    baseline_vwm_minor: int | None = None
    floor_median_minor: int | None = None
    reference_price_minor: int | None = None
    discount_pct: float = 0.0
    conservative_exit_gross_minor: int = 0
    conservative_exit_net_minor: int = 0
    conservative_net_profit_minor: int = 0
    conservative_roi_pct: float = 0.0
    base_net_profit_minor: int = 0
    optimistic_net_profit_minor: int = 0
    float_value: float | None = None
    paint_seed: int | None = None
    paint_index: int | None = None
    stickers: list[str] = Field(default_factory=list)
    inspect_link: str | None = None
    opportunity_score: float = 0.0
    confidence_score: float = 0.0
    warning_tags: list[str] = Field(default_factory=list)
    status: str = "Candidate"
    is_actionable: bool = False
    suppression_reason: str | None = None
    strategy_alert_id: int | None = None
    delivery_results: dict[str, NotificationResult] = Field(default_factory=dict)


class BuySniperEngine:
    """Evaluates individual CS2 SCM sell listings for risk-adjusted BUY opportunities."""

    def __init__(
        self,
        db: Database,
        market_adapter: SteamMarketAdapter,
        identity_service: ItemIdentityService | None = None,
        dispatcher: NotificationDispatcher | None = None,
        config: BuyEvaluationConfig | None = None,
    ) -> None:
        self._db = db
        self._adapter = market_adapter
        self._identity_service = identity_service or ItemIdentityService(db)
        self._dispatcher = dispatcher or NotificationDispatcher.from_database(db)
        self._config = config or BuyEvaluationConfig()

        self._item_repo = ItemRepository(db)
        self._currency_repo = CurrencyRepository(db)
        self._listing_repo = ListingRepository(db)
        self._orderbook_repo = OrderbookRepository(db)
        self._history_repo = PriceHistoryRepository(db)
        self._buy_eval_repo = BuyEvaluationRepository(db)
        self._watchlist_repo = WatchlistRepository(db)
        self._strategy_alert_repo = StrategyAlertRepository(db)

    def _get_currency_config(self, currency_id: int) -> tuple[CurrencyConfig, CurrencyRecord]:
        curr_rec = self._currency_repo.get_by_id(currency_id)
        if curr_rec is None:
            self._currency_repo.seed_default_currencies()
            curr_rec = self._currency_repo.get_by_id(currency_id)
            if curr_rec is None:
                raise ValueError(f"Currency ID {currency_id} is not configured.")

        cfg = CurrencyConfig(
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
        return cfg, curr_rec

    @staticmethod
    def _build_identity(item_record: ItemRecord) -> ItemIdentityResult:
        ext_enum = None
        if item_record.exterior:
            for w in WearCategory:
                if w.value == item_record.exterior:
                    ext_enum = w
                    break

        has_trusted_nameid = (
            type(item_record.item_nameid) is int
            and item_record.item_nameid > 0
            and bool(item_record.item_nameid_source)
            and bool(item_record.item_nameid_revision)
        )
        status = (
            ResolutionStatus.ITEM_NAMEID_RESOLVED
            if has_trusted_nameid
            else ResolutionStatus.CANONICAL_RESOLVED
        )
        return ItemIdentityResult(
            appid=730,
            input_url=f"https://steamcommunity.com/market/listings/730/{quote(item_record.market_hash_name, safe='')}",
            url_type=UrlType.CANONICAL,
            group_id=item_record.group_id,
            base_name=item_record.base_name,
            market_hash_name=item_record.market_hash_name,
            exterior=ext_enum,
            is_stattrak=bool(item_record.is_stattrak),
            is_souvenir=bool(item_record.is_souvenir),
            classid=item_record.classid,
            classid_source=item_record.classid_source,
            classid_revision=item_record.classid_revision,
            item_nameid=item_record.item_nameid if has_trusted_nameid else None,
            item_nameid_source=item_record.item_nameid_source if has_trusted_nameid else None,
            item_nameid_revision=item_record.item_nameid_revision if has_trusted_nameid else None,
            resolution_status=status,
            resolution_source=ResolutionSource.OFFLINE_DB if has_trusted_nameid else ResolutionSource.URL,
        )

    def _effective_thresholds(
        self,
        watchlist_entry: WatchlistEntryRecord | None,
    ) -> tuple[int, int | None, float | None, int | None, float]:
        if watchlist_entry is None:
            return (
                self._config.currency_id,
                self._config.max_buy_price_minor,
                self._config.min_discount_pct,
                self._config.min_net_profit_minor,
                self._config.min_roi_pct,
            )
        return (
            watchlist_entry.currency_id,
            watchlist_entry.max_buy_price_minor
            if watchlist_entry.max_buy_price_minor is not None
            else self._config.max_buy_price_minor,
            watchlist_entry.min_discount_pct
            if watchlist_entry.min_discount_pct is not None
            else self._config.min_discount_pct,
            watchlist_entry.min_net_profit_minor
            if watchlist_entry.min_net_profit_minor is not None
            else self._config.min_net_profit_minor,
            watchlist_entry.min_roi_pct,
        )

    @staticmethod
    def _evaluate_exit_scenarios(
        purchase_minor: int,
        reference_price_minor: int | None,
        floor_median_minor: int,
        optimistic_anchor_minor: int,
        risk_buffer_pct: int,
        currency_cfg: CurrencyConfig,
    ) -> tuple[int, int, int, float, int, int]:
        """Compute Conservative, Base, and Optimistic wallet profits using M4 integer math."""
        if reference_price_minor is None or reference_price_minor <= 0:
            return 0, 0, 0, 0.0, 0, 0

        fee_floor = 1 + currency_cfg.min_fee_steam_minor + currency_cfg.min_fee_publisher_minor

        def net_for_gross(gross: int) -> int:
            if gross < fee_floor:
                return 0
            return calculate_seller_receives(
                gross, currency=currency_cfg, validate_market_listing=False
            ).seller_receives

        cons_gross = (reference_price_minor * (100 - risk_buffer_pct)) // 100
        cons_net = net_for_gross(cons_gross)
        cons_profit = cons_net - purchase_minor
        cons_roi = round((float(cons_profit) / float(purchase_minor)) * 100.0, 2) if purchase_minor > 0 else 0.0

        base_net = net_for_gross(reference_price_minor)
        base_profit = base_net - purchase_minor

        opt_target = max(reference_price_minor, optimistic_anchor_minor, floor_median_minor)
        opt_net = net_for_gross(opt_target)
        opt_profit = opt_net - purchase_minor

        return cons_gross, cons_net, cons_profit, cons_roi, base_profit, opt_profit

    async def evaluate_item(
        self,
        item_record: ItemRecord,
        watchlist_entry: WatchlistEntryRecord | None = None,
        dispatch_notifications: bool = True,
        now_dt: datetime | None = None,
    ) -> list[BuyCandidateEvaluation]:
        """Fetch live listings for an item and evaluate each individual listing for BUY profitability."""
        if item_record.item_id is None:
            raise ValueError("ItemRecord must be persisted with item_id before BUY evaluation.")

        curr_id, max_buy_minor, min_disc_pct, min_net_profit, min_roi_pct = self._effective_thresholds(watchlist_entry)
        currency_cfg, curr_rec = self._get_currency_config(curr_id)
        if not currency_cfg.is_metadata_verified or not currency_cfg.is_fee_verified:
            raise ValueError(f"Unverified currency rules for {curr_rec.code}; BUY evaluation aborted.")

        now = now_dt or datetime.now(timezone.utc)
        now_iso = now.isoformat()
        identity = self._build_identity(item_record)

        # 1. Load daily historical points (cookie-free sources)
        pts_30d = self._history_repo.get_recent_points(
            item_id=item_record.item_id,
            currency_id=curr_id,
            days=30,
            now_dt=now,
        )
        if len(pts_30d) < self._config.min_daily_history_points and self._config.fetch_public_history_fallback:
            try:
                public_pts = await self._adapter.get_public_price_history(identity, currency_cfg)
                if public_pts:
                    records = [
                        PriceHistoryPointRecord(
                            item_id=item_record.item_id,
                            currency_id=curr_id,
                            bucket_date=bdate,
                            median_price_minor=median_p,
                            volume=vol,
                            source="steam_public_chart",
                            observed_at=now_iso,
                        )
                        for bdate, median_p, vol in public_pts
                    ]
                    self._history_repo.upsert_batch(records)
                    pts_30d = self._history_repo.get_recent_points(
                        item_id=item_record.item_id,
                        currency_id=curr_id,
                        days=30,
                        now_dt=now,
                    )
            except Exception as exc:
                logger.debug("Optional public chart history extraction skipped for '%s': %s", item_record.market_hash_name, exc)

        # 2. Retrieve individual listings from /render/
        render = await self._adapter.get_listing_render(
            identity=identity,
            currency=curr_id,
            start=0,
            count=self._config.render_count,
        )
        valid_cards, rejected_cards = parse_listing_render_cards(
            render=render,
            expected_market_hash_name=item_record.market_hash_name,
            expected_currency_id=curr_id,
            expected_exterior=identity.exterior,
        )

        # Mark listings missing from fresh render response as expired
        self._buy_eval_repo.mark_missing_listings_expired(
            item_id=item_record.item_id,
            currency_id=curr_id,
            active_steam_listing_ids=[c.steam_listing_id for c in valid_cards],
        )

        if not valid_cards:
            return [
                BuyCandidateEvaluation(
                    item_id=item_record.item_id,
                    market_hash_name=item_record.market_hash_name,
                    steam_listing_id="none",
                    currency_id=curr_id,
                    currency_code=curr_rec.code,
                    currency_symbol=curr_rec.symbol,
                    warning_tags=["NO VALID ACTIVE LISTINGS"]
                    + ([f"Rejected {len(rejected_cards)} listing(s)"] if rejected_cards else []),
                    status="No active listings",
                    is_actionable=False,
                    suppression_reason="Listing render returned zero valid active listings.",
                )
            ]

        # Record immutable raw observations in `listings`
        self._listing_repo.insert_batch([
            ListingRecord(
                steam_listing_id=c.steam_listing_id,
                item_id=item_record.item_id,
                price_minor=c.buyer_pays_minor,
                fee_minor=c.fee_minor,
                steam_fee_minor=c.steam_fee_minor,
                publisher_fee_minor=c.publisher_fee_minor,
                currency_id=curr_id,
                observed_at=now_iso,
                source=f"render_{c.price_source}",
                status="active",
                asset_id=c.asset_id,
            )
            for c in valid_cards
        ])

        # 3. Active market floor & optimistic anchor
        sorted_prices = [c.buyer_pays_minor for c in valid_cards]
        floor_median = int(statistics.median_low(sorted_prices[:3]))
        optimistic_anchor = sorted_prices[min(2, len(sorted_prices) - 1)]

        # 4. Baseline VWM & manipulation / liquidity analytics
        cutoff_14d = (now.date()).toordinal() - 14
        cutoff_7d = (now.date()).toordinal() - 7
        pts_14d = [p for p in pts_30d if datetime.strptime(p.bucket_date, "%Y-%m-%d").date().toordinal() >= cutoff_14d]
        pts_7d = [p for p in pts_30d if datetime.strptime(p.bucket_date, "%Y-%m-%d").date().toordinal() >= cutoff_7d]

        total_vol_14d = sum(p.volume for p in pts_14d)
        sales_7d = sum(p.volume for p in pts_7d)
        has_sufficient_baseline = (
            len(pts_14d) >= self._config.min_daily_history_points
            and total_vol_14d >= self._config.min_cumulative_volume
        )

        baseline_vwm: int | None = None
        reference_price: int | None = None
        std_dev_14d: float = 0.0
        is_manipulated_spike = False

        if has_sufficient_baseline:
            baseline_vwm = compute_weighted_median_minor(
                [(p.median_price_minor, p.volume) for p in pts_14d]
            )
            if len(pts_14d) >= 2:
                std_dev_14d = statistics.stdev([float(p.median_price_minor) for p in pts_14d])
            if len(pts_30d) >= 14 and pts_7d:
                vwm_7d = compute_weighted_median_minor([(p.median_price_minor, p.volume) for p in pts_7d])
                vwm_30d = compute_weighted_median_minor([(p.median_price_minor, p.volume) for p in pts_30d])
                if vwm_30d > 0 and vwm_7d > (2 * vwm_30d):
                    is_manipulated_spike = True
                    baseline_vwm = min(baseline_vwm, vwm_30d)
            reference_price = min(baseline_vwm, floor_median)

        # 5. Optional orderbook context (cached or live if enabled and trusted item_nameid exists)
        latest_ob = self._orderbook_repo.get_latest(item_record.item_id, curr_id)
        if (
            self._config.fetch_orderbook_histogram
            and identity.resolution_status == ResolutionStatus.ITEM_NAMEID_RESOLVED
        ):
            try:
                hist = await self._adapter.get_orderbook_histogram(
                    identity=identity, currency=curr_id, country=currency_cfg.country
                )
                latest_ob = self._orderbook_repo.insert_snapshot(
                    latest_ob.model_copy(
                        update={
                            "snapshot_id": None,
                            "highest_buy_order_minor": hist.highest_buy_order_minor,
                            "lowest_sell_order_minor": hist.lowest_sell_order_minor,
                            "buy_order_count": hist.cumulative_buy_quantity,
                            "top_level_buy_quantity": hist.top_level_buy_quantity,
                            "cumulative_buy_quantity": hist.cumulative_buy_quantity,
                            "observed_at": now_iso,
                        }
                    )
                    if latest_ob
                    else self._build_ob_snapshot(item_record.item_id, curr_id, hist, now_iso)
                )
            except Exception as exc:
                logger.debug("Optional histogram fetch skipped during BUY scan: %s", exc)

        buy_orders_count = (latest_ob.buy_order_count or latest_ob.cumulative_buy_quantity or 0) if latest_ob else 0
        highest_bid = latest_ob.highest_buy_order_minor if latest_ob else None
        lowest_ask = latest_ob.lowest_sell_order_minor if latest_ob else floor_median
        is_crossed_ob = bool(
            highest_bid is not None and lowest_ask is not None and highest_bid >= lowest_ask
        )

        # 6. Evaluate each valid listing card
        results: list[BuyCandidateEvaluation] = []
        listing_page_url = f"https://steamcommunity.com/market/listings/730/{quote(item_record.market_hash_name, safe='')}"

        for card in valid_cards:
            warnings: list[str] = ["7-Day Re-Market Lock Applies"]
            if card.has_notable_craft:
                warnings.append("Sticker Craft Detected")
            if card.is_low_float_tier:
                warnings.append("Low Float Tier")
            if is_manipulated_spike:
                warnings.append("MANIPULATED SPIKE WARNING")
            if is_crossed_ob:
                warnings.append("CROSS-CHECK ORDERBOOK")

            cons_gross, cons_net, cons_profit, cons_roi, base_profit, opt_profit = self._evaluate_exit_scenarios(
                purchase_minor=card.buyer_pays_minor,
                reference_price_minor=reference_price,
                floor_median_minor=floor_median,
                optimistic_anchor_minor=optimistic_anchor,
                risk_buffer_pct=self._config.risk_buffer_pct,
                currency_cfg=currency_cfg,
            )

            discount_pct = 0.0
            if reference_price is not None and reference_price > 0 and card.buyer_pays_minor < reference_price:
                discount_pct = round(((reference_price - card.buyer_pays_minor) / float(reference_price)) * 100.0, 2)

            if discount_pct > 60.0:
                warnings.append("EXTREME DISCOUNT (>60%)")

            opp_score = calculate_buy_opportunity_score(
                conservative_roi_pct=cons_roi,
                sales_7d=sales_7d,
                buy_orders_count=buy_orders_count,
                std_dev_14d_minor=std_dev_14d,
                baseline_minor=baseline_vwm,
                discount_pct=discount_pct,
                listing_age_seconds=0.0,
                low_liquidity_threshold=self._config.min_sales_7d,
            )
            conf_score = calculate_buy_confidence_score(
                daily_points_count=len(pts_14d),
                highest_buy_order_minor=highest_bid,
                lowest_sell_order_minor=lowest_ask,
                data_age_seconds=0.0,
                is_crossed_book=is_crossed_ob,
                is_manipulated_spike=is_manipulated_spike,
                min_daily_points=self._config.min_daily_history_points,
            )

            is_actionable = True
            suppression_reason: str | None = None
            status = "Candidate"

            if not has_sufficient_baseline:
                warnings.append("DATA INSUFFICIENT")
                status = "Data insufficient"
                is_actionable = False
                suppression_reason = (
                    f"Baseline requires >= {self._config.min_daily_history_points} daily points "
                    f"and >= {self._config.min_cumulative_volume} cumulative volume."
                )
            elif sales_7d < self._config.min_sales_7d or (
                self._config.min_buy_orders_count > 0 and buy_orders_count < self._config.min_buy_orders_count
            ):
                warnings.append("ILLIQUID / THIN VOLUME")
                status = "Illiquid market"
                is_actionable = False
                suppression_reason = "7-day sales volume or buy-order depth is below minimum liquidity threshold."
            elif max_buy_minor is not None and card.buyer_pays_minor > max_buy_minor:
                status = "Above max buy price"
                is_actionable = False
                suppression_reason = f"Listing price ({card.buyer_pays_minor}) exceeds max buy limit ({max_buy_minor})."
            elif cons_profit <= 0:
                status = "Not profitable"
                is_actionable = False
                suppression_reason = "Conservative 7-day exit produces non-positive net profit after Valve fees."
            elif min_net_profit is not None and cons_profit < min_net_profit:
                status = "Below min profit"
                is_actionable = False
                suppression_reason = f"Conservative net profit ({cons_profit}) below threshold ({min_net_profit})."
            elif cons_roi < min_roi_pct:
                status = "Below min ROI"
                is_actionable = False
                suppression_reason = f"Conservative ROI ({cons_roi:.2f}%) below minimum ({min_roi_pct:.2f}%)."
            elif min_disc_pct is not None and discount_pct < min_disc_pct:
                status = "Below min discount"
                is_actionable = False
                suppression_reason = f"Discount ({discount_pct:.2f}%) below minimum ({min_disc_pct:.2f}%)."
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
                        f"Opportunity score ({opp_score:.1f}) or confidence ({conf_score:.1f}) "
                        "below candidate threshold; requires manual verification."
                    )

            fingerprint = hashlib.sha256(
                f"BUY:{item_record.item_id}:{curr_id}:{card.steam_listing_id}:{card.buyer_pays_minor}".encode()
            ).hexdigest()

            delivery_results: dict[str, NotificationResult] = {}
            strategy_alert_id: int | None = None

            if is_actionable:
                configured_enabled = [
                    ch for ch in self._dispatcher.channels
                    if ch.is_configured and getattr(ch, "is_enabled", True)
                ]
                if configured_enabled:
                    all_in_cd = True
                    for ch in configured_enabled:
                        ch_cd = self._strategy_alert_repo.is_channel_in_cooldown(
                            strategy="BUY",
                            item_id=item_record.item_id,
                            currency_id=curr_id,
                            alert_type="buy_listing_candidate",
                            reference_key=card.steam_listing_id,
                            channel=ch.channel_name,
                            cooldown_seconds=self._config.cooldown_seconds,
                            new_price_minor=card.buyer_pays_minor,
                            price_direction="drop",
                            meaningful_change_pct=self._config.meaningful_drop_pct,
                            now_dt=now,
                        )
                        ch_bo = self._strategy_alert_repo.is_channel_in_retry_backoff(
                            strategy="BUY",
                            item_id=item_record.item_id,
                            currency_id=curr_id,
                            alert_type="buy_listing_candidate",
                            channel=ch.channel_name,
                            alert_fingerprint=fingerprint,
                            backoff_seconds=self._config.failed_retry_backoff_seconds,
                            max_retries=self._config.max_delivery_retries,
                            now_dt=now,
                        )
                        if not ch_cd and not ch_bo:
                            all_in_cd = False
                            break
                    if all_in_cd:
                        status = "Suppressed (Cooldown)"
                        is_actionable = False
                        suppression_reason = "All configured channels within cooldown or retry backoff."

            saved_eval = self._buy_eval_repo.record_evaluation(
                BuyListingEvaluationRecord(
                    item_id=item_record.item_id,
                    currency_id=curr_id,
                    steam_listing_id=card.steam_listing_id,
                    asset_id=card.asset_id,
                    listing_seller_receives_minor=card.seller_receives_minor,
                    listing_fee_minor=card.fee_minor,
                    purchase_buyer_pays_minor=card.buyer_pays_minor,
                    reference_price_minor=reference_price,
                    baseline_vwm_minor=baseline_vwm,
                    floor_median_minor=floor_median,
                    discount_pct=discount_pct,
                    conservative_exit_gross_minor=cons_gross if reference_price else None,
                    conservative_exit_net_minor=cons_net if reference_price else None,
                    conservative_net_profit_minor=cons_profit if reference_price else None,
                    conservative_roi_pct=cons_roi if reference_price else None,
                    base_net_profit_minor=base_profit if reference_price else None,
                    optimistic_net_profit_minor=opt_profit if reference_price else None,
                    opportunity_score=opp_score,
                    confidence_score=conf_score,
                    float_value=card.float_value,
                    paint_seed=card.paint_seed,
                    paint_index=card.paint_index,
                    stickers_json=json.dumps(card.stickers),
                    inspect_link=card.inspect_link,
                    warning_tags=json.dumps(warnings),
                    status=status,
                    is_actionable=is_actionable,
                    suppression_reason=suppression_reason,
                    alert_fingerprint=fingerprint,
                    listing_status="active",
                    evaluated_at=now_iso,
                )
            )

            if is_actionable and reference_price is not None:
                payload = BuyAlertPayload(
                    alert_fingerprint=fingerprint,
                    item_id=item_record.item_id,
                    currency_id=curr_id,
                    minor_unit_precision=curr_rec.minor_unit_precision,
                    market_hash_name=item_record.market_hash_name,
                    exterior=item_record.exterior,
                    steam_listing_id=card.steam_listing_id,
                    asset_id=card.asset_id,
                    currency_code=curr_rec.code,
                    currency_symbol=curr_rec.symbol,
                    purchase_buyer_pays_minor=card.buyer_pays_minor,
                    reference_price_minor=reference_price,
                    baseline_vwm_minor=baseline_vwm,
                    floor_median_minor=floor_median,
                    discount_pct=discount_pct,
                    conservative_exit_gross_minor=cons_gross,
                    conservative_exit_net_minor=cons_net,
                    conservative_net_profit_minor=cons_profit,
                    conservative_roi_pct=cons_roi,
                    base_net_profit_minor=base_profit,
                    optimistic_net_profit_minor=opt_profit,
                    float_value=card.float_value,
                    paint_seed=card.paint_seed,
                    stickers=card.stickers,
                    opportunity_score=opp_score,
                    confidence_score=conf_score,
                    warning_tags=warnings,
                    status=status,
                    created_at=now_iso,
                    steam_listing_url=listing_page_url,
                    inspect_link=card.inspect_link,
                )
                saved_alert = self._strategy_alert_repo.record_alert(
                    StrategyAlertRecord(
                        strategy="BUY",
                        item_id=item_record.item_id,
                        currency_id=curr_id,
                        alert_type="buy_listing_candidate",
                        alert_fingerprint=fingerprint,
                        reference_key=card.steam_listing_id,
                        reference_price_minor=card.buyer_pays_minor,
                        net_impact_minor=cons_profit,
                        roi_pct=cons_roi,
                        opportunity_score=opp_score,
                        confidence_score=conf_score,
                        warning_tags=json.dumps(warnings),
                        status=status,
                        payload_json=payload.model_dump_json(),
                        created_at=now_iso,
                    )
                )
                strategy_alert_id = saved_alert.strategy_alert_id
                if dispatch_notifications:
                    payload.alert_id = strategy_alert_id
                    delivery_results = await self._dispatcher.dispatch(
                        payload,
                        alert_id=strategy_alert_id,
                        check_cooldown=True,
                        cooldown_seconds=self._config.cooldown_seconds,
                        meaningful_increase_pct=self._config.meaningful_drop_pct,
                        failed_retry_backoff_seconds=self._config.failed_retry_backoff_seconds,
                        max_delivery_retries=self._config.max_delivery_retries,
                        now_dt=now,
                    )

            results.append(
                BuyCandidateEvaluation(
                    evaluation_id=saved_eval.evaluation_id,
                    item_id=item_record.item_id,
                    market_hash_name=item_record.market_hash_name,
                    steam_listing_id=card.steam_listing_id,
                    asset_id=card.asset_id,
                    currency_id=curr_id,
                    currency_code=curr_rec.code,
                    currency_symbol=curr_rec.symbol,
                    purchase_buyer_pays_minor=card.buyer_pays_minor,
                    listing_seller_receives_minor=card.seller_receives_minor,
                    listing_fee_minor=card.fee_minor,
                    baseline_vwm_minor=baseline_vwm,
                    floor_median_minor=floor_median,
                    reference_price_minor=reference_price,
                    discount_pct=discount_pct,
                    conservative_exit_gross_minor=cons_gross,
                    conservative_exit_net_minor=cons_net,
                    conservative_net_profit_minor=cons_profit,
                    conservative_roi_pct=cons_roi,
                    base_net_profit_minor=base_profit,
                    optimistic_net_profit_minor=opt_profit,
                    float_value=card.float_value,
                    paint_seed=card.paint_seed,
                    paint_index=card.paint_index,
                    stickers=card.stickers,
                    inspect_link=card.inspect_link,
                    opportunity_score=opp_score,
                    confidence_score=conf_score,
                    warning_tags=warnings,
                    status=status,
                    is_actionable=is_actionable,
                    suppression_reason=suppression_reason,
                    strategy_alert_id=strategy_alert_id,
                    delivery_results=delivery_results,
                )
            )

        return results

    @staticmethod
    def _build_ob_snapshot(item_id: int, currency_id: int, hist: Any, now_iso: str) -> Any:
        from sniper.storage.models import OrderbookSnapshotRecord
        return OrderbookSnapshotRecord(
            item_id=item_id,
            currency_id=currency_id,
            highest_buy_order_minor=hist.highest_buy_order_minor,
            lowest_sell_order_minor=hist.lowest_sell_order_minor,
            buy_order_count=hist.cumulative_buy_quantity,
            top_level_buy_quantity=hist.top_level_buy_quantity,
            cumulative_buy_quantity=hist.cumulative_buy_quantity,
            observed_at=now_iso,
            source_payload_version="histogram_v1",
        )

    async def scan_watchlist(
        self,
        currency_id: int | None = None,
        dispatch_notifications: bool = True,
    ) -> list[BuyCandidateEvaluation]:
        """Scan all active BUY (and BOTH) watchlist entries sequentially."""
        entries = self._watchlist_repo.list_entries(
            strategy="BUY",
            active_only=True,
            currency_id=currency_id,
        )
        if not entries:
            return []

        all_evals: list[BuyCandidateEvaluation] = []
        for entry in entries:
            item = self._item_repo.get_by_id(entry.item_id)
            if item is None:
                continue
            evals = await self.evaluate_item(
                item_record=item,
                watchlist_entry=entry,
                dispatch_notifications=dispatch_notifications,
            )
            self._watchlist_repo.update_last_scanned(entry.watchlist_id)
            all_evals.extend(evals)
        return all_evals


def import_manual_price_history_json(
    json_content: str,
    market_hash_name: str,
    db: Database,
    currency_id: int = 1,
) -> int:
    """Import daily price history baseline records from JSON for offline/cookie-free seeding.

    Accepts a list of dicts: `[{"bucket_date": "2026-11-20", "median_price_minor": 2500, "volume": 15}]`.
    Strictly validates integer `median_price_minor > 0` (rejects booleans/floats) and `volume >= 0`.
    """
    raw = json.loads(json_content)
    if not isinstance(raw, list):
        raise ValueError("Price history JSON must be an array of daily point objects.")

    item_repo = ItemRepository(db)
    item = item_repo.get_by_market_hash_name(market_hash_name)
    if item is None:
        base_name, ext, is_st, is_souv = decompose_market_hash_name(market_hash_name)
        item = item_repo.save(
            ItemRecord(
                market_hash_name=market_hash_name,
                base_name=base_name,
                exterior=ext.value if ext else None,
                is_stattrak=is_st,
                is_souvenir=is_souv,
            )
        )

    now_iso = datetime.now(timezone.utc).isoformat()
    records: list[PriceHistoryPointRecord] = []
    for idx, entry in enumerate(raw, start=1):
        if not isinstance(entry, dict):
            raise ValueError(f"History entry #{idx} must be a JSON object.")
        bdate = str(entry.get("bucket_date") or "").strip()
        try:
            datetime.strptime(bdate, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError(f"History entry #{idx} has invalid YYYY-MM-DD bucket_date: {bdate!r}") from exc

        price_minor = entry.get("median_price_minor")
        if type(price_minor) is not int or price_minor <= 0:
            raise ValueError(
                f"History entry #{idx}: median_price_minor must be a positive integer, got {price_minor!r}"
            )
        vol = entry.get("volume", 1)
        if type(vol) is not int or vol < 0:
            raise ValueError(f"History entry #{idx}: volume must be a non-negative integer, got {vol!r}")

        records.append(
            PriceHistoryPointRecord(
                item_id=item.item_id,
                currency_id=currency_id,
                bucket_date=bdate,
                median_price_minor=price_minor,
                volume=vol,
                source="manual_import",
                observed_at=now_iso,
            )
        )

    return PriceHistoryRepository(db).upsert_batch(records)
