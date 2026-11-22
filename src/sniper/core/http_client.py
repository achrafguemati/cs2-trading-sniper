"""Read-only asynchronous HTTP client with built-in sequential rate limiting and security guards."""

from __future__ import annotations

import asyncio
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


class UnexpectedResponseError(Exception):
    """Raised when an HTTP response contains unexpected content (e.g. HTML challenge when JSON expected)."""
    pass


class ReadOnlyHttpClient:
    """Asynchronous read-only HTTP client.
    
    Guarantees:
    1. Read-only operation: Only GET requests are permitted. Any mutation attempts raise PermissionError.
    2. Strict sequential request serialization: A dedicated request lock spans rate-limiter acquisition,
       network transmission, and response validation, guaranteeing zero concurrency overlap.
    3. Global backoff on HTTP 429 and immediate shutdown on HTTP 403.
    4. Deterministic error raising for non-2xx responses (e.g. 404, 5xx).
    5. Detection and rejection of unexpected HTML or challenge responses when JSON is expected.
    """

    def __init__(
        self,
        rate_limiter: SequentialRateLimiter | None = None,
        timeout_seconds: float = 12.0,
        headers: dict[str, str] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.rate_limiter = rate_limiter or SequentialRateLimiter(config=RateLimiterConfig())
        self.timeout = timeout_seconds
        self.headers = headers or DEFAULT_HEADERS.copy()
        self._transport = transport
        self._request_lock = getattr(self.rate_limiter, "_request_lock", asyncio.Lock())
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> ReadOnlyHttpClient:
        self._client = httpx.AsyncClient(
            headers=self.headers,
            timeout=self.timeout,
            follow_redirects=True,
            transport=self._transport,
        )
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        expect_json: bool = False,
    ) -> httpx.Response:
        """Execute a paced, strictly serialized HTTP GET request.
        
        The entire request lifecycle (pacing acquisition, HTTP transfer, and response processing)
        is protected by self._request_lock to guarantee zero concurrent execution overlap.
        
        Raises:
            SecurityBlockError: If halted by HTTP 403.
            RateLimitExceededError: If in backoff cooldown or received HTTP 429.
            httpx.HTTPStatusError: On HTTP 404, 5xx, or other non-2xx statuses.
            UnexpectedResponseError: If expect_json is True but HTML/challenge content is received.
            httpx.HTTPError: On low-level network or transport failures.
        """
        async with self._request_lock:
            # Enforce rate limiter pacing and cooldown checks
            await self.rate_limiter.acquire()

            if self._client is None:
                self._client = httpx.AsyncClient(
                    headers=self.headers,
                    timeout=self.timeout,
                    follow_redirects=True,
                    transport=self._transport,
                )

            logger.debug(f"Executing read-only GET: {url}")
            try:
                response = await self._client.get(url, params=params)
            except httpx.HTTPError as e:
                logger.error(f"HTTP transport failure for {url}: {e.__class__.__name__}")
                raise
            finally:
                self.rate_limiter.record_request_completed()

            # Handle rate-limiting and security blocks
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

            # Raise on other non-successful status codes (404, 500, 502, 503, etc.)
            if not response.is_success:
                logger.error(f"HTTP {response.status_code} received from {url}")
                response.raise_for_status()

            # Validate expected format
            if expect_json:
                content_type = response.headers.get("Content-Type", "").lower()
                text_start = response.text[:200].lstrip().lower()
                if "text/html" in content_type or text_start.startswith(
                    ("<html", "<!doctype", "<head", "<body")
                ):
                    raise UnexpectedResponseError(
                        f"Unexpected HTML/challenge response received from {url} when JSON was expected."
                    )

            self.rate_limiter.record_success()
            return response

    async def get_json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        """Execute GET and return parsed JSON data, raising UnexpectedResponseError on HTML or malformed body."""
        response = await self.get(url, params=params, expect_json=True)
        try:
            return response.json()
        except Exception as e:
            raise UnexpectedResponseError(f"Malformed JSON received from {url}: {e}") from e

    async def post(self, *args: Any, **kwargs: Any) -> Any:
        raise PermissionError("Security Rule Violation: POST requests are strictly prohibited in read-only mode.")

    async def put(self, *args: Any, **kwargs: Any) -> Any:
        raise PermissionError("Security Rule Violation: PUT requests are strictly prohibited in read-only mode.")

    async def delete(self, *args: Any, **kwargs: Any) -> Any:
        raise PermissionError("Security Rule Violation: DELETE requests are strictly prohibited in read-only mode.")

    async def patch(self, *args: Any, **kwargs: Any) -> Any:
        raise PermissionError("Security Rule Violation: PATCH requests are strictly prohibited in read-only mode.")
