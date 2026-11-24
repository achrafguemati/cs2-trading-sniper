"""Free standard-library SMTP email notification channel with plain-text and HTML MIME."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import html
import logging
import os
import smtplib
import ssl

from sniper.notifications.base import BaseNotificationChannel, redact_secrets
from sniper.notifications.models import AlertPayload, DeliveryStatus, NotificationResult

logger = logging.getLogger(__name__)


@dataclass
class EmailConfig:
    """Configuration for SMTP email notifications."""
    host: str
    port: int = 587
    username: str = ""
    password: str = ""
    from_email: str = ""
    to_email: str = ""
    use_tls: bool = True
    use_ssl: bool = False
    timeout_seconds: float = 10.0

    @classmethod
    def from_env(cls) -> EmailConfig | None:
        """Load SMTP configuration from environment variables."""
        host = os.getenv("SMTP_HOST", "").strip()
        to_email = os.getenv("SMTP_TO_EMAIL", "").strip()
        if not host or not to_email:
            return None

        port_str = os.getenv("SMTP_PORT", "587").strip()
        try:
            port = int(port_str)
        except ValueError:
            port = 587

        username = os.getenv("SMTP_USERNAME", "").strip()
        password = os.getenv("SMTP_PASSWORD", "").strip()
        from_email = os.getenv("SMTP_FROM_EMAIL", "").strip() or username or to_email

        use_ssl_str = os.getenv("SMTP_USE_SSL", "false").strip().lower()
        use_ssl = use_ssl_str in ("1", "true", "yes")

        use_tls_str = os.getenv("SMTP_USE_TLS", "true").strip().lower()
        use_tls = use_tls_str in ("1", "true", "yes") and not use_ssl

        return cls(
            host=host,
            port=port,
            username=username,
            password=password,
            from_email=from_email,
            to_email=to_email,
            use_tls=use_tls,
            use_ssl=use_ssl,
        )


class EmailSmtpChannel(BaseNotificationChannel):
    """Dispatches dual-format plain text and HTML alerts via free standard-library SMTP."""

    def __init__(self, config: EmailConfig | None = None) -> None:
        self._config = config or EmailConfig.from_env()

    @property
    def channel_name(self) -> str:
        return "email"

    @property
    def is_configured(self) -> bool:
        return (
            self._config is not None
            and bool(self._config.host)
            and bool(self._config.to_email)
        )

    def render_plain_text(self, alert: AlertPayload) -> str:
        """Render readable plain text email content."""
        lines = [
            f"CS2 SELL SNIPER ALERT: {alert.market_hash_name}",
            "=" * 50,
            f"Status: {alert.status}",
            "",
            f"Buyer Pays (Gross Bid): {alert.formatted_bid_gross()}",
            f"Seller Receives (Net):  {alert.formatted_seller_net()}",
            f"Top Bid Quantity:       {alert.top_level_buy_quantity} (Total Depth: {alert.cumulative_buy_quantity})",
            f"Lowest Listing (Ask):   {alert.formatted_lowest_ask()}",
            f"Historical Median:      {alert.formatted_baseline()}",
            f"Bid Multiple:           {f'{alert.bid_multiple:.2f}x' if alert.bid_multiple else 'N/A'}",
            f"Opportunity Score:      {alert.opportunity_score:.1f} / 100",
            f"Confidence Score:       {alert.confidence_score:.1f} / 100",
            f"Modeled Fees:           {alert.formatted_fees()}",
        ]

        if alert.warning_tags:
            lines.append("")
            lines.append("Warning / Anomaly Tags:")
            for tag in alert.warning_tags:
                lines.append(f"  * {tag}")

        if alert.steam_listing_url:
            lines.append("")
            lines.append(f"Steam Listing URL: {alert.steam_listing_url}")

        lines.append("")
        lines.append("-" * 50)
        lines.append("CS2 Trading Sniper • Strictly Read-Only • Manual Human Verification Required")
        return "\n".join(lines)

    def render_html(self, alert: AlertPayload) -> str:
        """Render responsive HTML email content."""
        clean_name = html.escape(alert.market_hash_name)
        status_tag = html.escape(alert.status)
        gross_bid = html.escape(alert.formatted_bid_gross())
        seller_net = html.escape(alert.formatted_seller_net())
        lowest_ask = html.escape(alert.formatted_lowest_ask())
        baseline = html.escape(alert.formatted_baseline())
        fees = html.escape(alert.formatted_fees())
        multiple_str = f"{alert.bid_multiple:.2f}x" if alert.bid_multiple else "N/A"

        tag_rows = ""
        if alert.warning_tags:
            tag_badges = " ".join(
                f'<span style="background-color: #ffeaa7; color: #d63031; padding: 3px 8px; border-radius: 4px; font-weight: bold; margin-right: 5px;">{html.escape(t)}</span>'
                for t in alert.warning_tags
            )
            tag_rows = f"""
            <tr>
                <td style="padding: 10px; border-bottom: 1px solid #ddd; font-weight: bold; color: #d63031;">Warnings</td>
                <td style="padding: 10px; border-bottom: 1px solid #ddd;">{tag_badges}</td>
            </tr>
            """

        link_btn = ""
        if alert.steam_listing_url:
            link_btn = f"""
            <div style="margin-top: 20px; text-align: center;">
                <a href="{html.escape(alert.steam_listing_url)}" style="background-color: #0984e3; color: #ffffff; padding: 10px 20px; text-decoration: none; border-radius: 5px; font-weight: bold; display: inline-block;">View on Steam Community Market</a>
            </div>
            """

        return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f4f6f8; margin: 0; padding: 20px;">
    <div style="max-width: 600px; margin: 0 auto; background-color: #ffffff; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 8px rgba(0,0,0,0.1);">
        <div style="background-color: #2d3436; color: #ffffff; padding: 20px; text-align: center;">
            <h2 style="margin: 0; font-size: 20px;">🚨 CS2 SELL SNIPER ALERT</h2>
            <p style="margin: 5px 0 0 0; color: #dfe6e9;">{clean_name}</p>
        </div>
        <div style="padding: 20px;">
            <table style="width: 100%; border-collapse: collapse; font-size: 14px;">
                <tr>
                    <td style="padding: 10px; border-bottom: 1px solid #ddd; font-weight: bold;">Status</td>
                    <td style="padding: 10px; border-bottom: 1px solid #ddd;"><code>{status_tag}</code></td>
                </tr>
                <tr style="background-color: #f9f9f9;">
                    <td style="padding: 10px; border-bottom: 1px solid #ddd; font-weight: bold;">Buyer Pays (Gross Bid)</td>
                    <td style="padding: 10px; border-bottom: 1px solid #ddd; color: #27ae60; font-size: 16px; font-weight: bold;">{gross_bid}</td>
                </tr>
                <tr>
                    <td style="padding: 10px; border-bottom: 1px solid #ddd; font-weight: bold;">Seller Receives (Net)</td>
                    <td style="padding: 10px; border-bottom: 1px solid #ddd; color: #2980b9; font-size: 16px; font-weight: bold;">{seller_net}</td>
                </tr>
                <tr style="background-color: #f9f9f9;">
                    <td style="padding: 10px; border-bottom: 1px solid #ddd; font-weight: bold;">Top Bid Quantity</td>
                    <td style="padding: 10px; border-bottom: 1px solid #ddd;">{alert.top_level_buy_quantity} (Total Depth: {alert.cumulative_buy_quantity})</td>
                </tr>
                <tr>
                    <td style="padding: 10px; border-bottom: 1px solid #ddd; font-weight: bold;">Lowest Listing (Ask)</td>
                    <td style="padding: 10px; border-bottom: 1px solid #ddd;">{lowest_ask}</td>
                </tr>
                <tr style="background-color: #f9f9f9;">
                    <td style="padding: 10px; border-bottom: 1px solid #ddd; font-weight: bold;">Historical Median</td>
                    <td style="padding: 10px; border-bottom: 1px solid #ddd;">{baseline}</td>
                </tr>
                <tr>
                    <td style="padding: 10px; border-bottom: 1px solid #ddd; font-weight: bold;">Bid Multiple</td>
                    <td style="padding: 10px; border-bottom: 1px solid #ddd;"><b>{multiple_str}</b></td>
                </tr>
                <tr style="background-color: #f9f9f9;">
                    <td style="padding: 10px; border-bottom: 1px solid #ddd; font-weight: bold;">Opportunity / Confidence</td>
                    <td style="padding: 10px; border-bottom: 1px solid #ddd;">Score: {alert.opportunity_score:.1f}/100 &bull; Confidence: {alert.confidence_score:.1f}/100</td>
                </tr>
                <tr>
                    <td style="padding: 10px; border-bottom: 1px solid #ddd; font-weight: bold;">Modeled Fees</td>
                    <td style="padding: 10px; border-bottom: 1px solid #ddd; color: #7f8c8d;">{fees}</td>
                </tr>
                {tag_rows}
            </table>
            {link_btn}
        </div>
        <div style="background-color: #f8f9fa; color: #636e72; padding: 15px; text-align: center; font-size: 12px; border-top: 1px solid #eee;">
            CS2 Trading Sniper &bull; Strictly Read-Only &bull; Manual Human Verification Required
        </div>
    </div>
</body>
</html>"""

    def _send_sync(self, msg: MIMEMultipart) -> None:
        """Synchronous SMTP delivery helper executed in thread worker."""
        assert self._config is not None
        cfg = self._config

        if cfg.use_ssl:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(cfg.host, cfg.port, context=context, timeout=cfg.timeout_seconds) as server:
                if cfg.username and cfg.password:
                    server.login(cfg.username, cfg.password)
                server.send_message(msg)
        else:
            with smtplib.SMTP(cfg.host, cfg.port, timeout=cfg.timeout_seconds) as server:
                if cfg.use_tls:
                    context = ssl.create_default_context()
                    server.starttls(context=context)
                if cfg.username and cfg.password:
                    server.login(cfg.username, cfg.password)
                server.send_message(msg)

    async def send(self, alert: AlertPayload) -> NotificationResult:
        """Deliver alert via SMTP in an isolated thread worker."""
        if not self.is_configured or self._config is None:
            return NotificationResult(
                channel=self.channel_name,
                status=DeliveryStatus.SKIPPED,
                attempts=0,
                last_error="SMTP host or recipient email not configured.",
            )

        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"[CS2 SELL Sniper] Alert: {alert.market_hash_name}"
        msg["From"] = self._config.from_email
        msg["To"] = self._config.to_email

        plain_part = MIMEText(self.render_plain_text(alert), "plain", "utf-8")
        html_part = MIMEText(self.render_html(alert), "html", "utf-8")
        msg.attach(plain_part)
        msg.attach(html_part)

        try:
            await asyncio.to_thread(self._send_sync, msg)
            logger.info("Email alert delivered successfully for '%s'", alert.market_hash_name)
            return NotificationResult(
                channel=self.channel_name,
                status=DeliveryStatus.DELIVERED,
                attempts=1,
                delivered_at=datetime.now(timezone.utc).isoformat(),
            )
        except Exception as exc:
            error_msg = redact_secrets(
                f"SMTP delivery failed: {type(exc).__name__}: {exc}",
                custom_secrets=[self._config.password, self._config.username],
            )
            logger.error("%s", error_msg)
            return NotificationResult(
                channel=self.channel_name,
                status=DeliveryStatus.FAILED,
                attempts=1,
                last_error=error_msg,
            )
