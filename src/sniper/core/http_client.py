"""Read-only asynchronous HTTP client with sequential rate limiting, transparent identification, and circuit breaking."""

from __future__ import annotations

import asyncio
from decimal import Decimal
import json
import logging
from typing import Any, TypeVar
from urllib.parse import urlparse

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

# Standard, minimal transparent headers (zero fabricated browser-navigation Sec-Fetch-* headers)
DEFAULT_HEADERS = {
    "User-Agent": DEFAULT_USER_AGENT,
    "Accept": "application/json, text/javascript;q=0.9, */*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
}

# Approved Steam hosts and endpoint paths
DEFAULT_ALLOWED_SCHEMES = frozenset({"https"})
DEFAULT_ALLOWED_HOSTS = frozenset({"steamcommunity.com"})
DEFAULT_ALLOWED_EXACT_PATHS = frozenset({
    "/market/itemordershistogram",
})
DEFAULT_ALLOWED_PATH_PREFIXES = (
    "/market/listings/730/",
    "/inventory/",
)

T = TypeVar("T", bound=BaseModel)


class SecurityError(Exception):
    """Raised when a request violates URL security constraints, scheme restrictions, or redirect policies."""
    pass


class UnexpectedResponseError(ProtocolViolationError):
    """Raised when an HTTP response contains unexpected content, malformed JSON, or schema violation."""
    pass


class ReadOnlyHttpClient:
    """Asynchronous read-only HTTP client.
    
    Guarantees:
    1. Read-only operation: Only GET requests are permitted. Any mutation attempts raise PermissionError.
    2. URL allowlist & HTTPS enforcement: Requests are strictly restricted to approved Steam hosts and paths.
    3. Safe redirect validation: Rejects cross-host redirects and HTTPS-to-HTTP downgrades before following.
    4. Transparent identification: Uses genuine application user-agent and standard headers.
    5. Strict sequential request serialization: A dedicated request lock spans rate-limiter acquisition,
       network transmission, response validation, JSON parsing, and schema validation.
    6. Exact decimal handling: Parses JSON numeric tokens using Decimal to avoid binary float corruption.
    7. Fail-closed circuit breaker: Unexpected HTML challenges, malformed JSON, and schema failures
       trip the circuit breaker and halt subsequent requests immediately.
    """

    def __init__(
        self,
        rate_limiter: SequentialRateLimiter | None = None,
        timeout_seconds: float = 12.0,
        headers: dict[str, str] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        allowed_schemes: frozenset[str] | None = None,
        allowed_hosts: frozenset[str] | None = None,
        allowed_exact_paths: frozenset[str] | None = None,
        allowed_path_prefixes: tuple[str, ...] | None = None,
        max_redirects: int = 3,
    ) -> None:
        self.rate_limiter = rate_limiter or SequentialRateLimiter(config=RateLimiterConfig())
        self.timeout = timeout_seconds
        self.headers = headers or DEFAULT_HEADERS.copy()
        self._transport = transport
        self.allowed_schemes = allowed_schemes or DEFAULT_ALLOWED_SCHEMES
        self.allowed_hosts = allowed_hosts or DEFAULT_ALLOWED_HOSTS
        self.allowed_exact_paths = allowed_exact_paths or DEFAULT_ALLOWED_EXACT_PATHS
        self.allowed_path_prefixes = (
            allowed_path_prefixes
            if allowed_path_prefixes is not None
            else DEFAULT_ALLOWED_PATH_PREFIXES
        )
        self.max_redirects = max_redirects
        self._request_lock = getattr(self.rate_limiter, "_request_lock", asyncio.Lock())
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> ReadOnlyHttpClient:
        self._client = httpx.AsyncClient(
            headers=self.headers,
            timeout=self.timeout,
            follow_redirects=False,  # Redirects are manually inspected and validated before following
            transport=self._transport,
        )
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    def validate_url(self, url: str) -> None:
        """Validate URL against security constraints: HTTPS, Steam host, exact/prefix paths.
        
        Security constraints:
        1. Scheme: HTTPS only (rejects HTTP and other schemes).
        2. Userinfo: Rejects credentials embedded in URL (user:pass@host).
        3. Port: Default port only (None or 443 for HTTPS; rejects non-default ports).
        4. Host: Must match approved Steam hosts (steamcommunity.com).
        5. Path:
           - Must match allowed exact paths (e.g. /market/itemordershistogram).
           - Or match allowed path prefixes with boundary enforcement
             (e.g. /market/listings/730/..., /inventory/...).
        
        Raises:
            SecurityError: If any security constraint is violated.
        """
        try:
            parsed = urlparse(url)
        except Exception as e:
            raise SecurityError(f"Security Rule Violation: Malformed URL '{url}': {e}") from e

        # 1. Scheme check
        if not parsed.scheme or parsed.scheme.lower() not in self.allowed_schemes:
            raise SecurityError(
                f"Security Rule Violation: Disallowed scheme '{parsed.scheme}'. "
                f"Only HTTPS is permitted. (URL: {url})"
            )

        # 2. Userinfo check (username/password in URL)
        if parsed.username is not None or parsed.password is not None or "@" in parsed.netloc:
            raise SecurityError(
                f"Security Rule Violation: URL userinfo (embedded credentials) is strictly prohibited. (URL: {url})"
            )

        # 3. Port check (must be default: None or 443 for HTTPS)
        if parsed.port is not None and parsed.port != 443:
            raise SecurityError(
                f"Security Rule Violation: Non-default port '{parsed.port}' is strictly prohibited. (URL: {url})"
            )

        # 4. Host check
        host = (parsed.hostname or "").lower()
        if not host or host not in self.allowed_hosts:
            raise SecurityError(
                f"Security Rule Violation: Host '{host}' is not in the approved Steam host allowlist. (URL: {url})"
            )

        # 5. Path check
        path = parsed.path or "/"

        # Check exact paths
        if path in self.allowed_exact_paths:
            return

        # Check path prefixes with boundary enforcement
        for prefix in self.allowed_path_prefixes:
            if prefix.endswith("/"):
                base = prefix.rstrip("/")
                if path == base or path.startswith(prefix):
                    return
            else:
                if path == prefix or path.startswith(prefix + "/"):
                    return

        raise SecurityError(
            f"Security Rule Violation: Endpoint path '{path}' is not in approved endpoint paths. (URL: {url})"
        )

    async def _execute_get_locked(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        expect_json: bool = False,
    ) -> httpx.Response:
        """Execute network GET request within active _request_lock with safe redirect inspection."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                headers=self.headers,
                timeout=self.timeout,
                follow_redirects=False,
                transport=self._transport,
            )

        current_url = url
        current_params = params
        redirect_count = 0

        while True:
            # Validate URL before every network request
            self.validate_url(current_url)
            # Acquire rate limiter for this specific network request (including any redirect hop)
            await self.rate_limiter.acquire()

            logger.debug(f"Executing read-only GET: {current_url}")
            try:
                response = await self._client.get(current_url, params=current_params)
            except httpx.HTTPError as e:
                logger.error(f"HTTP transport failure for {current_url}: {e.__class__.__name__}")
                raise
            finally:
                self.rate_limiter.record_request_completed()

            # Handle redirects safely
            if response.is_redirect:
                redirect_count += 1
                if redirect_count > self.max_redirects:
                    raise SecurityError(f"Excessive redirects ({redirect_count}) encountered for {url}")

                location = response.headers.get("Location")
                if not location:
                    raise SecurityError(f"HTTP {response.status_code} redirect without Location header from {current_url}")

                target_url = str(response.url.join(location))
                # Validate redirect destination before proceeding (prevents cross-host or HTTPS->HTTP downgrade)
                self.validate_url(target_url)

                logger.info(f"Following validated redirect ({redirect_count}/{self.max_redirects}): {current_url} -> {target_url}")
                current_url = target_url
                current_params = None  # Params are attached to the target redirect URL
                continue

            break

        # Handle rate-limiting and security blocks
        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            cooldown = self.rate_limiter.record_429(retry_after)
            raise RateLimitExceededError(
                f"Steam returned HTTP 429 Too Many Requests. Cooling down for {cooldown:.1f}s."
            )
        elif response.status_code == 403:
            self.rate_limiter.record_403_or_security_block(
                f"Steam returned HTTP 403 Forbidden on URL {current_url}"
            )
            raise SecurityBlockError(
                f"HTTP 403 Forbidden encountered on {current_url}. Circuit breaker tripped; all polling halted."
            )

        # Raise on other non-successful status codes (404, 500, 502, 503)
        if not response.is_success:
            logger.error(f"HTTP {response.status_code} received from {current_url}")
            response.raise_for_status()

        # Detect unexpected HTML or challenge responses
        if expect_json:
            content_type = response.headers.get("Content-Type", "").lower()
            text_start = response.text[:200].lstrip().lower()
            if "text/html" in content_type or text_start.startswith(
                ("<html", "<!doctype", "<head", "<body")
            ):
                reason = f"Unexpected HTML/challenge response received from {current_url} when JSON was expected."
                self.rate_limiter.record_protocol_violation(reason)
                raise UnexpectedResponseError(reason)

        return response

    async def get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        """Execute a paced, serialized HTTP GET request for HTML endpoints."""
        async with self._request_lock:
            response = await self._execute_get_locked(url, params=params, expect_json=False)
            self.rate_limiter.record_success()
            return response

    async def get_json(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        schema: type[T] | None = None,
    ) -> Any:
        """Execute GET and parse/validate JSON within a single serialized request lifecycle.
        
        Guarantees:
        - Parses JSON numeric tokens using Decimal (via parse_float=Decimal) to eliminate binary float errors.
        - Validates against Pydantic schema inside the request lock.
        - Trips circuit breaker and halts polling immediately on HTML challenges, malformed JSON, or schema failures.
        """
        async with self._request_lock:
            response = await self._execute_get_locked(url, params=params, expect_json=True)

            # Parse JSON with exact Decimal precision for all numeric tokens
            try:
                data = json.loads(response.text, parse_float=Decimal)
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
