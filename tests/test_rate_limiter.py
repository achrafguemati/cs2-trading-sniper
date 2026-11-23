"""Unit tests for sequential rate limiter, token bucket mechanics, and circuit breaker canary recovery (M3)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import email.utils
import time
import unittest

from sniper.core.rate_limiter import (
    CircuitBreakerState,
    RateLimiterConfig,
    RateLimitExceededError,
    SecurityBlockError,
    SequentialRateLimiter,
    parse_retry_after,
)


class FakeClock:
    """Deterministic fake clock and sleep seam for testing rate limiters without real-world latency."""

    def __init__(self, start: float = 1000.0) -> None:
        self.current_time = start
        self.sleep_calls: list[float] = []

    def time(self) -> float:
        return self.current_time

    async def sleep(self, seconds: float) -> None:
        self.sleep_calls.append(seconds)
        self.current_time += seconds

    def advance(self, seconds: float) -> None:
        self.current_time += seconds


class TestRateLimiter(unittest.IsolatedAsyncioTestCase):
    """Existing backwards-compatible rate limiter tests."""

    async def test_acquire_and_pacing(self) -> None:
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

    async def test_429_backoff_trigger(self) -> None:
        config = RateLimiterConfig(initial_backoff_seconds=0.4)
        limiter = SequentialRateLimiter(config=config)
        
        cooldown = limiter.record_429()
        self.assertEqual(cooldown, 0.4)
        self.assertTrue(limiter.is_cooling_down)
        
        with self.assertRaises(RateLimitExceededError):
            await limiter.acquire()
            
        await asyncio.sleep(0.45)
        self.assertFalse(limiter.is_cooling_down)

    def test_parse_retry_after_delta_seconds(self) -> None:
        self.assertEqual(parse_retry_after("120"), 120.0)
        self.assertEqual(parse_retry_after("  45.5 "), 45.5)
        self.assertEqual(parse_retry_after("-10"), 0.0)
        self.assertEqual(parse_retry_after(None), 0.0)
        self.assertEqual(parse_retry_after(""), 0.0)

    def test_parse_retry_after_http_date(self) -> None:
        future_dt = datetime.now(timezone.utc) + timedelta(seconds=90)
        http_date_str = email.utils.format_datetime(future_dt)
        parsed = parse_retry_after(http_date_str)
        self.assertAlmostEqual(parsed, 90.0, delta=2.0)

        # Past date returns 0.0
        past_dt = datetime.now(timezone.utc) - timedelta(seconds=60)
        past_date_str = email.utils.format_datetime(past_dt)
        self.assertEqual(parse_retry_after(past_date_str), 0.0)

    async def test_retry_after_cannot_reduce_local_minimum_backoff(self) -> None:
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

    async def test_403_permanent_circuit_breaker(self) -> None:
        limiter = SequentialRateLimiter()
        limiter.record_403_or_security_block("Simulated Cloudflare/Akamai 403")
        self.assertTrue(limiter.is_halted)
        self.assertEqual(limiter.state, CircuitBreakerState.HALTED)
        
        with self.assertRaises(SecurityBlockError):
            await limiter.acquire()


class TestTokenBucketMechanics(unittest.IsolatedAsyncioTestCase):
    """Deterministic offline unit tests verifying token-bucket mechanics and invariants."""

    async def test_token_refill_and_consumption(self) -> None:
        """Prove token bucket starts with 1 token, consumes 1 token, and refills at 12 RPM (5.0s/token)."""
        clock = FakeClock()
        config = RateLimiterConfig(requests_per_minute=12.0, jitter_max_seconds=0.0)
        limiter = SequentialRateLimiter(
            config=config,
            time_func=clock.time,
            sleep_func=clock.sleep,
            random_func=lambda a, b: 0.0,
        )

        self.assertEqual(limiter.available_tokens, 1.0)

        # Request 1: token available immediately, zero sleep
        await limiter.acquire()
        self.assertEqual(len(clock.sleep_calls), 0)
        self.assertEqual(limiter.available_tokens, 0.0)

        # Request 2: arrives immediately, must wait exactly 5.0 seconds for refill
        await limiter.acquire()
        self.assertEqual(len(clock.sleep_calls), 1)
        self.assertAlmostEqual(clock.sleep_calls[0], 5.0)
        self.assertEqual(clock.time(), 1005.0)

    async def test_capacity_one_burst_prevention(self) -> None:
        """Prove that idling for an extended period cannot accumulate more than 1 token (no bursts)."""
        clock = FakeClock()
        config = RateLimiterConfig(requests_per_minute=12.0, capacity=1.0, jitter_max_seconds=0.0)
        limiter = SequentialRateLimiter(
            config=config,
            time_func=clock.time,
            sleep_func=clock.sleep,
            random_func=lambda a, b: 0.0,
        )

        # Idle for 1000 seconds
        clock.advance(1000.0)
        # Capacity must cap strictly at 1.0 token
        self.assertEqual(limiter.available_tokens, 1.0)

        # Consumes the 1 token immediately
        await limiter.acquire()
        self.assertEqual(len(clock.sleep_calls), 0)

        # A burst request immediately following MUST wait 5.0 seconds
        await limiter.acquire()
        self.assertEqual(len(clock.sleep_calls), 1)
        self.assertAlmostEqual(clock.sleep_calls[0], 5.0)

    async def test_maximum_twelve_requests_per_minute_budget(self) -> None:
        """Prove that in 60 seconds of virtual time, exactly 12 requests are permitted."""
        clock = FakeClock()
        config = RateLimiterConfig(requests_per_minute=12.0, jitter_max_seconds=0.0)
        limiter = SequentialRateLimiter(
            config=config,
            time_func=clock.time,
            sleep_func=clock.sleep,
            random_func=lambda a, b: 0.0,
        )

        start_time = clock.time()
        request_timestamps = []

        # Acquire 13 requests sequentially
        for _ in range(13):
            await limiter.acquire()
            request_timestamps.append(clock.time())

        # In exactly 60.0s, requests at t=0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55 (12 requests)
        # Request #13 occurs at exactly t=60.0s (the beginning of minute 2)
        elapsed_for_12 = request_timestamps[11] - start_time
        self.assertEqual(elapsed_for_12, 55.0)

        total_elapsed_13 = request_timestamps[12] - start_time
        self.assertEqual(total_elapsed_13, 60.0)

    async def test_jitter_only_slows_requests_down(self) -> None:
        """Prove randomized jitter only increases wait duration and can never accelerate requests."""
        clock = FakeClock()
        config = RateLimiterConfig(requests_per_minute=12.0, jitter_max_seconds=1.5)
        # Mock random to return max jitter (1.5s)
        limiter = SequentialRateLimiter(
            config=config,
            time_func=clock.time,
            sleep_func=clock.sleep,
            random_func=lambda a, b: 1.5,
        )

        # First request consumes initial token, plus 1.5s jitter
        await limiter.acquire()
        self.assertEqual(clock.sleep_calls, [1.5])

        # Second request: 5.0s token wait + 1.5s jitter = 6.5s
        await limiter.acquire()
        self.assertEqual(clock.sleep_calls, [1.5, 6.5])


class TestCircuitBreakerCanaryRecovery(unittest.IsolatedAsyncioTestCase):
    """Deterministic offline unit tests for circuit breaker state transitions and canary recovery."""

    async def test_circuit_breaker_exponential_backoff_schedule(self) -> None:
        """Verify exponential backoff schedule: 300s, 600s, 1200s, 2400s, 3600s (capped)."""
        clock = FakeClock()
        limiter = SequentialRateLimiter(
            config=RateLimiterConfig(initial_backoff_seconds=300.0, max_backoff_seconds=3600.0),
            time_func=clock.time,
        )

        self.assertEqual(limiter.state, CircuitBreakerState.CLOSED)

        # Strike 1: 300s
        self.assertEqual(limiter.record_429(), 300.0)
        self.assertEqual(limiter.state, CircuitBreakerState.OPEN)

        # Strike 2: 600s
        self.assertEqual(limiter.record_429(), 600.0)

        # Strike 3: 1200s
        self.assertEqual(limiter.record_429(), 1200.0)

        # Strike 4: 2400s
        self.assertEqual(limiter.record_429(), 2400.0)

        # Strike 5: 3600s (capped at max_backoff_seconds)
        self.assertEqual(limiter.record_429(), 3600.0)

        # Strike 6: stays 3600s
        self.assertEqual(limiter.record_429(), 3600.0)
        self.assertEqual(limiter.consecutive_429_count, 6)

    async def test_canary_recovery_success_closes_breaker(self) -> None:
        """Prove that after cooldown, exactly one canary request is permitted, and success closes breaker."""
        clock = FakeClock()
        config = RateLimiterConfig(initial_backoff_seconds=300.0, requests_per_minute=12.0, jitter_max_seconds=0.0)
        limiter = SequentialRateLimiter(
            config=config,
            time_func=clock.time,
            sleep_func=clock.sleep,
            random_func=lambda a, b: 0.0,
        )

        # 429 opens breaker for 300s
        limiter.record_429()
        self.assertEqual(limiter.state, CircuitBreakerState.OPEN)

        # Within cooldown: rejected
        with self.assertRaises(RateLimitExceededError):
            await limiter.acquire()

        # Advance past 300s cooldown
        clock.advance(301.0)
        self.assertEqual(limiter.state, CircuitBreakerState.HALF_OPEN)

        results = []

        async def worker(name: str) -> None:
            await limiter.acquire()
            results.append((name, "acquired", limiter.state))
            # First task is the canary: simulate success
            if name == "canary":
                limiter.record_success()
                results.append((name, "succeeded", limiter.state))

        # Launch concurrent canary and waiting worker
        t_canary = asyncio.create_task(worker("canary"))
        t_waiting = asyncio.create_task(worker("waiting"))
        await asyncio.gather(t_canary, t_waiting)

        # Canary acquired in HALF_OPEN, succeeded and moved state to CLOSED, then waiting acquired in CLOSED
        self.assertEqual(results[0], ("canary", "acquired", CircuitBreakerState.HALF_OPEN))
        self.assertEqual(results[1], ("canary", "succeeded", CircuitBreakerState.CLOSED))
        self.assertEqual(results[2], ("waiting", "acquired", CircuitBreakerState.CLOSED))
        self.assertEqual(limiter.state, CircuitBreakerState.CLOSED)
        self.assertEqual(limiter.consecutive_429_count, 0)

    async def test_canary_recovery_failure_reopens_breaker_with_increased_backoff(self) -> None:
        """Prove that a failed canary request reopens the breaker with increased backoff and blocks waiters."""
        clock = FakeClock()
        config = RateLimiterConfig(initial_backoff_seconds=300.0, requests_per_minute=12.0, jitter_max_seconds=0.0)
        limiter = SequentialRateLimiter(
            config=config,
            time_func=clock.time,
            sleep_func=clock.sleep,
            random_func=lambda a, b: 0.0,
        )

        limiter.record_429()  # Strike 1: 300s
        clock.advance(301.0)
        self.assertEqual(limiter.state, CircuitBreakerState.HALF_OPEN)

        results = []

        async def canary_worker() -> None:
            await limiter.acquire()
            results.append(("canary", "acquired", limiter.state))
            # Canary receives another 429! (Strike 2 -> 600s)
            cd = limiter.record_429()
            results.append(("canary", f"failed_429_{cd}s", limiter.state))

        async def waiting_worker() -> None:
            try:
                await limiter.acquire()
                results.append(("waiting", "unexpected_success", limiter.state))
            except RateLimitExceededError as e:
                results.append(("waiting", "rejected_by_reopened_breaker", str(e)))

        t1 = asyncio.create_task(canary_worker())
        t2 = asyncio.create_task(waiting_worker())
        await asyncio.gather(t1, t2)

        self.assertEqual(results[0], ("canary", "acquired", CircuitBreakerState.HALF_OPEN))
        self.assertEqual(results[1], ("canary", "failed_429_600.0s", CircuitBreakerState.OPEN))
        self.assertEqual(results[2][0], "waiting")
        self.assertEqual(results[2][1], "rejected_by_reopened_breaker")
        self.assertEqual(limiter.state, CircuitBreakerState.OPEN)
        self.assertEqual(limiter.consecutive_429_count, 2)

    async def test_canary_protocol_violation_halts_breaker_permanently(self) -> None:
        """Prove that a protocol or schema violation during canary permanently halts all polling."""
        clock = FakeClock()
        limiter = SequentialRateLimiter(
            config=RateLimiterConfig(initial_backoff_seconds=300.0),
            time_func=clock.time,
        )

        limiter.record_429()
        clock.advance(301.0)

        results = []

        async def canary_worker() -> None:
            await limiter.acquire()
            results.append(("canary", "acquired"))
            limiter.record_protocol_violation("Invalid schema during canary response")

        async def waiting_worker() -> None:
            try:
                await limiter.acquire()
            except SecurityBlockError as e:
                results.append(("waiting", "halted", str(e)))

        t1 = asyncio.create_task(canary_worker())
        t2 = asyncio.create_task(waiting_worker())
        await asyncio.gather(t1, t2)

        self.assertEqual(limiter.state, CircuitBreakerState.HALTED)
        self.assertTrue(limiter.is_halted)
        self.assertEqual(results[1][1], "halted")

    async def test_manual_reset_restores_closed_state(self) -> None:
        """Prove manual reset restores clean operational state after human intervention."""
        limiter = SequentialRateLimiter()
        limiter.record_403_or_security_block("HTTP 403 test")
        self.assertEqual(limiter.state, CircuitBreakerState.HALTED)

        limiter.reset()
        self.assertEqual(limiter.state, CircuitBreakerState.CLOSED)
        self.assertFalse(limiter.is_halted)
        self.assertEqual(limiter.consecutive_429_count, 0)


if __name__ == "__main__":
    unittest.main()
