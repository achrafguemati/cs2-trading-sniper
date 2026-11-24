"""Comprehensive tests for CS2 Trading Sniper CLI interface."""

from __future__ import annotations

import io
from pathlib import Path
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch
import unittest

from sniper.cli import main
from sniper.storage.database import Database
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
            self.assertIn("Schema Version:        v4", output)
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


if __name__ == "__main__":
    unittest.main()
