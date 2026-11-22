"""Unit tests for read-only HTTP client safety, status handling, and sequential serialization."""

import asyncio
import unittest

import httpx

from sniper.core.http_client import (
    ReadOnlyHttpClient,
    UnexpectedResponseError,
)
from sniper.core.rate_limiter import (
    RateLimiterConfig,
    RateLimitExceededError,
    SecurityBlockError,
    SequentialRateLimiter,
)


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

    async def test_status_200_valid_json(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"success": 1, "data": "ok"})

        client = ReadOnlyHttpClient(transport=httpx.MockTransport(handler))
        resp = await client.get("https://steamcommunity.com/test", expect_json=True)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["success"], 1)

    async def test_status_200_malformed_json(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"{malformed json")

        client = ReadOnlyHttpClient(transport=httpx.MockTransport(handler))
        with self.assertRaises(UnexpectedResponseError):
            await client.get_json("https://steamcommunity.com/test")

    async def test_unexpected_html_response_rejection(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"Content-Type": "text/html; charset=utf-8"},
                content=b"<!DOCTYPE html><html><body><h3>Cloudflare Challenge</h3></body></html>"
            )

        client = ReadOnlyHttpClient(transport=httpx.MockTransport(handler))
        with self.assertRaises(UnexpectedResponseError):
            await client.get("https://steamcommunity.com/test", expect_json=True)

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

    async def test_concurrent_request_strict_serialization(self):
        """Verify that concurrent calls to get() never overlap in the transport."""
        active_requests = 0
        max_concurrent_seen = 0
        lock = asyncio.Lock()

        async def slow_handler(request: httpx.Request) -> httpx.Response:
            nonlocal active_requests, max_concurrent_seen
            async with lock:
                active_requests += 1
                if active_requests > max_concurrent_seen:
                    max_concurrent_seen = active_requests
            
            # Simulate network latency of 60ms
            await asyncio.sleep(0.06)
            
            async with lock:
                active_requests -= 1
            return httpx.Response(200, json={"status": "ok"})

        transport = httpx.MockTransport(slow_handler)
        # Fast pacing config (0.01s)
        config = RateLimiterConfig(min_delay_seconds=0.01, max_delay_seconds=0.02, jitter_max_seconds=0.0)
        limiter = SequentialRateLimiter(config=config)
        client = ReadOnlyHttpClient(rate_limiter=limiter, transport=transport)

        # Launch 3 concurrent requests simultaneously
        await asyncio.gather(
            client.get("https://steamcommunity.com/req1"),
            client.get("https://steamcommunity.com/req2"),
            client.get("https://steamcommunity.com/req3"),
        )

        # Strict sequential execution mandate: max_concurrent_seen must be exactly 1
        self.assertEqual(max_concurrent_seen, 1)


if __name__ == "__main__":
    unittest.main()
