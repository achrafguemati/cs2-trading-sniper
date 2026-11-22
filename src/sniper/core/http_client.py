"""Read-only asynchronous HTTP client with sequential rate limiting, transparent identification, and circuit breaking."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from sniper.core.rate_limiter import (
    ProtocolViolationError,
    RateLimiterConfig,
    RateLimitExceededError,
    SecurityBlockError,
    SequentialRateLimiter,
)

logger = logging.getLogger("sniper.http_client")

# Transparent, application-identifying User-Agent (no spoofing)
DEFAULT_USER_AGENT = (
    "CS2TradingSniper/0.1.0 (Windows NT 10.0; Win64; x64; +https://github.com/achrafguemati/cs2-trading-sniper)"
)

# Standard, minimal transparent headers (no fabricated browser-navigation Sec-Fetch-* headers)
DEFAULT_HEADERS = {
    "User-Agent": DEFAULT_USER_AGENT,
    "Accept": "application/json, text/javascript;q=0.9, */*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
}

T = TypeVar("T", bound=BaseModel)


class UnexpectedResponseError(ProtocolViolationError):
    """Raised when an HTTP response contains unexpected content, malformed JSON, or schema violation."""
    pass


class ReadOnlyHttpClient:
    """Asynchronous read-only HTTP client.
    
    Guarantees:
    1. Read-only operation: Only GET requests are permitted. Any mutation attempts raise PermissionError.
    2. Transparent identification: Uses genuine application user-agent and standard headers.
    3. Strict sequential request serialization: A dedicated request lock spans rate-limiter acquisition,
       network transmission, response validation, JSON parsing, and schema validation.
    4. Fail-closed circuit breaker: Unexpected HTML challenges, malformed JSON, and schema failures
       trip the circuit breaker and halt subsequent requests immediately.
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

    async def _execute_get_locked(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        expect_json: bool = False,
    ) -> httpx.Response:
        """Execute network GET request within active _request_lock."""
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

        # Raise on other non-successful status codes (404, 500, 502, 503)
        if not response.is_success:
            logger.error(f"HTTP {response.status_code} received from {url}")
            response.raise_for_status()

        # Detect unexpected HTML or challenge responses
        if expect_json:
            content_type = response.headers.get("Content-Type", "").lower()
            text_start = response.text[:200].lstrip().lower()
            if "text/html" in content_type or text_start.startswith(
                ("<html", "<!doctype", "<head", "<body")
            ):
                reason = f"Unexpected HTML/challenge response received from {url} when JSON was expected."
                self.rate_limiter.record_protocol_violation(reason)
                raise UnexpectedResponseError(reason)

        return response

    async def get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        expect_json: bool = False,
    ) -> httpx.Response:
        """Execute a paced, serialized HTTP GET request."""
        async with self._request_lock:
            response = await self._execute_get_locked(url, params=params, expect_json=expect_json)
            self.rate_limiter.record_success()
            return response

    async def get_json(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        schema: type[T] | None = None,
    ) -> Any:
        """Execute GET and parse/validate JSON within a single serialized lifecycle.
        
        Trips the circuit breaker and halts polling immediately on HTML challenges,
        malformed JSON, or schema validation failures.
        """
        async with self._request_lock:
            response = await self._execute_get_locked(url, params=params, expect_json=True)
            
            # Parse JSON safely
            try:
                data = response.json()
            except Exception as e:
                reason = f"Malformed JSON received from {url}: {e}"
                self.rate_limiter.record_protocol_violation(reason)
                raise UnexpectedResponseError(reason) from e

            # Validate schema if provided
            if schema is not None:
                try:
                    validated = schema.model_validate(data)
                except ValidationError as e:
                    reason = f"Schema validation failed for {url} against {schema.__name__}: {e}"
                    self.rate_limiter.record_protocol_violation(reason)
                    raise UnexpectedResponseError(reason) from e
                self.rate_limiter.record_success()
                return validated

            self.rate_limiter.record_success()
            return data

    async def post(self, *args: Any, **kwargs: Any) -> Any:
        raise PermissionError("Security Rule Violation: POST requests are strictly prohibited in read-only mode.")

    async def put(self, *args: Any, **kwargs: Any) -> Any:
        raise PermissionError("Security Rule Violation: PUT requests are strictly prohibited in read-only mode.")

    async def delete(self, *args: Any, **kwargs: Any) -> Any:
        raise PermissionError("Security Rule Violation: DELETE requests are strictly prohibited in read-only mode.")

    async def patch(self, *args: Any, **kwargs: Any) -> Any:
        raise PermissionError("Security Rule Violation: PATCH requests are strictly prohibited in read-only mode.")
