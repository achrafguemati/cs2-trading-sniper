"""Notification dispatcher coordinating Discord and Telegram deliveries with isolation and SQLite persistence."""

from __future__ import annotations

from datetime import datetime
import logging
from typing import Sequence

from sniper.notifications.base import BaseNotificationChannel, BaseNotifier, redact_secrets
from sniper.notifications.discord import DiscordNotifier
from sniper.notifications.models import (
    AlertPayload,
    BuyAlertPayload,
    DeliveryStatus,
    NotificationResult,
    PortfolioAlertPayload,
)
from sniper.notifications.telegram import TelegramNotifier
from sniper.storage.database import Database
from sniper.storage.models import NotificationDeliveryRecord, StrategyAlertDeliveryRecord
from sniper.storage.repositories import AlertRepository, StrategyAlertRepository

logger = logging.getLogger(__name__)


class NotificationDispatcher:
    """Dispatches alerts across Discord and Telegram with failure isolation, delivery cooldown, and audit logging."""

    def __init__(
        self,
        channels: Sequence[BaseNotifier] | None = None,
        alert_repo: AlertRepository | None = None,
        strategy_alert_repo: StrategyAlertRepository | None = None,
    ) -> None:
        self._channels = list(channels) if channels is not None else [
            DiscordNotifier(),
            TelegramNotifier(),
        ]
        self._alert_repo = alert_repo
        self._strategy_alert_repo = strategy_alert_repo

    @property
    def channels(self) -> list[BaseNotifier]:
        """Registered notification channels."""
        return self._channels

    @classmethod
    def from_database(cls, db: Database) -> NotificationDispatcher:
        """Create a default dispatcher with SQLite delivery tracking."""
        return cls(
            alert_repo=AlertRepository(db),
            strategy_alert_repo=StrategyAlertRepository(db),
        )

    async def dispatch(
        self,
        alert: AlertPayload | BuyAlertPayload | PortfolioAlertPayload,
        alert_id: int | None = None,
        only_configured: bool = True,
        check_cooldown: bool = True,
        cooldown_seconds: int = 1800,
        meaningful_increase_pct: float = 0.05,
        failed_retry_backoff_seconds: int = 60,
        max_delivery_retries: int = 3,
        now_dt: datetime | None = None,
    ) -> dict[str, NotificationResult]:
        """Dispatch alert across notification channels.

        Supports SELL (`AlertPayload`), BUY (`BuyAlertPayload`), and PORTFOLIO (`PortfolioAlertPayload`)
        payloads with channel isolation, delivery-aware per-channel cooldown, cumulative retry limits,
        and SQLite audit logging.
        """
        results: dict[str, NotificationResult] = {}
        target_alert_id = alert_id or alert.alert_id
        is_buy = isinstance(alert, BuyAlertPayload)
        is_portfolio = isinstance(alert, PortfolioAlertPayload)
        is_strategy = is_buy or is_portfolio
        persisted_created_at = now_dt.isoformat() if now_dt is not None else None

        for ch in self._channels:
            ch_name = ch.channel_name

            # 1. Configuration and enabled gate:
            if only_configured and (not ch.is_configured or not getattr(ch, "is_enabled", True)):
                reason = (
                    f"Channel '{ch_name}' is disabled."
                    if not getattr(ch, "is_enabled", True)
                    else f"Channel '{ch_name}' is not configured."
                )
                results[ch_name] = NotificationResult(
                    channel=ch_name,
                    status=DeliveryStatus.SKIPPED,
                    attempts=0,
                    last_error=reason,
                )
                self._persist_result(
                    target_alert_id,
                    results[ch_name],
                    is_strategy=is_strategy,
                    created_at=persisted_created_at,
                )
                continue

            # 2. Per-channel cooldown & retry backoff gate:
            if check_cooldown and alert.item_id is not None:
                if is_strategy and self._strategy_alert_repo is not None:
                    strat_name = "BUY" if is_buy else "PORTFOLIO"
                    a_type = "buy_listing_candidate" if is_buy else alert.alert_type
                    ref_key = alert.steam_listing_id if is_buy else str(alert.holding_id)
                    ref_price = alert.purchase_buyer_pays_minor if is_buy else alert.recommended_list_gross_minor
                    direction = "drop" if is_buy else "rise"

                    in_cd = self._strategy_alert_repo.is_channel_in_cooldown(
                        strategy=strat_name,
                        item_id=alert.item_id,
                        currency_id=alert.currency_id,
                        alert_type=a_type,
                        reference_key=ref_key,
                        channel=ch_name,
                        cooldown_seconds=cooldown_seconds,
                        new_price_minor=ref_price,
                        price_direction=direction,
                        meaningful_change_pct=meaningful_increase_pct,
                        now_dt=now_dt,
                    )
                    if in_cd:
                        results[ch_name] = NotificationResult(
                            channel=ch_name,
                            status=DeliveryStatus.SKIPPED,
                            attempts=0,
                            last_error=f"Channel '{ch_name}' in cooldown from previous {strat_name} delivery.",
                        )
                        self._persist_result(
                            target_alert_id,
                            results[ch_name],
                            is_strategy=True,
                            created_at=persisted_created_at,
                        )
                        continue

                    in_backoff = self._strategy_alert_repo.is_channel_in_retry_backoff(
                        strategy=strat_name,
                        item_id=alert.item_id,
                        currency_id=alert.currency_id,
                        alert_type=a_type,
                        channel=ch_name,
                        alert_fingerprint=alert.alert_fingerprint or "",
                        backoff_seconds=failed_retry_backoff_seconds,
                        max_retries=max_delivery_retries,
                        now_dt=now_dt,
                    )
                    if in_backoff:
                        results[ch_name] = NotificationResult(
                            channel=ch_name,
                            status=DeliveryStatus.SKIPPED,
                            attempts=0,
                            last_error=f"Channel '{ch_name}' in {strat_name} retry backoff after previous failed delivery.",
                        )
                        self._persist_result(
                            target_alert_id,
                            results[ch_name],
                            is_strategy=True,
                            created_at=persisted_created_at,
                        )
                        continue

                elif not is_strategy and self._alert_repo is not None:
                    in_cd = self._alert_repo.is_channel_in_cooldown(
                        item_id=alert.item_id,
                        currency_id=getattr(alert, "currency_id", 1),
                        alert_type="sell_target_bid",
                        channel=ch_name,
                        cooldown_seconds=cooldown_seconds,
                        new_bid_minor=alert.highest_buy_order_minor,
                        meaningful_increase_pct=meaningful_increase_pct,
                        now_dt=now_dt,
                    )
                    if in_cd:
                        results[ch_name] = NotificationResult(
                            channel=ch_name,
                            status=DeliveryStatus.SKIPPED,
                            attempts=0,
                            last_error=f"Channel '{ch_name}' in cooldown from previous successful delivery.",
                        )
                        self._persist_result(
                            target_alert_id,
                            results[ch_name],
                            is_strategy=False,
                            created_at=persisted_created_at,
                        )
                        continue

                    in_backoff = self._alert_repo.is_channel_in_retry_backoff(
                        item_id=alert.item_id,
                        currency_id=getattr(alert, "currency_id", 1),
                        alert_type="sell_target_bid",
                        channel=ch_name,
                        alert_fingerprint=getattr(alert, "alert_fingerprint", None),
                        backoff_seconds=failed_retry_backoff_seconds,
                        max_retries=max_delivery_retries,
                        now_dt=now_dt,
                    )
                    if in_backoff:
                        results[ch_name] = NotificationResult(
                            channel=ch_name,
                            status=DeliveryStatus.SKIPPED,
                            attempts=0,
                            last_error=f"Channel '{ch_name}' in retry backoff after previous failed delivery.",
                        )
                        self._persist_result(
                            target_alert_id,
                            results[ch_name],
                            is_strategy=False,
                            created_at=persisted_created_at,
                        )
                        continue

            # 4. Dispatch alert to channel:
            try:
                res = await ch.send(alert)
                results[ch_name] = res
            except Exception as exc:
                sanitized_err = redact_secrets(f"Dispatcher error: {type(exc).__name__}: {exc}")
                logger.error("Unhandled error in channel %s: %s", ch_name, sanitized_err)
                results[ch_name] = NotificationResult(
                    channel=ch_name,
                    status=DeliveryStatus.FAILED,
                    attempts=1,
                    last_error=sanitized_err,
                )

            # Persist delivery attempt
            self._persist_result(
                target_alert_id,
                results[ch_name],
                is_strategy=is_strategy,
                created_at=persisted_created_at,
            )

        return results

    def _persist_result(
        self,
        target_alert_id: int | None,
        r: NotificationResult,
        *,
        is_strategy: bool = False,
        created_at: str | None = None,
    ) -> None:
        """Persist delivery outcome or skipped status to SQLite database."""
        if target_alert_id is None:
            return
        try:
            if is_strategy and self._strategy_alert_repo is not None:
                kwargs = {
                    "strategy_alert_id": target_alert_id,
                    "channel": r.channel,
                    "status": r.status.value,
                    "attempts": r.attempts,
                    "last_error": r.last_error,
                    "delivered_at": r.delivered_at,
                }
                if created_at is not None:
                    kwargs["created_at"] = created_at
                self._strategy_alert_repo.record_delivery(StrategyAlertDeliveryRecord(**kwargs))
            elif not is_strategy and self._alert_repo is not None:
                kwargs = {
                    "alert_id": target_alert_id,
                    "channel": r.channel,
                    "status": r.status.value,
                    "attempts": r.attempts,
                    "last_error": r.last_error,
                    "delivered_at": r.delivered_at,
                }
                if created_at is not None:
                    kwargs["created_at"] = created_at
                self._alert_repo.record_delivery(NotificationDeliveryRecord(**kwargs))
        except Exception as db_exc:
            sanitized_db_err = redact_secrets(str(db_exc))
            logger.error("Failed to persist delivery record for %s: %s", r.channel, sanitized_db_err)
