"""Strictly read-only external public CS2 market reference feed adapters (Skinport + CS.Market).

Enforces strict separation from authenticated/Steam transport:
- Host allowlist limited strictly to `api.skinport.com` and `market.csgo.com`.
- Read-only HTTPS GET requests only; zero credentials, cookies, or proxy rotation.
- Exact `Decimal` -> integer cents parsing (never binary floats in pricing math).
- Cached in memory (default 900s TTL) so catalog & sticker lookups spend zero Steam requests.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import logging
import time
from typing import Any
from urllib.parse import quote, urlparse

import httpx

from sniper.core.fees import calculate_seller_receives
from sniper.core.currency import CurrencyConfig
from sniper.core.identity import ItemNameIdDatabase, decompose_market_hash_name

logger = logging.getLogger("sniper.adapters.external_markets")

SKINPORT_ITEMS_URL = "https://api.skinport.com/v1/items"
CSMARKET_PRICES_URL = "https://market.csgo.com/api/v2/prices/USD.json"
ALLOWED_EXTERNAL_HOSTS = frozenset({"api.skinport.com", "market.csgo.com"})


def _parse_usd_to_cents(raw_val: Any) -> int | None:
    """Parse a positive JSON numeric or string USD amount strictly via Decimal into integer cents."""
    if raw_val is None or type(raw_val) is bool:
        return None
    try:
        dec = Decimal(str(raw_val).strip())
    except (InvalidOperation, ValueError):
        return None
    if not dec.is_finite() or dec <= 0:
        return None
    cents = int((dec * Decimal(100)).to_integral_value(rounding=ROUND_HALF_UP))
    return cents if cents > 0 else None


def _validate_external_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_EXTERNAL_HOSTS:
        raise PermissionError(f"Blocked unallowlisted external host: {url}")


class ExternalMarketFeedAdapter:
    """Cached strictly read-only public CS2 reference feeds for cross-market spreads & sticker pricing."""

    def __init__(
        self,
        *,
        cache_ttl_seconds: float = 900.0,
        timeout_seconds: float = 25.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._cache_ttl = max(60.0, float(cache_ttl_seconds))
        self._timeout = float(timeout_seconds)
        self._transport = transport
        self._lock = asyncio.Lock()
        self._skinport_cache: dict[str, dict[str, Any]] = {}
        self._skinport_ts: float = 0.0
        self._csmarket_cache: dict[str, int] = {}
        self._csmarket_ts: float = 0.0

    def _build_client(self) -> httpx.AsyncClient:
        kwargs: dict[str, Any] = {
            "timeout": httpx.Timeout(self._timeout),
            "follow_redirects": False,
        }
        if self._transport is not None:
            kwargs["transport"] = self._transport
        return httpx.AsyncClient(**kwargs)

    async def get_skinport_index(self, force_refresh: bool = False) -> dict[str, dict[str, Any]]:
        """Fetch Skinport's public read-only `/v1/items` CS2 catalog (37k+ items) in USD cents."""
        now = time.monotonic()
        if not force_refresh and self._skinport_cache and (now - self._skinport_ts) < self._cache_ttl:
            return self._skinport_cache

        async with self._lock:
            now = time.monotonic()
            if not force_refresh and self._skinport_cache and (now - self._skinport_ts) < self._cache_ttl:
                return self._skinport_cache

            _validate_external_url(SKINPORT_ITEMS_URL)
            headers = {
                "Accept-Encoding": "br",
                "User-Agent": "CS2-Trading-Sniper/0.1 (Read-Only Research)",
            }
            try:
                async with self._build_client() as client:
                    resp = await client.get(
                        SKINPORT_ITEMS_URL,
                        params={"app_id": 730, "currency": "USD"},
                        headers=headers,
                    )
                if resp.status_code != 200:
                    logger.warning("Skinport feed returned HTTP %d", resp.status_code)
                    return self._skinport_cache
                payload = resp.json()
                if not isinstance(payload, list):
                    return self._skinport_cache
            except Exception as exc:
                logger.warning("Skinport public feed unavailable (degrading gracefully): %s", exc)
                return self._skinport_cache

            parsed: dict[str, dict[str, Any]] = {}
            for row in payload:
                if not isinstance(row, dict):
                    continue
                name = str(row.get("market_hash_name") or "").strip()
                if not name:
                    continue
                min_minor = _parse_usd_to_cents(row.get("min_price"))
                sug_minor = _parse_usd_to_cents(row.get("suggested_price"))
                med_minor = _parse_usd_to_cents(row.get("median_price"))
                if min_minor is None and sug_minor is None:
                    continue
                qty_raw = row.get("quantity")
                qty = int(qty_raw) if isinstance(qty_raw, int) and qty_raw >= 0 else 0
                parsed[name] = {
                    "market_hash_name": name,
                    "min_price_minor": min_minor,
                    "suggested_price_minor": sug_minor,
                    "median_price_minor": med_minor,
                    "quantity": qty,
                    "item_page": str(row.get("item_page") or "").strip() or None,
                    "market_page": str(row.get("market_page") or "").strip() or None,
                }

            if parsed:
                self._skinport_cache = parsed
                self._skinport_ts = time.monotonic()
            return self._skinport_cache

    async def get_csmarket_prices(self, force_refresh: bool = False) -> dict[str, int]:
        """Fetch CS.Market's public read-only `/api/v2/prices/USD.json` CS2 price map in USD cents."""
        now = time.monotonic()
        if not force_refresh and self._csmarket_cache and (now - self._csmarket_ts) < self._cache_ttl:
            return self._csmarket_cache

        async with self._lock:
            now = time.monotonic()
            if not force_refresh and self._csmarket_cache and (now - self._csmarket_ts) < self._cache_ttl:
                return self._csmarket_cache

            _validate_external_url(CSMARKET_PRICES_URL)
            try:
                async with self._build_client() as client:
                    resp = await client.get(
                        CSMARKET_PRICES_URL,
                        headers={"User-Agent": "CS2-Trading-Sniper/0.1 (Read-Only Research)"},
                    )
                if resp.status_code != 200:
                    logger.warning("CS.Market feed returned HTTP %d", resp.status_code)
                    return self._csmarket_cache
                payload = resp.json()
                raw_items = payload.get("items") if isinstance(payload, dict) else None
                if not isinstance(raw_items, list):
                    return self._csmarket_cache
            except Exception as exc:
                logger.warning("CS.Market public feed unavailable (degrading gracefully): %s", exc)
                return self._csmarket_cache

            parsed: dict[str, int] = {}
            for row in raw_items:
                if not isinstance(row, dict):
                    continue
                name = str(row.get("market_hash_name") or "").strip()
                price_minor = _parse_usd_to_cents(row.get("price"))
                if name and price_minor is not None:
                    parsed[name] = price_minor

            if parsed:
                self._csmarket_cache = parsed
                self._csmarket_ts = time.monotonic()
            return self._csmarket_cache

    async def get_merged_reference_index(self) -> dict[str, dict[str, Any]]:
        """Return merged Skinport + CS.Market reference index keyed by exact market_hash_name."""
        sp_task = asyncio.create_task(self.get_skinport_index())
        cm_task = asyncio.create_task(self.get_csmarket_prices())
        sp_map, cm_map = await asyncio.gather(sp_task, cm_task)

        merged: dict[str, dict[str, Any]] = {}
        for name in set(sp_map.keys()) | set(cm_map.keys()):
            sp = sp_map.get(name)
            cm_minor = cm_map.get(name)
            merged[name] = {
                "market_hash_name": name,
                "skinport_min_minor": sp.get("min_price_minor") if sp else None,
                "skinport_suggested_minor": sp.get("suggested_price_minor") if sp else None,
                "skinport_median_minor": sp.get("median_price_minor") if sp else None,
                "skinport_quantity": sp.get("quantity", 0) if sp else 0,
                "skinport_url": sp.get("item_page") if sp else None,
                "csmarket_min_minor": cm_minor,
            }
        return merged

    async def find_cross_market_candidates(
        self,
        *,
        currency_cfg: CurrencyConfig,
        nameid_db: ItemNameIdDatabase | None = None,
        query: str = "",
        category: str | None = None,
        exterior: str | None = None,
        min_price_minor: int = 300,
        max_price_minor: int | None = None,
        min_supply: int = 5,
        min_roi_pct: float = 5.0,
        only_verified_nameid: bool = False,
        limit: int = 35,
    ) -> list[dict[str, Any]]:
        """Rank best cross-market Cash -> Steam Wallet spread candidates in 0 Steam requests."""
        merged = await self.get_merged_reference_index()
        tokens = [t.strip().lower() for t in (query or "").split() if t.strip()]
        fee_floor = 1 + currency_cfg.min_fee_steam_minor + currency_cfg.min_fee_publisher_minor
        cat = (category or "").strip().lower()
        ext_filter = (exterior or "").strip().lower()

        candidates: list[dict[str, Any]] = []
        for name, ref in merged.items():
            # Souvenirs & Music Kits can suffer illiquidity spikes on third-party feeds unless explicitly searched
            if not tokens and name.startswith("Souvenir "):
                continue
            low_name = name.lower()
            if tokens and not all(tok in low_name for tok in tokens):
                continue

            _, ext_enum, is_st, is_souv = decompose_market_hash_name(name)
            if ext_filter and (ext_enum is None or ext_enum.value.lower() != ext_filter):
                continue

            if cat == "knives" and (not name.startswith("★ ") or "gloves" in low_name or "wraps" in low_name):
                continue
            elif cat == "gloves" and ("gloves" not in low_name and "wraps" not in low_name):
                continue
            elif cat == "cases" and not (low_name.endswith("case") or low_name.endswith("capsule")):
                continue
            elif cat == "stickers" and not name.startswith("Sticker | "):
                continue
            elif cat == "rifles" and not name.startswith(("AK-47 | ", "M4A1-S | ", "M4A4 | ", "AWP | ", "FAMAS | ", "Galil AR | ", "AUG | ", "SG 553 | ", "SSG 08 | ")):
                continue
            elif cat == "pistols" and not name.startswith(("USP-S | ", "Glock-18 | ", "Desert Eagle | ", "P250 | ", "Five-SeveN | ", "Tec-9 | ", "CZ75-Auto | ", "Dual Berettas | ")):
                continue

            sp_min = ref.get("skinport_min_minor")
            sp_sug = ref.get("skinport_suggested_minor")
            sp_qty = int(ref.get("skinport_quantity") or 0)
            cm_min = ref.get("csmarket_min_minor")

            if sp_min is None or sp_sug is None or sp_qty < min_supply:
                continue
            if sp_min < max(fee_floor, min_price_minor):
                continue
            if max_price_minor is not None and sp_min > max_price_minor:
                continue
            if sp_sug < fee_floor:
                continue

            mapping = nameid_db.lookup_verified_mapping(name) if nameid_db is not None else None
            if only_verified_nameid and mapping is None:
                continue

            # Guard against stale / outlier suggested_price spikes (> 3x min price usually indicates souvenir/anomaly)
            if sp_sug > sp_min * 3:
                continue

            steam_net = calculate_seller_receives(
                sp_sug,
                currency=currency_cfg,
                validate_market_listing=False,
            ).seller_receives
            est_profit = steam_net - sp_min
            est_roi_pct = round((float(est_profit) / float(sp_min)) * 100.0, 2)
            if est_roi_pct < min_roi_pct:
                continue

            # Score rewards high net spread + deeper Skinport liquidity without over-weighting tiny $0.10 flips
            profit_bonus = min(40.0, float(est_profit) / 100.0 * 3.0)
            liq_bonus = min(20.0, float(sp_qty) * 0.4)
            cs_conf_bonus = 8.0 if (cm_min is not None and cm_min <= sp_sug) else 0.0
            score = round(min(100.0, max(0.0, est_roi_pct * 1.5 + profit_bonus + liq_bonus + cs_conf_bonus)), 1)

            candidates.append({
                "market_hash_name": name,
                "exterior": ext_enum.value if ext_enum else None,
                "is_stattrak": is_st,
                "is_souvenir": is_souv,
                "item_nameid": int(mapping["item_nameid"]) if mapping else None,
                "has_trusted_nameid": mapping is not None,
                "currency_code": currency_cfg.code,
                "currency_symbol": currency_cfg.symbol,
                "skinport_min_minor": sp_min,
                "skinport_suggested_minor": sp_sug,
                "skinport_quantity": sp_qty,
                "skinport_url": ref.get("skinport_url"),
                "csmarket_min_minor": cm_min,
                "est_steam_net_minor": steam_net,
                "est_wallet_profit_minor": est_profit,
                "est_wallet_roi_pct": est_roi_pct,
                "rank_score": score,
                "steam_listing_url": f"https://steamcommunity.com/market/listings/730/{quote(name, safe='')}",
            })

        candidates.sort(
            key=lambda x: (x["rank_score"], x["est_wallet_profit_minor"]),
            reverse=True,
        )
        return candidates[: max(1, min(100, limit))]
