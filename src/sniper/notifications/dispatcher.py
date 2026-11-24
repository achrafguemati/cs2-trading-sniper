"""Notification dispatcher coordinating Discord and Telegram deliveries with isolation and SQLite persistence."""

from __future__ import annotations

import logging
from typing import Sequence

from sniper.notifications.base import BaseNotificationChannel, BaseNotifier
from sniper.notifications.discord import DiscordNotifier
from sniper.notifications.models import AlertPayload, DeliveryStatus, NotificationResult
from sniper.notifications.telegram import TelegramNotifier
from sniper.storage.database import Database
from sniper.storage.models import NotificationDeliveryRecord
from sniper.storage.repositories import AlertRepository

logger = logging.getLogger(__name__)


class NotificationDispatcher:
    """Dispatches alerts across Discord and Telegram with failure isolation and audit logging."""

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
    ) -> dict[str, NotificationResult]:
        """Dispatch alert across notification channels.

        Failure in one channel never prevents or blocks delivery to other channels.
        Records delivery attempts and outcomes to SQLite database when alert_id is provided.
        """
        results: dict[str, NotificationResult] = {}
        target_alert_id = alert_id or alert.alert_id

        for ch in self._channels:
            ch_name = ch.channel_name

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
                continue

            try:
                res = await ch.send(alert)
                results[ch_name] = res
            except Exception as exc:
                logger.error("Unhandled error in channel %s: %s", ch_name, exc)
                results[ch_name] = NotificationResult(
                    channel=ch_name,
                    status=DeliveryStatus.FAILED,
                    attempts=1,
                    last_error=f"Dispatcher error: {type(exc).__name__}: {exc}",
                )

            # Persist delivery attempt to database if repository is configured
            if target_alert_id is not None and self._alert_repo is not None:
                r = results[ch_name]
                try:
                    self._alert_repo.record_delivery(
                        NotificationDeliveryRecord(
                            alert_id=target_alert_id,
                            channel=ch_name,
                            status=r.status.value,
                            attempts=r.attempts,
                            last_error=r.last_error,
                            delivered_at=r.delivered_at,
                        )
                    )
                except Exception as db_exc:
                    logger.error("Failed to persist delivery record for %s: %s", ch_name, db_exc)

        return results
