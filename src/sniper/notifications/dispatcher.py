"""Notification dispatcher coordinating Discord and Telegram deliveries with isolation and SQLite persistence."""

from __future__ import annotations

import logging
from typing import Sequence

from sniper.notifications.base import BaseNotificationChannel, BaseNotifier, redact_secrets
from sniper.notifications.discord import DiscordNotifier
from sniper.notifications.models import AlertPayload, DeliveryStatus, NotificationResult
from sniper.notifications.telegram import TelegramNotifier
from sniper.storage.database import Database
from sniper.storage.models import NotificationDeliveryRecord
from sniper.storage.repositories import AlertRepository

logger = logging.getLogger(__name__)


class NotificationDispatcher:
    """Dispatches alerts across Discord and Telegram with failure isolation, delivery cooldown, and audit logging."""

    def __init__(
        self,
        channels: Sequence[BaseNotifier] | None = None,
        alert_repo: AlertRepository | None = None,
    ) -> None:
        self._channels = list(channels) if channels is not None else [
            DiscordNotifier(),
            TelegramNotifier(),
        ]
        self._alert_repo = alert_repo

    @property
    def channels(self) -> list[BaseNotifier]:
        """Registered notification channels."""
        return self._channels

    @classmethod
    def from_database(cls, db: Database) -> NotificationDispatcher:
        """Create a default dispatcher with SQLite delivery tracking."""
        repo = AlertRepository(db)
        return cls(alert_repo=repo)

    async def dispatch(
        self,
        alert: AlertPayload,
        alert_id: int | None = None,
        only_configured: bool = True,
        check_cooldown: bool = True,
        cooldown_seconds: int = 1800,
        meaningful_increase_pct: float = 0.05,
        failed_retry_backoff_seconds: int = 60,
        max_delivery_retries: int = 3,
    ) -> dict[str, NotificationResult]:
        """Dispatch alert across notification channels.

        Failure in one channel never prevents or blocks delivery to other channels.
        Checks per-channel cooldown and retry backoff against prior delivery records.
        Records delivery attempts, skips, and outcomes to SQLite database when alert_id is provided.
        """
        results: dict[str, NotificationResult] = {}
        target_alert_id = alert_id or alert.alert_id

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
                self._persist_result(target_alert_id, results[ch_name])
                continue

            # 2. Per-channel successful delivery cooldown gate:
            if (
                check_cooldown
                and self._alert_repo is not None
                and alert.item_id is not None
            ):
                in_cd = self._alert_repo.is_channel_in_cooldown(
                    item_id=alert.item_id,
                    currency_id=getattr(alert, "currency_id", 1),
                    alert_type="sell_target_bid",
                    channel=ch_name,
                    cooldown_seconds=cooldown_seconds,
                    new_bid_minor=alert.highest_buy_order_minor,
                    meaningful_increase_pct=meaningful_increase_pct,
                )
                if in_cd:
                    results[ch_name] = NotificationResult(
                        channel=ch_name,
                        status=DeliveryStatus.SKIPPED,
                        attempts=0,
                        last_error=f"Channel '{ch_name}' in cooldown from previous successful delivery.",
                    )
                    self._persist_result(target_alert_id, results[ch_name])
                    continue

                # 3. Per-channel failed delivery retry backoff gate:
                in_backoff = self._alert_repo.is_channel_in_retry_backoff(
                    item_id=alert.item_id,
                    currency_id=getattr(alert, "currency_id", 1),
                    alert_type="sell_target_bid",
                    channel=ch_name,
                    alert_fingerprint=getattr(alert, "alert_fingerprint", None),
                    backoff_seconds=failed_retry_backoff_seconds,
                    max_retries=max_delivery_retries,
                )
                if in_backoff:
                    results[ch_name] = NotificationResult(
                        channel=ch_name,
                        status=DeliveryStatus.SKIPPED,
                        attempts=0,
                        last_error=f"Channel '{ch_name}' in retry backoff after previous failed delivery.",
                    )
                    self._persist_result(target_alert_id, results[ch_name])
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
            self._persist_result(target_alert_id, results[ch_name])

        return results

    def _persist_result(self, target_alert_id: int | None, r: NotificationResult) -> None:
        """Persist delivery outcome or skipped status to SQLite database."""
        if target_alert_id is None or self._alert_repo is None:
            return
        try:
            self._alert_repo.record_delivery(
                NotificationDeliveryRecord(
                    alert_id=target_alert_id,
                    channel=r.channel,
                    status=r.status.value,
                    attempts=r.attempts,
                    last_error=r.last_error,
                    delivered_at=r.delivered_at,
                )
            )
        except Exception as db_exc:
            sanitized_db_err = redact_secrets(str(db_exc))
            logger.error("Failed to persist delivery record for %s: %s", r.channel, sanitized_db_err)
