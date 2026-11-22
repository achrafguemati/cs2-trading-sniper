# Steam Community Market API Verification & Endpoint Technical Guide

**Document Version:** 1.0  
**Verification Date:** 22 November 2026  
**Status:** Independently Verified via Direct Network Probing, Response Inspection, and Schema Extraction  

---

## 1. Executive Summary

The Steam Community Market (SCM) does not provide a documented, public developer REST API for deep market data. Instead, the ecosystem relies on:
1. **Server-Side Rendered (SSR) HTML & Hydration Payloads** (`valve-ssr-data` JSON script tags introduced in the May 12, 2026 redesign).
2. **Frontend XHR / JSON Endpoints** (`/market/listings/.../render/` and `/market/itemordershistogram`).
3. **Public Community Inventory Endpoints** (`/inventory/{steamid64}/730/2`).

This document provides verified schemas, exact parameters, observed HTTP quirks (e.g., HEAD request 404s), and live-tested rate-limit characteristics.

---

## 2. The May 12, 2026 Redesign & SSR Architecture

### 2.1 Technical Discovery: Modern Hydration Payload
On May 12, 2026, Valve overhauled the Steam Community Market into a responsive, Server-Side Rendered (SSR) React frontend.
When a user or client issues an HTTP `GET` request to a market listing URL, Steam returns an HTML page containing an embedded JSON data payload:

```html
<script type="application/json" id="valve-ssr-data" nonce="...">
{
  "route": "/market/listings/:appid/:name/:bucket?",
  "cdn": "cdn.fastly.steamstatic.com",
  "loaderData": [
    "{...storeBrowseContext...}",
    "{...filterConfig, bNewMarket...}",
    "true",
    "{\"success\":true,\"appid\":730,\"buckets\":[...],\"relevantAssetProperties\":{...}}"
  ]
}
</script>
```

### 2.2 The Grouped Listing Model (`G...` Identifiers)
* **Standard Canonical URL:** `https://steamcommunity.com/market/listings/730/AK-47%20%7C%20Redline%20%28Field-Tested%29`
* **Observed Network Behavior:** Steam automatically issues an `HTTP 302 Moved Temporarily` redirect to:
  `https://steamcommunity.com/market/listings/730/G1807209A023004`
* **The `G...` Anatomy:**
  - `G1807209A023004` is a **Group Identifier** that aggregates all wear and quality conditions for a base weapon skin (e.g., AK-47 | Redline across Factory New, Minimal Wear, Field-Tested, Well-Worn, Battle-Scarred, and StatTrak variants).
  - The URL parameter `?category_Exterior=WearCategory2` dynamically selects the specific wear bucket (where `WearCategory2` = Field-Tested).
* **Observed `buckets` Schema in `loaderData[3]`:**
  ```json
  {
    "bucket_id": "AK-47 | Redline (Battle-Scarred)",
    "localized_name": "AK-47 | Redline (Battle-Scarred)",
    "min_price": "2488",
    "classid": "7993038091",
    "localized_name_inside_group": "Battle-Scarred",
    "strPrice": "$24.88",
    "filters": [
      ["Quality", "normal"],
      ["Exterior", "WearCategory4"]
    ]
  }
  ```
* **Price Unit:** `min_price` is an integer string representing price in minor currency units (e.g., `"2488"` = $24.88 USD).

---

## 3. Verified Endpoints & Schemas

### 3.1 Endpoint 1: Active Listings (`/market/listings/{appid}/{market_hash_name}/render/`)

* **HTTP Method:** `GET`
* **URL Structure:**
  `https://steamcommunity.com/market/listings/730/{market_hash_name}/render/?query=&start={start}&count={count}&currency={currency}&format=json`
* **Required Parameters:**
  - `market_hash_name`: URL-encoded item name (e.g., `AK-47%20%7C%20Redline%20%28Field-Tested%29`).
  - `start`: Pagination offset (integer, starting at 0).
  - `count`: Batch size (typically 10 to 100).
  - `currency`: Steam numeric currency ID (`1` = USD, `3` = EUR, etc.).
* **Verified Response Payload Shape:**
  ```json
  {
    "success": true,
    "start": 0,
    "pagesize": 10,
    "total_count": 254,
    "results_html": "<div>...table rows...</div>",
    "listinginfo": {
      "49827391028374": {
        "listingid": "49827391028374",
        "price": 2488,
        "fee": 324,
        "steam_fee": 108,
        "publisher_fee": 216,
        "converted_price": 2488,
        "converted_fee": 324,
        "converted_steam_fee": 108,
        "converted_publisher_fee": 216,
        "converted_price_per_unit": 2488,
        "converted_fee_per_unit": 324,
        "converted_steam_fee_per_unit": 108,
        "converted_publisher_fee_per_unit": 216,
        "asset": {
          "currency": 0,
          "appid": 730,
          "contextid": "2",
          "id": "38291049281"
        }
      }
    },
    "assets": {
      "730": {
        "2": {
          "38291049281": {
            "id": "38291049281",
            "classid": "7993038091",
            "instanceid": "188530138",
            "amount": "1",
            "name": "AK-47 | Redline (Field-Tested)",
            "market_hash_name": "AK-47 | Redline (Field-Tested)",
            "descriptions": [
              {"value": "Exterior: Field-Tested"},
              {"value": "Sticker: FaZe Clan | Austin 2025"}
            ],
            "market_actions": [
              {
                "link": "steam://rungame/730/76561202255233023/+csgo_econ_action_preview%20M%listingid%A%assetid%D5083003",
                "name": "Inspect in Game..."
              }
            ]
          }
        }
      }
    }
  }
  ```

---

### 3.2 Endpoint 2: Deep Order Book Histogram (`/market/itemordershistogram`)

* **HTTP Method:** `GET`
* **URL Structure:**
  `https://steamcommunity.com/market/itemordershistogram?country=US&language=english&currency=1&item_nameid={item_nameid}&two_factor=0`
* **Crucial Dependency (`item_nameid`):** This endpoint requires an internal numeric item ID and does NOT accept `market_hash_name`. The packaged offline mapping is the JSON object from `somespecialone/steam-market-ids`, pinned in M2.1 to revision `caac34bd8a28578e800501da5cc9f16c7cb3fa67` and verified by SHA-256. Missing mappings stay unresolved (`None`); do not invent or derive IDs.
* **Verified Response Payload Shape:**
  ```json
  {
    "success": 1,
    "sell_order_count": "1,240",
    "sell_order_price": "$24.88",
    "sell_order_table": "<table>...</table>",
    "buy_order_count": "211,731",
    "buy_order_price": "$22.50",
    "buy_order_table": "<table><tr><td>$22.50</td><td>1</td></tr><tr><td>$22.45</td><td>14</td></tr></table>",
    "highest_buy_order": "2250",
    "lowest_sell_order": "2488",
    "buy_order_graph": [
      [22.50, 1, "1 buy order at $22.50 or higher"],
      [22.45, 15, "15 buy orders at $22.45 or higher"],
      [20.00, 4520, "4,520 buy orders at $20.00 or higher"]
    ],
    "sell_order_graph": [
      [24.88, 1, "1 listing at $24.88 or lower"],
      [25.00, 18, "18 listings at $25.00 or lower"]
    ]
  }
  ```
* **Critical Semantic Distinction (Top-Level vs. Cumulative Quantity):**
  - In `buy_order_graph[0]`, the second element is `cumulative_quantity` = 1.
  - In `buy_order_graph[1]`, `cumulative_quantity` = 15. The incremental quantity at exactly $22.45 is $15 - 1 = 14$.
  - The UI and opportunity engine must parse both `top_level_quantity` ($1$) and `cumulative_quantity` ($211,731$).

---

### 3.3 Endpoint 3: Public CS2 Inventory

* **HTTP Method:** `GET`
* **URL Structure:**
  `https://steamcommunity.com/inventory/{steamid64}/730/2?l=english&count=5000`
* **Requirements:** The target Steam profile must have its inventory privacy set to "Public". No authentication, cookies, or API keys are required.
* **Verified Item Object Attributes:**
  - `assetid`: Unique inventory item instance ID.
  - `classid` / `instanceid`: Identifiers linking to item description metadata.
  - `marketable`: Integer (`1` if immediately marketable, `0` if restricted).
  - `tradable`: Integer (`1` if immediately tradable, `0` if trade-locked).
  - `market_marketable_restriction`: Integer days (e.g. `7`).
  - `market_buy_country_restriction`: ISO country code string if restricted.

---

## 4. Observed Server Quirks & Rate Limits

### 4.1 Steam Nginx Returns HTTP 404 on `HEAD` Requests
* **Observed Fact:** Probing `https://steamcommunity.com/market/` or any listing page with HTTP `HEAD` (e.g. `curl -I`) returns `HTTP/1.1 404 Not Found`.
* **Engineering Rule:** Never use HTTP `HEAD` to check URL health. All adapters must use HTTP `GET` with appropriate `Range` or accept headers.

### 4.2 Rate Limit Behavior (HTTP 429) Verified Live
* **Direct Test Result:** Direct unauthenticated requests from a single residential IP triggered `HTTP Error 429: Too Many Requests` after only **2 to 3 rapid requests within 5 seconds**.
* **Global Backoff Mandate:**
  - Every HTTP request must pass through an asynchronous Token Bucket / Leaky Bucket rate limiter.
  - Base request rate: **1 request every 4.0 to 6.0 seconds** (conservative local pacing target of ~10–15 RPM; this is an internal safety policy, not an official Valve quota or SLA).
  - Jitter: Add random delay of 0.5s to 2.0s per request.
  - Backoff algorithm on 429:
    1. Immediately freeze all market polling.
    2. Wait 300 seconds (5 minutes) before re-testing with a single canary ping.
    3. If 429 persists, double backoff (600s, 1200s, up to 3600s).
    4. Surface visible "Source Degraded / Rate Limited" warning on dashboard.
