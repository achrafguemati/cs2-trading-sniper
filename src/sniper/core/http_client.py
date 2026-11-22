"""Read-only asynchronous HTTP client with built-in sequential rate limiting and security guards."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from sniper.core.rate_limiter import (
    RateLimiterConfig,
    RateLimitExceededError,
    SecurityBlockError,
    SequentialRateLimiter,
)

logger = logging.getLogger("sniper.http_client")

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

DEFAULT_HEADERS = {
    "User-Agent": DEFAULT_USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "DNT": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}


class ReadOnlyHttpClient:
    """Asynchronous read-only HTTP client.
    
    Guarantees:
    1. Read-only operation: Only GET requests are permitted. Any mutation attempts raise PermissionError.
    2. Sequential request throttling via SequentialRateLimiter.
    3. Global backoff on HTTP 429 and immediate shutdown on HTTP 403.
    """

    def __init__(
        self,
        rate_limiter: SequentialRateLimiter | None = None,
        timeout_seconds: float = 12.0,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.rate_limiter = rate_limiter or SequentialRateLimiter(config=RateLimiterConfig())
        self.timeout = timeout_seconds
        self.headers = headers or DEFAULT_HEADERS.copy()
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> ReadOnlyHttpClient:
        self._client = httpx.AsyncClient(
            headers=self.headers,
            timeout=self.timeout,
            follow_redirects=True,
        )
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def get(self, url: str, params: dict[str, Any] | None = None) -> httpx.Response:
        """Execute a paced, sequential HTTP GET request.
        
        Raises:
            SecurityBlockError: If halted by HTTP 403.
            RateLimitExceededError: If in backoff cooldown or received HTTP 429.
            httpx.HTTPError: On network or transport failures.
        """
        # Enforce rate limiter pacing and cooldown checks
        await self.rate_limiter.acquire()

        if self._client is None:
            self._client = httpx.AsyncClient(
                headers=self.headers,
                timeout=self.timeout,
                follow_redirects=True,
            )

        logger.debug(f"Executing read-only GET: {url}")
        try:
            response = await self._client.get(url, params=params)
        except httpx.HTTPError as e:
            logger.error(f"HTTP transport error for {url}: {e}")
            raise

        # Check response status codes for rate-limiting and security blocks
        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            cooldown = self.rate_limiter.record_429(retry_after)
            raise RateLimitExceededError(
                f"Steam returned HTTP 429 Too Many Requests. Cooling down for {cooldown:.1f}s."
            )
        elif response.status_code == 403:
            self.rate_limiter.record_403_or_security_block(
                f"Steam returned HTTP 403 Forbidden on URL {url}"
            )
            raise SecurityBlockError(
                f"HTTP 403 Forbidden encountered on {url}. Circuit breaker tripped; all polling halted."
            )

        if response.is_success:
            self.rate_limiter.record_success()

        return response

    async def post(self, *args: Any, **kwargs: Any) -> Any:
        raise PermissionError("Security Rule Violation: POST requests are strictly prohibited in read-only mode.")

    async def put(self, *args: Any, **kwargs: Any) -> Any:
        raise PermissionError("Security Rule Violation: PUT requests are strictly prohibited in read-only mode.")

    async def delete(self, *args: Any, **kwargs: Any) -> Any:
        raise PermissionError("Security Rule Violation: DELETE requests are strictly prohibited in read-only mode.")
