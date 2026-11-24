"""Deterministic unit tests for free multi-channel notification transports and secret redaction."""

from __future__ import annotations

import asyncio
import smtplib
from typing import Any
from unittest.mock import MagicMock, patch
import unittest

import httpx

_REAL_ASYNC_CLIENT = httpx.AsyncClient

from sniper.notifications import (
    AlertPayload,
    DeliveryStatus,
    DiscordWebhookChannel,
    EmailConfig,
    EmailSmtpChannel,
    MessengerChannel,
    NotificationDispatcher,
    NotificationResult,
    TelegramBotChannel,
    redact_secrets,
)
from sniper.storage.database import Database
from sniper.storage.models import AlertEventRecord
from sniper.storage.repositories import AlertRepository


class TestNotifications(unittest.IsolatedAsyncioTestCase):
    """Test suite for Discord, Telegram, Email, and Messenger notification transports."""

    def setUp(self) -> None:
        self.alert = AlertPayload(
            alert_id=42,
            item_id=1,
            market_hash_name="AK-47 | Redline (Field-Tested) <Special Edition> & 'Rare'",
            exterior="Field-Tested",
            currency_code="USD",
            currency_symbol="$",
            highest_buy_order_minor=2550,
            lowest_sell_order_minor=2800,
            top_level_buy_quantity=1,
            cumulative_buy_quantity=15,
            modeled_steam_fee_minor=127,
            modeled_publisher_fee_minor=255,
            modeled_seller_receives_minor=2168,
            historical_baseline_minor=2000,
            bid_multiple=1.275,
            opportunity_score=85.0,
            confidence_score=78.5,
            warning_tags=["Single-Item Top Bid"],
            status="Candidate",
            created_at="2026-05-20T12:00:00Z",
            steam_listing_url="https://steamcommunity.com/market/listings/730/AK-47%20%7C%20Redline%20%28Field-Tested%29",
        )

    def test_secret_redaction(self) -> None:
        """Verify centralized secret redaction scrubs webhooks, tokens, and passwords."""
        # 1. Discord Webhook
        discord_raw = "Error sending to https://discord.com/api/webhooks/1234567890/abcXYZ123_456-789"
        discord_redacted = redact_secrets(discord_raw)
        self.assertNotIn("1234567890/abcXYZ", discord_redacted)
        self.assertIn("[REDACTED_WEBHOOK]", discord_redacted)

        # 2. Telegram Bot Token with prefix
        tg_raw = "Failed call to https://api.telegram.org/bot123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ_12345678/sendMessage"
        tg_redacted = redact_secrets(tg_raw)
        self.assertNotIn("ABCdefGhIJKlmNoPQRsTUVwxyZ_12345678", tg_redacted)
        self.assertIn("bot***[REDACTED_TELEGRAM_TOKEN]***", tg_redacted)

        # 3. Bare Telegram Token
        bare_tg = "Token is 987654321:XYZabc1234567890_-abcdefghijklmnopqr"
        bare_redacted = redact_secrets(bare_tg)
        self.assertNotIn("XYZabc1234567890", bare_redacted)
        self.assertIn("***[REDACTED_TELEGRAM_TOKEN]***", bare_redacted)

        # 4. Basic Auth credentials in URL
        url_auth = "Connecting to https://user_bob:super_secret_password@proxy.example.com:8080"
        url_redacted = redact_secrets(url_auth)
        self.assertNotIn("super_secret_password", url_redacted)
        self.assertIn("user_bob:***@", url_redacted)

        # 5. Explicit custom secret
        custom_raw = "SMTP auth failed with password 'my_google_app_password_123'"
        custom_redacted = redact_secrets(custom_raw, custom_secrets=["my_google_app_password_123"])
        self.assertNotIn("my_google_app_password_123", custom_redacted)
        self.assertIn("***[REDACTED]***", custom_redacted)

    async def test_discord_webhook_success(self) -> None:
        """Verify Discord webhook builds embed and sends successfully."""
        captured_request: dict[str, Any] = {}

        def mock_handler(req: httpx.Request) -> httpx.Response:
            import json
            captured_request["body"] = json.loads(req.content.decode("utf-8"))
            return httpx.Response(204)

        channel = DiscordWebhookChannel(webhook_url="https://discord.com/api/webhooks/123/token")
        with patch("httpx.AsyncClient", side_effect=lambda **kw: _REAL_ASYNC_CLIENT(transport=httpx.MockTransport(mock_handler))):
            res = await channel.send(self.alert)

        self.assertEqual(res.status, DeliveryStatus.DELIVERED)
        self.assertEqual(res.attempts, 1)
        self.assertIsNotNone(res.delivered_at)

        # Check embed structure
        embed = captured_request["body"]["embeds"][0]
        self.assertIn("AK-47 | Redline", embed["title"])
        self.assertEqual(embed["color"], 0xE67E22)  # Warning color because warning_tags present
        field_names = [f["name"] for f in embed["fields"]]
        self.assertTrue(any("Gross" in n for n in field_names))
        self.assertTrue(any("Net" in n for n in field_names))
        self.assertTrue(any("Warning" in n for n in field_names))

    async def test_discord_webhook_rate_limit_retry(self) -> None:
        """Verify Discord webhook handles 429 and retries."""
        call_count = 0

        def mock_handler(req: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(429, json={"retry_after": 0.01})
            return httpx.Response(204)

        channel = DiscordWebhookChannel(webhook_url="https://discord.com/api/webhooks/123/token")
        with patch("httpx.AsyncClient", side_effect=lambda **kw: _REAL_ASYNC_CLIENT(transport=httpx.MockTransport(mock_handler))):
            res = await channel.send(self.alert)

        self.assertEqual(res.status, DeliveryStatus.DELIVERED)
        self.assertEqual(res.attempts, 2)

    async def test_discord_unconfigured_skips(self) -> None:
        """Verify unconfigured Discord channel skips cleanly."""
        channel = DiscordWebhookChannel(webhook_url="")
        self.assertFalse(channel.is_configured)
        res = await channel.send(self.alert)
        self.assertEqual(res.status, DeliveryStatus.SKIPPED)

    async def test_telegram_bot_success(self) -> None:
        """Verify Telegram bot formats HTML message and sends successfully."""
        captured_request: dict[str, Any] = {}

        def mock_handler(req: httpx.Request) -> httpx.Response:
            import json
            captured_request["body"] = json.loads(req.content.decode("utf-8"))
            return httpx.Response(200, json={"ok": True})

        channel = TelegramBotChannel(
            bot_token="123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ_12345678",
            chat_id="987654321",
        )
        self.assertTrue(channel.is_configured)

        with patch("httpx.AsyncClient", side_effect=lambda **kw: _REAL_ASYNC_CLIENT(transport=httpx.MockTransport(mock_handler))):
            res = await channel.send(self.alert)

        self.assertEqual(res.status, DeliveryStatus.DELIVERED)
        body = captured_request["body"]
        self.assertEqual(body["chat_id"], "987654321")
        self.assertEqual(body["parse_mode"], "HTML")

        # Verify HTML special character escaping
        msg_text = body["text"]
        self.assertIn("&lt;Special Edition&gt;", msg_text)
        self.assertIn("&amp; &#x27;Rare&#x27;", msg_text)
        self.assertIn("<b>Buyer Pays (Gross):</b> $25.50", msg_text)
        self.assertIn("<b>Seller Receives (Net):</b> $21.68", msg_text)

    async def test_telegram_rate_limit_retry(self) -> None:
        """Verify Telegram bot handles 429 retry-after."""
        call_count = 0

        def mock_handler(req: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(429, json={"ok": False, "parameters": {"retry_after": 0.01}})
            return httpx.Response(200, json={"ok": True})

        channel = TelegramBotChannel(
            bot_token="123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ_12345678",
            chat_id="987654321",
        )
        with patch("httpx.AsyncClient", side_effect=lambda **kw: _REAL_ASYNC_CLIENT(transport=httpx.MockTransport(mock_handler))):
            res = await channel.send(self.alert)

        self.assertEqual(res.status, DeliveryStatus.DELIVERED)
        self.assertEqual(res.attempts, 2)

    async def test_telegram_unconfigured_skips(self) -> None:
        """Verify unconfigured Telegram channel skips cleanly."""
        channel = TelegramBotChannel(bot_token="", chat_id="")
        self.assertFalse(channel.is_configured)
        res = await channel.send(self.alert)
        self.assertEqual(res.status, DeliveryStatus.SKIPPED)

    async def test_email_smtp_success(self) -> None:
        """Verify standard-library SMTP channel sends dual plain-text and HTML MIME."""
        cfg = EmailConfig(
            host="smtp.gmail.com",
            port=587,
            username="test_user@gmail.com",
            password="test_app_password",
            from_email="test_user@gmail.com",
            to_email="dest_user@gmail.com",
            use_tls=True,
            use_ssl=False,
        )
        channel = EmailSmtpChannel(config=cfg)
        self.assertTrue(channel.is_configured)

        mock_server = MagicMock()
        with patch("smtplib.SMTP", return_value=mock_server):
            res = await channel.send(self.alert)

        self.assertEqual(res.status, DeliveryStatus.DELIVERED)
        mock_server.__enter__.return_value.starttls.assert_called_once()
        mock_server.__enter__.return_value.login.assert_called_once_with("test_user@gmail.com", "test_app_password")
        mock_server.__enter__.return_value.send_message.assert_called_once()

    async def test_email_smtp_failure_redacts_password(self) -> None:
        """Verify SMTP failure scrubs password from error message."""
        cfg = EmailConfig(
            host="smtp.gmail.com",
            port=587,
            username="test_user@gmail.com",
            password="secret_app_password_999",
            from_email="test_user@gmail.com",
            to_email="dest_user@gmail.com",
        )
        channel = EmailSmtpChannel(config=cfg)

        def mock_smtp(*args, **kwargs):
            raise smtplib.SMTPAuthenticationError(535, b"Auth failed for secret_app_password_999")

        with patch("smtplib.SMTP", side_effect=mock_smtp):
            res = await channel.send(self.alert)

        self.assertEqual(res.status, DeliveryStatus.FAILED)
        self.assertNotIn("secret_app_password_999", res.last_error or "")
        self.assertIn("***[REDACTED]***", res.last_error or "")

    async def test_messenger_unavailable_status(self) -> None:
        """Verify Messenger channel explicitly reports UNAVAILABLE due to Meta policy."""
        channel = MessengerChannel()
        self.assertFalse(channel.is_configured)

        res = await channel.send(self.alert)
        self.assertEqual(res.status, DeliveryStatus.UNAVAILABLE)
        self.assertIn("UNAVAILABLE", res.last_error or "")
        self.assertIn("Meta messaging setup", res.last_error or "")

    async def test_dispatcher_channel_isolation_and_persistence(self) -> None:
        """Verify dispatcher delivers across channels with failure isolation and SQLite logging."""
        db = Database(":memory:")
        db.initialize()
        from sniper.storage.models import ItemRecord
        from sniper.storage.repositories import CurrencyRepository, ItemRepository
        CurrencyRepository(db).seed_default_currencies()
        item = ItemRepository(db).save(ItemRecord(
            market_hash_name="AK-47 | Redline (Field-Tested)",
            base_name="AK-47 | Redline",
            exterior="Field-Tested",
        ))
        repo = AlertRepository(db)

        # Record test alert event in DB
        alert_event = repo.record_alert(
            AlertEventRecord(
                item_id=item.item_id,
                currency_id=1,
                alert_type="sell_target_bid",
                alert_fingerprint="test_fp",
                highest_buy_order_minor=2550,
                lowest_sell_order_minor=2800,
                top_level_buy_quantity=1,
                cumulative_buy_quantity=15,
                modeled_steam_fee_minor=127,
                modeled_publisher_fee_minor=255,
                modeled_seller_receives_minor=2168,
                historical_baseline_minor=2000,
                bid_multiple=1.28,
                opportunity_score=85.0,
                confidence_score=78.5,
                warning_tags="[]",
                status="Candidate",
                created_at="2026-05-20T12:00:00Z",
            )
        )

        # Mock channels: channel A succeeds, channel B fails, channel C raises unhandled exception
        mock_a = MagicMock()
        mock_a.channel_name = "discord"
        mock_a.is_configured = True
        mock_a.send = unittest.mock.AsyncMock(return_value=NotificationResult(
            channel="discord", status=DeliveryStatus.DELIVERED, attempts=1
        ))

        mock_b = MagicMock()
        mock_b.channel_name = "telegram"
        mock_b.is_configured = True
        mock_b.send = unittest.mock.AsyncMock(return_value=NotificationResult(
            channel="telegram", status=DeliveryStatus.FAILED, attempts=1, last_error="HTTP 401 Unauthorized"
        ))

        mock_c = MagicMock()
        mock_c.channel_name = "email"
        mock_c.is_configured = True
        mock_c.send = unittest.mock.AsyncMock(side_effect=RuntimeError("Unexpected network crash"))

        dispatcher = NotificationDispatcher(
            channels=[mock_a, mock_b, mock_c],
            alert_repo=repo,
        )

        results = await dispatcher.dispatch(self.alert, alert_id=alert_event.alert_id)

        # Verification of results
        self.assertEqual(results["discord"].status, DeliveryStatus.DELIVERED)
        self.assertEqual(results["telegram"].status, DeliveryStatus.FAILED)
        self.assertEqual(results["email"].status, DeliveryStatus.FAILED)
        self.assertIn("Unexpected network crash", results["email"].last_error or "")

        # Verification of SQLite delivery audit logging
        deliveries = repo.get_deliveries_for_alert(alert_event.alert_id)
        self.assertEqual(len(deliveries), 3)

        by_ch = {d.channel: d for d in deliveries}
        self.assertEqual(by_ch["discord"].status, "delivered")
        self.assertEqual(by_ch["telegram"].status, "failed")
        self.assertEqual(by_ch["email"].status, "failed")

        db.close()


if __name__ == "__main__":
    unittest.main()
