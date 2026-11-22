"""Manual single-request read-only smoke test.

Executes a single paced GET request to verify Steam endpoint connectivity and response parsing.
Zero continuous loops, background workers, or state-mutating requests.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Add src to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sniper.core.currency import CurrencyConfig
from sniper.core.fees import calculate_seller_receives
from sniper.core.http_client import ReadOnlyHttpClient, UnexpectedResponseError
from sniper.core.rate_limiter import RateLimitExceededError, SecurityBlockError
from sniper.models.schemas import OrderbookHistogramResponse


async def main() -> None:
    print("=" * 65)
    print("CS2 Trading Sniper - Milestone 1.2 Read-Only Smoke Test")
    print("Mode: Single-Request Connectivity & Schema Validation")
    print("=" * 65)

    # Note: item_nameid 176000000 is used exclusively as a synthetic connectivity probe.
    # It is NOT an active market tracking target and item identity mapping is deferred to M2.
    probe_item_nameid = 176000000
    currency = CurrencyConfig.get_default_usd()
    
    url = "https://steamcommunity.com/market/itemordershistogram"
    params = {
        "country": currency.country,
        "language": "english",
        "currency": currency.currency_id,
        "item_nameid": probe_item_nameid,
        "two_factor": 0,
    }

    print(f"\n[Target] Endpoint: {url}")
    print(f"[Target] Params: country={params['country']}, currency={currency.code}, item_nameid={probe_item_nameid}")
    print("\nExecuting single sequential GET request...")

    async with ReadOnlyHttpClient() as client:
        try:
            resp = await client.get(url, params=params, expect_json=True)
            http_status = resp.status_code
            print(f"\n[1/3] Endpoint Connectivity : PASS (HTTP {http_status})")
            
            data = resp.json()
            parsed = OrderbookHistogramResponse.model_validate(data)
            print("[2/3] Schema Parsing        : PASS (OrderbookHistogramResponse validated)")
            
            # Item identity verification status
            if parsed.success == 1:
                print(f"      Steam Payload Success : 1 (Active orderbook detected for ID {probe_item_nameid})")
                print(f"      Highest Buy Bid       : {currency.format_amount(parsed.highest_buy_order_minor or 0)}")
                print(f"      Lowest Sell Ask       : {currency.format_amount(parsed.lowest_sell_order_minor or 0)}")
                print(f"      Top Buy Quantity      : {parsed.top_level_buy_quantity}")
                if parsed.highest_buy_order_minor:
                    fees = calculate_seller_receives(parsed.highest_buy_order_minor)
                    print(f"      Top Bid Seller Net    : {currency.format_amount(fees.seller_receives)} (exact: {fees.is_exact_match})")
            else:
                print(f"      Steam Payload Success : {parsed.success} (Item orders not found or inactive)")

            print("[3/3] Item Identity         : UNVERIFIED (ID 176000000 is a raw probe; resolver deferred to M2)")
            print("\nSmoke test passed safely without state mutations.")

        except RateLimitExceededError as e:
            print(f"\n[RATE LIMIT TRIGGERED] Steam returned HTTP 429: {e}")
            print("Circuit breaker active. Halting safely.")
        except SecurityBlockError as e:
            print(f"\n[SECURITY HALT] Circuit breaker tripped on HTTP 403: {e}")
            print("Immediate halt triggered.")
        except UnexpectedResponseError as e:
            print(f"\n[UNEXPECTED RESPONSE] Malformed or HTML content: {e}")
        except Exception as e:
            print(f"\n[REQUEST FAILURE] {e.__class__.__name__}: {e}")


if __name__ == "__main__":
    asyncio.run(main())
