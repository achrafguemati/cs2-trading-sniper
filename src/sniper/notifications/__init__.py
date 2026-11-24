"""Multi-channel notification infrastructure for CS2 Trading Sniper."""

from sniper.notifications.base import BaseNotificationChannel, redact_secrets
from sniper.notifications.discord import DiscordWebhookChannel
from sniper.notifications.dispatcher import NotificationDispatcher
from sniper.notifications.email import EmailConfig, EmailSmtpChannel
from sniper.notifications.messenger import MessengerChannel
from sniper.notifications.models import (
    AlertPayload,
    ChannelType,
    DeliveryStatus,
    NotificationResult,
)
from sniper.notifications.telegram import TelegramBotChannel

__all__ = [
    "AlertPayload",
    "BaseNotificationChannel",
    "ChannelType",
    "DeliveryStatus",
    "DiscordWebhookChannel",
    "EmailConfig",
    "EmailSmtpChannel",
    "MessengerChannel",
    "NotificationDispatcher",
    "NotificationResult",
    "TelegramBotChannel",
    "redact_secrets",
]
