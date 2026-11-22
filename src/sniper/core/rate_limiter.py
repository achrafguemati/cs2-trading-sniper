"""Sequential asynchronous rate limiter with circuit breaker and stop-on-block safety.

Enforces:
1. Strict sequential execution (max 1 concurrent request).
2. Paced delays with randomized jitter (10-15 requests per minute target).
3. Exponential backoff on HTTP 429 respecting Retry-After header.
4. Immediate halt on HTTP 403 requiring manual inspection.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field

logger = logging.getLogger("sniper.rate_limiter")


class SecurityBlockError(Exception):
    """Raised when an HTTP 403 or security challenge is encountered, halting all requests."""
    pass


class RateLimitExceededError(Exception):
    """Raised when an HTTP 429 is encountered and backoff is active."""
    pass


@dataclass
class RateLimiterConfig:
    min_delay_seconds: float = 4.0
    max_delay_seconds: float = 6.0
    jitter_max_seconds: float = 1.5
    initial_backoff_seconds: float = 300.0
    max_backoff_seconds: float = 3600.0


@dataclass
class SequentialRateLimiter:
    """Strictly sequential rate limiter managing request pacing and circuit breaking."""
    config: RateLimiterConfig = field(default_factory=RateLimiterConfig)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _last_request_timestamp: float = 0.0
    _consecutive_429_count: int = 0
    _blocked_until: float = 0.0
    _is_permanently_halted: bool = False
    _halt_reason: str = ""

    @property
    def is_halted(self) -> bool:
        return self._is_permanently_halted

    @property
    def is_cooling_down(self) -> bool:
        return time.time() < self._blocked_until

    @property
    def cooldown_seconds_remaining(self) -> float:
        return max(0.0, self._blocked_until - time.time())

    async def acquire(self) -> None:
        """Wait until a request is safe to execute sequentially.
        
        Raises SecurityBlockError if halted by HTTP 403.
        Raises RateLimitExceededError if currently within backoff cooldown.
        """
        if self._is_permanently_halted:
            raise SecurityBlockError(f"Requests halted: {self._halt_reason}")

        async with self._lock:
            # Check cooldown
            now = time.time()
            if now < self._blocked_until:
                remaining = self._blocked_until - now
                raise RateLimitExceededError(
                    f"Rate limiter in backoff cooldown. {remaining:.1f}s remaining before retry."
                )

            # Enforce pacing delay from previous request
            elapsed = now - self._last_request_timestamp
            base_delay = random.uniform(self.config.min_delay_seconds, self.config.max_delay_seconds)
            jitter = random.uniform(0.0, self.config.jitter_max_seconds)
            required_delay = base_delay + jitter

            if self._last_request_timestamp > 0 and elapsed < required_delay:
                sleep_time = required_delay - elapsed
                logger.debug(f"Pacing delay: sleeping {sleep_time:.2f}s")
                await asyncio.sleep(sleep_time)

            self._last_request_timestamp = time.time()

    def record_success(self) -> None:
        """Reset consecutive 429 counter on successful response."""
        self._consecutive_429_count = 0

    def record_429(self, retry_after_header: str | None = None) -> float:
        """Trigger exponential backoff on HTTP 429 and return cooldown duration in seconds."""
        self._consecutive_429_count += 1
        
        # Calculate exponential backoff: 300, 600, 1200, up to max
        multiplier = 2 ** (self._consecutive_429_count - 1)
        calculated_backoff = min(
            self.config.max_backoff_seconds,
            self.config.initial_backoff_seconds * multiplier
        )
        
        # Respect Retry-After if provided by server
        server_retry = 0.0
        if retry_after_header:
            try:
                server_retry = float(retry_after_header.strip())
            except ValueError:
                pass
                
        cooldown = max(calculated_backoff, server_retry)
        self._blocked_until = time.time() + cooldown
        logger.warning(
            f"HTTP 429 received (strike #{self._consecutive_429_count}). "
            f"Backing off globally for {cooldown:.1f}s."
        )
        return cooldown

    def record_403_or_security_block(self, reason: str = "HTTP 403 Forbidden") -> None:
        """Permanently halt further requests upon security challenge or 403."""
        self._is_permanently_halted = True
        self._halt_reason = reason
        logger.critical(f"Circuit breaker tripped: {reason}. Halting all polling.")
