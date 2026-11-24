"""Portfolio & 7-Day Marketability Restriction Service (Milestone 7)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import re
import statistics
from typing import Any, Sequence
from urllib.parse import quote

from pydantic import BaseModel, Field

from sniper.adapters.listing_parser import parse_listing_render_cards, strip_html_text
from sniper.adapters.steam_market import SteamMarketAdapter
from sniper.core.currency import CurrencyConfig
from sniper.core.fees import (
    calculate_exact_breakeven_buyer_pays,
    calculate_seller_receives,
)
from sniper.core.identity import (
    ItemIdentityResult,
    ResolutionStatus,
    UrlType,
    WearCategory,
    decompose_market_hash_name,
)
from sniper.notifications.dispatcher import NotificationDispatcher
from sniper.notifications.models import NotificationResult, PortfolioAlertPayload
from sniper.storage.database import Database
from sniper.storage.models import (
    CurrencyRecord,
    HoldingRecord,
    ItemRecord,
    PortfolioTransactionRecord,
    StrategyAlertRecord,
)
from sniper.storage.repositories import (
    BuyEvaluationRepository,
    CurrencyRepository,
    HoldingRepository,
    ItemRepository,
    ListingRepository,
    OrderbookRepository,
    PortfolioRepository,
    StrategyAlertRepository,
)

logger = logging.getLogger(__name__)

_STEAM_RESTRICTION_DATE_RE = re.compile(
    r"(?:until|after)\s+([A-Za-z]{3}\s+\d{1,2},\s+\d{4}\s*\(\d{1,2}:\d{2}:\d{2}\)(?:\s*(?:GMT|UTC))?)",
    re.IGNORECASE,
)


def parse_steam_marketable_timestamp(
    descriptions: Sequence[Any] | None = None,
    restriction_days: int | None = None,
    acquired_at: datetime | None = None,
) -> tuple[datetime | None, str]:
    """Parse Steam's authoritative item unlock timestamp from descriptions before falling back.

    Steam renders messages like:
      "Cannot be traded or listed on Community Market until Nov 29, 2026 (7:00:00) GMT"
      "Tradable After Nov 29, 2026 (07:00:00) UTC"
    Returns (marketable_at_utc, restriction_source).
    Never blindly uses +7 days when Steam exposes an explicit unlock date.
    """
    if descriptions:
        for entry in descriptions:
            raw_val = entry.get("value") if isinstance(entry, dict) else getattr(entry, "value", "")
            plain = strip_html_text(str(raw_val or ""))
            match = _STEAM_RESTRICTION_DATE_RE.search(plain)
            if match:
                date_part = re.sub(r"\s+(?:GMT|UTC)$", "", match.group(1).strip(), flags=re.IGNORECASE).strip()
                date_part = re.sub(r"\s+", " ", date_part)
                for fmt in ("%b %d, %Y (%H:%M:%S)", "%b %d, %Y %H:%M:%S"):
                    try:
                        parsed = datetime.strptime(date_part, fmt).replace(tzinfo=timezone.utc)
                        return parsed, "steam_observed"
                    except ValueError:
                        continue

    if restriction_days is not None:
        if type(restriction_days) is not int or restriction_days < 0:
            raise ValueError(f"restriction_days must be a non-negative integer, got {restriction_days!r}")
        base = acquired_at or datetime.now(timezone.utc)
        if base.tzinfo is None:
            base = base.replace(tzinfo=timezone.utc)
        if restriction_days == 0:
            return base, "steam_observed"
        return base + timedelta(days=restriction_days), "manual_days"

    return None, "steam_observed"


def _parse_iso_utc(raw: str | None) -> datetime | None:
    if not raw:
        return None
    s = raw.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


class HoldingStatusView(BaseModel):
    """Enriched holding status including real-time lock countdown and wallet P&L."""
    holding_id: int
    item_id: int
    asset_id: str | None = None
    market_hash_name: str
    currency_id: int
    currency_code: str = "USD"
    currency_symbol: str = "$"
    quantity: int = 1
    acquisition_cost_minor: int
    breakeven_buyer_pays_minor: int
    acquired_at: str
    marketable_at: str | None = None
    restriction_source: str
    status: str
    seconds_until_marketable: int = 0
    is_locked: bool = False
    days_held: int = 0
    current_floor_minor: int | None = None
    current_est_net_minor: int | None = None
    unrealized_pnl_minor: int | None = None
    unrealized_roi_pct: float | None = None
    realized_net_proceeds_minor: int | None = None
    realized_pnl_minor: int | None = None
    is_under_water: bool = False
    under_water_gap_minor: int = 0
    alert_dispatched: bool = False
    delivery_results: dict[str, NotificationResult] = Field(default_factory=dict)


class PortfolioOverview(BaseModel):
    """Aggregate portfolio metrics strictly separating Steam Wallet amounts by currency."""
    currency_id: int = 1
    currency_code: str = "USD"
    currency_symbol: str = "$"
    total_holdings: int = 0
    locked_count: int = 0
    ready_to_list_count: int = 0
    listed_count: int = 0
    sold_count: int = 0
    under_water_count: int = 0
    capital_deployed_minor: int = 0
    estimated_wallet_value_minor: int = 0
    unrealized_wallet_pnl_minor: int = 0
    realized_wallet_pnl_minor: int = 0
    holdings: list[HoldingStatusView] = Field(default_factory=list)


class PortfolioService:
    """Manages acquired CS2 holdings, 7-day locks, break-even tracking, and resale readiness."""

    def __init__(
        self,
        db: Database,
        dispatcher: NotificationDispatcher | None = None,
    ) -> None:
        self._db = db
        self._dispatcher = dispatcher or NotificationDispatcher.from_database(db)
        self._item_repo = ItemRepository(db)
        self._curr_repo = CurrencyRepository(db)
        self._portfolio_repo = PortfolioRepository(db)
        self._holding_repo = HoldingRepository(db)
        self._buy_eval_repo = BuyEvaluationRepository(db)
        self._listing_repo = ListingRepository(db)
        self._ob_repo = OrderbookRepository(db)
        self._strategy_alert_repo = StrategyAlertRepository(db)

    def _get_currency_cfg(self, currency_id: int) -> tuple[CurrencyConfig, CurrencyRecord]:
        rec = self._curr_repo.get_by_id(currency_id)
        if rec is None:
            self._curr_repo.seed_default_currencies()
            rec = self._curr_repo.get_by_id(currency_id)
            if rec is None:
                raise ValueError(f"Currency ID {currency_id} is not configured.")
        cfg = CurrencyConfig(
            currency_id=rec.currency_id,
            code=rec.code,
            symbol=rec.symbol,
            minor_unit_precision=rec.minor_unit_precision,
            country=rec.country,
            min_listing_price_minor=rec.min_listing_price_minor,
            price_step_minor=rec.price_step_minor,
            min_fee_steam_minor=rec.min_fee_steam_minor,
            min_fee_publisher_minor=rec.min_fee_publisher_minor,
            is_metadata_verified=rec.is_metadata_verified,
            is_fee_verified=rec.is_fee_verified,
            is_listing_min_verified=rec.is_listing_min_verified,
            is_price_step_verified=rec.is_price_step_verified,
            notes=rec.notes,
        )
        return cfg, rec

    def _ensure_item(self, market_hash_name: str) -> ItemRecord:
        existing = self._item_repo.get_by_market_hash_name(market_hash_name)
        if existing is not None:
            return existing
        base_name, ext, is_st, is_souv = decompose_market_hash_name(market_hash_name)
        return self._item_repo.save(
            ItemRecord(
                market_hash_name=market_hash_name,
                base_name=base_name,
                exterior=ext.value if ext else None,
                is_stattrak=is_st,
                is_souvenir=is_souv,
            )
        )

    def add_holding(
        self,
        market_hash_name: str,
        acquisition_cost_minor: int,
        currency_id: int = 1,
        quantity: int = 1,
        asset_id: str | None = None,
        acquired_at: datetime | None = None,
        marketable_at: datetime | None = None,
        restriction_days: int | None = 7,
        steam_descriptions: Sequence[Any] | None = None,
        notes: str | None = None,
        now_dt: datetime | None = None,
    ) -> HoldingRecord:
        """Record an acquired holding and compute exact M4 break-even price."""
        if type(acquisition_cost_minor) is not int or acquisition_cost_minor <= 0:
            raise ValueError(
                f"acquisition_cost_minor must be a positive integer, got {acquisition_cost_minor!r}"
            )
        if type(quantity) is not int or quantity <= 0:
            raise ValueError(f"quantity must be a positive integer, got {quantity!r}")

        cfg, _ = self._get_currency_cfg(currency_id)
        item = self._ensure_item(market_hash_name)
        now = now_dt or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)

        acq_dt = acquired_at or now
        if acq_dt.tzinfo is None:
            acq_dt = acq_dt.replace(tzinfo=timezone.utc)

        # Resolve marketable date preferring authoritative Steam timestamp over estimates
        resolved_unlock: datetime | None = None
        source = "default_7d"
        if steam_descriptions:
            resolved_unlock, source = parse_steam_marketable_timestamp(
                descriptions=steam_descriptions,
                restriction_days=restriction_days,
                acquired_at=acq_dt,
            )

        if resolved_unlock is None:
            if marketable_at is not None:
                resolved_unlock = (
                    marketable_at if marketable_at.tzinfo else marketable_at.replace(tzinfo=timezone.utc)
                )
                source = "manual_date"
            elif restriction_days is not None:
                if type(restriction_days) is not int or restriction_days < 0:
                    raise ValueError(f"Invalid restriction_days: {restriction_days!r}")
                resolved_unlock = acq_dt + timedelta(days=restriction_days)
                source = "manual_days" if restriction_days != 7 else "default_7d"
            else:
                resolved_unlock = acq_dt
                source = "manual_date"

        breakeven_gross = calculate_exact_breakeven_buyer_pays(
            cost_basis_minor=acquisition_cost_minor,
            currency=cfg,
        )

        # Record corresponding BUY ledger transaction
        buy_tx = self._portfolio_repo.record_transaction(
            PortfolioTransactionRecord(
                item_id=item.item_id,
                transaction_type="BUY",
                quantity=quantity,
                unit_price_minor=acquisition_cost_minor,
                total_fees_minor=0,
                currency_id=currency_id,
                timestamp=acq_dt.isoformat(),
                notes=notes or "Portfolio holding acquisition",
            )
        )

        initial_status = "HOLDING" if now < resolved_unlock else "READY_TO_LIST"
        return self._holding_repo.create(
            HoldingRecord(
                item_id=item.item_id,
                buy_transaction_id=buy_tx.transaction_id,
                asset_id=asset_id,
                market_hash_name=item.market_hash_name,
                currency_id=currency_id,
                quantity=quantity,
                acquisition_cost_minor=acquisition_cost_minor,
                acquired_at=acq_dt.isoformat(),
                marketable_at=resolved_unlock.isoformat(),
                restriction_source=source,
                status=initial_status,
                breakeven_buyer_pays_minor=breakeven_gross,
                notes=notes,
                created_at=now.isoformat(),
                updated_at=now.isoformat(),
            )
        )

    def _lookup_cached_floor(self, item_id: int, currency_id: int) -> int | None:
        """Resolve latest verified market floor from BUY evaluations, listings, or orderbook."""
        evals = self._buy_eval_repo.list_recent(item_id=item_id, currency_id=currency_id, limit=5)
        active_prices = [
            e.purchase_buyer_pays_minor
            for e in evals
            if e.listing_status == "active" and e.purchase_buyer_pays_minor > 0
        ]
        if active_prices:
            return int(statistics.median_low(sorted(active_prices)[:3]))

        latest_ob = self._ob_repo.get_latest(item_id, currency_id)
        if latest_ob and latest_ob.lowest_sell_order_minor and latest_ob.lowest_sell_order_minor > 0:
            return latest_ob.lowest_sell_order_minor

        latest_listing = self._listing_repo.get_latest(item_id, currency_id)
        if latest_listing and latest_listing.price_minor > 0:
            return latest_listing.price_minor
        return None

    async def _fetch_live_floor(
        self,
        item: ItemRecord,
        currency_cfg: CurrencyConfig,
        market_adapter: SteamMarketAdapter,
    ) -> int | None:
        ident = ItemIdentityResult(
            appid=730,
            input_url=f"https://steamcommunity.com/market/listings/730/{quote(item.market_hash_name, safe='')}",
            url_type=UrlType.CANONICAL,
            base_name=item.base_name,
            market_hash_name=item.market_hash_name,
            exterior=WearCategory(item.exterior) if item.exterior else None,
            is_stattrak=bool(item.is_stattrak),
            is_souvenir=bool(item.is_souvenir),
            resolution_status=ResolutionStatus.CANONICAL_RESOLVED,
        )
        render = await market_adapter.get_listing_render(
            identity=ident,
            currency=currency_cfg.currency_id,
            start=0,
            count=10,
        )
        valid_cards, _ = parse_listing_render_cards(
            render=render,
            expected_market_hash_name=item.market_hash_name,
            expected_currency_id=currency_cfg.currency_id,
            expected_exterior=ident.exterior,
        )
        if not valid_cards:
            return None
        return int(statistics.median_low([c.buyer_pays_minor for c in valid_cards[:3]]))

    async def evaluate_holdings(
        self,
        floor_overrides: dict[int, int] | None = None,
        market_adapter: SteamMarketAdapter | None = None,
        dispatch_notifications: bool = True,
        cooldown_seconds: int = 3600,
        now_dt: datetime | None = None,
    ) -> list[HoldingStatusView]:
        """Evaluate active holdings for 7-day unlock transitions, break-even health, and RESALE READY alerts."""
        now = now_dt or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)

        active = self._holding_repo.list_holdings(active_only=True)
        views: list[HoldingStatusView] = []
        floor_overrides = floor_overrides or {}

        for h in active:
            cfg, curr_rec = self._get_currency_cfg(h.currency_id)
            item = self._item_repo.get_by_id(h.item_id)

            unlock_dt = _parse_iso_utc(h.marketable_at)
            acq_dt = _parse_iso_utc(h.acquired_at) or now
            is_locked = bool(unlock_dt is not None and now < unlock_dt)
            secs_remaining = max(0, int((unlock_dt - now).total_seconds())) if unlock_dt else 0
            days_held = max(0, (now.date() - acq_dt.date()).days)

            new_status = h.status
            if h.status == "HOLDING" and not is_locked:
                new_status = "READY_TO_LIST"
            elif h.status == "READY_TO_LIST" and is_locked:
                new_status = "HOLDING"

            # Determine market floor
            floor_minor = floor_overrides.get(h.holding_id)
            if floor_minor is None and market_adapter is not None and item is not None:
                try:
                    floor_minor = await self._fetch_live_floor(item, cfg, market_adapter)
                except Exception as exc:
                    logger.debug("Live floor fetch skipped for holding #%s: %s", h.holding_id, exc)
            if floor_minor is None:
                floor_minor = self._lookup_cached_floor(h.item_id, h.currency_id)

            est_net_minor: int | None = None
            unrealized_pnl: int | None = None
            unrealized_roi: float | None = None
            is_under_water = False
            gap_minor = 0

            fee_floor = 1 + cfg.min_fee_steam_minor + cfg.min_fee_publisher_minor
            if floor_minor is not None and floor_minor >= fee_floor:
                est_net_minor = calculate_seller_receives(
                    floor_minor, currency=cfg, validate_market_listing=False
                ).seller_receives
                unit_pnl = est_net_minor - h.acquisition_cost_minor
                unrealized_pnl = unit_pnl * h.quantity
                unrealized_roi = round(
                    (float(unit_pnl) / float(h.acquisition_cost_minor)) * 100.0, 2
                )
                if floor_minor < h.breakeven_buyer_pays_minor:
                    is_under_water = True
                    gap_minor = h.breakeven_buyer_pays_minor - floor_minor

            resale_sent_at = h.resale_alert_sent_at
            underwater_sent_at = h.underwater_alert_sent_at
            delivery_results: dict[str, NotificationResult] = {}
            alert_dispatched = False

            listing_url = f"https://steamcommunity.com/market/listings/730/{quote(h.market_hash_name, safe='')}"

            # Fire RESALE READY ONLY when NOT locked and current est net > cost basis
            if (
                new_status == "READY_TO_LIST"
                and not is_locked
                and floor_minor is not None
                and est_net_minor is not None
                and est_net_minor > h.acquisition_cost_minor
            ):
                in_cd = self._strategy_alert_repo.is_channel_in_cooldown(
                    strategy="PORTFOLIO",
                    item_id=h.item_id,
                    currency_id=h.currency_id,
                    alert_type="portfolio_resale_ready",
                    reference_key=str(h.holding_id),
                    channel="discord",
                    cooldown_seconds=cooldown_seconds,
                    new_price_minor=floor_minor,
                    price_direction="rise",
                    now_dt=now,
                )
                tg_cd = self._strategy_alert_repo.is_channel_in_cooldown(
                    strategy="PORTFOLIO",
                    item_id=h.item_id,
                    currency_id=h.currency_id,
                    alert_type="portfolio_resale_ready",
                    reference_key=str(h.holding_id),
                    channel="telegram",
                    cooldown_seconds=cooldown_seconds,
                    new_price_minor=floor_minor,
                    price_direction="rise",
                    now_dt=now,
                )
                if resale_sent_at is None and not (in_cd and tg_cd):
                    unit_pnl = est_net_minor - h.acquisition_cost_minor
                    roi_pct = round((float(unit_pnl) / float(h.acquisition_cost_minor)) * 100.0, 2)
                    fp = hashlib.sha256(
                        f"PORTFOLIO:RESALE:{h.holding_id}:{floor_minor}".encode()
                    ).hexdigest()
                    payload = PortfolioAlertPayload(
                        alert_type="portfolio_resale_ready",
                        alert_fingerprint=fp,
                        item_id=h.item_id,
                        holding_id=h.holding_id,
                        currency_id=h.currency_id,
                        minor_unit_precision=cfg.minor_unit_precision,
                        market_hash_name=h.market_hash_name,
                        currency_code=curr_rec.code,
                        currency_symbol=curr_rec.symbol,
                        acquisition_cost_minor=h.acquisition_cost_minor,
                        breakeven_buyer_pays_minor=h.breakeven_buyer_pays_minor,
                        recommended_list_gross_minor=floor_minor,
                        expected_net_proceeds_minor=est_net_minor,
                        expected_profit_minor=unit_pnl * h.quantity,
                        expected_roi_pct=roi_pct,
                        days_held=days_held,
                        status="RESALE READY",
                        created_at=now.isoformat(),
                        steam_listing_url=listing_url,
                    )
                    saved_alert = self._strategy_alert_repo.record_alert(
                        StrategyAlertRecord(
                            strategy="PORTFOLIO",
                            item_id=h.item_id,
                            currency_id=h.currency_id,
                            alert_type="portfolio_resale_ready",
                            alert_fingerprint=fp,
                            reference_key=str(h.holding_id),
                            reference_price_minor=floor_minor,
                            net_impact_minor=unit_pnl * h.quantity,
                            roi_pct=roi_pct,
                            opportunity_score=min(100.0, max(50.0, roi_pct * 3.0)),
                            confidence_score=90.0,
                            warning_tags=json.dumps(["7-Day Lock Expired"]),
                            status="RESALE READY",
                            payload_json=payload.model_dump_json(),
                            created_at=now.isoformat(),
                        )
                    )
                    if dispatch_notifications and self._dispatcher is not None:
                        payload.alert_id = saved_alert.strategy_alert_id
                        delivery_results = await self._dispatcher.dispatch(
                            payload,
                            alert_id=saved_alert.strategy_alert_id,
                            check_cooldown=True,
                            cooldown_seconds=cooldown_seconds,
                            now_dt=now,
                        )
                    resale_sent_at = now.isoformat()
                    alert_dispatched = True

            # Optional Under Water warning while locked
            elif (
                is_under_water
                and floor_minor is not None
                and est_net_minor is not None
                and underwater_sent_at is None
            ):
                unit_pnl = est_net_minor - h.acquisition_cost_minor
                roi_pct = round((float(unit_pnl) / float(h.acquisition_cost_minor)) * 100.0, 2)
                fp = hashlib.sha256(
                    f"PORTFOLIO:UW:{h.holding_id}:{floor_minor}".encode()
                ).hexdigest()
                payload = PortfolioAlertPayload(
                    alert_type="portfolio_under_water",
                    alert_fingerprint=fp,
                    item_id=h.item_id,
                    holding_id=h.holding_id,
                    currency_id=h.currency_id,
                    minor_unit_precision=cfg.minor_unit_precision,
                    market_hash_name=h.market_hash_name,
                    currency_code=curr_rec.code,
                    currency_symbol=curr_rec.symbol,
                    acquisition_cost_minor=h.acquisition_cost_minor,
                    breakeven_buyer_pays_minor=h.breakeven_buyer_pays_minor,
                    recommended_list_gross_minor=floor_minor,
                    expected_net_proceeds_minor=est_net_minor,
                    expected_profit_minor=unit_pnl * h.quantity,
                    expected_roi_pct=roi_pct,
                    days_held=days_held,
                    status="Under Water",
                    created_at=now.isoformat(),
                    steam_listing_url=listing_url,
                )
                saved_alert = self._strategy_alert_repo.record_alert(
                    StrategyAlertRecord(
                        strategy="PORTFOLIO",
                        item_id=h.item_id,
                        currency_id=h.currency_id,
                        alert_type="portfolio_under_water",
                        alert_fingerprint=fp,
                        reference_key=str(h.holding_id),
                        reference_price_minor=floor_minor,
                        net_impact_minor=unit_pnl * h.quantity,
                        roi_pct=roi_pct,
                        opportunity_score=0.0,
                        confidence_score=85.0,
                        warning_tags=json.dumps(["Under Water: Floor Below Break-Even"]),
                        status="Under Water",
                        payload_json=payload.model_dump_json(),
                        created_at=now.isoformat(),
                    )
                )
                if dispatch_notifications and self._dispatcher is not None:
                    payload.alert_id = saved_alert.strategy_alert_id
                    delivery_results = await self._dispatcher.dispatch(
                        payload,
                        alert_id=saved_alert.strategy_alert_id,
                        check_cooldown=True,
                        cooldown_seconds=cooldown_seconds,
                        now_dt=now,
                    )
                underwater_sent_at = now.isoformat()
                alert_dispatched = True

            updated_rec = self._holding_repo.update(
                h.model_copy(
                    update={
                        "status": new_status,
                        "current_floor_minor": floor_minor,
                        "current_est_net_minor": est_net_minor,
                        "is_under_water": is_under_water,
                        "under_water_gap_minor": gap_minor,
                        "last_evaluated_at": now.isoformat(),
                        "resale_alert_sent_at": resale_sent_at,
                        "underwater_alert_sent_at": underwater_sent_at,
                    }
                )
            )
            views.append(
                HoldingStatusView(
                    holding_id=updated_rec.holding_id,
                    item_id=updated_rec.item_id,
                    asset_id=updated_rec.asset_id,
                    market_hash_name=updated_rec.market_hash_name,
                    currency_id=updated_rec.currency_id,
                    currency_code=curr_rec.code,
                    currency_symbol=curr_rec.symbol,
                    quantity=updated_rec.quantity,
                    acquisition_cost_minor=updated_rec.acquisition_cost_minor,
                    breakeven_buyer_pays_minor=updated_rec.breakeven_buyer_pays_minor,
                    acquired_at=updated_rec.acquired_at,
                    marketable_at=updated_rec.marketable_at,
                    restriction_source=updated_rec.restriction_source,
                    status=updated_rec.status,
                    seconds_until_marketable=secs_remaining,
                    is_locked=is_locked,
                    days_held=days_held,
                    current_floor_minor=floor_minor,
                    current_est_net_minor=est_net_minor,
                    unrealized_pnl_minor=unrealized_pnl,
                    unrealized_roi_pct=unrealized_roi,
                    realized_net_proceeds_minor=updated_rec.realized_net_proceeds_minor,
                    realized_pnl_minor=None,
                    is_under_water=is_under_water,
                    under_water_gap_minor=gap_minor,
                    alert_dispatched=alert_dispatched,
                    delivery_results=delivery_results,
                )
            )

        return views

    def record_sale(
        self,
        holding_id: int,
        gross_sale_price_minor: int,
        notes: str | None = None,
        now_dt: datetime | None = None,
    ) -> HoldingRecord:
        """Record manual completion of a Steam Market sale using exact M4 fee calculation."""
        if type(gross_sale_price_minor) is not int or gross_sale_price_minor <= 0:
            raise ValueError("gross_sale_price_minor must be a positive integer.")

        holding = self._holding_repo.get_by_id(holding_id)
        if holding is None:
            raise ValueError(f"Holding #{holding_id} not found.")
        if holding.status == "SOLD":
            raise ValueError(f"Holding #{holding_id} is already marked SOLD.")

        now = now_dt or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)

        cfg, _ = self._get_currency_cfg(holding.currency_id)
        fee_bd = calculate_seller_receives(
            gross_sale_price_minor, currency=cfg, validate_market_listing=False
        )
        total_fees = fee_bd.total_fee * holding.quantity
        total_net = fee_bd.seller_receives * holding.quantity

        sell_tx = self._portfolio_repo.record_transaction(
            PortfolioTransactionRecord(
                item_id=holding.item_id,
                transaction_type="SELL",
                quantity=holding.quantity,
                unit_price_minor=gross_sale_price_minor,
                total_fees_minor=total_fees,
                currency_id=holding.currency_id,
                timestamp=now.isoformat(),
                notes=notes or f"Manual sale of holding #{holding_id}",
            )
        )

        return self._holding_repo.update(
            holding.model_copy(
                update={
                    "sell_transaction_id": sell_tx.transaction_id,
                    "status": "SOLD",
                    "listed_price_minor": gross_sale_price_minor,
                    "realized_net_proceeds_minor": total_net,
                    "is_under_water": False,
                    "under_water_gap_minor": 0,
                    "last_evaluated_at": now.isoformat(),
                    "notes": notes or holding.notes,
                }
            )
        )

    def get_overview(
        self,
        currency_id: int = 1,
        now_dt: datetime | None = None,
    ) -> PortfolioOverview:
        """Return strictly typed, exact integer-cent portfolio overview."""
        now = now_dt or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)

        _, curr_rec = self._get_currency_cfg(currency_id)
        all_rows = self._holding_repo.list_holdings(currency_id=currency_id)

        capital_deployed = 0
        est_value = 0
        unrealized_pnl = 0
        realized_pnl = 0
        locked = ready = listed = sold = under_water = 0
        views: list[HoldingStatusView] = []

        for h in all_rows:
            unlock_dt = _parse_iso_utc(h.marketable_at)
            acq_dt = _parse_iso_utc(h.acquired_at) or now
            is_locked = bool(h.status != "SOLD" and unlock_dt is not None and now < unlock_dt)
            secs_rem = max(0, int((unlock_dt - now).total_seconds())) if is_locked and unlock_dt else 0
            days_held = max(0, (now.date() - acq_dt.date()).days)

            cost_total = h.acquisition_cost_minor * h.quantity
            realized_item_pnl: int | None = None
            unrealized_item_pnl: int | None = None
            unrealized_roi: float | None = None

            if h.status == "SOLD":
                sold += 1
                if h.realized_net_proceeds_minor is not None:
                    realized_item_pnl = h.realized_net_proceeds_minor - cost_total
                    realized_pnl += realized_item_pnl
            else:
                capital_deployed += cost_total
                if h.status == "HOLDING":
                    locked += 1
                elif h.status == "READY_TO_LIST":
                    ready += 1
                elif h.status == "LISTED":
                    listed += 1
                if h.is_under_water:
                    under_water += 1

                if h.current_est_net_minor is not None:
                    est_total = h.current_est_net_minor * h.quantity
                    est_value += est_total
                    unrealized_item_pnl = est_total - cost_total
                    unrealized_pnl += unrealized_item_pnl
                    unrealized_roi = round(
                        (float(h.current_est_net_minor - h.acquisition_cost_minor) / float(h.acquisition_cost_minor)) * 100.0,
                        2,
                    )

            views.append(
                HoldingStatusView(
                    holding_id=h.holding_id,
                    item_id=h.item_id,
                    asset_id=h.asset_id,
                    market_hash_name=h.market_hash_name,
                    currency_id=h.currency_id,
                    currency_code=curr_rec.code,
                    currency_symbol=curr_rec.symbol,
                    quantity=h.quantity,
                    acquisition_cost_minor=h.acquisition_cost_minor,
                    breakeven_buyer_pays_minor=h.breakeven_buyer_pays_minor,
                    acquired_at=h.acquired_at,
                    marketable_at=h.marketable_at,
                    restriction_source=h.restriction_source,
                    status=h.status,
                    seconds_until_marketable=secs_rem,
                    is_locked=is_locked,
                    days_held=days_held,
                    current_floor_minor=h.current_floor_minor,
                    current_est_net_minor=h.current_est_net_minor,
                    unrealized_pnl_minor=unrealized_item_pnl,
                    unrealized_roi_pct=unrealized_roi,
                    realized_net_proceeds_minor=h.realized_net_proceeds_minor,
                    realized_pnl_minor=realized_item_pnl,
                    is_under_water=h.is_under_water,
                    under_water_gap_minor=h.under_water_gap_minor,
                )
            )

        return PortfolioOverview(
            currency_id=currency_id,
            currency_code=curr_rec.code,
            currency_symbol=curr_rec.symbol,
            total_holdings=len(all_rows),
            locked_count=locked,
            ready_to_list_count=ready,
            listed_count=listed,
            sold_count=sold,
            under_water_count=under_water,
            capital_deployed_minor=capital_deployed,
            estimated_wallet_value_minor=est_value,
            unrealized_wallet_pnl_minor=unrealized_pnl,
            realized_wallet_pnl_minor=realized_pnl,
            holdings=views,
        )
