"""Telegram Bot API notification channel with HTML formatting and token redaction."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import html
import logging
import os
from typing import Any

import httpx

from sniper.notifications.base import BaseNotificationChannel, BaseNotifier, redact_secrets
from sniper.notifications.models import (
    AlertPayload,
    BuyAlertPayload,
    DeliveryStatus,
    NotificationResult,
)

logger = logging.getLogger(__name__)


class TelegramNotifier(BaseNotifier):
    """Dispatches formatted alerts to a Telegram chat using official Bot API sendMessage."""

    def __init__(
        self,
        bot_token: str | None = None,
        chat_id: str | None = None,
        enabled: bool | None = None,
    ) -> None:
        self._bot_token = bot_token if bot_token is not None else os.getenv("TELEGRAM_BOT_TOKEN", "")
        self._chat_id = chat_id if chat_id is not None else os.getenv("TELEGRAM_CHAT_ID", "")
        if enabled is not None:
            self._enabled = bool(enabled)
        else:
            env_val = os.getenv("TELEGRAM_ENABLED", "true").strip().lower()
            self._enabled = env_val not in ("0", "false", "no", "off")

    @property
    def channel_name(self) -> str:
        return "telegram"

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    @property
    def is_configured(self) -> bool:
        return bool(
            self._bot_token
            and self._bot_token.strip()
            and self._chat_id
            and self._chat_id.strip()
        )

    def format_buy_html_message(self, alert: BuyAlertPayload) -> str:
        """Render a clean, safely escaped Telegram HTML message for a BUY CANDIDATE."""
        clean_name = html.escape(alert.market_hash_name)
        status_tag = html.escape(alert.status)
        buy_price = html.escape(alert.formatted_buy_price())
        ref_price = html.escape(alert.formatted_reference())
        cons_profit = html.escape(alert.formatted_conservative_profit())
        exit_gross = html.escape(alert.formatted_conservative_exit_gross())
        exit_net = html.escape(alert.formatted_conservative_exit_net())
        listing_id = html.escape(alert.steam_listing_id)

        lines = [
            "🟢 <b>CS2 BUY SNIPER CANDIDATE</b>",
            f"<b>Item:</b> <code>{clean_name}</code>",
            f"<b>Status:</b> <i>{status_tag}</i> • Listing: <code>{listing_id}</code>",
            "",
            f"💳 <b>Buyer Pays (Acq):</b> {buy_price}",
            f"📊 <b>Reference Baseline:</b> {ref_price} (-{alert.discount_pct:.1f}%)",
            f"🛡️ <b>7d Conservative Profit:</b> {cons_profit} ({alert.conservative_roi_pct:.1f}% ROI) [{html.escape(alert.currency_code)} Wallet]",
            f"📉 <b>Conservative Exit:</b> {exit_gross} → {exit_net} net",
            f"🎯 <b>Opportunity:</b> {alert.opportunity_score:.1f}/100 • <b>Confidence:</b> {alert.confidence_score:.1f}/100",
        ]

        if alert.float_value is not None or alert.paint_seed is not None:
            parts = []
            if alert.float_value is not None:
                parts.append(f"Float <code>{alert.float_value:.6f}</code>")
            if alert.paint_seed is not None:
                parts.append(f"Seed <code>{alert.paint_seed}</code>")
            lines.append("🔬 " + " • ".join(parts))

        if alert.stickers:
            escaped_stickers = ", ".join(html.escape(s) for s in alert.stickers)
            lines.append(f"🏷️ <b>Stickers:</b> {escaped_stickers}")

        lines.append("⏳ <i>7-day SCM re-market restriction applies. Steam Wallet ≠ cash.</i>")

        if alert.warning_tags:
            lines.append("")
            lines.append("⚠️ <b>Warnings:</b>")
            for tag in alert.warning_tags:
                lines.append(f"• <code>{html.escape(tag)}</code>")

        if alert.steam_listing_url:
            lines.append("")
            lines.append(f'<a href="{html.escape(alert.steam_listing_url)}">🔗 Open on Steam Community Market</a>')

        lines.append("")
        lines.append("<i>Open on Steam, manually verify sticker/float, and confirm purchase.</i>")
        return "\n".join(lines)

    def format_html_message(self, alert: AlertPayload | BuyAlertPayload) -> str:
        """Render a clean, safely escaped Telegram HTML message."""
        if isinstance(alert, BuyAlertPayload):
            return self.format_buy_html_message(alert)
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
        if not self.is_enabled:
            return NotificationResult(
                channel=self.channel_name,
                status=DeliveryStatus.SKIPPED,
                attempts=0,
                last_error="Telegram channel is disabled.",
            )

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


# Backwards compatibility alias
TelegramBotChannel = TelegramNotifier
