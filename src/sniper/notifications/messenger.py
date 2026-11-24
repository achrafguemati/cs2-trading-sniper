"""Meta Facebook Messenger channel analysis and unavailable status implementation."""

from __future__ import annotations

import logging

from sniper.notifications.base import BaseNotificationChannel
from sniper.notifications.models import AlertPayload, DeliveryStatus, NotificationResult

logger = logging.getLogger(__name__)

MESSENGER_UNAVAILABLE_REASON = (
    "UNAVAILABLE — requires official Meta messaging setup and an active permitted messaging window"
)


class MessengerChannel(BaseNotificationChannel):
    """Messenger channel stub enforcing Meta Platform Policy compliance.

    Technical Feasibility & Policy Analysis:
    -----------------------------------------
    1. 24-Hour Messaging Window Policy:
       Meta's Messenger Platform Terms strictly enforce a 24-hour messaging window.
       A Page may only message a user within 24 hours of that user's last interaction.
       Unsolicited outbound market alerts sent to personal accounts outside this window
       are strictly prohibited and rejected by the Send API.

    2. Message Tag Restrictions:
       Meta restricts messages outside the 24-hour window to four approved tags:
       (CONFIRMED_EVENT_UPDATE, POST_PURCHASE_UPDATE, ACCOUNT_UPDATE, HUMAN_AGENT).
       Trading alerts and price opportunity notifications do not qualify under any tag,
       and using them leads to permanent Page and App revocation.

    3. Public Webhook & Infrastructure Prerequisites:
       Receiving user opt-ins requires a verified Meta App, App Review approval, Page Access Token,
       and a public HTTPS webhook endpoint with SSL certificates and SHA256 signature verification.
       This directly conflicts with the project's zero-cost, local-first architecture (no VPS / paid domains).

    4. Deprecation of One-Time Notifications (OTN):
       Meta has restricted or deprecated free Recurring Notifications tokens for unattended bots.

    Consequently, unattended local-first alerts to personal Messenger accounts cannot be
    supported under official Meta Platform terms.
    """

    @property
    def channel_name(self) -> str:
        return "messenger"

    @property
    def is_configured(self) -> bool:
        # Intentionally returns False to prevent dispatch while documenting exact reason
        return False

    async def send(self, alert: AlertPayload) -> NotificationResult:
        """Return structured UNAVAILABLE result explaining platform policy constraints."""
        logger.warning("Messenger channel requested: %s", MESSENGER_UNAVAILABLE_REASON)
        return NotificationResult(
            channel=self.channel_name,
            status=DeliveryStatus.UNAVAILABLE,
            attempts=0,
            last_error=MESSENGER_UNAVAILABLE_REASON,
            metadata={
                "policy_constraint": "Meta 24-hour messaging window and Message Tag restrictions",
                "docs_reference": "docs/notifications-setup.md",
            },
        )
