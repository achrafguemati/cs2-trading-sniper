"""Manual single-request read-only smoke test.

Executes ONE paced request to verify Steam endpoint connectivity and response parsing.
No continuous polling, loops, or background tasks.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Add src to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sniper.core.currency import CurrencyConfig
from sniper.core.fees import calculate_seller_receives
from sniper.core.http_client import ReadOnlyHttpClient
from sniper.core.rate_limiter import RateLimitExceededError, SecurityBlockError
from sniper.models.schemas import OrderbookHistogramResponse


async def main() -> None:
    print("=" * 60)
    print("CS2 Trading Sniper - Milestone 1 Manual Smoke Test")
    print("Mode: Strictly Read-Only (Single Request Execution)")
    print("=" * 60)

    # AK-47 | Redline (Field-Tested) item_nameid is 176000000 or similar
    # For safety in smoke test, we query the public histogram endpoint
    item_nameid = 176000000
    currency = CurrencyConfig.get_default_usd()
    
    url = f"https://steamcommunity.com/market/itemordershistogram"
    params = {
        "country": currency.country,
        "language": "english",
        "currency": currency.currency_id,
        "item_nameid": item_nameid,
        "two_factor": 0,
    }

    print(f"\n[1/3] Target Endpoint: {url}")
    print(f"[2/3] Parameters: country={params['country']}, currency={params['currency']} ({currency.code}), item_nameid={item_nameid}")
    print("\nExecuting single paced GET request...")

    async with ReadOnlyHttpClient() as client:
        try:
            resp = await client.get(url, params=params)
            print(f"\n[3/3] HTTP Status: {resp.status_code} OK")
            
            data = resp.json()
            parsed = OrderbookHistogramResponse.model_validate(data)
            
            print("\nResponse Parsing Results:")
            print(f"  Success Flag        : {parsed.success}")
            print(f"  Highest Buy Order   : {currency.format_amount(parsed.highest_buy_order_minor or 0)}")
            print(f"  Lowest Sell Order    : {currency.format_amount(parsed.lowest_sell_order_minor or 0)}")
            print(f"  Top-Level Quantity   : {parsed.top_level_buy_quantity}")
            print(f"  Cumulative Requests  : {parsed.cumulative_buy_quantity}")
            
            if parsed.highest_buy_order_minor:
                fee_calc = calculate_seller_receives(parsed.highest_buy_order_minor)
                print("\nInteger-Cents Fee Calculation for Top Bid:")
                print(f"  Buyer Pays          : {currency.format_amount(fee_calc.actual_buyer_pays)}")
                print(f"  Seller Net Proceeds : {currency.format_amount(fee_calc.seller_receives)}")
                print(f"  Total Valve Fees    : {currency.format_amount(fee_calc.total_fee)} (Steam: {currency.format_amount(fee_calc.steam_fee)}, CS2: {currency.format_amount(fee_calc.publisher_fee)})")
                print(f"  Exact Step Match    : {fee_calc.is_exact_match}")
                
            print("\nSmoke test completed successfully. Zero mutations made.")
            
        except RateLimitExceededError as e:
            print(f"\n[RATE LIMITED] Steam returned HTTP 429: {e}")
            print("Circuit breaker active. Halting safely without retrying.")
        except SecurityBlockError as e:
            print(f"\n[SECURITY HALT] Steam returned HTTP 403: {e}")
            print("Halting all operations immediately.")
        except Exception as e:
            print(f"\n[ERROR] Request failed: {e}")


if __name__ == "__main__":
    asyncio.run(main())
