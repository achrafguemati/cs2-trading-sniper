"""Unit tests for read-only HTTP client safety, status handling, headers, URL security, redirect pacing, and circuit breaking."""

from __future__ import annotations

import asyncio
import time
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

# Test-specific configuration injecting mock test routes (not present in production defaults)
TEST_ALLOWED_EXACT_PATHS = frozenset({
    "/test",
    "/subsequent",
    "/missing",
    "/error",
    "/market/itemordershistogram",
})


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
            client.validate_url("http://steamcommunity.com/market/itemordershistogram")

        # Disallowed external host
        with self.assertRaises(SecurityError):
            client.validate_url("https://evil-market.com/market/itemordershistogram")

        # Disallowed unapproved path
        with self.assertRaises(SecurityError):
            client.validate_url("https://steamcommunity.com/login")

    def test_default_production_url_allowlist_rejects_mock_test_routes(self):
        """Default production client must reject /test, /subsequent, /missing, and /error."""
        client = ReadOnlyHttpClient()
        for path in ["/test", "/subsequent", "/missing", "/error"]:
            with self.assertRaises(SecurityError):
                client.validate_url(f"https://steamcommunity.com{path}")

    def test_reject_url_userinfo(self):
        """Reject URLs containing embedded username or password."""
        client = ReadOnlyHttpClient()
        with self.assertRaises(SecurityError):
            client.validate_url("https://user:pass@steamcommunity.com/market/itemordershistogram")
        with self.assertRaises(SecurityError):
            client.validate_url("https://user@steamcommunity.com/market/itemordershistogram")

    def test_reject_non_default_ports(self):
        """Reject non-default ports; accept default port 443."""
        client = ReadOnlyHttpClient()
        with self.assertRaises(SecurityError):
            client.validate_url("https://steamcommunity.com:8080/market/itemordershistogram")
        with self.assertRaises(SecurityError):
            client.validate_url("https://steamcommunity.com:80/market/itemordershistogram")
        with self.assertRaises(SecurityError):
            client.validate_url("https://steamcommunity.com:8443/market/itemordershistogram")

        # Port 443 is valid default for HTTPS
        client.validate_url("https://steamcommunity.com:443/market/itemordershistogram")

    def test_exact_path_matching_for_itemordershistogram(self):
        """Exact path matching for itemordershistogram: reject extended or nested paths."""
        client = ReadOnlyHttpClient()
        client.validate_url("https://steamcommunity.com/market/itemordershistogram")

        with self.assertRaises(SecurityError):
            client.validate_url("https://steamcommunity.com/market/itemordershistogram_fake")
        with self.assertRaises(SecurityError):
            client.validate_url("https://steamcommunity.com/market/itemordershistogram/nested")

    def test_path_boundaries_listings_and_inventory(self):
        """Validate path boundaries for listing and inventory endpoints."""
        client = ReadOnlyHttpClient()

        # Valid listing endpoints
        client.validate_url("https://steamcommunity.com/market/listings/730/AK-47%20%7C%20Redline%20(Field-Tested)")
        client.validate_url("https://steamcommunity.com/market/listings/730")
        client.validate_url("https://steamcommunity.com/market/listings/730/")

        # Invalid listing endpoints (boundary violations)
        with self.assertRaises(SecurityError):
            client.validate_url("https://steamcommunity.com/market/listings/730_evil")
        with self.assertRaises(SecurityError):
            client.validate_url("https://steamcommunity.com/market/listings/731/item")
        with self.assertRaises(SecurityError):
            client.validate_url("https://steamcommunity.com/market/listings")

        # Valid inventory endpoints
        client.validate_url("https://steamcommunity.com/inventory/76561198000000000/730/2")
        client.validate_url("https://steamcommunity.com/inventory")
        client.validate_url("https://steamcommunity.com/inventory/")

        # Invalid inventory endpoints (boundary violations)
        with self.assertRaises(SecurityError):
            client.validate_url("https://steamcommunity.com/inventory_evil/123")
        with self.assertRaises(SecurityError):
            client.validate_url("https://steamcommunity.com/inventoryfake")

    async def test_redirect_cross_host_rejection(self):
        """Cross-host redirect must be rejected immediately before following and make no second request."""
        request_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal request_count
            request_count += 1
            if request.url.path == "/market/listings/730/source":
                return httpx.Response(302, headers={"Location": "https://attacker.com/steal"})
            return httpx.Response(200, json={"success": 1})

        client = ReadOnlyHttpClient(transport=httpx.MockTransport(handler))
        with self.assertRaises(SecurityError):
            await client.get("https://steamcommunity.com/market/listings/730/source")

        self.assertEqual(request_count, 1)

    async def test_redirect_scheme_downgrade_rejection(self):
        """HTTPS to HTTP downgrade redirect must be rejected immediately before following."""
        request_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal request_count
            request_count += 1
            if request.url.path == "/market/listings/730/source":
                return httpx.Response(301, headers={"Location": "http://steamcommunity.com/market/listings/730/dest"})
            return httpx.Response(200, json={"success": 1})

        client = ReadOnlyHttpClient(transport=httpx.MockTransport(handler))
        with self.assertRaises(SecurityError):
            await client.get("https://steamcommunity.com/market/listings/730/source")

        self.assertEqual(request_count, 1)

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

    async def test_redirect_pacing_and_multirequest_rate_limiter_acquisition(self):
        """Every redirect hop must acquire the limiter and pace actual network requests."""
        hop_requests = []
        hop_timestamps = []
        acquire_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            hop_timestamps.append(time.perf_counter())
            hop_requests.append(str(request.url))
            if request.url.path == "/market/listings/730/hop1":
                return httpx.Response(302, headers={"Location": "/market/listings/730/hop2"})
            elif request.url.path == "/market/listings/730/hop2":
                return httpx.Response(302, headers={"Location": "/market/listings/730/hop3"})
            elif request.url.path == "/market/listings/730/hop3":
                return httpx.Response(200, json={"success": 1, "final": True})
            return httpx.Response(404)

        # Accelerated test-only rate (2000 RPM = 0.03s interval) to test redirect pacing without real-world latency
        config = RateLimiterConfig(requests_per_minute=2000.0, jitter_max_seconds=0.0)
        limiter = SequentialRateLimiter(config=config)

        orig_acquire = limiter.acquire

        async def spy_acquire():
            nonlocal acquire_count
            acquire_count += 1
            await orig_acquire()

        limiter.acquire = spy_acquire

        client = ReadOnlyHttpClient(rate_limiter=limiter, transport=httpx.MockTransport(handler))
        data = await client.get_json("https://steamcommunity.com/market/listings/730/hop1")

        self.assertEqual(data["success"], 1)
        self.assertTrue(data["final"])
        self.assertEqual(len(hop_requests), 3)
        # Exactly 3 acquires for 3 requests (1 initial + 2 redirect hops)
        self.assertEqual(acquire_count, 3)
        # Prove the interval exists between successive redirect network requests.
        self.assertEqual(len(hop_timestamps), 3)
        self.assertGreaterEqual(hop_timestamps[1] - hop_timestamps[0], 0.025)
        self.assertGreaterEqual(hop_timestamps[2] - hop_timestamps[1], 0.025)

    async def test_exact_outgoing_headers(self):
        """Verify client sends transparent, non-spoofed application headers and zero Sec-Fetch-* headers."""
        captured_request: httpx.Request | None = None

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal captured_request
            captured_request = request
            return httpx.Response(200, json={"success": 1})

        client = ReadOnlyHttpClient(
            allowed_exact_paths=TEST_ALLOWED_EXACT_PATHS,
            transport=httpx.MockTransport(handler),
        )
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

        client = ReadOnlyHttpClient(
            allowed_exact_paths=TEST_ALLOWED_EXACT_PATHS,
            transport=httpx.MockTransport(handler),
        )
        data = await client.get_json("https://steamcommunity.com/test")
        self.assertEqual(data["success"], 1)

    async def test_status_404_raises_http_status_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, text="Not Found")

        client = ReadOnlyHttpClient(
            allowed_exact_paths=TEST_ALLOWED_EXACT_PATHS,
            transport=httpx.MockTransport(handler),
        )
        with self.assertRaises(httpx.HTTPStatusError):
            await client.get("https://steamcommunity.com/missing")

    async def test_status_500_raises_http_status_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="Internal Server Error")

        client = ReadOnlyHttpClient(
            allowed_exact_paths=TEST_ALLOWED_EXACT_PATHS,
            transport=httpx.MockTransport(handler),
        )
        with self.assertRaises(httpx.HTTPStatusError):
            await client.get("https://steamcommunity.com/error")

    async def test_status_429_triggers_rate_limit_exceeded(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, headers={"Retry-After": "45"}, text="Too Many Requests")

        limiter = SequentialRateLimiter(config=RateLimiterConfig(initial_backoff_seconds=10.0))
        client = ReadOnlyHttpClient(
            rate_limiter=limiter,
            allowed_exact_paths=TEST_ALLOWED_EXACT_PATHS,
            transport=httpx.MockTransport(handler),
        )
        
        with self.assertRaises(RateLimitExceededError):
            await client.get("https://steamcommunity.com/test")
            
        self.assertTrue(limiter.is_cooling_down)

    async def test_status_403_trips_security_breaker(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, text="Forbidden")

        limiter = SequentialRateLimiter()
        client = ReadOnlyHttpClient(
            rate_limiter=limiter,
            allowed_exact_paths=TEST_ALLOWED_EXACT_PATHS,
            transport=httpx.MockTransport(handler),
        )
        
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

        # Test-only accelerated limiter (6000 RPM) to keep unit tests fast
        limiter = SequentialRateLimiter(config=RateLimiterConfig(requests_per_minute=6000.0, jitter_max_seconds=0.0))
        client = ReadOnlyHttpClient(
            rate_limiter=limiter,
            allowed_exact_paths=TEST_ALLOWED_EXACT_PATHS,
            transport=httpx.MockTransport(handler),
        )

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

        # Test-only accelerated limiter (6000 RPM) to keep unit tests fast
        limiter = SequentialRateLimiter(config=RateLimiterConfig(requests_per_minute=6000.0, jitter_max_seconds=0.0))
        client = ReadOnlyHttpClient(
            rate_limiter=limiter,
            allowed_exact_paths=TEST_ALLOWED_EXACT_PATHS,
            transport=httpx.MockTransport(handler),
        )

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

        # Test-only accelerated limiter (6000 RPM) to keep unit tests fast
        limiter = SequentialRateLimiter(config=RateLimiterConfig(requests_per_minute=6000.0, jitter_max_seconds=0.0))
        client = ReadOnlyHttpClient(
            rate_limiter=limiter,
            allowed_exact_paths=TEST_ALLOWED_EXACT_PATHS,
            transport=httpx.MockTransport(handler),
        )

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
        # Test-only accelerated rate (6000 RPM) to test serialization without long pacing delays
        config = RateLimiterConfig(requests_per_minute=6000.0, jitter_max_seconds=0.0)
        limiter = SequentialRateLimiter(config=config)
        client = ReadOnlyHttpClient(
            rate_limiter=limiter,
            allowed_exact_paths=TEST_ALLOWED_EXACT_PATHS,
            transport=transport,
        )

        await asyncio.gather(
            client.get("https://steamcommunity.com/test"),
            client.get("https://steamcommunity.com/test"),
            client.get("https://steamcommunity.com/test"),
        )

        self.assertEqual(max_concurrent_seen, 1)


if __name__ == "__main__":
    unittest.main()
