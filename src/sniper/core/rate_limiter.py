"""Sequential asynchronous rate limiter with circuit breaker and stop-on-block safety.

Enforces:
1. Strict sequential request serialization (max 1 concurrent request).
2. Paced delays with randomized jitter using monotonic timers.
3. Exponential backoff on HTTP 429 respecting Retry-After (both delta-seconds and HTTP-date formats).
   Local safety minimum backoff is strictly preserved even if server sends shorter delay.
4. Immediate halt on HTTP 403, unexpected HTML challenge, malformed JSON, or schema failure.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import email.utils
import logging
import random
import time
from dataclasses import dataclass, field

logger = logging.getLogger("sniper.rate_limiter")


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
    min_delay_seconds: float = 4.0
    max_delay_seconds: float = 6.0
    jitter_max_seconds: float = 1.5
    initial_backoff_seconds: float = 300.0
    max_backoff_seconds: float = 3600.0


@dataclass
class SequentialRateLimiter:
    """Strictly sequential rate limiter managing request pacing and circuit breaking.
    
    Uses monotonic time throughout to prevent wall-clock shifts from corrupting pacing
    or cooldown intervals.
    """
    config: RateLimiterConfig = field(default_factory=RateLimiterConfig)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _request_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _last_request_timestamp: float = 0.0
    _consecutive_429_count: int = 0
    _blocked_until: float = 0.0
    _is_permanently_halted: bool = False
    _halt_reason: str = ""

    @property
    def is_halted(self) -> bool:
        return self._is_permanently_halted

    @property
    def halt_reason(self) -> str:
        return self._halt_reason

    @property
    def is_cooling_down(self) -> bool:
        return time.monotonic() < self._blocked_until

    @property
    def cooldown_seconds_remaining(self) -> float:
        return max(0.0, self._blocked_until - time.monotonic())

    async def acquire(self) -> None:
        """Wait until a request is safe to execute sequentially.
        
        Raises:
            SecurityBlockError: If halted by HTTP 403, protocol violation, or schema failure.
            RateLimitExceededError: If currently within backoff cooldown.
        """
        if self._is_permanently_halted:
            raise SecurityBlockError(f"Requests halted: {self._halt_reason}")

        async with self._lock:
            # Check cooldown using monotonic time
            now = time.monotonic()
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

            self._last_request_timestamp = time.monotonic()

    def record_request_completed(self) -> None:
        """Record the completion timestamp of an HTTP request to pace from response end."""
        self._last_request_timestamp = time.monotonic()

    def record_success(self) -> None:
        """Reset consecutive 429 counter on successful response."""
        self._consecutive_429_count = 0

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
        self._blocked_until = time.monotonic() + cooldown
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

    def record_protocol_violation(self, reason: str) -> None:
        """Trip circuit breaker and permanently halt polling due to protocol or schema failure."""
        self._is_permanently_halted = True
        self._halt_reason = reason
        logger.critical(f"Circuit breaker tripped by protocol/schema violation: {reason}. Halting all polling.")
