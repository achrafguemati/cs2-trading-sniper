"""Base interface and secret redaction utilities for notification transports."""

from __future__ import annotations

from abc import ABC, abstractmethod
import logging
import re
from typing import Sequence

from sniper.notifications.models import AlertPayload, DeliveryStatus, NotificationResult

logger = logging.getLogger(__name__)

# Secret redaction regular expressions
_DISCORD_WEBHOOK_PATTERN = re.compile(
    r"https://(?:ptb\.|canary\.)?discord(?:app)?\.com/api/webhooks/\d+/[A-Za-z0-9_-]+",
    re.IGNORECASE,
)
_TELEGRAM_TOKEN_PATTERN = re.compile(
    r"(?:bot)?(\d{8,12}:[A-Za-z0-9_-]{30,45})",
)
_URL_CREDENTIALS_PATTERN = re.compile(
    r"://([^:/@\s]+):([^@/\s]+)@",
)


def redact_secrets(text: str, custom_secrets: Sequence[str] | None = None) -> str:
    """Scrub sensitive credentials, bot tokens, webhooks, and passwords from logs and error messages.

    Ensures zero credential leakage to database records, console logs, or exception traces.
    """
    if not text:
        return text

    redacted = str(text)

    # 1. Custom explicit secrets (e.g. SMTP passwords or token strings)
    if custom_secrets:
        for secret in custom_secrets:
            if secret and len(secret.strip()) > 3:
                redacted = redacted.replace(secret, "***[REDACTED]***")

    # 2. Discord Webhook URLs
    redacted = _DISCORD_WEBHOOK_PATTERN.sub(
        "https://discord.com/api/webhooks/[REDACTED_WEBHOOK]",
        redacted,
    )

    # 3. Telegram Bot Tokens
    def _sub_tg(match: re.Match[str]) -> str:
        orig = match.group(0)
        prefix = "bot" if orig.startswith("bot") else ""
        return f"{prefix}***[REDACTED_TELEGRAM_TOKEN]***"

    redacted = _TELEGRAM_TOKEN_PATTERN.sub(_sub_tg, redacted)

    # 4. Basic Auth credentials in URLs (e.g. https://user:pass@host)
    redacted = _URL_CREDENTIALS_PATTERN.sub(r"://\1:***@", redacted)

    return redacted


class BaseNotificationChannel(ABC):
    """Abstract base class for notification channels."""

    @property
    @abstractmethod
    def channel_name(self) -> str:
        """Channel identifier string (e.g. 'discord', 'telegram', 'email', 'messenger')."""
        ...

    @property
    @abstractmethod
    def is_configured(self) -> bool:
        """Return True if this channel has valid configuration credentials."""
        ...

    @abstractmethod
    async def send(self, alert: AlertPayload) -> NotificationResult:
        """Format and dispatch an alert to this notification channel.

        Must never raise unhandled network exceptions; return NotificationResult with status and error.
        """
        ...
