"""Steam Community Market adapters for read-only opportunity detection and pricing.

Endpoints implemented:
1. Deep order-book histogram:
   GET https://steamcommunity.com/market/itemordershistogram
2. Active listing render:
   GET https://steamcommunity.com/market/listings/730/{market_hash_name}/render/
3. SSR hydration data extractor:
   GET https://steamcommunity.com/market/listings/730/{market_hash_name or group_id}
   Extracts <script id="valve-ssr-data" type="application/json">
"""

from __future__ import annotations

from decimal import Decimal
from html.parser import HTMLParser
import json
import logging
from typing import Any
from urllib.parse import quote

import httpx

from sniper.adapters.listing_parser import (
    _SsrScriptExtractor,
    extract_public_price_history,
    extract_ssr_listing_render_response,
    extract_ssr_orderbook_snapshot,
    extract_ssr_search_results,
)
from sniper.core.currency import CurrencyConfig
from sniper.core.http_client import ReadOnlyHttpClient
from sniper.core.identity import (
    WEAR_CATEGORY_TO_PARAM,
    ItemIdentityResult,
    ResolutionStatus,
    SsrListingContext,
    is_trusted_mapping_source,
    parse_steam_market_url,
)
from sniper.models.schemas import (
    ListingRenderResponse,
    OrderbookHistogramResponse,
    SsrListingLoaderData,
)

logger = logging.getLogger("sniper.adapters.steam_market")


def extract_ssr_hydration_data(html_content: str, source_url: str) -> SsrListingContext:
    """Extract and validate SSR hydration payload from HTML string.
    
    Guarantees:
    - Extracts exactly one matching <script id="valve-ssr-data">.
    - Fails closed on missing or duplicate hydration scripts.
    - Parses outer JSON envelope, then unpacks nested loaderData[3].
    - Validates against SsrListingLoaderData schema.
    - Verifies appid == 730 (CS2).
    - Preserves exact source_url for exterior bucket pairing.
    """
    if not isinstance(html_content, str) or not html_content.strip():
        raise ValueError(f"Empty or non-string HTML content received from {source_url}.")

    extractor = _SsrScriptExtractor()
    extractor.feed(html_content)

    if len(extractor.matching_scripts) == 0:
        raise ValueError(f"Missing 'valve-ssr-data' hydration script in HTML payload from {source_url}.")
    if len(extractor.matching_scripts) > 1:
        raise ValueError(
            f"Duplicate 'valve-ssr-data' hydration scripts ({len(extractor.matching_scripts)}) "
            f"found in HTML payload from {source_url}."
        )

    script_text = extractor.matching_scripts[0].strip()
    if not script_text:
        raise ValueError(f"Empty 'valve-ssr-data' script tag content from {source_url}.")

    try:
        outer = json.loads(script_text, parse_float=Decimal)
    except Exception as e:
        raise ValueError(f"Malformed outer JSON in 'valve-ssr-data' from {source_url}: {e}") from e

    if not isinstance(outer, dict):
        raise ValueError(f"Outer SSR payload from {source_url} must be a JSON object, got {type(outer).__name__}.")

    loader_data_list = outer.get("loaderData")
    if not isinstance(loader_data_list, list):
        raise ValueError(f"Outer SSR payload from {source_url} missing 'loaderData' array.")
    if len(loader_data_list) < 4:
        raise ValueError(
            f"SSR 'loaderData' array from {source_url} has insufficient elements "
            f"({len(loader_data_list)}, expected >= 4)."
        )

    raw_bucket_data = loader_data_list[3]
    if isinstance(raw_bucket_data, str):
        try:
            parsed_bucket_data = json.loads(raw_bucket_data, parse_float=Decimal)
        except Exception as e:
            raise ValueError(f"Malformed nested JSON string in loaderData[3] from {source_url}: {e}") from e
    elif isinstance(raw_bucket_data, dict):
        parsed_bucket_data = raw_bucket_data
    else:
        raise ValueError(
            f"Unexpected loaderData[3] type from {source_url}: {type(raw_bucket_data).__name__}, "
            f"expected JSON string or dict."
        )

    try:
        loader_data = SsrListingLoaderData.model_validate(parsed_bucket_data)
    except Exception as e:
        raise ValueError(f"SSR loader data validation failed against SsrListingLoaderData for {source_url}: {e}") from e

    if loader_data.appid != 730:
        raise ValueError(f"Invalid SSR appid {loader_data.appid} from {source_url}, expected 730.")

    return SsrListingContext(source_url=source_url, loader_data=loader_data)


class SteamMarketAdapter:
    """Typed, read-only Steam Community Market adapter.
    
    Shares a single ReadOnlyHttpClient and its token-bucket rate limiter.
    """

    def __init__(self, http_client: ReadOnlyHttpClient) -> None:
        self.http_client = http_client

    async def get_orderbook_histogram(
        self,
        identity: ItemIdentityResult,
        currency: int = 1,
        country: str = "US",
        language: str = "english",
    ) -> OrderbookHistogramResponse:
        """Fetch deep order-book histogram for a resolved CS2 item identity.
        
        Endpoint: GET https://steamcommunity.com/market/itemordershistogram
        
        Requirements:
        - Accepts a resolved ItemIdentityResult, not an arbitrary caller-supplied ID.
        - Requires appid == 730.
        - Requires ResolutionStatus.ITEM_NAMEID_RESOLVED.
        - Requires item_nameid to be a positive integer (type(value) is int, value > 0).
        - Requires source and revision to match the pinned verified mapping.
        - Rejects missing, synthetic, legacy, unknown, or mismatched provenance.
        - Preserves distinction between top-level quantity and cumulative quantity.
        """
        if not isinstance(identity, ItemIdentityResult):
            raise TypeError(f"identity must be an ItemIdentityResult, got {type(identity).__name__}.")

        if identity.appid != 730:
            raise ValueError(f"Invalid appid {identity.appid}: market adapters only support CS2 (appid 730).")

        if identity.resolution_status != ResolutionStatus.ITEM_NAMEID_RESOLVED:
            raise ValueError(
                f"Cannot query order book for unresolved identity: resolution_status is "
                f"{identity.resolution_status.value}, expected ITEM_NAMEID_RESOLVED."
            )

        nameid = identity.item_nameid
        if type(nameid) is not int or nameid <= 0:
            raise ValueError(
                f"Cannot query order book: item_nameid must be a strictly positive integer, "
                f"got {type(nameid).__name__} ({nameid!r})."
            )

        if not is_trusted_mapping_source(identity.item_nameid_source, identity.item_nameid_revision):
            raise ValueError(
                f"Unverified item_nameid provenance for '{identity.market_hash_name}': "
                f"source={identity.item_nameid_source!r}, revision={identity.item_nameid_revision!r}. "
                f"Network request rejected before execution."
            )

        if type(currency) is not int or currency <= 0:
            raise ValueError(f"currency must be a strictly positive integer Steam currency ID, got {currency!r}.")

        if not country or not isinstance(country, str):
            raise ValueError("country parameter must be a non-empty string.")

        if not language or not isinstance(language, str):
            raise ValueError("language parameter must be a non-empty string.")

        params = {
            "country": country,
            "language": language,
            "currency": currency,
            "item_nameid": nameid,
            "two_factor": 0,
        }

        url = "https://steamcommunity.com/market/itemordershistogram"
        logger.debug(f"Querying orderbook histogram for '{identity.market_hash_name}' (nameid={nameid})")
        return await self.http_client.get_json(url, params=params, schema=OrderbookHistogramResponse)

    async def get_listing_render(
        self,
        identity: ItemIdentityResult,
        currency: int,
        start: int = 0,
        count: int = 100,
        query: str = "",
    ) -> ListingRenderResponse:
        """Fetch active listings page for a canonical CS2 item identity.
        
        Endpoint: GET https://steamcommunity.com/market/listings/730/{market_hash_name}/render/
        
        Requirements:
        - Accepts a canonical resolved identity and preserves exact market_hash_name.
        - Rejects incomplete PARSED_URL_ONLY identities.
        - Correctly URL-encodes market_hash_name in path.
        - Validates pagination boundaries (start >= 0, 0 < count <= 100).
        - Rejects booleans masquerading as integers.
        - Requires explicitly supplied valid Steam currency ID.
        """
        if not isinstance(identity, ItemIdentityResult):
            raise TypeError(f"identity must be an ItemIdentityResult, got {type(identity).__name__}.")

        if identity.appid != 730:
            raise ValueError(f"Invalid appid {identity.appid}: market adapters only support CS2 (appid 730).")

        if identity.resolution_status == ResolutionStatus.PARSED_URL_ONLY:
            raise ValueError("Cannot query listings render for PARSED_URL_ONLY identity. Canonical resolution required.")

        name = identity.market_hash_name
        if not name or not isinstance(name, str) or not name.strip():
            raise ValueError("Cannot query listings render with an empty or blank market_hash_name.")

        if type(start) is not int or start < 0:
            raise ValueError(f"start pagination offset must be a non-negative integer, got {type(start).__name__} ({start!r}).")

        if type(count) is not int or count <= 0 or count > 100:
            raise ValueError(
                f"count batch size must be an integer between 1 and 100, got {type(count).__name__} ({count!r})."
            )

        if type(currency) is not int or currency <= 0:
            raise ValueError(
                f"currency must be an explicitly supplied strictly positive Steam currency ID, got {currency!r}."
            )

        encoded_name = quote(name.strip(), safe="")
        url = f"https://steamcommunity.com/market/listings/730/{encoded_name}/render/"
        params = {
            "query": query,
            "start": start,
            "count": count,
            "currency": currency,
            "format": "json",
        }

        logger.debug(f"Querying listings render for '{name}' (start={start}, count={count}, currency={currency})")

        def parser_callback(response: httpx.Response) -> ListingRenderResponse:
            raw_text = response.text or ""
            stripped = raw_text.lstrip()
            if stripped.startswith(("{", "[")):
                data = json.loads(raw_text, parse_float=Decimal)
                return ListingRenderResponse.model_validate(data)
            ssr_render = extract_ssr_listing_render_response(raw_text)
            if ssr_render is not None:
                return ssr_render
            raise ValueError(
                f"Unexpected HTML/challenge response received from {response.url} when listings JSON/SSR was expected."
            )

        return await self.http_client.get_parsed(
            url,
            parser_callback=parser_callback,
            params=params,
            expect_json=False,
        )

    def _build_ssr_page_url(
        self,
        identity: ItemIdentityResult,
        source_url: str | None = None,
    ) -> str:
        """Select the wear-filtered SSR URL when group_id is known so Steam hydrates exact wear listings."""
        if source_url:
            try:
                parsed = parse_steam_market_url(source_url)
                if (
                    identity.group_id
                    and parsed.group_id == identity.group_id
                    and (identity.exterior is None or parsed.exterior == identity.exterior)
                ):
                    return source_url.strip()
            except Exception:
                pass
        if identity.group_id and identity.exterior is not None:
            wear_param = WEAR_CATEGORY_TO_PARAM.get(identity.exterior)
            if wear_param:
                q_suffix = "&category_Quality=tag_strange" if identity.is_stattrak else ""
                return f"https://steamcommunity.com/market/listings/730/{identity.group_id}?category_Exterior={wear_param}{q_suffix}&appid=730"
        if identity.group_id:
            return f"https://steamcommunity.com/market/listings/730/{identity.group_id}"
        encoded_name = quote(identity.market_hash_name.strip(), safe="")
        return f"https://steamcommunity.com/market/listings/730/{encoded_name}"

    async def search_market_ssr(
        self,
        query: str = "",
        currency: CurrencyConfig | None = None,
        start: int = 0,
    ) -> list[dict[str, Any]]:
        """Execute a strictly read-only CS2 Market search via Steam's SSR `/market/search` page."""
        if start < 0:
            raise ValueError("start must be >= 0.")
        params: dict[str, Any] = {"appid": 730, "q": query.strip()}
        if start > 0:
            params["start"] = start

        def parser_callback(response: httpx.Response) -> list[dict[str, Any]]:
            results = extract_ssr_search_results(response.text or "", currency=currency)
            if results is None:
                raise ValueError(
                    f"Missing valid SSR market_search cache in HTML response from {response.url}."
                )
            return results

        return await self.http_client.get_parsed(
            "https://steamcommunity.com/market/search",
            parser_callback=parser_callback,
            params=params,
            expect_json=False,
        )

    async def get_ssr_market_bundle(
        self,
        identity: ItemIdentityResult,
        currency: CurrencyConfig,
        source_url: str | None = None,
    ) -> tuple[ListingRenderResponse, list[tuple[str, int, int]], dict[str, Any] | None]:
        """Fetch Steam's 2026 SSR listing HTML and extract live listings, chart history, and orderbook top in 1 GET."""
        if not isinstance(identity, ItemIdentityResult):
            raise TypeError(f"identity must be an ItemIdentityResult, got {type(identity).__name__}.")
        if identity.appid != 730:
            raise ValueError(f"Invalid appid {identity.appid}: market adapters only support CS2 (appid 730).")
        if identity.resolution_status == ResolutionStatus.PARSED_URL_ONLY or not identity.market_hash_name:
            raise ValueError("Canonical resolution required to fetch SSR market page.")
        if not currency.is_metadata_verified:
            raise ValueError(f"Verified currency metadata required, got {currency.code}.")

        target_url = self._build_ssr_page_url(identity, source_url=source_url)

        def parser_callback(
            response: httpx.Response,
        ) -> tuple[ListingRenderResponse, list[tuple[str, int, int]], dict[str, Any] | None]:
            text = response.text or ""
            render = extract_ssr_listing_render_response(text)
            if render is None:
                raise ValueError(
                    f"Missing valid SSR market listing cache in HTML response from {response.url}."
                )
            hist = extract_public_price_history(
                text,
                currency,
                expected_market_hash_name=identity.market_hash_name,
            )
            orderbook_snap = extract_ssr_orderbook_snapshot(
                text,
                expected_market_hash_name=identity.market_hash_name,
                expected_currency_id=currency.currency_id,
            )
            return render, hist, orderbook_snap

        return await self.http_client.get_parsed(
            target_url,
            parser_callback=parser_callback,
            expect_json=False,
        )

    async def get_ssr_market_page(
        self,
        identity: ItemIdentityResult,
        currency: CurrencyConfig,
        source_url: str | None = None,
    ) -> tuple[ListingRenderResponse, list[tuple[str, int, int]]]:
        """Fetch Steam's 2026 SSR listing HTML and extract BOTH live listings and cookie-free history in 1 GET."""
        render, hist, _ = await self.get_ssr_market_bundle(
            identity=identity,
            currency=currency,
            source_url=source_url,
        )
        return render, hist

    async def get_ssr_listing_context(
        self,
        target: str | ItemIdentityResult,
    ) -> SsrListingContext:
        """Fetch a listing page and extract the verified SSR hydration context.
        
        Requirements:
        - Uses Steam listing URL parser and HTTP security allowlist boundaries.
        - Executes parsing and validation inside the serialized request lock.
        - Fails closed on missing or duplicate valve-ssr-data scripts.
        - Trips circuit breaker immediately if outer JSON, nested JSON, or loaderData is invalid.
        - Returns an SsrListingContext preserving source URL and wear buckets.
        """
        if isinstance(target, ItemIdentityResult):
            target_url = target.canonical_url or target.input_url
        elif isinstance(target, str):
            target_url = target.strip()
        else:
            raise TypeError(f"target must be a str URL or ItemIdentityResult, got {type(target).__name__}.")

        parsed = parse_steam_market_url(target_url)
        if parsed.appid != 730:
            raise ValueError(f"Invalid appid {parsed.appid}: SSR adapter only supports CS2 (appid 730).")

        logger.debug(f"Fetching SSR listing context for URL: {target_url}")

        def parser_callback(response: httpx.Response) -> SsrListingContext:
            source_url = str(response.url)
            return extract_ssr_hydration_data(response.text, source_url)

        return await self.http_client.get_parsed(
            target_url,
            parser_callback=parser_callback,
            expect_json=False,
        )

    async def get_public_price_history(
        self,
        identity: ItemIdentityResult,
        currency: CurrencyConfig,
    ) -> list[tuple[str, int, int]]:
        """Fetch cookie-free public listing page HTML and extract chart series if available.

        Never sends cookies or calls authenticated /market/pricehistory/.
        Returns [] cleanly if chart data is omitted on public unauthenticated pages.
        """
        if not isinstance(identity, ItemIdentityResult):
            raise TypeError(f"identity must be an ItemIdentityResult, got {type(identity).__name__}.")
        if identity.appid != 730:
            raise ValueError(f"Invalid appid {identity.appid}: only CS2 (appid 730) is supported.")
        if identity.resolution_status == ResolutionStatus.PARSED_URL_ONLY or not identity.market_hash_name:
            raise ValueError("Canonical resolution required to fetch public listing history.")
        if not currency.is_metadata_verified:
            raise ValueError(f"Verified currency metadata required, got {currency.code}.")

        target_url = self._build_ssr_page_url(identity)

        def parser_callback(response: httpx.Response) -> list[tuple[str, int, int]]:
            text = response.text
            if not isinstance(text, str) or "<html" not in text.lower() and "steamcommunity" not in text.lower():
                raise ValueError(f"Unexpected non-HTML response from {response.url}")
            return extract_public_price_history(
                text,
                currency,
                expected_market_hash_name=identity.market_hash_name,
            )

        return await self.http_client.get_parsed(
            target_url,
            parser_callback=parser_callback,
            expect_json=False,
        )
