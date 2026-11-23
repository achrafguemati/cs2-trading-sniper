"""Comprehensive offline unit tests for Steam Market adapters (M3).

Covers:
1. Order-book histogram adapter (endpoint, query params, provenance verification, schema, top-level vs cumulative qty).
2. Listing-render adapter (endpoint, URL encoding, pagination bounds, integer validation, identifiers preservation).
3. SSR hydration adapter (script extraction, nested JSON parsing, duplicate/missing script fail-closed, schema validation).
4. Shared circuit breaker and rate limiter behavior across all adapters.
"""

from __future__ import annotations

import json
from pathlib import Path
import unittest
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from sniper.adapters.steam_market import (
    SteamMarketAdapter,
    extract_ssr_hydration_data,
)
from sniper.core.http_client import (
    DEFAULT_ALLOWED_EXACT_PATHS,
    DEFAULT_ALLOWED_PATH_PREFIXES,
    ReadOnlyHttpClient,
    UnexpectedResponseError,
)
from sniper.core.identity import (
    VERIFIED_MAPPING_REPOSITORY,
    VERIFIED_MAPPING_REVISION,
    ItemIdentityResult,
    ResolutionSource,
    ResolutionStatus,
    SsrListingContext,
    UrlType,
    WearCategory,
    resolve_item_identity,
)
from sniper.core.rate_limiter import (
    CircuitBreakerState,
    RateLimiterConfig,
    RateLimitExceededError,
    SecurityBlockError,
    SequentialRateLimiter,
)
from sniper.models.schemas import (
    ListingRenderResponse,
    OrderbookHistogramResponse,
    SsrListingLoaderData,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_fixture(filename: str) -> str:
    """Load text fixture from tests/fixtures."""
    return (FIXTURES_DIR / filename).read_text(encoding="utf-8")


def make_valid_resolved_identity(
    market_hash_name: str = "AK-47 | Redline (Field-Tested)",
    item_nameid: int | None = 7178002,
    source: str | None = VERIFIED_MAPPING_REPOSITORY,
    revision: str | None = VERIFIED_MAPPING_REVISION,
    status: ResolutionStatus = ResolutionStatus.ITEM_NAMEID_RESOLVED,
    appid: int = 730,
) -> ItemIdentityResult:
    """Helper to construct an ItemIdentityResult for adapter testing."""
    return ItemIdentityResult(
        appid=appid,
        input_url=f"https://steamcommunity.com/market/listings/{appid}/{market_hash_name}",
        url_type=UrlType.CANONICAL,
        market_hash_name=market_hash_name,
        base_name=market_hash_name.split(" (")[0],
        exterior=WearCategory.FIELD_TESTED,
        item_nameid=item_nameid,
        item_nameid_source=source,
        item_nameid_revision=revision,
        resolution_status=status,
        resolution_source=ResolutionSource.OFFLINE_DB if item_nameid else ResolutionSource.URL,
    )


class TestOrderbookHistogramAdapter(unittest.IsolatedAsyncioTestCase):
    """Offline unit tests for GET /market/itemordershistogram adapter."""

    async def test_orderbook_histogram_success_and_quantities(self) -> None:
        """Verify query parameters, response parsing, and top-level vs cumulative quantity extraction."""
        captured_url: str | None = None
        captured_params: dict[str, Any] | None = None
        raw_fixture = load_fixture("orderbook_histogram.json")

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal captured_url, captured_params
            captured_url = str(request.url).split("?")[0]
            captured_params = dict(request.url.params)
            return httpx.Response(200, content=raw_fixture.encode("utf-8"))

        client = ReadOnlyHttpClient(
            transport=httpx.MockTransport(handler),
            rate_limiter=SequentialRateLimiter(config=RateLimiterConfig(min_delay_seconds=0.01)),
        )
        adapter = SteamMarketAdapter(client)
        identity = make_valid_resolved_identity()

        resp = await adapter.get_orderbook_histogram(identity, currency=1, country="US", language="english")

        self.assertIsInstance(resp, OrderbookHistogramResponse)
        self.assertEqual(captured_url, "https://steamcommunity.com/market/itemordershistogram")
        self.assertEqual(captured_params["country"], "US")
        self.assertEqual(captured_params["language"], "english")
        self.assertEqual(captured_params["currency"], "1")
        self.assertEqual(captured_params["item_nameid"], "7178002")
        self.assertEqual(captured_params["two_factor"], "0")

        # Check top-level vs cumulative quantity preservation
        self.assertEqual(resp.highest_buy_order_minor, 2250)
        self.assertEqual(resp.lowest_sell_order_minor, 2488)
        self.assertEqual(resp.top_level_buy_quantity, 1)
        self.assertEqual(resp.cumulative_buy_quantity, 211731)

    async def test_orderbook_histogram_rejects_unresolved_identity(self) -> None:
        """Reject identities that have not reached ITEM_NAMEID_RESOLVED status before network call."""
        client = ReadOnlyHttpClient(transport=httpx.MockTransport(lambda req: httpx.Response(200)))
        adapter = SteamMarketAdapter(client)

        unresolved = make_valid_resolved_identity(
            item_nameid=None,
            source=None,
            revision=None,
            status=ResolutionStatus.PARSED_URL_ONLY,
        )
        with self.assertRaises(ValueError) as ctx:
            await adapter.get_orderbook_histogram(unresolved)
        self.assertIn("ITEM_NAMEID_RESOLVED", str(ctx.exception))

    async def test_orderbook_histogram_rejects_unverified_provenance(self) -> None:
        """Reject synthetic, legacy, unknown, or mismatched provenance before network request."""
        client = ReadOnlyHttpClient(transport=httpx.MockTransport(lambda req: httpx.Response(200)))
        adapter = SteamMarketAdapter(client)

        cases = [
            ("SYNTHETIC_TEST_DATA", VERIFIED_MAPPING_REVISION),
            ("legacy_unverified", VERIFIED_MAPPING_REVISION),
            ("https://unknown-repo.com", VERIFIED_MAPPING_REVISION),
            (VERIFIED_MAPPING_REPOSITORY, "wrong_revision"),
            (VERIFIED_MAPPING_REPOSITORY, None),
            (None, VERIFIED_MAPPING_REVISION),
        ]
        for src, rev in cases:
            with self.subTest(source=src, revision=rev):
                ident = make_valid_resolved_identity(source=src, revision=rev)
                with self.assertRaises(ValueError) as ctx:
                    await adapter.get_orderbook_histogram(ident)
                self.assertIn("Unverified item_nameid provenance", str(ctx.exception))

    async def test_orderbook_histogram_rejects_non_integer_item_nameid(self) -> None:
        """Reject booleans, floats, strings, zero, and negative values for item_nameid."""
        client = ReadOnlyHttpClient(transport=httpx.MockTransport(lambda req: httpx.Response(200)))
        adapter = SteamMarketAdapter(client)

        bad_ids = [True, False, 1.0, "7178002", 0, -1, -7178002]
        for bad_id in bad_ids:
            with self.subTest(bad_id=bad_id):
                ident = make_valid_resolved_identity()
                object.__setattr__(ident, "item_nameid", bad_id)
                with self.assertRaises(ValueError):
                    await adapter.get_orderbook_histogram(ident)

    async def test_orderbook_histogram_rejects_wrong_appid(self) -> None:
        """Reject non-CS2 appids."""
        client = ReadOnlyHttpClient(transport=httpx.MockTransport(lambda req: httpx.Response(200)))
        adapter = SteamMarketAdapter(client)
        ident = make_valid_resolved_identity(appid=440)
        with self.assertRaises(ValueError) as ctx:
            await adapter.get_orderbook_histogram(ident)
        self.assertIn("CS2 (appid 730)", str(ctx.exception))

    async def test_orderbook_histogram_rejects_invalid_currency(self) -> None:
        """Reject non-positive or boolean currencies."""
        client = ReadOnlyHttpClient(transport=httpx.MockTransport(lambda req: httpx.Response(200)))
        adapter = SteamMarketAdapter(client)
        ident = make_valid_resolved_identity()

        for bad_curr in [0, -1, True, False]:
            with self.subTest(bad_curr=bad_curr):
                with self.assertRaises(ValueError):
                    await adapter.get_orderbook_histogram(ident, currency=bad_curr)  # type: ignore[arg-type]


class TestListingRenderAdapter(unittest.IsolatedAsyncioTestCase):
    """Offline unit tests for GET /market/listings/730/{market_hash_name}/render/ adapter."""

    async def test_listing_render_success_and_preservation(self) -> None:
        """Verify URL path, encoding, pagination params, and identifier preservation."""
        captured_url: str | None = None
        captured_params: dict[str, Any] | None = None
        raw_fixture = load_fixture("standard_render.json")

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal captured_url, captured_params
            captured_url = str(request.url).split("?")[0]
            captured_params = dict(request.url.params)
            return httpx.Response(200, content=raw_fixture.encode("utf-8"))

        client = ReadOnlyHttpClient(
            transport=httpx.MockTransport(handler),
            rate_limiter=SequentialRateLimiter(config=RateLimiterConfig(min_delay_seconds=0.01)),
        )
        adapter = SteamMarketAdapter(client)
        identity = make_valid_resolved_identity("AK-47 | Redline (Field-Tested)")

        resp = await adapter.get_listing_render(identity, currency=1, start=0, count=10)

        self.assertIsInstance(resp, ListingRenderResponse)
        expected_path = "https://steamcommunity.com/market/listings/730/AK-47%20%7C%20Redline%20%28Field-Tested%29/render/"
        self.assertEqual(captured_url, expected_path)
        self.assertEqual(captured_params["start"], "0")
        self.assertEqual(captured_params["count"], "10")
        self.assertEqual(captured_params["currency"], "1")
        self.assertEqual(captured_params["format"], "json")

        # Check preservation of listing and asset IDs
        self.assertIn("49827391028374", resp.listinginfo)
        info = resp.listinginfo["49827391028374"]
        self.assertEqual(info.price, 2488)
        self.assertEqual(info.fee, 324)
        self.assertEqual(info.steam_fee, 108)
        self.assertEqual(info.publisher_fee, 216)
        self.assertEqual(info.asset.id, "38291049281")

        asset = resp.assets["730"]["2"]["38291049281"]
        self.assertEqual(asset.classid, "7993038091")
        self.assertEqual(asset.name, "AK-47 | Redline (Field-Tested)")
        self.assertEqual(len(asset.descriptions), 2)
        self.assertTrue(any("Inspect in Game" in a.name for a in asset.market_actions))

    async def test_listing_render_url_encoding_special_characters(self) -> None:
        """Verify proper percent-encoding of special characters (slashes, spaces, parentheses, pipes)."""
        captured_url: str | None = None
        raw_fixture = load_fixture("standard_render.json")

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal captured_url
            captured_url = str(request.url).split("?")[0]
            return httpx.Response(200, content=raw_fixture.encode("utf-8"))

        client = ReadOnlyHttpClient(
            transport=httpx.MockTransport(handler),
            rate_limiter=SequentialRateLimiter(config=RateLimiterConfig(min_delay_seconds=0.01)),
        )
        adapter = SteamMarketAdapter(client)

        complex_name = "Sticker | FaZe Clan (Holo) / Austin 2025 & More"
        identity = make_valid_resolved_identity(complex_name)
        await adapter.get_listing_render(identity, currency=1)

        self.assertIsNotNone(captured_url)
        # Slashes and spaces must be percent-encoded, not bare in URL path
        self.assertNotIn(" ", captured_url)
        self.assertIn("%20", captured_url)
        self.assertIn("%2F", captured_url)  # forward slash encoded
        self.assertIn("%26", captured_url)  # ampersand encoded

    async def test_listing_render_rejects_invalid_pagination(self) -> None:
        """Reject negative start offsets and zero or oversized counts (> 100)."""
        client = ReadOnlyHttpClient(transport=httpx.MockTransport(lambda req: httpx.Response(200)))
        adapter = SteamMarketAdapter(client)
        identity = make_valid_resolved_identity()

        # Negative start
        with self.assertRaises(ValueError):
            await adapter.get_listing_render(identity, currency=1, start=-1)

        # Zero or negative count
        with self.assertRaises(ValueError):
            await adapter.get_listing_render(identity, currency=1, count=0)
        with self.assertRaises(ValueError):
            await adapter.get_listing_render(identity, currency=1, count=-5)

        # Oversized count (> 100)
        with self.assertRaises(ValueError):
            await adapter.get_listing_render(identity, currency=1, count=101)

        # Booleans masquerading as integers
        with self.assertRaises(ValueError):
            await adapter.get_listing_render(identity, currency=1, start=True)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            await adapter.get_listing_render(identity, currency=1, count=True)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            await adapter.get_listing_render(identity, currency=True)  # type: ignore[arg-type]


class TestSsrHydrationAdapter(unittest.IsolatedAsyncioTestCase):
    """Offline unit tests for SSR hydration script extraction and validation."""

    def test_extract_ssr_hydration_data_success(self) -> None:
        """Verify successful extraction of nested JSON from valve-ssr-data HTML script."""
        loader_json = load_fixture("grouped_ssr_data.json")
        outer_payload = {
            "route": "/market/listings/:appid/:name/:bucket?",
            "cdn": "cdn.fastly.steamstatic.com",
            "loaderData": [
                "{}",
                "{}",
                "true",
                loader_json,  # nested JSON string representation
            ],
        }
        html = f"""<!DOCTYPE html><html><head>
        <script id="valve-ssr-data" type="application/json">
        {json.dumps(outer_payload)}
        </script>
        </head><body><div id="root"></div></body></html>"""

        source_url = "https://steamcommunity.com/market/listings/730/G1807209A023004"
        context = extract_ssr_hydration_data(html, source_url)

        self.assertIsInstance(context, SsrListingContext)
        self.assertEqual(context.source_url, source_url)
        self.assertEqual(context.loader_data.appid, 730)
        self.assertEqual(len(context.loader_data.buckets), 5)
        # Check specific bucket contents
        ft_bucket = next(b for b in context.loader_data.buckets if b.localized_name == "AK-47 | Redline (Field-Tested)")
        self.assertEqual(ft_bucket.classid, "7993038091")
        self.assertEqual(ft_bucket.min_price_minor, 2488)

    def test_extract_ssr_fails_closed_on_missing_script(self) -> None:
        """Fail closed when valve-ssr-data script is absent from HTML."""
        html = "<!DOCTYPE html><html><head><title>No SSR Data</title></head><body></body></html>"
        with self.assertRaises(ValueError) as ctx:
            extract_ssr_hydration_data(html, "https://steamcommunity.com/market/listings/730/test")
        self.assertIn("Missing 'valve-ssr-data' hydration script", str(ctx.exception))

    def test_extract_ssr_fails_closed_on_duplicate_scripts(self) -> None:
        """Fail closed when duplicate valve-ssr-data script tags exist in HTML."""
        loader_json = load_fixture("grouped_ssr_data.json")
        outer = json.dumps({"loaderData": ["", "", "", loader_json]})
        html = f"""<html><head>
        <script id="valve-ssr-data" type="application/json">{outer}</script>
        <script id="valve-ssr-data" type="application/json">{outer}</script>
        </head></html>"""
        with self.assertRaises(ValueError) as ctx:
            extract_ssr_hydration_data(html, "https://steamcommunity.com/market/listings/730/test")
        self.assertIn("Duplicate 'valve-ssr-data' hydration scripts", str(ctx.exception))

    def test_extract_ssr_fails_closed_on_malformed_outer_json(self) -> None:
        """Fail closed when script content is not valid JSON."""
        html = '<html><head><script id="valve-ssr-data" type="application/json">{malformed json</script></head></html>'
        with self.assertRaises(ValueError) as ctx:
            extract_ssr_hydration_data(html, "https://steamcommunity.com/market/listings/730/test")
        self.assertIn("Malformed outer JSON", str(ctx.exception))

    def test_extract_ssr_fails_closed_on_insufficient_loader_data(self) -> None:
        """Fail closed when loaderData array has fewer than 4 elements."""
        outer = json.dumps({"loaderData": ["a", "b"]})
        html = f'<html><head><script id="valve-ssr-data" type="application/json">{outer}</script></head></html>'
        with self.assertRaises(ValueError) as ctx:
            extract_ssr_hydration_data(html, "https://steamcommunity.com/market/listings/730/test")
        self.assertIn("insufficient elements", str(ctx.exception))

    def test_extract_ssr_fails_closed_on_malformed_nested_json(self) -> None:
        """Fail closed when loaderData[3] is an unparseable JSON string."""
        outer = json.dumps({"loaderData": ["a", "b", "c", "{not valid json inside string"]})
        html = f'<html><head><script id="valve-ssr-data" type="application/json">{outer}</script></head></html>'
        with self.assertRaises(ValueError) as ctx:
            extract_ssr_hydration_data(html, "https://steamcommunity.com/market/listings/730/test")
        self.assertIn("Malformed nested JSON", str(ctx.exception))

    def test_extract_ssr_fails_closed_on_wrong_appid(self) -> None:
        """Fail closed when SSR appid does not match CS2 (730)."""
        data = json.loads(load_fixture("grouped_ssr_data.json"))
        data["appid"] = 440  # TF2
        outer = json.dumps({"loaderData": ["", "", "", json.dumps(data)]})
        html = f'<html><head><script id="valve-ssr-data" type="application/json">{outer}</script></head></html>'
        with self.assertRaises(ValueError) as ctx:
            extract_ssr_hydration_data(html, "https://steamcommunity.com/market/listings/730/test")
        self.assertIn("expected 730", str(ctx.exception))

    async def test_get_ssr_listing_context_trips_breaker_on_malformed_html(self) -> None:
        """Prove that a malformed SSR payload trips the circuit breaker and cannot pass as canary."""
        limiter = SequentialRateLimiter(config=RateLimiterConfig(min_delay_seconds=0.01))
        client = ReadOnlyHttpClient(
            rate_limiter=limiter,
            transport=httpx.MockTransport(lambda req: httpx.Response(200, text="<html><body>Missing script</body></html>")),
        )
        adapter = SteamMarketAdapter(client)

        with self.assertRaises(UnexpectedResponseError):
            await adapter.get_ssr_listing_context("https://steamcommunity.com/market/listings/730/G1807209A023004")

        # Breaker must be permanently halted by protocol violation
        self.assertTrue(limiter.is_halted)
        self.assertEqual(limiter.state, CircuitBreakerState.HALTED)

    async def test_ssr_context_resolves_wear_bucket_with_existing_resolver(self) -> None:
        """Prove extracted SsrListingContext resolves clean exterior identity using existing resolver."""
        loader_json = load_fixture("grouped_ssr_data.json")
        outer = json.dumps({"loaderData": ["", "", "", loader_json]})
        html = f'<html><head><script id="valve-ssr-data" type="application/json">{outer}</script></head></html>'

        client = ReadOnlyHttpClient(
            rate_limiter=SequentialRateLimiter(config=RateLimiterConfig(min_delay_seconds=0.01)),
            transport=httpx.MockTransport(lambda req: httpx.Response(200, text=html)),
        )
        adapter = SteamMarketAdapter(client)
        source_url = "https://steamcommunity.com/market/listings/730/G1807209A023004?category_Exterior=WearCategory2"
        context = await adapter.get_ssr_listing_context(source_url)

        # Pass context into existing identity resolver
        resolved = resolve_item_identity(source_url, ssr_data=context)
        self.assertEqual(resolved.resolution_status, ResolutionStatus.CANONICAL_RESOLVED)
        self.assertEqual(resolved.classid, "7993038091")
        self.assertEqual(resolved.exterior, WearCategory.FIELD_TESTED)
        self.assertEqual(resolved.market_hash_name, "AK-47 | Redline (Field-Tested)")


class TestSharedCircuitBreakerAcrossAdapters(unittest.IsolatedAsyncioTestCase):
    """Offline unit tests confirming all adapters share the same limiter and circuit breaker state."""

    async def test_429_on_one_adapter_blocks_all_adapters(self) -> None:
        """HTTP 429 encountered on one adapter opens breaker for all other adapter endpoints globally."""
        limiter = SequentialRateLimiter(config=RateLimiterConfig(initial_backoff_seconds=300.0, min_delay_seconds=0.01))

        # First request returns 429
        def handler(request: httpx.Request) -> httpx.Response:
            if "itemordershistogram" in str(request.url):
                return httpx.Response(429, headers={"Retry-After": "300"})
            return httpx.Response(200, json={"success": True})

        client = ReadOnlyHttpClient(rate_limiter=limiter, transport=httpx.MockTransport(handler))
        adapter = SteamMarketAdapter(client)
        identity = make_valid_resolved_identity()

        # Call orderbook histogram: triggers 429
        with self.assertRaises(RateLimitExceededError):
            await adapter.get_orderbook_histogram(identity)

        self.assertEqual(limiter.state, CircuitBreakerState.OPEN)
        self.assertTrue(limiter.is_cooling_down)

        # Now listing render adapter must be immediately blocked by the shared circuit breaker
        with self.assertRaises(RateLimitExceededError):
            await adapter.get_listing_render(identity, currency=1)

        # SSR adapter must also be immediately blocked
        with self.assertRaises(RateLimitExceededError):
            await adapter.get_ssr_listing_context(identity)

    async def test_403_on_one_adapter_halts_all_adapters_permanently(self) -> None:
        """HTTP 403 on one adapter permanently halts all adapters."""
        limiter = SequentialRateLimiter(config=RateLimiterConfig(min_delay_seconds=0.01))

        def handler(request: httpx.Request) -> httpx.Response:
            if "render" in str(request.url):
                return httpx.Response(403)
            return httpx.Response(200, json={"success": True})

        client = ReadOnlyHttpClient(rate_limiter=limiter, transport=httpx.MockTransport(handler))
        adapter = SteamMarketAdapter(client)
        identity = make_valid_resolved_identity()

        with self.assertRaises(SecurityBlockError):
            await adapter.get_listing_render(identity, currency=1)

        self.assertTrue(limiter.is_halted)
        self.assertEqual(limiter.state, CircuitBreakerState.HALTED)

        # Orderbook adapter is permanently halted
        with self.assertRaises(SecurityBlockError):
            await adapter.get_orderbook_histogram(identity)

        # SSR adapter is permanently halted
        with self.assertRaises(SecurityBlockError):
            await adapter.get_ssr_listing_context(identity)


if __name__ == "__main__":
    unittest.main()
