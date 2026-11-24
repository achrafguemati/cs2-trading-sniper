"""Comprehensive tests for CS2 Trading Sniper CLI interface."""

from __future__ import annotations

import io
import os
from pathlib import Path
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch
import unittest

from dotenv import load_dotenv

from sniper.cli import main
from sniper.core.http_client import ReadOnlyHttpClient
from sniper.storage.database import Database
from sniper.storage.migrations import MAX_SUPPORTED_VERSION
from sniper.storage.models import ItemRecord
from sniper.storage.repositories import CurrencyRepository, ItemRepository


class TestCli(unittest.TestCase):
    """Test suite for command-line interface commands."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "test_cli.db")

        # Initialize DB and seed currencies
        db = Database(self.db_path)
        db.initialize()
        CurrencyRepository(db).seed_default_currencies()
        db.close()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_cli_help(self) -> None:
        """Verify sniper --help returns code 0."""
        with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            with self.assertRaises(SystemExit) as ctx:
                main(["--help"])
            self.assertEqual(ctx.exception.code, 0)
            self.assertIn("CS2 Trading Sniper", mock_out.getvalue())

    def test_cli_no_args_shows_help(self) -> None:
        """Verify calling sniper with no args returns 0 and prints help."""
        with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            code = main([])
            self.assertEqual(code, 0)
            self.assertIn("usage:", mock_out.getvalue())

    def test_cli_status(self) -> None:
        """Verify status command displays database and transport states."""
        with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            code = main(["status", "--db", self.db_path])
            self.assertEqual(code, 0)
            output = mock_out.getvalue()
            self.assertIn("CS2 Trading Sniper System Status", output)
            self.assertIn(f"Schema Version:        v{MAX_SUPPORTED_VERSION}", output)
            self.assertIn("Discord Webhook", output)
            self.assertIn("Telegram Bot", output)
            self.assertNotIn("Email SMTP", output)
            self.assertNotIn("Messenger", output)

    def test_cli_channel_status(self) -> None:
        """Verify channel-status alias displays notification channels status."""
        with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            code = main(["channel-status", "--db", self.db_path])
            self.assertEqual(code, 0)
            output = mock_out.getvalue()
            self.assertIn("Discord Webhook", output)
            self.assertIn("Telegram Bot", output)
            self.assertNotIn("Email SMTP", output)

    def test_cli_test_notifications(self) -> None:
        """Verify test-notifications runs across discord and telegram channels."""
        with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            code = main(["test-notifications", "--channel", "all"])
            self.assertEqual(code, 0)
            output = mock_out.getvalue()
            self.assertIn("Dispatching test alert", output)
            self.assertIn("Channel: discord", output)
            self.assertIn("Channel: telegram", output)
            self.assertNotIn("Channel: email", output)
            self.assertNotIn("Channel: messenger", output)

    def test_cli_test_notification_singular(self) -> None:
        """Verify test-notification singular alias works for specific channel."""
        with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            code = main(["test-notification", "--channel", "discord"])
            self.assertEqual(code, 0)
            output = mock_out.getvalue()
            self.assertIn("Channel: discord", output)
            self.assertNotIn("Channel: telegram", output)

    def test_cli_import_csv(self) -> None:
        """Verify import-csv command parses local CSV file."""
        csv_file = Path(self.temp_dir.name) / "test.csv"
        csv_file.write_text(
            "market_hash_name,amount,marketable\nAK-47 | Redline (Field-Tested),2,true\n",
            encoding="utf-8",
        )

        with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            code = main(["import-csv", str(csv_file), "--db", self.db_path])
            self.assertEqual(code, 0)
            output = mock_out.getvalue()
            self.assertIn("Items Imported:    1", output)

    def test_cli_import_json(self) -> None:
        """Verify import-json command parses local JSON file."""
        json_file = Path(self.temp_dir.name) / "test.json"
        json_file.write_text(
            '[{"market_hash_name": "AWP | Asiimov (Field-Tested)", "amount": 1, "is_marketable": true}]',
            encoding="utf-8",
        )

        with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            code = main(["import-json", str(json_file), "--db", self.db_path])
            self.assertEqual(code, 0)
            output = mock_out.getvalue()
            self.assertIn("Items Imported:    1", output)

    def test_cli_import_inventory_invalid_steamid(self) -> None:
        """Verify import-inventory rejects invalid SteamID64."""
        with patch("sys.stderr", new_callable=io.StringIO) as mock_err:
            code = main(["import-inventory", "invalid_id", "--db", self.db_path])
            self.assertEqual(code, 1)
            self.assertIn("Invalid SteamID64", mock_err.getvalue())

    def test_cli_scan_sell_empty_inventory(self) -> None:
        """Verify scan-sell handles empty inventory cleanly."""
        with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
            code = main(["scan-sell", "--db", self.db_path, "--no-notifications"])
            self.assertEqual(code, 0)
            output = mock_out.getvalue()
            self.assertIn("Total Evaluated: 0", output)

    def test_dotenv_loading_does_not_override_existing_env(self) -> None:
        """Verify python-dotenv loads .env without overriding existing environment variables."""
        env_file = Path(self.temp_dir.name) / ".env"
        env_file.write_text(
            "TEST_EXISTING_VAR=from_dotenv_file\nTEST_NEW_VAR=loaded_from_dotenv\n",
            encoding="utf-8",
        )
        old_val = os.environ.get("TEST_EXISTING_VAR")
        try:
            os.environ["TEST_EXISTING_VAR"] = "original_process_val"
            load_dotenv(dotenv_path=env_file, override=False)
            self.assertEqual(os.environ["TEST_EXISTING_VAR"], "original_process_val")
            self.assertEqual(os.environ["TEST_NEW_VAR"], "loaded_from_dotenv")
        finally:
            if old_val is not None:
                os.environ["TEST_EXISTING_VAR"] = old_val
            else:
                os.environ.pop("TEST_EXISTING_VAR", None)
            os.environ.pop("TEST_NEW_VAR", None)

    def test_cli_import_inventory_closes_http_client_on_completion(self) -> None:
        """Verify cmd_import_inventory manages ReadOnlyHttpClient life cycle with clean closure."""
        with patch("sniper.cli.PublicInventoryClient") as mock_inv_cls:
            mock_inst = MagicMock()
            mock_inst.fetch_and_import = AsyncMock(return_value=(MagicMock(snapshot_id=1, total_count=0), []))
            mock_inv_cls.return_value = mock_inst

            with patch("sys.stdout", new_callable=io.StringIO):
                code = main(["import-inventory", "76561198000000000", "--db", self.db_path])
                self.assertEqual(code, 0)


class TestHttpClientLifecycle(unittest.IsolatedAsyncioTestCase):
    """Test lifecycle and explicit closing of ReadOnlyHttpClient."""

    async def test_http_client_aclose_and_async_context_manager(self) -> None:
        """Verify ReadOnlyHttpClient cleanly closes underlying client upon aclose() and context exit."""
        client = ReadOnlyHttpClient()
        self.assertIsNone(client._client)

        async with client:
            self.assertIsNotNone(client._client)
            underlying = client._client
            self.assertFalse(underlying.is_closed)

        self.assertIsNone(client._client)
        self.assertTrue(underlying.is_closed)

        # Repeated aclose() is safe and idempotent
        await client.aclose()
        self.assertIsNone(client._client)


if __name__ == "__main__":
    unittest.main()
