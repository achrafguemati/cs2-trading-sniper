"""Multi-channel notification infrastructure for CS2 Trading Sniper (Discord + Telegram only)."""

from sniper.notifications.base import BaseNotificationChannel, BaseNotifier, redact_secrets
from sniper.notifications.discord import DiscordNotifier, DiscordWebhookChannel
from sniper.notifications.dispatcher import NotificationDispatcher
from sniper.notifications.models import (
    AlertPayload,
    BuyAlertPayload,
    ChannelType,
    DeliveryStatus,
    NotificationResult,
    PortfolioAlertPayload,
)
from sniper.notifications.telegram import TelegramBotChannel, TelegramNotifier

__all__ = [
    "AlertPayload",
    "BaseNotificationChannel",
    "BaseNotifier",
    "BuyAlertPayload",
    "ChannelType",
    "DeliveryStatus",
    "DiscordNotifier",
    "DiscordWebhookChannel",
    "NotificationDispatcher",
    "NotificationResult",
    "PortfolioAlertPayload",
    "TelegramBotChannel",
    "TelegramNotifier",
    "redact_secrets",
]
