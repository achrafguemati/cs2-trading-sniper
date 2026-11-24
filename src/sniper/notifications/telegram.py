"""Telegram Bot API notification channel with HTML formatting and token redaction."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import html
import logging
import os
from typing import Any

import httpx

from sniper.notifications.base import BaseNotificationChannel, redact_secrets
from sniper.notifications.models import AlertPayload, DeliveryStatus, NotificationResult

logger = logging.getLogger(__name__)


class TelegramBotChannel(BaseNotificationChannel):
    """Dispatches formatted alerts to a Telegram chat using official Bot API sendMessage."""

    def __init__(
        self,
        bot_token: str | None = None,
        chat_id: str | None = None,
    ) -> None:
        self._bot_token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "")
        self._chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID", "")

    @property
    def channel_name(self) -> str:
        return "telegram"

    @property
    def is_configured(self) -> bool:
        return bool(
            self._bot_token
            and self._bot_token.strip()
            and self._chat_id
            and self._chat_id.strip()
        )

    def format_html_message(self, alert: AlertPayload) -> str:
        """Render a clean, safely escaped Telegram HTML message."""
        clean_name = html.escape(alert.market_hash_name)
        status_tag = html.escape(alert.status)
        bid_gross = html.escape(alert.formatted_bid_gross())
        seller_net = html.escape(alert.formatted_seller_net())
        lowest_ask = html.escape(alert.formatted_lowest_ask())
        baseline = html.escape(alert.formatted_baseline())
        fees = html.escape(alert.formatted_fees())
        multiple_str = f"{alert.bid_multiple:.2f}x" if alert.bid_multiple else "N/A"

        lines = [
            "🚨 <b>CS2 SELL SNIPER ALERT</b>",
            f"<b>Item:</b> <code>{clean_name}</code>",
            f"<b>Status:</b> <i>{status_tag}</i>",
            "",
            f"💰 <b>Buyer Pays (Gross):</b> {bid_gross}",
            f"💵 <b>Seller Receives (Net):</b> {seller_net}",
            f"📊 <b>Top Bid Qty:</b> {alert.top_level_buy_quantity} (Total Depth: {alert.cumulative_buy_quantity})",
            f"🏷️ <b>Lowest Ask:</b> {lowest_ask}",
            f"📈 <b>Historical Median:</b> {baseline}",
            f"⚡ <b>Bid Multiple:</b> {multiple_str}",
            f"🎯 <b>Opportunity Score:</b> {alert.opportunity_score:.1f} / 100",
            f"🛡️ <b>Confidence Score:</b> {alert.confidence_score:.1f} / 100",
            f"🧾 <b>Modeled Fees:</b> {fees}",
        ]

        if alert.warning_tags:
            lines.append("")
            lines.append("⚠️ <b>Warning Tags:</b>")
            for tag in alert.warning_tags:
                lines.append(f"• <code>{html.escape(tag)}</code>")

        if alert.steam_listing_url:
            lines.append("")
            lines.append(f'<a href="{html.escape(alert.steam_listing_url)}">🔗 Open on Steam Community Market</a>')

        lines.append("")
        lines.append("<i>CS2 Trading Sniper • Strictly Read-Only • Manual Verification Required</i>")

        return "\n".join(lines)

    async def send(self, alert: AlertPayload) -> NotificationResult:
        """Deliver alert to configured Telegram chat."""
        if not self.is_configured:
            return NotificationResult(
                channel=self.channel_name,
                status=DeliveryStatus.SKIPPED,
                attempts=0,
                last_error="Telegram bot token or chat ID not configured.",
            )

        api_url = f"https://api.telegram.org/bot{self._bot_token}/sendMessage"
        html_text = self.format_html_message(alert)

        payload: dict[str, Any] = {
            "chat_id": self._chat_id,
            "text": html_text,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        }

        attempts = 0
        max_attempts = 2

        while attempts < max_attempts:
            attempts += 1
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(api_url, json=payload)

                    if resp.status_code == 200:
                        logger.info("Telegram alert delivered successfully for '%s'", alert.market_hash_name)
                        return NotificationResult(
                            channel=self.channel_name,
                            status=DeliveryStatus.DELIVERED,
                            attempts=attempts,
                            delivered_at=datetime.now(timezone.utc).isoformat(),
                        )

                    if resp.status_code == 429 and attempts < max_attempts:
                        try:
                            data = resp.json()
                            retry_after = float(data.get("parameters", {}).get("retry_after", 1.0))
                        except Exception:
                            retry_after = 1.0

                        if retry_after <= 3.0:
                            logger.warning("Telegram rate limited (429); sleeping %.1fs and retrying", retry_after)
                            await asyncio.sleep(retry_after)
                            continue

                    error_msg = redact_secrets(
                        f"Telegram HTTP {resp.status_code}: {resp.text[:300]}",
                        custom_secrets=[self._bot_token, self._chat_id],
                    )
                    return NotificationResult(
                        channel=self.channel_name,
                        status=DeliveryStatus.FAILED,
                        attempts=attempts,
                        last_error=error_msg,
                    )

            except Exception as exc:
                error_msg = redact_secrets(
                    f"Telegram request failed: {type(exc).__name__}: {exc}",
                    custom_secrets=[self._bot_token, self._chat_id],
                )
                logger.error("%s", error_msg)
                return NotificationResult(
                    channel=self.channel_name,
                    status=DeliveryStatus.FAILED,
                    attempts=attempts,
                    last_error=error_msg,
                )

        return NotificationResult(
            channel=self.channel_name,
            status=DeliveryStatus.FAILED,
            attempts=attempts,
            last_error="Telegram delivery exhausted retry attempts.",
        )
