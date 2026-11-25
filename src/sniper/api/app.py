"""FastAPI local backend service for CS2 Trading Sniper dashboard (Milestone 8)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
from typing import Any
from urllib.parse import quote

from dotenv import load_dotenv
from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from sniper.adapters.inventory import ManualInventoryImporter, PublicInventoryClient
from sniper.adapters.steam_market import SteamMarketAdapter
from sniper.api.schemas import (
    AppSettingsUpdateRequest,
    BreakerResetRequest,
    DiscoverScreenRequest,
    DiscoverSteamSearchRequest,
    HistoryImportRequest,
    HoldingCreateRequest,
    HoldingSellRequest,
    ManualInventoryImportRequest,
    NotificationTestRequest,
    SellScanRequest,
    SteamInventoryImportRequest,
    WatchlistCreateRequest,
    WatchlistToggleRequest,
)
from sniper.core.http_client import ReadOnlyHttpClient
from sniper.core.identity import (
    ItemNameIdDatabase,
    ResolutionStatus,
    decompose_market_hash_name,
    is_trusted_mapping_source,
)
from sniper.core.rate_limiter import RateLimiterConfig, SequentialRateLimiter
from sniper.engine.buy_engine import (
    BuyEvaluationConfig,
    BuySniperEngine,
    import_manual_price_history_json,
)
from sniper.engine.discovery import (
    build_grouped_inventory_views,
    compute_target_buy_price_minor,
    get_presets_with_metadata,
    load_currency_config,
    screen_market_items,
)
from sniper.engine.portfolio_service import PortfolioService
from sniper.engine.scheduler import SchedulerConfig, SniperScheduler
from sniper.engine.sell_engine import SellEvaluationConfig, SellSniperEngine
from sniper.notifications import (
    AlertPayload,
    DiscordNotifier,
    NotificationDispatcher,
    TelegramNotifier,
)
from sniper.storage.database import Database
from sniper.storage.models import ItemRecord, WatchlistEntryRecord
from sniper.storage.repositories import (
    AlertRepository,
    BuyEvaluationRepository,
    CurrencyRepository,
    HoldingRepository,
    InventoryRepository,
    ItemRepository,
    SettingsRepository,
    SourceHealthRepository,
    StrategyAlertRepository,
    WatchlistRepository,
)
from sniper.storage.service import ItemIdentityService, load_default_nameid_db

logger = logging.getLogger("sniper.api")


DEFAULT_SETTINGS: dict[str, Any] = {
    "default_currency_id": 1,
    "requests_per_minute": 12.0,
    "cycle_interval_seconds": 300.0,
    "min_roi_pct": 5.0,
    "min_sell_multiplier": 1.5,
    "min_bid_minor": 100,
    "cooldown_seconds": 1800,
    "scheduler_enabled": True,
}


@dataclass
class AppContainer:
    """Explicit runtime dependency container wired into FastAPI app state."""
    db: Database
    nameid_db: ItemNameIdDatabase | None
    identity_service: ItemIdentityService
    scheduler: SniperScheduler
    portfolio_service: PortfolioService
    owns_db: bool = True

    @property
    def rate_limiter(self) -> SequentialRateLimiter:
        return self.scheduler.rate_limiter

    @property
    def http_client(self) -> ReadOnlyHttpClient:
        return self.scheduler.http_client

    @property
    def market_adapter(self) -> SteamMarketAdapter:
        return self.scheduler.adapter


def _read_app_settings(db: Database) -> dict[str, Any]:
    repo = SettingsRepository(db)
    stored = repo.get_all()
    merged = DEFAULT_SETTINGS.copy()
    for k, raw in stored.items():
        if k in merged:
            try:
                merged[k] = json.loads(raw)
            except Exception:
                pass
    return merged


def _build_container(
    db: Database | None = None,
    db_path: str | Path = "data/sniper.db",
    http_client: ReadOnlyHttpClient | None = None,
    nameid_db: ItemNameIdDatabase | None = None,
    scheduler_enabled: bool = False,
) -> AppContainer:
    owns_db = db is None
    if db is None:
        p = Path(db_path)
        if str(db_path) != ":memory:":
            p.parent.mkdir(parents=True, exist_ok=True)
        db = Database(str(db_path))

    db.initialize()
    CurrencyRepository(db).seed_default_currencies()

    if nameid_db is None:
        if db.is_memory:
            nameid_db = load_default_nameid_db(":memory:")
        else:
            nid_path = Path(db.db_path).parent / "item_nameids.db"
            nameid_db = load_default_nameid_db(nid_path)

    identity_service = ItemIdentityService(db, nameid_db=nameid_db)
    saved_settings = _read_app_settings(db)
    sched_cfg = SchedulerConfig(
        currency_id=int(saved_settings["default_currency_id"]),
        cycle_interval_seconds=max(5.0, float(saved_settings["cycle_interval_seconds"])),
        max_requests_per_minute=min(12.0, float(saved_settings["requests_per_minute"])),
    )
    if http_client is None:
        limiter = SequentialRateLimiter(
            config=RateLimiterConfig(requests_per_minute=sched_cfg.max_requests_per_minute)
        )
        http_client = ReadOnlyHttpClient(rate_limiter=limiter)

    scheduler = SniperScheduler(db=db, http_client=http_client, config=sched_cfg)
    scheduler.buy_engine._identity_service = identity_service
    scheduler.sell_engine._identity_service = identity_service
    portfolio_service = PortfolioService(db=db, dispatcher=scheduler.dispatcher)

    return AppContainer(
        db=db,
        nameid_db=nameid_db,
        identity_service=identity_service,
        scheduler=scheduler,
        portfolio_service=portfolio_service,
        owns_db=owns_db,
    )


def create_app(
    db: Database | None = None,
    db_path: str | Path | None = None,
    http_client: ReadOnlyHttpClient | None = None,
    nameid_db: ItemNameIdDatabase | None = None,
    enable_scheduler: bool = False,
    frontend_dist: str | Path | None = None,
) -> FastAPI:
    """Construct the FastAPI application serving REST API endpoints & built React SPA."""
    target_db_path = db_path or os.getenv("SNIPER_DB_PATH", "data/sniper.db")
    container = _build_container(
        db=db,
        db_path=target_db_path,
        http_client=http_client,
        nameid_db=nameid_db,
        scheduler_enabled=enable_scheduler,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if enable_scheduler:
            await container.scheduler.start_background_loop()
        try:
            yield
        finally:
            await container.scheduler.stop()
            if container.nameid_db is not None:
                try:
                    container.nameid_db.close()
                except Exception:
                    pass
            if container.owns_db:
                container.db.close()

    app = FastAPI(
        title="CS2 Trading Sniper API",
        version="0.1.0",
        description="Strictly read-only CS2 Steam Community Market research & opportunity dashboard API.",
        lifespan=lifespan,
    )
    app.state.container = container

    # Local dev Vite server CORS support (localhost / 127.0.0.1 only)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:5173",
            "http://localhost:5173",
            "http://127.0.0.1:8000",
            "http://localhost:8000",
        ],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["*"],
    )

    def _ctx() -> AppContainer:
        return app.state.container

    # -------------------------------------------------------------------------
    # Health & Telemetry
    # -------------------------------------------------------------------------

    @app.get("/api/health")
    def get_health() -> dict[str, Any]:
        c = _ctx()
        lim = c.rate_limiter
        return {
            "status": "ok",
            "read_only": True,
            "schema_version": c.db.get_version(),
            "breaker_state": lim.state.value,
            "is_halted": lim.is_halted,
            "halt_reason": lim.halt_reason,
            "is_cooling_down": lim.is_cooling_down,
            "cooldown_remaining_seconds": round(lim.cooldown_seconds_remaining, 1),
            "consecutive_429_count": lim.consecutive_429_count,
            "available_tokens": round(lim.available_tokens, 3),
            "rpm_ceiling": lim.config.requests_per_minute,
            "nameid_mappings_loaded": bool(c.nameid_db and c.nameid_db.has_verified_metadata()),
            "last_cycle": c.scheduler.last_cycle.__dict__ if c.scheduler.last_cycle else None,
        }

    @app.get("/api/overview")
    def get_overview(currency_id: int = Query(1, ge=1)) -> dict[str, Any]:
        c = _ctx()
        wl_repo = WatchlistRepository(c.db)
        buy_repo = BuyEvaluationRepository(c.db)
        alert_repo = AlertRepository(c.db)
        strat_repo = StrategyAlertRepository(c.db)
        inv_repo = InventoryRepository(c.db)

        active_wl = wl_repo.list_entries(active_only=True)
        buy_evals = buy_repo.list_recent(currency_id=currency_id, limit=200)
        buy_signals = [e for e in buy_evals if e.is_actionable and e.listing_status == "active"]
        sell_alerts = alert_repo.get_recent_alerts(limit=100)
        sell_signals = [a for a in sell_alerts if a.status in ("Candidate", "High-confidence signal")]
        strat_alerts = strat_repo.list_recent_alerts(limit=50)
        latest_snap = inv_repo.get_latest_snapshot()
        port_overview = c.portfolio_service.get_overview(currency_id=currency_id)

        lim = c.rate_limiter
        return {
            "health": {
                "breaker_state": lim.state.value,
                "is_halted": lim.is_halted,
                "halt_reason": lim.halt_reason,
                "is_cooling_down": lim.is_cooling_down,
                "cooldown_remaining_seconds": round(lim.cooldown_seconds_remaining, 1),
                "rpm_ceiling": lim.config.requests_per_minute,
                "last_cycle": c.scheduler.last_cycle.__dict__ if c.scheduler.last_cycle else None,
            },
            "kpis": {
                "active_watchlists": len(active_wl),
                "active_buy_signals": len(buy_signals),
                "recent_sell_signals": len(sell_signals),
                "total_alerts": len(sell_alerts) + len(strat_alerts),
                "inventory_items": latest_snap.total_count if latest_snap else 0,
                "last_inventory_sync": latest_snap.observed_at if latest_snap else None,
            },
            "portfolio": port_overview.model_dump(),
            "wallet_notice": "Steam Wallet funds are closed-loop virtual balances and are never combined with withdrawable cash.",
        }

    # -------------------------------------------------------------------------
    # Watchlists & Cookie-Free History Seeding
    # -------------------------------------------------------------------------

    @app.get("/api/watchlists")
    def list_watchlists() -> dict[str, Any]:
        c = _ctx()
        entries = WatchlistRepository(c.db).list_entries()
        item_repo = ItemRepository(c.db)
        enriched = []
        for e in entries:
            item = item_repo.get_by_id(e.item_id)
            enriched.append({
                **e.model_dump(),
                "market_hash_name": item.market_hash_name if item else f"item#{e.item_id}",
                "exterior": item.exterior if item else None,
                "item_nameid": item.item_nameid if item else None,
                "has_trusted_nameid": bool(item and item.item_nameid and item.item_nameid_source),
            })
        return {"items": enriched}

    @app.post("/api/watchlists", status_code=201)
    async def add_watchlist(req: WatchlistCreateRequest) -> dict[str, Any]:
        c = _ctx()
        url = req.url.strip()
        try:
            res = c.identity_service.resolve_and_persist(url)
        except Exception:
            try:
                ssr_ctx = await c.market_adapter.get_ssr_listing_context(url)
                res = c.identity_service.resolve_and_persist(url, ssr_data=ssr_ctx)
            except Exception as exc:
                raise HTTPException(status_code=400, detail=f"Could not resolve Steam URL: {exc}") from exc

        saved = WatchlistRepository(c.db).save(
            WatchlistEntryRecord(
                item_id=res.record.item_id,
                input_url=url,
                strategy=req.strategy,
                currency_id=req.currency_id,
                max_buy_price_minor=req.max_buy_price_minor,
                min_discount_pct=req.min_discount_pct,
                min_net_profit_minor=req.min_net_profit_minor,
                min_roi_pct=req.min_roi_pct,
                min_sell_multiplier=req.min_sell_multiplier,
                min_bid_minor=req.min_bid_minor,
                poll_interval_seconds=max(30, req.poll_interval_seconds),
                notes=req.notes,
            )
        )
        return {
            "watchlist": saved.model_dump(),
            "item": res.record.model_dump(),
            "resolution_status": res.identity.resolution_status.value,
        }

    @app.patch("/api/watchlists/{watchlist_id}")
    def toggle_watchlist(watchlist_id: int, req: WatchlistToggleRequest) -> dict[str, Any]:
        ok = WatchlistRepository(_ctx().db).set_active(watchlist_id, req.is_active)
        if not ok:
            raise HTTPException(status_code=404, detail=f"Watchlist #{watchlist_id} not found.")
        return {"watchlist_id": watchlist_id, "is_active": req.is_active}

    @app.delete("/api/watchlists/{watchlist_id}")
    def delete_watchlist(watchlist_id: int) -> dict[str, Any]:
        ok = WatchlistRepository(_ctx().db).delete(watchlist_id)
        if not ok:
            raise HTTPException(status_code=404, detail=f"Watchlist #{watchlist_id} not found.")
        return {"deleted": True, "watchlist_id": watchlist_id}

    @app.post("/api/history/import")
    def import_history(req: HistoryImportRequest) -> dict[str, Any]:
        try:
            count = import_manual_price_history_json(
                json.dumps(req.points),
                market_hash_name=req.market_hash_name,
                db=_ctx().db,
                currency_id=req.currency_id,
            )
            return {"imported": count, "market_hash_name": req.market_hash_name}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    # -------------------------------------------------------------------------
    # BUY Engine Routes
    # -------------------------------------------------------------------------

    @app.get("/api/buy/candidates")
    def get_buy_candidates(
        currency_id: int | None = None,
        actionable_only: bool = False,
        limit: int = Query(100, ge=1, le=500),
    ) -> dict[str, Any]:
        c = _ctx()
        rows = BuyEvaluationRepository(c.db).list_recent(
            currency_id=currency_id,
            actionable_only=actionable_only,
            limit=limit,
        )
        settings = _read_app_settings(c.db)
        min_roi_pct = float(settings.get("min_roi_pct", 5.0))
        item_repo = ItemRepository(c.db)
        curr_repo = CurrencyRepository(c.db)
        curr_cache: dict[int, Any] = {}
        curr_cfg_cache: dict[int, Any] = {}
        item_cache: dict[int, ItemRecord | None] = {}
        out = []
        for r in rows:
            if r.item_id not in item_cache:
                item_cache[r.item_id] = item_repo.get_by_id(r.item_id)
            if r.currency_id not in curr_cache:
                curr_cache[r.currency_id] = curr_repo.get_by_id(r.currency_id)
            if r.currency_id not in curr_cfg_cache:
                try:
                    cfg, _ = load_currency_config(c.db, r.currency_id)
                    curr_cfg_cache[r.currency_id] = cfg
                except Exception:
                    curr_cfg_cache[r.currency_id] = None
            it = item_cache[r.item_id]
            cr = curr_cache[r.currency_id]
            cfg = curr_cfg_cache[r.currency_id]
            breakeven_buy_minor, target_buy_minor = (
                compute_target_buy_price_minor(r.reference_price_minor, min_roi_pct, cfg)
                if cfg is not None
                else (0, 0)
            )
            mname = it.market_hash_name if it else f"item#{r.item_id}"
            out.append({
                **r.model_dump(),
                "market_hash_name": mname,
                "exterior": it.exterior if it else None,
                "currency_code": cr.code if cr else "USD",
                "currency_symbol": cr.symbol if cr else "$",
                "stickers": json.loads(r.stickers_json or "[]"),
                "warning_tags": json.loads(r.warning_tags or "[]"),
                "breakeven_buy_price_minor": breakeven_buy_minor or None,
                "target_buy_price_minor": target_buy_minor or None,
                "steam_listing_url": f"https://steamcommunity.com/market/listings/730/{quote(mname, safe='')}",
            })
        return {"candidates": out}

    @app.post("/api/buy/scan")
    async def trigger_buy_scan(dispatch_notifications: bool = True) -> dict[str, Any]:
        c = _ctx()
        try:
            evals = await c.scheduler.buy_engine.scan_watchlist(
                dispatch_notifications=dispatch_notifications
            )
            return {
                "evaluated": len(evals),
                "actionable": sum(1 for e in evals if e.is_actionable),
                "candidates": [e.model_dump() for e in evals],
            }
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    # -------------------------------------------------------------------------
    # Self-Discovering Catalog Search & Auto-Hunt Screener (Strictly Read-Only)
    # -------------------------------------------------------------------------

    @app.get("/api/catalog/search")
    def search_local_catalog(
        q: str = "",
        category: str | None = None,
        exterior: str | None = None,
        stattrak_only: bool = False,
        limit: int = Query(40, ge=1, le=100),
    ) -> dict[str, Any]:
        c = _ctx()
        items = (
            c.nameid_db.search_mappings(
                query=q,
                category=category,
                exterior=exterior,
                stattrak_only=stattrak_only,
                limit=limit,
            )
            if c.nameid_db is not None
            else []
        )
        return {
            "query": q,
            "mappings_loaded": bool(c.nameid_db and c.nameid_db.has_verified_metadata()),
            "items": items,
            "presets": get_presets_with_metadata(c.nameid_db),
        }

    @app.post("/api/discover/steam")
    async def search_steam_market_live(req: DiscoverSteamSearchRequest) -> dict[str, Any]:
        c = _ctx()
        try:
            cfg, _ = load_currency_config(c.db, req.currency_id)
            raw_items = await c.scheduler.adapter.search_market_ssr(
                query=req.query,
                currency=cfg,
                start=req.start,
            )
        except ValueError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        filtered: list[dict[str, Any]] = []
        for row in raw_items:
            mname = str(row["market_hash_name"])
            price_minor = row.get("min_sell_price_minor")
            supply = int(row.get("sell_listings") or 0)

            if req.min_price_minor is not None and (price_minor is None or price_minor < req.min_price_minor):
                continue
            if req.max_price_minor is not None and (price_minor is None or price_minor > req.max_price_minor):
                continue
            if supply < req.min_supply:
                continue

            mapping = c.nameid_db.lookup_verified_mapping(mname) if c.nameid_db is not None else None
            has_verified = bool(
                mapping is not None
                and is_trusted_mapping_source(
                    mapping.get("item_nameid_source"),
                    mapping.get("item_nameid_revision"),
                )
            )
            if req.only_verified_nameid and not has_verified:
                continue

            _, ext, is_st, is_souv = decompose_market_hash_name(mname)
            filtered.append({
                **row,
                "exterior": ext.value if ext else None,
                "is_stattrak": is_st,
                "is_souvenir": is_souv,
                "item_nameid": int(mapping["item_nameid"]) if mapping else None,
                "has_trusted_nameid": has_verified,
                "currency_code": cfg.code,
                "currency_symbol": cfg.symbol,
                "steam_listing_url": f"https://steamcommunity.com/market/listings/730/{quote(mname, safe='')}",
            })

        return {
            "query": req.query,
            "total_raw": len(raw_items),
            "currency": {
                "currency_id": cfg.currency_id,
                "code": cfg.code,
                "symbol": cfg.symbol,
            },
            "items": filtered,
        }

    @app.post("/api/discover/screen")
    async def screen_discover_candidates(req: DiscoverScreenRequest) -> dict[str, Any]:
        c = _ctx()
        try:
            candidates = await screen_market_items(
                market_hash_names=req.market_hash_names,
                adapter=c.scheduler.adapter,
                identity_service=c.identity_service,
                db=c.db,
                currency_id=req.currency_id,
                min_roi_pct=req.min_roi_pct,
                auto_watchlist=req.auto_watchlist,
                max_budget_minor=req.max_budget_minor,
            )
            return {
                "screened": len(candidates),
                "actionable": sum(1 for item in candidates if item["verdict_tone"] == "good"),
                "candidates": candidates,
            }
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    # -------------------------------------------------------------------------
    # SELL Engine & Inventory Routes
    # -------------------------------------------------------------------------

    @app.get("/api/inventory/latest")
    def get_latest_inventory() -> dict[str, Any]:
        c = _ctx()
        inv_repo = InventoryRepository(c.db)
        snap = inv_repo.get_latest_snapshot()
        if snap is None or snap.snapshot_id is None:
            return {"snapshot": None, "items": [], "grouped_items": [], "min_samples_needed": 3}
        items = inv_repo.get_items_for_snapshot(snap.snapshot_id)
        settings = _read_app_settings(c.db)
        curr_id = int(settings.get("default_currency_id", 1))
        min_needed = c.scheduler.sell_engine._config.min_historical_samples
        _, grouped = build_grouped_inventory_views(
            c.db,
            currency_id=curr_id,
            min_samples_needed=min_needed,
            nameid_db=c.nameid_db,
        )
        return {
            "snapshot": snap.model_dump(),
            "items": [i.model_dump() for i in items],
            "grouped_items": grouped,
            "min_samples_needed": min_needed,
        }

    @app.post("/api/inventory/import-steam")
    async def import_steam_inventory(req: SteamInventoryImportRequest) -> dict[str, Any]:
        c = _ctx()
        client = PublicInventoryClient(http_client=c.http_client, db=c.db)
        try:
            snap, items = await client.fetch_and_import(
                steamid64=req.steamid64,
                count=req.count,
                max_pages=req.max_pages,
            )
            return {
                "snapshot": snap.model_dump(),
                "imported_count": len(items),
                "marketable_count": sum(1 for i in items if i.is_marketable),
            }
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/inventory/import-manual")
    def import_manual_inventory(req: ManualInventoryImportRequest) -> dict[str, Any]:
        c = _ctx()
        fmt = req.format.strip().lower()
        if fmt == "csv":
            snap, items, errors = ManualInventoryImporter.import_csv(req.content, c.db, owner_id=req.owner_id)
        elif fmt == "json":
            snap, items, errors = ManualInventoryImporter.import_json(req.content, c.db, owner_id=req.owner_id)
        else:
            raise HTTPException(status_code=400, detail="format must be 'csv' or 'json'.")
        return {
            "snapshot": snap.model_dump(),
            "imported_count": len(items),
            "errors": errors,
        }

    @app.get("/api/sell/candidates")
    def get_sell_candidates(limit: int = Query(100, ge=1, le=500)) -> dict[str, Any]:
        c = _ctx()
        alerts = AlertRepository(c.db).get_recent_alerts(limit=limit)
        item_repo = ItemRepository(c.db)
        curr_repo = CurrencyRepository(c.db)
        item_cache: dict[int, ItemRecord | None] = {}
        curr_cache: dict[int, Any] = {}
        out = []
        for a in alerts:
            if a.item_id not in item_cache:
                item_cache[a.item_id] = item_repo.get_by_id(a.item_id)
            if a.currency_id not in curr_cache:
                curr_cache[a.currency_id] = curr_repo.get_by_id(a.currency_id)
            it = item_cache[a.item_id]
            cr = curr_cache[a.currency_id]
            mname = it.market_hash_name if it else f"item#{a.item_id}"
            out.append({
                **a.model_dump(),
                "market_hash_name": mname,
                "exterior": it.exterior if it else None,
                "currency_code": cr.code if cr else "USD",
                "currency_symbol": cr.symbol if cr else "$",
                "warning_tags": json.loads(a.warning_tags or "[]"),
                "steam_listing_url": f"https://steamcommunity.com/market/listings/730/{quote(mname, safe='')}",
            })
        return {"candidates": out}

    @app.post("/api/sell/scan")
    async def trigger_sell_scan(
        dispatch_notifications: bool = True,
        req: SellScanRequest | None = Body(default=None),
    ) -> dict[str, Any]:
        c = _ctx()
        eff_dispatch = dispatch_notifications and (req.dispatch_notifications if req else True)
        selected_names = req.market_hash_names if req else None
        limit = req.limit if req else None

        if selected_names is None and limit is not None:
            settings = _read_app_settings(c.db)
            curr_id = int(settings.get("default_currency_id", 1))
            min_needed = c.scheduler.sell_engine._config.min_historical_samples
            _, grouped = build_grouped_inventory_views(
                c.db,
                currency_id=curr_id,
                min_samples_needed=min_needed,
                nameid_db=c.nameid_db,
            )
            marketable_groups = [g for g in grouped if g["marketable_count"] > 0]
            # Prioritize items with trusted nameid that still need baseline warmup, then highest quantity/value
            marketable_groups.sort(
                key=lambda g: (
                    0 if g["has_trusted_nameid"] else 1,
                    0 if g["baseline_samples_count"] < min_needed else 1,
                    g["baseline_samples_count"],
                    -int(g["quantity"]),
                    g["market_hash_name"],
                )
            )
            selected_names = [g["market_hash_name"] for g in marketable_groups[:limit]]

        try:
            evals = await c.scheduler.sell_engine.scan_inventory(
                dispatch_notifications=eff_dispatch,
                max_unique_items=limit if selected_names is None else None,
                market_hash_names=selected_names,
            )
            return {
                "evaluated": len(evals),
                "actionable": sum(1 for e in evals if e.is_actionable),
                "candidates": [e.model_dump() for e in evals],
            }
        except Exception as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    # -------------------------------------------------------------------------
    # Portfolio & 7-Day Tracker Routes
    # -------------------------------------------------------------------------

    @app.get("/api/portfolio/holdings")
    def get_portfolio_holdings(currency_id: int = Query(1, ge=1)) -> dict[str, Any]:
        return _ctx().portfolio_service.get_overview(currency_id=currency_id).model_dump()

    @app.post("/api/portfolio/holdings", status_code=201)
    def create_holding(req: HoldingCreateRequest) -> dict[str, Any]:
        try:
            marketable_dt = None
            if req.marketable_at:
                s = req.marketable_at.strip()
                if s.endswith("Z"):
                    s = s[:-1] + "+00:00"
                marketable_dt = datetime.fromisoformat(s)
            rec = _ctx().portfolio_service.add_holding(
                market_hash_name=req.market_hash_name,
                acquisition_cost_minor=req.acquisition_cost_minor,
                currency_id=req.currency_id,
                quantity=req.quantity,
                asset_id=req.asset_id,
                marketable_at=marketable_dt,
                restriction_days=req.restriction_days,
                notes=req.notes,
            )
            return {"holding": rec.model_dump()}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/portfolio/holdings/{holding_id}/sell")
    def sell_holding(holding_id: int, req: HoldingSellRequest) -> dict[str, Any]:
        try:
            rec = _ctx().portfolio_service.record_sale(
                holding_id=holding_id,
                gross_sale_price_minor=req.gross_sale_price_minor,
                notes=req.notes,
            )
            return {"holding": rec.model_dump()}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/portfolio/evaluate")
    async def evaluate_portfolio(dispatch_notifications: bool = True) -> dict[str, Any]:
        views = await _ctx().portfolio_service.evaluate_holdings(
            dispatch_notifications=dispatch_notifications
        )
        return {"evaluated": len(views), "holdings": [v.model_dump() for v in views]}

    # -------------------------------------------------------------------------
    # Alerts, Notifications & Source Health
    # -------------------------------------------------------------------------

    @app.get("/api/alerts")
    def get_alerts(limit: int = Query(100, ge=1, le=500)) -> dict[str, Any]:
        c = _ctx()
        item_repo = ItemRepository(c.db)
        item_cache: dict[int, str] = {}

        def item_name(iid: int) -> str:
            if iid not in item_cache:
                it = item_repo.get_by_id(iid)
                item_cache[iid] = it.market_hash_name if it else f"item#{iid}"
            return item_cache[iid]

        sell_alerts = AlertRepository(c.db).get_recent_alerts(limit=limit)
        strat_alerts = StrategyAlertRepository(c.db).list_recent_alerts(limit=limit)

        unified: list[dict[str, Any]] = []
        for a in sell_alerts:
            delivs = AlertRepository(c.db).get_deliveries_for_alert(a.alert_id)
            unified.append({
                "id": f"sell-{a.alert_id}",
                "strategy": "SELL",
                "item_id": a.item_id,
                "market_hash_name": item_name(a.item_id),
                "alert_type": a.alert_type,
                "price_minor": a.highest_buy_order_minor,
                "net_minor": a.modeled_seller_receives_minor,
                "opportunity_score": a.opportunity_score,
                "confidence_score": a.confidence_score,
                "status": a.status,
                "warning_tags": json.loads(a.warning_tags or "[]"),
                "deliveries": [d.model_dump() for d in delivs],
                "created_at": a.created_at,
            })

        for sa in strat_alerts:
            unified.append({
                "id": f"{sa.strategy.lower()}-{sa.strategy_alert_id}",
                "strategy": sa.strategy,
                "item_id": sa.item_id,
                "market_hash_name": item_name(sa.item_id),
                "alert_type": sa.alert_type,
                "price_minor": sa.reference_price_minor,
                "net_minor": sa.net_impact_minor,
                "roi_pct": sa.roi_pct,
                "opportunity_score": sa.opportunity_score,
                "confidence_score": sa.confidence_score,
                "status": sa.status,
                "warning_tags": json.loads(sa.warning_tags or "[]"),
                "deliveries": [],
                "created_at": sa.created_at,
            })

        unified.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
        return {"alerts": unified[:limit]}

    @app.post("/api/notifications/test")
    async def test_notifications(req: NotificationTestRequest) -> dict[str, Any]:
        ch_filter = req.channel.strip().lower()
        channels = []
        if ch_filter in ("all", "discord"):
            channels.append(DiscordNotifier())
        if ch_filter in ("all", "telegram"):
            channels.append(TelegramNotifier())
        if not channels:
            raise HTTPException(status_code=400, detail="channel must be all, discord, or telegram.")

        test_alert = AlertPayload(
            alert_id=999999,
            market_hash_name="AK-47 | Redline (Field-Tested)",
            currency_code="USD",
            currency_symbol="$",
            highest_buy_order_minor=2550,
            lowest_sell_order_minor=2800,
            top_level_buy_quantity=3,
            cumulative_buy_quantity=15,
            modeled_steam_fee_minor=127,
            modeled_publisher_fee_minor=255,
            modeled_seller_receives_minor=2168,
            historical_baseline_minor=2000,
            bid_multiple=1.28,
            opportunity_score=82.5,
            confidence_score=85.0,
            warning_tags=["Test Alert Only"],
            status="Candidate",
            created_at=datetime.now(timezone.utc).isoformat(),
            steam_listing_url="https://steamcommunity.com/market/listings/730/AK-47%20%7C%20Redline%20%28Field-Tested%29",
        )
        dispatcher = NotificationDispatcher(channels=channels)
        results = await dispatcher.dispatch(test_alert, only_configured=False, check_cooldown=False)
        return {"results": {k: v.model_dump() for k, v in results.items()}}

    @app.get("/api/source-health")
    def get_source_health(limit: int = Query(50, ge=1, le=200)) -> dict[str, Any]:
        c = _ctx()
        lim = c.rate_limiter
        events = SourceHealthRepository(c.db).list_recent(limit=limit)
        return {
            "breaker": {
                "state": lim.state.value,
                "is_halted": lim.is_halted,
                "halt_reason": lim.halt_reason,
                "is_cooling_down": lim.is_cooling_down,
                "cooldown_remaining_seconds": round(lim.cooldown_seconds_remaining, 1),
                "consecutive_429_count": lim.consecutive_429_count,
                "available_tokens": round(lim.available_tokens, 3),
                "rpm_ceiling": lim.config.requests_per_minute,
            },
            "events": [e.model_dump() for e in events],
        }

    @app.post("/api/source-health/reset-breaker")
    def reset_breaker(req: BreakerResetRequest) -> dict[str, Any]:
        if not req.confirm_human_review:
            raise HTTPException(
                status_code=400,
                detail="Human review confirmation required (confirm_human_review=true) before resetting breaker.",
            )
        lim = _ctx().rate_limiter
        lim.reset()
        return {"reset": True, "breaker_state": lim.state.value}

    # -------------------------------------------------------------------------
    # Settings (Strictly ZERO Secret Echo-Back)
    # -------------------------------------------------------------------------

    @app.get("/api/settings")
    def get_settings() -> dict[str, Any]:
        c = _ctx()
        settings = _read_app_settings(c.db)
        discord = DiscordNotifier()
        telegram = TelegramNotifier()
        return {
            "settings": settings,
            "channels": {
                "discord": {
                    "enabled": discord.is_enabled,
                    "configured": discord.is_configured,
                },
                "telegram": {
                    "enabled": telegram.is_enabled,
                    "configured": telegram.is_configured,
                },
            },
            "safety": {
                "read_only": True,
                "max_rpm_allowed": 12.0,
                "steam_credentials_stored": False,
            },
        }

    @app.put("/api/settings")
    def update_settings(req: AppSettingsUpdateRequest) -> dict[str, Any]:
        c = _ctx()
        repo = SettingsRepository(c.db)
        payload = req.model_dump()
        for k, v in payload.items():
            repo.set(k, json.dumps(v))

        # Apply safe RPM update cleanly (capped <= 12.0)
        c.scheduler.config.cycle_interval_seconds = max(5.0, req.cycle_interval_seconds)
        c.scheduler.config.max_requests_per_minute = min(12.0, req.requests_per_minute)
        c.rate_limiter.config.requests_per_minute = min(12.0, req.requests_per_minute)
        c.scheduler.buy_engine._config.min_roi_pct = req.min_roi_pct
        c.scheduler.buy_engine._config.cooldown_seconds = req.cooldown_seconds
        c.scheduler.sell_engine._config.min_bid_multiple = req.min_sell_multiplier
        c.scheduler.sell_engine._config.min_bid_minor = req.min_bid_minor
        c.scheduler.sell_engine._config.cooldown_seconds = req.cooldown_seconds
        return get_settings()

    # -------------------------------------------------------------------------
    # Built Frontend SPA Serving (when frontend/dist exists)
    # -------------------------------------------------------------------------

    dist_dir = Path(frontend_dist) if frontend_dist else (
        Path(__file__).resolve().parents[3] / "frontend" / "dist"
    )
    index_file = dist_dir / "index.html"
    assets_dir = dist_dir / "assets"

    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="frontend-assets")

    @app.get("/", include_in_schema=False)
    def serve_spa_root() -> Any:
        if index_file.is_file():
            return FileResponse(str(index_file))
        return HTMLResponse(
            "<html><body style='font-family:sans-serif;background:#0b1120;color:#e2e8f0;padding:2rem'>"
            "<h2>CS2 Trading Sniper API Online</h2>"
            "<p>Frontend bundle not built yet. Run <code>cd frontend && npm run build</code> "
            "or explore API docs at <a href='/docs' style='color:#38bdf8'>/docs</a>.</p>"
            "</body></html>"
        )

    @app.get("/{full_path:path}", include_in_schema=False)
    def serve_spa_fallback(full_path: str) -> Any:
        if full_path.startswith("api/") or full_path == "api":
            raise HTTPException(status_code=404, detail="API endpoint not found.")
        if dist_dir.is_dir():
            candidate = (dist_dir / full_path).resolve()
            if str(candidate).startswith(str(dist_dir.resolve())) and candidate.is_file():
                return FileResponse(str(candidate))
        if index_file.is_file():
            return FileResponse(str(index_file))
        raise HTTPException(status_code=404, detail="Frontend bundle not found.")

    return app
