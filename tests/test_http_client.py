"""Unit tests for read-only HTTP client safety, status handling, headers, URL security, and circuit breaking."""

import asyncio
import unittest

import httpx
from pydantic import BaseModel

from sniper.core.http_client import (
    DEFAULT_HEADERS,
    ReadOnlyHttpClient,
    SecurityError,
    UnexpectedResponseError,
)
from sniper.core.rate_limiter import (
    RateLimiterConfig,
    RateLimitExceededError,
    SecurityBlockError,
    SequentialRateLimiter,
)


class DummySchema(BaseModel):
    required_key: str
    numeric_val: int


class TestHttpClient(unittest.IsolatedAsyncioTestCase):
    async def test_mutation_methods_strictly_prohibited(self):
        client = ReadOnlyHttpClient()
        
        with self.assertRaises(PermissionError):
            await client.post("https://steamcommunity.com/market/buynow")
            
        with self.assertRaises(PermissionError):
            await client.put("https://steamcommunity.com/market/createlisting")
            
        with self.assertRaises(PermissionError):
            await client.delete("https://steamcommunity.com/market/cancellisting")

        with self.assertRaises(PermissionError):
            await client.patch("https://steamcommunity.com/market/updatelisting")

    async def test_url_security_allowlist_validation(self):
        """Verify URL validation restricts schemes, hosts, and paths strictly to approved Steam endpoints."""
        client = ReadOnlyHttpClient()

        # Disallowed scheme: HTTP
        with self.assertRaises(SecurityError):
            await client.get("http://steamcommunity.com/market/itemordershistogram")

        # Disallowed external host
        with self.assertRaises(SecurityError):
            await client.get("https://evil-market.com/market/itemordershistogram")

        # Disallowed unapproved path
        with self.assertRaises(SecurityError):
            await client.get("https://steamcommunity.com/login")

    async def test_redirect_cross_host_rejection(self):
        """Cross-host redirect must be rejected immediately before following."""
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/test_redirect":
                return httpx.Response(302, headers={"Location": "https://attacker.com/steal"})
            return httpx.Response(200, json={"success": 1})

        client = ReadOnlyHttpClient(
            allowed_path_prefixes=("/test_redirect", "/market/"),
            transport=httpx.MockTransport(handler)
        )
        with self.assertRaises(SecurityError):
            await client.get("https://steamcommunity.com/test_redirect")

    async def test_redirect_scheme_downgrade_rejection(self):
        """HTTPS to HTTP downgrade redirect must be rejected."""
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/test_downgrade":
                return httpx.Response(301, headers={"Location": "http://steamcommunity.com/market/listings/730/test"})
            return httpx.Response(200, json={"success": 1})

        client = ReadOnlyHttpClient(
            allowed_path_prefixes=("/test_downgrade", "/market/"),
            transport=httpx.MockTransport(handler)
        )
        with self.assertRaises(SecurityError):
            await client.get("https://steamcommunity.com/test_downgrade")

    async def test_valid_safe_redirect_followed(self):
        """Safe redirect within allowed host and path must be followed and succeed."""
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/market/listings/730/source":
                return httpx.Response(302, headers={"Location": "/market/listings/730/destination"})
            elif request.url.path == "/market/listings/730/destination":
                return httpx.Response(200, json={"success": 1, "final": True})
            return httpx.Response(404)

        client = ReadOnlyHttpClient(transport=httpx.MockTransport(handler))
        data = await client.get_json("https://steamcommunity.com/market/listings/730/source")
        self.assertEqual(data["success"], 1)
        self.assertTrue(data["final"])

    async def test_exact_outgoing_headers(self):
        """Verify client sends transparent, non-spoofed application headers and zero Sec-Fetch-* headers."""
        captured_request: httpx.Request | None = None

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal captured_request
            captured_request = request
            return httpx.Response(200, json={"success": 1})

        client = ReadOnlyHttpClient(transport=httpx.MockTransport(handler))
        await client.get("https://steamcommunity.com/test")

        self.assertIsNotNone(captured_request)
        headers = dict(captured_request.headers)

        self.assertIn("user-agent", headers)
        self.assertTrue(headers["user-agent"].startswith("CS2TradingSniper/"))
        self.assertNotIn("Chrome", headers["user-agent"])

        for h in headers:
            self.assertFalse(h.lower().startswith("sec-fetch-"), f"Disallowed header present: {h}")

        for expected_key, expected_val in DEFAULT_HEADERS.items():
            self.assertEqual(headers.get(expected_key.lower()), expected_val)

    async def test_status_200_valid_json(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"success": 1, "data": "ok"})

        client = ReadOnlyHttpClient(transport=httpx.MockTransport(handler))
        data = await client.get_json("https://steamcommunity.com/test")
        self.assertEqual(data["success"], 1)

    async def test_status_404_raises_http_status_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, text="Not Found")

        client = ReadOnlyHttpClient(transport=httpx.MockTransport(handler))
        with self.assertRaises(httpx.HTTPStatusError):
            await client.get("https://steamcommunity.com/missing")

    async def test_status_500_raises_http_status_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="Internal Server Error")

        client = ReadOnlyHttpClient(transport=httpx.MockTransport(handler))
        with self.assertRaises(httpx.HTTPStatusError):
            await client.get("https://steamcommunity.com/error")

    async def test_status_429_triggers_rate_limit_exceeded(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, headers={"Retry-After": "45"}, text="Too Many Requests")

        limiter = SequentialRateLimiter(config=RateLimiterConfig(initial_backoff_seconds=10.0))
        client = ReadOnlyHttpClient(rate_limiter=limiter, transport=httpx.MockTransport(handler))
        
        with self.assertRaises(RateLimitExceededError):
            await client.get("https://steamcommunity.com/test")
            
        self.assertTrue(limiter.is_cooling_down)

    async def test_status_403_trips_security_breaker(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, text="Forbidden")

        limiter = SequentialRateLimiter()
        client = ReadOnlyHttpClient(rate_limiter=limiter, transport=httpx.MockTransport(handler))
        
        with self.assertRaises(SecurityBlockError):
            await client.get("https://steamcommunity.com/test")
            
        self.assertTrue(limiter.is_halted)

    async def test_unexpected_html_trips_circuit_breaker_blocking_next_request(self):
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(
                200,
                headers={"Content-Type": "text/html; charset=utf-8"},
                content=b"<!DOCTYPE html><html><body><h3>Cloudflare Challenge</h3></body></html>"
            )

        limiter = SequentialRateLimiter(config=RateLimiterConfig(min_delay_seconds=0.01, max_delay_seconds=0.02, jitter_max_seconds=0.0))
        client = ReadOnlyHttpClient(rate_limiter=limiter, transport=httpx.MockTransport(handler))

        with self.assertRaises(UnexpectedResponseError):
            await client.get_json("https://steamcommunity.com/test")

        self.assertTrue(limiter.is_halted)
        self.assertEqual(call_count, 1)

        with self.assertRaises(SecurityBlockError):
            await client.get_json("https://steamcommunity.com/subsequent")

        self.assertEqual(call_count, 1)

    async def test_malformed_json_trips_circuit_breaker_blocking_next_request(self):
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(200, content=b"{malformed json without close")

        limiter = SequentialRateLimiter(config=RateLimiterConfig(min_delay_seconds=0.01, max_delay_seconds=0.02, jitter_max_seconds=0.0))
        client = ReadOnlyHttpClient(rate_limiter=limiter, transport=httpx.MockTransport(handler))

        with self.assertRaises(UnexpectedResponseError):
            await client.get_json("https://steamcommunity.com/test")

        self.assertTrue(limiter.is_halted)
        self.assertEqual(call_count, 1)

        with self.assertRaises(SecurityBlockError):
            await client.get_json("https://steamcommunity.com/subsequent")

        self.assertEqual(call_count, 1)

    async def test_schema_validation_failure_trips_circuit_breaker_blocking_next_request(self):
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(200, json={"required_key": 12345})

        limiter = SequentialRateLimiter(config=RateLimiterConfig(min_delay_seconds=0.01, max_delay_seconds=0.02, jitter_max_seconds=0.0))
        client = ReadOnlyHttpClient(rate_limiter=limiter, transport=httpx.MockTransport(handler))

        with self.assertRaises(UnexpectedResponseError):
            await client.get_json("https://steamcommunity.com/test", schema=DummySchema)

        self.assertTrue(limiter.is_halted)
        self.assertEqual(call_count, 1)

        with self.assertRaises(SecurityBlockError):
            await client.get_json("https://steamcommunity.com/subsequent", schema=DummySchema)

        self.assertEqual(call_count, 1)

    async def test_concurrent_request_strict_serialization(self):
        active_requests = 0
        max_concurrent_seen = 0
        lock = asyncio.Lock()

        async def slow_handler(request: httpx.Request) -> httpx.Response:
            nonlocal active_requests, max_concurrent_seen
            async with lock:
                active_requests += 1
                if active_requests > max_concurrent_seen:
                    max_concurrent_seen = active_requests
            
            await asyncio.sleep(0.06)
            
            async with lock:
                active_requests -= 1
            return httpx.Response(200, json={"status": "ok"})

        transport = httpx.MockTransport(slow_handler)
        config = RateLimiterConfig(min_delay_seconds=0.01, max_delay_seconds=0.02, jitter_max_seconds=0.0)
        limiter = SequentialRateLimiter(config=config)
        client = ReadOnlyHttpClient(rate_limiter=limiter, transport=transport)

        await asyncio.gather(
            client.get("https://steamcommunity.com/test"),
            client.get("https://steamcommunity.com/test"),
            client.get("https://steamcommunity.com/test"),
        )

        self.assertEqual(max_concurrent_seen, 1)


if __name__ == "__main__":
    unittest.main()
