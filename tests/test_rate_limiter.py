"""Unit tests for sequential rate limiter and backoff safety mechanisms."""

import asyncio
import time
import unittest

from sniper.core.rate_limiter import (
    RateLimiterConfig,
    RateLimitExceededError,
    SecurityBlockError,
    SequentialRateLimiter,
)


class TestRateLimiter(unittest.IsolatedAsyncioTestCase):
    async def test_acquire_and_pacing(self):
        # Configure very fast test pacing (0.05s)
        config = RateLimiterConfig(
            min_delay_seconds=0.05,
            max_delay_seconds=0.08,
            jitter_max_seconds=0.01,
            initial_backoff_seconds=1.0,
        )
        limiter = SequentialRateLimiter(config=config)
        
        t0 = time.time()
        await limiter.acquire()
        await limiter.acquire()
        elapsed = time.time() - t0
        self.assertGreaterEqual(elapsed, 0.04)

    async def test_429_backoff_trigger(self):
        config = RateLimiterConfig(initial_backoff_seconds=0.5)
        limiter = SequentialRateLimiter(config=config)
        
        cooldown = limiter.record_429()
        self.assertEqual(cooldown, 0.5)
        self.assertTrue(limiter.is_cooling_down)
        
        # Immediate acquire must raise RateLimitExceededError
        with self.assertRaises(RateLimitExceededError):
            await limiter.acquire()
            
        # Wait out cooldown
        await asyncio.sleep(0.55)
        self.assertFalse(limiter.is_cooling_down)

    async def test_retry_after_header_precedence(self):
        config = RateLimiterConfig(initial_backoff_seconds=10.0)
        limiter = SequentialRateLimiter(config=config)
        
        # If server sends Retry-After: 60, cooldown must take max(calculated, 60) = 60
        cooldown = limiter.record_429(retry_after_header="60")
        self.assertEqual(cooldown, 60.0)

    async def test_403_permanent_circuit_breaker(self):
        limiter = SequentialRateLimiter()
        limiter.record_403_or_security_block("Simulated Cloudflare/Akamai 403")
        self.assertTrue(limiter.is_halted)
        
        with self.assertRaises(SecurityBlockError):
            await limiter.acquire()


if __name__ == "__main__":
    unittest.main()
