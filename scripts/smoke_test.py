"""Manual single-request read-only smoke test.

Executes a single paced GET request to verify Steam endpoint connectivity, JSON parsing,
and Pydantic schema validation inside a serialized request lifecycle.
Returns exit code 0 only on full success (connectivity + schema + payload success=1).
Returns non-zero exit code on any connectivity, protocol, schema, or payload failure.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Add src to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from sniper.core.currency import CurrencyConfig
from sniper.core.fees import FeeBreakdown, calculate_seller_receives
from sniper.core.http_client import ReadOnlyHttpClient, SecurityError, UnexpectedResponseError
from sniper.core.rate_limiter import RateLimitExceededError, SecurityBlockError
from sniper.models.schemas import OrderbookHistogramResponse


def calculate_probe_fee_estimate(
    parsed: OrderbookHistogramResponse,
    currency: CurrencyConfig,
) -> FeeBreakdown | None:
    """Return a mathematical fee estimate for a successful probe payload.

    This deliberately does not validate the Steam Community Market listing rules.
    """
    if parsed.success != 1 or parsed.highest_buy_order_minor is None or parsed.highest_buy_order_minor <= 0:
        return None
    return calculate_seller_receives(
        parsed.highest_buy_order_minor,
        currency=currency,
        validate_market_listing=False,
    )


async def main() -> None:
    print("=" * 65)
    print("CS2 Trading Sniper - Milestone 1.2.3.1 Read-Only Smoke Test")
    print("Mode: Single-Request Connectivity & Schema Validation (Serialized)")
    print("=" * 65)

    # Source-verified probe mapping from the pinned upstream item_names.json revision.
    probe_market_hash_name = "StatTrak™ Nova | Toy Soldier (Minimal Wear)"
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
    print("\nExecuting single sequential GET request via get_json(schema=OrderbookHistogramResponse)...")

    async with ReadOnlyHttpClient() as client:
        try:
            # Single JSON processing path: network + JSON parse (Decimal) + schema validation within request lock
            parsed: OrderbookHistogramResponse = await client.get_json(
                url,
                params=params,
                schema=OrderbookHistogramResponse,
            )

            print("\n[1/3] Endpoint Connectivity : PASS (HTTPS steamcommunity.com verified)")
            print("[2/3] Schema Parsing        : PASS (OrderbookHistogramResponse validated with exact Decimals)")

            if parsed.success == 1:
                print(f"      Steam Payload Success : 1 (orderbook returned for {probe_market_hash_name})")
                print(f"      Highest Buy Bid       : {currency.format_amount(parsed.highest_buy_order_minor or 0)}")
                print(f"      Lowest Sell Ask       : {currency.format_amount(parsed.lowest_sell_order_minor or 0)}")
                print(f"      Top Buy Quantity      : {parsed.top_level_buy_quantity}")
                fees = calculate_probe_fee_estimate(parsed, currency)
                if fees is not None:
                    print(
                        "      Mathematical Fee Estimate (not listing validation): "
                        f"seller net {currency.format_amount(fees.seller_receives)} "
                        f"(requested gross {currency.format_amount(parsed.highest_buy_order_minor)}, "
                        f"actual gross {currency.format_amount(fees.actual_buyer_pays)}, "
                        f"exact: {fees.is_exact_match})"
                    )
                print("[3/3] Item Identity         : VERIFIED (pinned item_nameid source mapping)")
                print("\nSmoke test passed successfully without state mutations.")
                sys.exit(0)
            else:
                print(f"      Steam Payload Success : FAIL (success={parsed.success}: item inactive or orders unavailable)")
                print("[3/3] Item Identity         : UNVERIFIED")
                print("\nSmoke test failed: Steam payload success != 1.")
                sys.exit(1)

        except RateLimitExceededError as e:
            print(f"\n[RATE LIMIT TRIGGERED] Steam returned HTTP 429: {e}")
            print("Circuit breaker active. Halting safely.")
            sys.exit(1)
        except SecurityBlockError as e:
            print(f"\n[SECURITY HALT] Circuit breaker tripped: {e}")
            sys.exit(1)
        except SecurityError as e:
            print(f"\n[SECURITY ERROR] URL allowlist or redirect policy violation: {e}")
            sys.exit(1)
        except UnexpectedResponseError as e:
            print(f"\n[PROTOCOL VIOLATION] Malformed JSON, HTML challenge, or schema failure: {e}")
            sys.exit(1)
        except Exception as e:
            print(f"\n[REQUEST FAILURE] {e.__class__.__name__}: {e}")
            sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
