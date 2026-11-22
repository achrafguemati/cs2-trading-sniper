"""Unit tests for sequential rate limiter, monotonic pacing, and backoff safety mechanisms."""

import asyncio
from datetime import datetime, timedelta, timezone
import email.utils
import time
import unittest

from sniper.core.rate_limiter import (
    RateLimiterConfig,
    RateLimitExceededError,
    SecurityBlockError,
    SequentialRateLimiter,
    parse_retry_after,
)


class TestRateLimiter(unittest.IsolatedAsyncioTestCase):
    async def test_acquire_and_pacing(self):
        config = RateLimiterConfig(
            min_delay_seconds=0.04,
            max_delay_seconds=0.06,
            jitter_max_seconds=0.01,
            initial_backoff_seconds=1.0,
        )
        limiter = SequentialRateLimiter(config=config)
        
        t0 = time.monotonic()
        await limiter.acquire()
        await limiter.acquire()
        elapsed = time.monotonic() - t0
        self.assertGreaterEqual(elapsed, 0.035)

    async def test_429_backoff_trigger(self):
        config = RateLimiterConfig(initial_backoff_seconds=0.4)
        limiter = SequentialRateLimiter(config=config)
        
        cooldown = limiter.record_429()
        self.assertEqual(cooldown, 0.4)
        self.assertTrue(limiter.is_cooling_down)
        
        with self.assertRaises(RateLimitExceededError):
            await limiter.acquire()
            
        await asyncio.sleep(0.45)
        self.assertFalse(limiter.is_cooling_down)

    def test_parse_retry_after_delta_seconds(self):
        self.assertEqual(parse_retry_after("120"), 120.0)
        self.assertEqual(parse_retry_after("  45.5 "), 45.5)
        self.assertEqual(parse_retry_after("-10"), 0.0)
        self.assertEqual(parse_retry_after(None), 0.0)
        self.assertEqual(parse_retry_after(""), 0.0)

    def test_parse_retry_after_http_date(self):
        future_dt = datetime.now(timezone.utc) + timedelta(seconds=90)
        http_date_str = email.utils.format_datetime(future_dt)
        parsed = parse_retry_after(http_date_str)
        self.assertAlmostEqual(parsed, 90.0, delta=2.0)

        # Past date returns 0.0
        past_dt = datetime.now(timezone.utc) - timedelta(seconds=60)
        past_date_str = email.utils.format_datetime(past_dt)
        self.assertEqual(parse_retry_after(past_date_str), 0.0)

    async def test_retry_after_cannot_reduce_local_minimum_backoff(self):
        # Local initial backoff is 300 seconds
        config = RateLimiterConfig(initial_backoff_seconds=300.0)
        limiter = SequentialRateLimiter(config=config)
        
        # Server sends Retry-After: 30 (shorter than local 300s)
        cooldown = limiter.record_429(retry_after_header="30")
        # Must retain local safety minimum of 300s
        self.assertEqual(cooldown, 300.0)

        # If server sends Retry-After: 600 (longer than local 300s), respect server
        limiter_server = SequentialRateLimiter(config=config)
        cooldown_server = limiter_server.record_429(retry_after_header="600")
        self.assertEqual(cooldown_server, 600.0)

    async def test_403_permanent_circuit_breaker(self):
        limiter = SequentialRateLimiter()
        limiter.record_403_or_security_block("Simulated Cloudflare/Akamai 403")
        self.assertTrue(limiter.is_halted)
        
        with self.assertRaises(SecurityBlockError):
            await limiter.acquire()


if __name__ == "__main__":
    unittest.main()
