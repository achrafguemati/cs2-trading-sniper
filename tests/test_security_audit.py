"""Automated security & compliance audit suite (Milestone 9).

Verifies all non-negotiable guarantees from AGENTS.md and SECURITY.md:
1. Strictly read-only Steam transport (zero buy/sell/list/trade mutations).
2. Zero Steam credential storage in SQLite tables, API schemas, or React UI.
3. Zero proxy rotation, IP spoofing, header forgery, or CAPTCHA bypasses.
4. Zero secret echo-back for Discord webhook URLs and Telegram bot tokens.
5. Mandatory RPM ceiling clamping (<= 12.0 RPM) across settings & runtime.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path
import re
import tempfile
import unittest

import httpx

from sniper.api.app import create_app
from sniper.core.http_client import ReadOnlyHttpClient
from sniper.core.rate_limiter import RateLimiterConfig, SequentialRateLimiter
from sniper.notifications.base import redact_secrets
from sniper.storage.database import Database


REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = REPO_ROOT / "src" / "sniper"
FRONTEND_SRC = REPO_ROOT / "frontend" / "src"


def _iter_python_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _extract_non_docstring_string_literals(tree: ast.AST) -> list[str]:
    """Return string constants excluding module/class/function docstrings."""
    docstring_nodes: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if (
                node.body
                and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)
            ):
                docstring_nodes.add(id(node.body[0].value))

    literals: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in docstring_nodes:
                literals.append(node.value)
    return literals


class TestStaticSecurityAudit(unittest.TestCase):
    """Static AST and repository compliance checks."""

    def test_sqlite_schema_has_no_credential_or_secret_columns(self) -> None:
        banned_col_tokens = (
            "password",
            "cookie",
            "sessionid",
            "steamlogin",
            "mafile",
            "shared_secret",
            "identity_secret",
            "steam_guard",
            "twofactor",
            "webhook_url",
            "bot_token",
        )
        with tempfile.TemporaryDirectory() as td:
            db = Database(str(Path(td) / "audit.db"))
            try:
                db.initialize()
                conn = db.connection
                tables = [
                    r[0]
                    for r in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                ]
                self.assertGreaterEqual(len(tables), 10)
                for tbl in tables:
                    cols = [
                        str(r[1]).lower()
                        for r in conn.execute(f'PRAGMA table_info("{tbl}")').fetchall()
                    ]
                    for col in cols:
                        for banned in banned_col_tokens:
                            self.assertNotIn(
                                banned,
                                col,
                                f"Prohibited credential/secret column {tbl}.{col}",
                            )
            finally:
                db.close()

    def test_no_steam_write_endpoints_or_proxy_rotation_or_bypass_libs(self) -> None:
        banned_steam_literals = (
            "market/buylisting",
            "market/createbuyorder",
            "market/sellitem",
            "tradeoffer/new/send",
            "economy/confirm",
            "/market/pricehistory",
            "sec-fetch-",
        )
        banned_imports = {
            "cloudscraper",
            "curl_cffi",
            "undetected_chromedriver",
            "playwright",
            "selenium",
            "2captcha",
            "anticaptcha",
            "capsolver",
        }

        for py_file in _iter_python_files(SRC_ROOT):
            source = py_file.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(py_file))

            # Check banned automation / anti-bot bypass imports
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        top = alias.name.split(".")[0].lower()
                        self.assertNotIn(top, banned_imports, f"Banned import in {py_file}: {top}")
                elif isinstance(node, ast.ImportFrom) and node.module:
                    top = node.module.split(".")[0].lower()
                    self.assertNotIn(top, banned_imports, f"Banned import in {py_file}: {top}")

            # Check non-docstring literals for Steam write / cookie-gated / forged headers
            for lit in _extract_non_docstring_string_literals(tree):
                low = lit.lower()
                for forbidden in banned_steam_literals:
                    self.assertNotIn(
                        forbidden,
                        low,
                        f"Forbidden literal {forbidden!r} in {py_file}",
                    )

            # Specifically verify http_client never attaches proxy rotation and only allows fail-closed PermissionError mutation stubs
            if py_file.name == "http_client.py":
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        if node.name.lower() in {"post", "put", "patch", "delete"}:
                            stmts = [
                                s for s in node.body
                                if not (
                                    isinstance(s, ast.Expr)
                                    and isinstance(s.value, ast.Constant)
                                    and isinstance(s.value.value, str)
                                )
                            ]
                            self.assertEqual(len(stmts), 1, f"{node.name} must unconditionally raise PermissionError")
                            self.assertIsInstance(stmts[0], ast.Raise)
                    if isinstance(node, ast.Call):
                        if isinstance(node.func, ast.Attribute) and node.func.attr.lower() in {"post", "put", "patch", "delete"}:
                            self.fail(f"http_client.py must never call .{node.func.attr}()")
                        for kw in node.keywords:
                            self.assertNotIn(
                                kw.arg,
                                {"proxies", "proxy"},
                                "Proxy configuration is forbidden in http_client.py",
                            )

    def test_frontend_and_launcher_enforce_read_only_and_zero_credentials(self) -> None:
        self.assertTrue(FRONTEND_SRC.exists())
        forbidden_ui_inputs = re.compile(
            r"(steamLoginSecure|maFile|shared_secret|identity_secret|steam_password)",
            re.IGNORECASE,
        )
        for ts_file in sorted(FRONTEND_SRC.rglob("*.ts*")):
            text = ts_file.read_text(encoding="utf-8")
            # App.test.tsx mocks settings state; non-test UI components must not reference Steam auth tokens
            if not ts_file.name.endswith(".test.tsx"):
                self.assertIsNone(
                    forbidden_ui_inputs.search(text),
                    f"Credential pattern found in UI file {ts_file}",
                )

        launch_bat = REPO_ROOT / "launch.bat"
        self.assertTrue(launch_bat.exists())
        bat_text = launch_bat.read_text(encoding="utf-8")
        self.assertIn("127.0.0.1", bat_text)
        self.assertNotIn("0.0.0.0", bat_text)


class TestRuntimeApiSecurityAudit(unittest.IsolatedAsyncioTestCase):
    """Runtime ASGI audit for zero secret leakage, RPM clamp, and route read-only guarantees."""

    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._old_env = {
            "DISCORD_WEBHOOK_URL": os.environ.get("DISCORD_WEBHOOK_URL"),
            "TELEGRAM_BOT_TOKEN": os.environ.get("TELEGRAM_BOT_TOKEN"),
            "TELEGRAM_CHAT_ID": os.environ.get("TELEGRAM_CHAT_ID"),
        }
        self._dc_secret = "SECRET_DISCORD_WEBHOOK_AUDIT_99999"
        self._tg_secret = "SECRET_TELEGRAM_TOKEN_AUDIT_88888"
        os.environ["DISCORD_WEBHOOK_URL"] = f"https://discord.com/api/webhooks/1010101/{self._dc_secret}"
        os.environ["TELEGRAM_BOT_TOKEN"] = f"999000111:{self._tg_secret}"
        os.environ["TELEGRAM_CHAT_ID"] = "424242"

        self.http_client = ReadOnlyHttpClient(
            rate_limiter=SequentialRateLimiter(RateLimiterConfig(requests_per_minute=6000.0, jitter_max_seconds=0.0)),
            transport=httpx.MockTransport(lambda req: httpx.Response(200, json={})),
        )
        self.app = create_app(
            db_path=Path(self._tmp.name) / "sniper_sec.db",
            http_client=self.http_client,
        )
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app),
            base_url="http://127.0.0.1:8000",
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        await self.http_client.aclose()
        container = self.app.state.container
        if container.nameid_db is not None:
            container.nameid_db.close()
        container.db.close()
        self._tmp.cleanup()
        for k, v in self._old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    async def test_openapi_has_no_steam_execution_routes_or_credential_fields(self) -> None:
        schema = self.app.openapi()
        paths = list(schema.get("paths", {}).keys())
        for path in paths:
            self.assertNotRegex(path.lower(), r"execute|buylisting|sellitem|tradeoffer|login|auth")

        raw_openapi = str(schema).lower()
        for banned in ("steamloginsecure", "mafile", "shared_secret", "steam_password"):
            self.assertNotIn(banned, raw_openapi)

    async def test_all_get_endpoints_never_leak_secrets_and_rpm_hard_clamped(self) -> None:
        # Attempt to configure excessive RPM -> must fail-closed with 422 validation error
        r_bad = await self.client.put("/api/settings", json={"requests_per_minute": 120.0})
        self.assertEqual(r_bad.status_code, 422)
        self.assertNotIn(self._dc_secret, r_bad.text)
        self.assertNotIn(self._tg_secret, r_bad.text)

        r_put = await self.client.put("/api/settings", json={"requests_per_minute": 10.0})
        self.assertEqual(r_put.status_code, 200)
        self.assertLessEqual(r_put.json()["settings"]["requests_per_minute"], 12.0)
        self.assertNotIn(self._dc_secret, r_put.text)
        self.assertNotIn(self._tg_secret, r_put.text)

        endpoints = [
            "/api/health",
            "/api/overview",
            "/api/settings",
            "/api/source-health",
            "/api/alerts",
            "/api/watchlists",
            "/api/buy/candidates",
            "/api/sell/candidates",
            "/api/portfolio/holdings",
            "/api/inventory/latest",
        ]
        for ep in endpoints:
            resp = await self.client.get(ep)
            self.assertEqual(resp.status_code, 200, f"GET {ep} failed: {resp.text}")
            self.assertNotIn(self._dc_secret, resp.text, f"Discord secret leaked on {ep}")
            self.assertNotIn(self._tg_secret, resp.text, f"Telegram secret leaked on {ep}")

        # Verify redaction helper directly
        raw_err = (
            f"Error posting https://discord.com/api/webhooks/1010101/{self._dc_secret} "
            f"and https://api.telegram.org/bot999000111:{self._tg_secret}/sendMessage"
        )
        redacted = redact_secrets(raw_err)
        self.assertNotIn(self._dc_secret, redacted)
        self.assertNotIn(self._tg_secret, redacted)
