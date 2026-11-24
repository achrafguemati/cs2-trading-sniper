"""Discord Incoming Webhook notification channel with rich embeds and secret redaction."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
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
    PortfolioAlertPayload,
)

logger = logging.getLogger(__name__)


class DiscordNotifier(BaseNotifier):
    """Dispatches formatted rich embed alerts via Discord Incoming Webhooks."""

    def __init__(
        self,
        webhook_url: str | None = None,
        enabled: bool | None = None,
    ) -> None:
        self._webhook_url = webhook_url if webhook_url is not None else os.getenv("DISCORD_WEBHOOK_URL", "")
        if enabled is not None:
            self._enabled = bool(enabled)
        else:
            env_val = os.getenv("DISCORD_ENABLED", "true").strip().lower()
            self._enabled = env_val not in ("0", "false", "no", "off")

    @property
    def channel_name(self) -> str:
        return "discord"

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    @property
    def is_configured(self) -> bool:
        return bool(self._webhook_url and self._webhook_url.strip().startswith("https://"))

    def build_buy_embed(self, alert: BuyAlertPayload) -> dict[str, Any]:
        """Construct a validated Discord rich embed for a BUY CANDIDATE listing alert."""
        color = 0x10B981  # Emerald green
        if alert.warning_tags:
            color = 0xF59E0B  # Amber warning highlight

        title = f"🟢 BUY CANDIDATE: {alert.market_hash_name}"[:256]
        desc = (
            f"**Underpriced Individual Listing Detected (`BUY CANDIDATE`)**\n"
            f"Manual human verification required before buying on Steam.\n"
            f"Status: `{alert.status}` • Listing ID: `{alert.steam_listing_id}`"
        )[:4096]

        fields: list[dict[str, Any]] = [
            {
                "name": "💳 Acquisition Price (Buyer Pays)",
                "value": alert.formatted_buy_price(),
                "inline": True,
            },
            {
                "name": "📊 Reference Baseline",
                "value": f"{alert.formatted_reference()} (-{alert.discount_pct:.1f}%)",
                "inline": True,
            },
            {
                "name": "🛡️ 7-Day Conservative Wallet Profit",
                "value": f"{alert.formatted_conservative_profit()} ({alert.conservative_roi_pct:.1f}% ROI) [{alert.currency_code} Steam Wallet]",
                "inline": True,
            },
            {
                "name": "📉 Expected Exit (Gross → Net)",
                "value": f"{alert.formatted_conservative_exit_gross()} → {alert.formatted_conservative_exit_net()}",
                "inline": True,
            },
            {
                "name": "🎯 Opportunity Score",
                "value": f"{alert.opportunity_score:.1f} / 100",
                "inline": True,
            },
            {
                "name": "🔍 Confidence Score",
                "value": f"{alert.confidence_score:.1f} / 100",
                "inline": True,
            },
        ]

        if alert.float_value is not None or alert.paint_seed is not None:
            meta_parts = []
            if alert.float_value is not None:
                meta_parts.append(f"Float: `{alert.float_value:.6f}`")
            if alert.paint_seed is not None:
                meta_parts.append(f"Pattern Seed: `{alert.paint_seed}`")
            fields.append({
                "name": "🔬 Wear & Pattern",
                "value": " • ".join(meta_parts),
                "inline": True,
            })

        if alert.stickers:
            fields.append({
                "name": "🏷️ Applied Stickers",
                "value": ", ".join(alert.stickers)[:1024],
                "inline": False,
            })

        fields.append({
            "name": "⏳ Holding Notice",
            "value": "CS2 7-day re-market & re-trade lock applies after SCM purchase. Steam Wallet ≠ withdrawable cash.",
            "inline": False,
        })

        if alert.warning_tags:
            fields.append({
                "name": "⚠️ Risk & Anomaly Warnings",
                "value": "\n".join(f"• `{t}`" for t in alert.warning_tags)[:1024],
                "inline": False,
            })

        embed: dict[str, Any] = {
            "title": title,
            "description": desc,
            "color": color,
            "fields": fields[:25],
            "footer": {
                "text": "CS2 Trading Sniper • Open on Steam & confirm manually • Read-Only",
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if alert.steam_listing_url:
            embed["url"] = alert.steam_listing_url
        return embed

    def build_portfolio_embed(self, alert: PortfolioAlertPayload) -> dict[str, Any]:
        """Construct a Discord rich embed for RESALE READY or UNDER WATER portfolio events."""
        is_underwater = alert.alert_type == "portfolio_under_water"
        color = 0xE74C3C if is_underwater else 0x10B981
        prefix = "🔴 UNDER WATER" if is_underwater else "🟢 RESALE READY"
        title = f"{prefix}: {alert.market_hash_name}"[:256]
        desc = (
            f"Holding `#{alert.holding_id}` held for `{alert.days_held}` day(s). "
            f"Status: `{alert.status}`. Steam Wallet proceeds are non-withdrawable."
        )[:4096]
        fields = [
            {"name": "💳 Cost Basis", "value": alert.formatted_cost(), "inline": True},
            {"name": "⚖️ Break-Even Gross", "value": alert.formatted_breakeven(), "inline": True},
            {"name": "🏷️ Current Floor Gross", "value": alert.formatted_recommended_list(), "inline": True},
            {"name": "💵 Modeled Net Proceeds", "value": alert.formatted_expected_net(), "inline": True},
            {
                "name": "📈 Expected Wallet P&L",
                "value": f"{alert.formatted_expected_profit()} ({alert.expected_roi_pct:.1f}% ROI) [{alert.currency_code}]",
                "inline": True,
            },
        ]
        embed: dict[str, Any] = {
            "title": title,
            "description": desc,
            "color": color,
            "fields": fields,
            "footer": {
                "text": "CS2 Trading Sniper • Manual Steam listing required • Read-Only",
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if alert.steam_listing_url:
            embed["url"] = alert.steam_listing_url
        return embed

    def build_embed(self, alert: AlertPayload | BuyAlertPayload | PortfolioAlertPayload) -> dict[str, Any]:
        """Construct a validated Discord rich embed conforming to API field limits."""
        if isinstance(alert, BuyAlertPayload):
            return self.build_buy_embed(alert)
        if isinstance(alert, PortfolioAlertPayload):
            return self.build_portfolio_embed(alert)
        color = 0x2ECC71  # Green
        if alert.warning_tags:
            color = 0xE67E22  # Orange/Warning

        title = f"🚨 SELL SNIPER: {alert.market_hash_name}"[:256]
        desc = (
            f"**High Buy Order Detected!**\n"
            f"Modeled net seller proceeds exceed baseline thresholds. "
            f"Status: `{alert.status}`"
        )[:4096]

        fields: list[dict[str, Any]] = [
            {
                "name": "💰 Buyer Pays (Gross Bid)",
                "value": alert.formatted_bid_gross(),
                "inline": True,
            },
            {
                "name": "💵 Seller Receives (Net)",
                "value": alert.formatted_seller_net(),
                "inline": True,
            },
            {
                "name": "📊 Top Bid Quantity",
                "value": f"{alert.top_level_buy_quantity} (Total Depth: {alert.cumulative_buy_quantity})",
                "inline": True,
            },
            {
                "name": "🏷️ Lowest Listing (Ask)",
                "value": alert.formatted_lowest_ask(),
                "inline": True,
            },
            {
                "name": "📈 Historical Median",
                "value": alert.formatted_baseline(),
                "inline": True,
            },
            {
                "name": "⚡ Bid Multiple",
                "value": f"{alert.bid_multiple:.2f}x" if alert.bid_multiple else "N/A",
                "inline": True,
            },
            {
                "name": "🎯 Opportunity Score",
                "value": f"{alert.opportunity_score:.1f} / 100",
                "inline": True,
            },
            {
                "name": "🛡️ Confidence Score",
                "value": f"{alert.confidence_score:.1f} / 100",
                "inline": True,
            },
            {
                "name": "🧾 Modeled Fees (Valve Dual Floor)",
                "value": alert.formatted_fees(),
                "inline": False,
            },
        ]

        if alert.warning_tags:
            tag_str = "\n".join(f"• `{t}`" for t in alert.warning_tags)[:1024]
            fields.append({
                "name": "⚠️ Warning / Anomaly Tags",
                "value": tag_str,
                "inline": False,
            })

        embed: dict[str, Any] = {
            "title": title,
            "description": desc,
            "color": color,
            "fields": fields[:25],
            "footer": {
                "text": "CS2 Trading Sniper • Strictly Read-Only • Manual Human Verification Required",
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        if alert.steam_listing_url:
            embed["url"] = alert.steam_listing_url

        return embed

    async def send(self, alert: AlertPayload) -> NotificationResult:
        """Deliver alert to configured Discord Webhook."""
        if not self.is_enabled:
            return NotificationResult(
                channel=self.channel_name,
                status=DeliveryStatus.SKIPPED,
                attempts=0,
                last_error="Discord channel is disabled.",
            )

        if not self.is_configured:
            return NotificationResult(
                channel=self.channel_name,
                status=DeliveryStatus.SKIPPED,
                attempts=0,
                last_error="Discord webhook URL not configured.",
            )

        payload = {
            "embeds": [self.build_embed(alert)],
        }

        attempts = 0
        max_attempts = 2

        while attempts < max_attempts:
            attempts += 1
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(self._webhook_url, json=payload)

                    if resp.status_code in (200, 204):
                        logger.info("Discord alert delivered successfully for '%s'", alert.market_hash_name)
                        return NotificationResult(
                            channel=self.channel_name,
                            status=DeliveryStatus.DELIVERED,
                            attempts=attempts,
                            delivered_at=datetime.now(timezone.utc).isoformat(),
                        )

                    if resp.status_code == 429 and attempts < max_attempts:
                        try:
                            data = resp.json()
                            retry_after = float(data.get("retry_after", 1.0))
                        except Exception:
                            retry_after = 1.0

                        if retry_after <= 3.0:
                            logger.warning("Discord rate limited (429); sleeping %.1fs and retrying", retry_after)
                            await asyncio.sleep(retry_after)
                            continue

                    error_msg = redact_secrets(
                        f"Discord HTTP {resp.status_code}: {resp.text[:300]}",
                        custom_secrets=[self._webhook_url],
                    )
                    return NotificationResult(
                        channel=self.channel_name,
                        status=DeliveryStatus.FAILED,
                        attempts=attempts,
                        last_error=error_msg,
                    )

            except Exception as exc:
                error_msg = redact_secrets(
                    f"Discord request failed: {type(exc).__name__}: {exc}",
                    custom_secrets=[self._webhook_url],
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
            last_error="Discord delivery exhausted retry attempts.",
        )


# Backwards compatibility alias
DiscordWebhookChannel = DiscordNotifier
