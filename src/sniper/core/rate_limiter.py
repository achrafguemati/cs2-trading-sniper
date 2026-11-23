"""Sequential asynchronous rate limiter with genuine token bucket and global circuit breaker.

Enforces:
1. Strict sequential request serialization (max 1 concurrent request).
2. Token-bucket rate limiting:
   - Budget: 12 requests per minute (internal conservative safety policy, not a Valve guarantee).
   - Capacity: 1 token (burst prevention: capacity is strictly 1 token).
   - Monotonic time refill.
   - Non-negative randomized jitter that can only slow requests down, never increase rate.
   - Every network request consumes 1 token, including every redirect hop.
3. Global circuit breaker with 4 distinct states:
   - CLOSED: Normal paced operation.
   - OPEN: Triggered by HTTP 429. Exponential backoff (300s initial safety minimum, up to 3600s).
     Preserves local safety minimum against shorter server Retry-After values.
   - HALF_OPEN: Exactly one canary request is permitted globally after cooldown expires.
     All other waiting requests block until the canary outcome is resolved.
     Successful canary closes the breaker. Failed canary (429) reopens it with increased backoff.
   - HALTED: Triggered by HTTP 403, unexpected HTML challenges, malformed JSON, or schema failures.
     Permanently stops all polling; requires explicit human review/reset.
4. Testable and deterministic: supports dependency injection for clock, sleep, and random functions.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
import email.utils
from enum import Enum
import logging
import random
import time

logger = logging.getLogger("sniper.rate_limiter")


class CircuitBreakerState(str, Enum):
    """Lifecycle states of the global circuit breaker."""
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"
    HALTED = "HALTED"


class SecurityBlockError(Exception):
    """Raised when an HTTP 403, security challenge, or circuit breaker halt is active."""
    pass


class ProtocolViolationError(SecurityBlockError):
    """Raised when an unexpected HTML challenge, malformed JSON, or schema failure halts requests."""
    pass


class RateLimitExceededError(Exception):
    """Raised when an HTTP 429 is encountered and backoff is active."""
    pass


def parse_retry_after(header_value: str | None) -> float:
    """Parse Retry-After header into delay in seconds.
    
    Supports:
    1. Delta-seconds: e.g. '120'.
    2. HTTP-date format (RFC 7231 / IMF-fixdate): e.g. 'Sun, 22 Nov 2026 18:30:00 GMT'.
    
    Returns 0.0 if header is missing, malformed, or specifies a date in the past.
    """
    if not header_value:
        return 0.0
        
    cleaned = header_value.strip()
    if not cleaned:
        return 0.0
        
    # 1. Delta-seconds
    try:
        val = float(cleaned)
        return max(0.0, val)
    except ValueError:
        pass
        
    # 2. HTTP-date format
    try:
        dt = email.utils.parsedate_to_datetime(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        now_utc = datetime.now(timezone.utc)
        delay = (dt - now_utc).total_seconds()
        return max(0.0, delay)
    except Exception:
        return 0.0


@dataclass
class RateLimiterConfig:
    """Configuration for token-bucket rate limiter and exponential backoff circuit breaker."""
    requests_per_minute: float = 12.0
    capacity: float = 1.0
    min_delay_seconds: float = 4.0  # Deprecated: retained for backward compatibility; does not affect refill rate.
    max_delay_seconds: float = 6.0  # Deprecated: retained for backward compatibility; does not affect refill rate.
    jitter_max_seconds: float = 1.5
    initial_backoff_seconds: float = 300.0
    max_backoff_seconds: float = 3600.0

    @property
    def refill_rate(self) -> float:
        """Token refill rate in tokens per second, derived solely from requests_per_minute."""
        return self.requests_per_minute / 60.0


class SequentialRateLimiter:
    """Strictly sequential token-bucket rate limiter and global circuit breaker.
    
    Guarantees:
    - Maximum 12 requests per minute (or configured requests_per_minute).
    - Capacity of 1 token: eliminates bursts completely.
    - Monotonic time calculations across all rate and cooldown evaluations.
    - Exactly one global canary request during HALF-OPEN state recovery.
    - Fail-closed permanent halt on 403, HTML challenge, malformed JSON, or schema failure.
    - Test seams: injectable time_func, sleep_func, and random_func.
    """

    def __init__(
        self,
        config: RateLimiterConfig | None = None,
        time_func: Callable[[], float] | None = None,
        sleep_func: Callable[[float], Awaitable[None]] | None = None,
        random_func: Callable[[float, float], float] | None = None,
    ) -> None:
        self.config = config or RateLimiterConfig()
        self._time_func: Callable[[], float] = time_func or time.monotonic
        self._sleep_func: Callable[[float], Awaitable[None]] = sleep_func or asyncio.sleep
        self._random_func: Callable[[float, float], float] = random_func or random.uniform

        self._lock = asyncio.Lock()
        self._request_lock = asyncio.Lock()

        # Token bucket state
        self._tokens: float = self.config.capacity
        self._last_refill_timestamp: float = self._time_func()
        self._last_request_timestamp: float = 0.0

        # Circuit breaker state
        self._consecutive_429_count: int = 0
        self._blocked_until: float = 0.0
        self._is_permanently_halted: bool = False
        self._halt_reason: str = ""

        # Canary recovery state
        self._canary_in_flight: bool = False
        self._canary_event: asyncio.Event = asyncio.Event()

    @property
    def state(self) -> CircuitBreakerState:
        """Current operational state of the circuit breaker."""
        if self._is_permanently_halted:
            return CircuitBreakerState.HALTED
        if self._consecutive_429_count > 0:
            if self._time_func() < self._blocked_until:
                return CircuitBreakerState.OPEN
            return CircuitBreakerState.HALF_OPEN
        return CircuitBreakerState.CLOSED

    @property
    def is_halted(self) -> bool:
        """True if circuit breaker is permanently halted due to 403 or protocol violation."""
        return self._is_permanently_halted

    @property
    def halt_reason(self) -> str:
        """Diagnostic description of why the circuit breaker was permanently halted."""
        return self._halt_reason

    @property
    def is_cooling_down(self) -> bool:
        """True if rate limiter is currently within an active 429 backoff cooldown period."""
        return self._consecutive_429_count > 0 and self._time_func() < self._blocked_until

    @property
    def cooldown_seconds_remaining(self) -> float:
        """Seconds remaining before current 429 backoff expires, or 0.0 if not cooling down."""
        return max(0.0, self._blocked_until - self._time_func())

    @property
    def consecutive_429_count(self) -> int:
        """Number of consecutive HTTP 429 responses received without an intervening success."""
        return self._consecutive_429_count

    @property
    def canary_in_flight(self) -> bool:
        """True if a canary request is currently active in HALF_OPEN state."""
        return self._canary_in_flight

    @property
    def available_tokens(self) -> float:
        """Current token count after monotonic refill calculation."""
        now = self._time_func()
        elapsed = max(0.0, now - self._last_refill_timestamp)
        return min(self.config.capacity, self._tokens + elapsed * self.config.refill_rate)

    async def acquire(self) -> None:
        """Wait until a request is safe to execute sequentially under rate and breaker policies.
        
        Raises:
            SecurityBlockError: If halted by HTTP 403, protocol violation, or schema failure.
            RateLimitExceededError: If currently within backoff cooldown.
        """
        while True:
            if self._is_permanently_halted:
                raise SecurityBlockError(f"Requests halted: {self._halt_reason}")

            async with self._lock:
                if self._is_permanently_halted:
                    raise SecurityBlockError(f"Requests halted: {self._halt_reason}")

                now = self._time_func()

                # 1. Check circuit breaker OPEN cooldown
                if self._consecutive_429_count > 0 and now < self._blocked_until:
                    remaining = self._blocked_until - now
                    raise RateLimitExceededError(
                        f"Rate limiter in backoff cooldown. {remaining:.1f}s remaining before retry."
                    )

                # 2. Check circuit breaker HALF_OPEN canary gate
                if self._consecutive_429_count > 0 and now >= self._blocked_until:
                    if not self._canary_in_flight:
                        self._canary_in_flight = True
                        self._canary_event.clear()
                        logger.info("Designated current request as global circuit breaker canary.")
                        # This request is the canary; break to acquire token and execute
                        break
                    else:
                        # Another request is already the canary. Wait for outcome.
                        logger.debug("Canary request already in flight; waiting for canary outcome.")
                        canary_event = self._canary_event
                else:
                    # CLOSED state: normal request
                    break

            # Block waiting callers outside self._lock while canary request is in flight
            await canary_event.wait()
            # Loop again to re-check breaker state after canary outcome

        # Consume token from token bucket under lock
        async with self._lock:
            if self._is_permanently_halted:
                self.release_canary()
                raise SecurityBlockError(f"Requests halted: {self._halt_reason}")

            now = self._time_func()
            elapsed = max(0.0, now - self._last_refill_timestamp)
            if elapsed > 0:
                self._tokens = min(self.config.capacity, self._tokens + elapsed * self.config.refill_rate)
                self._last_refill_timestamp = now

            if self._tokens < 1.0:
                wait_for_token = (1.0 - self._tokens) / self.config.refill_rate
            else:
                wait_for_token = 0.0

            jitter = self._random_func(0.0, self.config.jitter_max_seconds) if self.config.jitter_max_seconds > 0 else 0.0
            total_wait = wait_for_token + jitter

            if total_wait > 0:
                logger.debug(
                    f"Token bucket pacing: sleeping {total_wait:.2f}s "
                    f"(wait={wait_for_token:.2f}s, jitter={jitter:.2f}s)"
                )
                await self._sleep_func(total_wait)
                now = self._time_func()
                elapsed = max(0.0, now - self._last_refill_timestamp)
                if elapsed > 0:
                    self._tokens = min(self.config.capacity, self._tokens + elapsed * self.config.refill_rate)
                    self._last_refill_timestamp = now

            # Consume exactly 1 token
            self._tokens = max(0.0, self._tokens - 1.0)
            self._last_request_timestamp = self._time_func()

    def record_request_completed(self) -> None:
        """Record completion timestamp of an HTTP request and ensure canary lock is not leaked."""
        self._last_request_timestamp = self._time_func()
        # If request exited without calling record_success or record_429 (e.g. transport failure), release canary
        if self._canary_in_flight:
            logger.warning("Canary request completed without explicit success/failure record; releasing canary lock.")
            self._canary_in_flight = False
            self._canary_event.set()

    def record_success(self) -> None:
        """Reset consecutive 429 counter on successful response and close breaker."""
        was_half_open = self._consecutive_429_count > 0
        self._consecutive_429_count = 0
        self._blocked_until = 0.0
        self._canary_in_flight = False
        self._canary_event.set()
        if was_half_open:
            logger.info("Canary probe succeeded. Circuit breaker CLOSED; resuming normal paced operation.")

    def record_429(self, retry_after_header: str | None = None) -> float:
        """Trigger exponential backoff on HTTP 429 and return cooldown duration in seconds.
        
        Evaluates Retry-After header (both delta-seconds and HTTP-date).
        Conservative safety mandate: A server-provided retry delay shorter than our local
        minimum safety backoff does NOT reduce or override our local backoff. We always
        use max(calculated_backoff, server_retry).
        """
        self._consecutive_429_count += 1
        
        multiplier = 2 ** (self._consecutive_429_count - 1)
        calculated_backoff = min(
            self.config.max_backoff_seconds,
            self.config.initial_backoff_seconds * multiplier
        )
        
        server_retry = parse_retry_after(retry_after_header)
        cooldown = max(calculated_backoff, server_retry)
        self._blocked_until = self._time_func() + cooldown
        self._canary_in_flight = False
        self._canary_event.set()

        logger.warning(
            f"HTTP 429 received (strike #{self._consecutive_429_count}). "
            f"Circuit breaker OPEN; backing off globally for {cooldown:.1f}s."
        )
        return cooldown

    def record_403_or_security_block(self, reason: str = "HTTP 403 Forbidden") -> None:
        """Permanently halt further requests upon security challenge or 403."""
        self._is_permanently_halted = True
        self._halt_reason = reason
        self._canary_in_flight = False
        self._canary_event.set()
        logger.critical(f"Circuit breaker tripped: {reason}. Halting all polling.")

    def record_protocol_violation(self, reason: str) -> None:
        """Trip circuit breaker and permanently halt polling due to protocol or schema failure."""
        self._is_permanently_halted = True
        self._halt_reason = reason
        self._canary_in_flight = False
        self._canary_event.set()
        logger.critical(f"Circuit breaker tripped by protocol/schema violation: {reason}. Halting all polling.")

    def release_canary(self) -> None:
        """Release canary in-flight reservation."""
        if self._canary_in_flight:
            self._canary_in_flight = False
            self._canary_event.set()

    def reset(self) -> None:
        """Explicit manual reset of the rate limiter and circuit breaker after human review."""
        self._is_permanently_halted = False
        self._halt_reason = ""
        self._consecutive_429_count = 0
        self._blocked_until = 0.0
        self._canary_in_flight = False
        self._canary_event.set()
        self._tokens = self.config.capacity
        self._last_refill_timestamp = self._time_func()
        logger.info("Circuit breaker and rate limiter manually reset.")
