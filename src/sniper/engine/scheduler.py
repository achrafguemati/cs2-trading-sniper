"""Unified sequential scheduler coordinating BUY, SELL, and Portfolio cycles under one shared 12-RPM budget."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import time
from typing import Any

from sniper.adapters.steam_market import SteamMarketAdapter
from sniper.core.http_client import ReadOnlyHttpClient
from sniper.core.rate_limiter import (
    CircuitBreakerState,
    RateLimiterConfig,
    RateLimitExceededError,
    SecurityBlockError,
    SequentialRateLimiter,
)
from sniper.engine.buy_engine import BuyEvaluationConfig, BuySniperEngine
from sniper.engine.portfolio_service import PortfolioService
from sniper.engine.sell_engine import SellEvaluationConfig, SellSniperEngine
from sniper.notifications.dispatcher import NotificationDispatcher
from sniper.storage.database import Database
from sniper.storage.models import SourceHealthEventRecord
from sniper.storage.repositories import SourceHealthRepository

logger = logging.getLogger("sniper.scheduler")


@dataclass
class SchedulerConfig:
    """Configuration for sequential BUY/SELL/Portfolio polling scheduler."""
    currency_id: int = 1
    cycle_interval_seconds: float = 300.0
    max_requests_per_minute: float = 12.0  # Strictly capped <= 12 RPM safety ceiling
    enable_buy_scan: bool = True
    enable_sell_scan: bool = True
    enable_portfolio_eval: bool = True
    dispatch_notifications: bool = True

    def __post_init__(self) -> None:
        if self.max_requests_per_minute <= 0 or self.max_requests_per_minute > 12.0:
            raise ValueError(
                f"max_requests_per_minute must be in (0, 12.0] per safety policy, got {self.max_requests_per_minute}"
            )
        if self.cycle_interval_seconds < 5.0:
            raise ValueError("cycle_interval_seconds must be >= 5.0 seconds.")


@dataclass
class SchedulerCycleResult:
    """Outcome summary of one complete scheduler pass."""
    started_at: str
    finished_at: str
    breaker_state: str
    buy_evaluated: int = 0
    buy_actionable: int = 0
    sell_evaluated: int = 0
    sell_actionable: int = 0
    portfolio_evaluated: int = 0
    portfolio_ready_alerts: int = 0
    skipped_reason: str | None = None
    error: str | None = None


class SniperScheduler:
    """Single-IP sequential coordinator sharing one ReadOnlyHttpClient & SequentialRateLimiter."""

    def __init__(
        self,
        db: Database,
        http_client: ReadOnlyHttpClient | None = None,
        config: SchedulerConfig | None = None,
        dispatcher: NotificationDispatcher | None = None,
    ) -> None:
        self.db = db
        self.config = config or SchedulerConfig()
        if http_client is not None:
            self.http_client = http_client
            self._owns_client = False
        else:
            limiter = SequentialRateLimiter(
                config=RateLimiterConfig(requests_per_minute=self.config.max_requests_per_minute)
            )
            self.http_client = ReadOnlyHttpClient(rate_limiter=limiter)
            self._owns_client = True

        self.adapter = SteamMarketAdapter(self.http_client)
        self.dispatcher = dispatcher or NotificationDispatcher.from_database(db)
        self.health_repo = SourceHealthRepository(db)

        self.buy_engine = BuySniperEngine(
            db=db,
            market_adapter=self.adapter,
            dispatcher=self.dispatcher,
            config=BuyEvaluationConfig(currency_id=self.config.currency_id),
        )
        self.sell_engine = SellSniperEngine(
            db=db,
            market_adapter=self.adapter,
            dispatcher=self.dispatcher,
            config=SellEvaluationConfig(currency_id=self.config.currency_id),
        )
        self.portfolio_service = PortfolioService(db=db, dispatcher=self.dispatcher)

        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self.last_cycle: SchedulerCycleResult | None = None

    @property
    def rate_limiter(self) -> SequentialRateLimiter:
        return self.http_client.rate_limiter

    def _log_health(
        self,
        endpoint: str,
        http_status: int | None = None,
        elapsed_ms: int | None = None,
        detail: str | None = None,
    ) -> None:
        breaker = self.rate_limiter
        retry_rem = int(breaker.cooldown_seconds_remaining) if breaker.is_cooling_down else None
        try:
            self.health_repo.log_event(
                SourceHealthEventRecord(
                    endpoint=endpoint,
                    http_status=http_status,
                    response_time_ms=elapsed_ms,
                    breaker_state=breaker.state.value,
                    retry_after_seconds=retry_rem,
                    detail=detail,
                )
            )
        except Exception as exc:
            logger.debug("Failed to persist source health log: %s", exc)

    async def run_once(self, now_dt: datetime | None = None) -> SchedulerCycleResult:
        """Execute one coordinated BUY -> SELL -> Portfolio pass sequentially."""
        now = now_dt or datetime.now(timezone.utc)
        started_iso = now.isoformat()
        breaker_state = self.rate_limiter.state

        if breaker_state == CircuitBreakerState.HALTED:
            reason = f"Scheduler paused: circuit breaker HALTED ({self.rate_limiter.halt_reason})"
            self._log_health("scheduler/cycle", detail=reason)
            res = SchedulerCycleResult(
                started_at=started_iso,
                finished_at=datetime.now(timezone.utc).isoformat(),
                breaker_state=breaker_state.value,
                skipped_reason=reason,
            )
            self.last_cycle = res
            return res

        if breaker_state == CircuitBreakerState.OPEN:
            rem = self.rate_limiter.cooldown_seconds_remaining
            reason = f"Scheduler backing off: circuit breaker OPEN ({rem:.0f}s remaining)"
            self._log_health("scheduler/cycle", http_status=429, detail=reason)
            res = SchedulerCycleResult(
                started_at=started_iso,
                finished_at=datetime.now(timezone.utc).isoformat(),
                breaker_state=breaker_state.value,
                skipped_reason=reason,
            )
            self.last_cycle = res
            return res

        buy_count = buy_act = sell_count = sell_act = port_count = port_alerts = 0
        t0 = time.monotonic()
        err_msg: str | None = None

        try:
            if self.config.enable_buy_scan:
                buy_evals = await self.buy_engine.scan_watchlist(
                    currency_id=self.config.currency_id,
                    dispatch_notifications=self.config.dispatch_notifications,
                )
                buy_count = len(buy_evals)
                buy_act = sum(1 for e in buy_evals if e.is_actionable)

            if self.config.enable_sell_scan:
                sell_evals = await self.sell_engine.scan_inventory(
                    dispatch_notifications=self.config.dispatch_notifications,
                )
                sell_count = len(sell_evals)
                sell_act = sum(1 for e in sell_evals if e.is_actionable)

            if self.config.enable_portfolio_eval:
                port_views = await self.portfolio_service.evaluate_holdings(
                    dispatch_notifications=self.config.dispatch_notifications,
                    now_dt=now,
                )
                port_count = len(port_views)
                port_alerts = sum(1 for v in port_views if v.alert_dispatched)

            elapsed_ms = int((time.monotonic() - t0) * 1000)
            self._log_health(
                "scheduler/cycle",
                http_status=200,
                elapsed_ms=elapsed_ms,
                detail=f"BUY={buy_count}({buy_act}), SELL={sell_count}({sell_act}), PORT={port_count}({port_alerts})",
            )
        except RateLimitExceededError as exc:
            err_msg = str(exc)
            self._log_health("scheduler/cycle", http_status=429, detail=err_msg)
        except SecurityBlockError as exc:
            err_msg = str(exc)
            self._log_health("scheduler/cycle", http_status=403, detail=err_msg)
        except Exception as exc:
            err_msg = f"{type(exc).__name__}: {exc}"
            logger.error("Unexpected error in scheduler cycle: %s", err_msg)
            self._log_health("scheduler/cycle", detail=err_msg)

        res = SchedulerCycleResult(
            started_at=started_iso,
            finished_at=datetime.now(timezone.utc).isoformat(),
            breaker_state=self.rate_limiter.state.value,
            buy_evaluated=buy_count,
            buy_actionable=buy_act,
            sell_evaluated=sell_count,
            sell_actionable=sell_act,
            portfolio_evaluated=port_count,
            portfolio_ready_alerts=port_alerts,
            error=err_msg,
        )
        self.last_cycle = res
        return res

    async def start_background_loop(self) -> None:
        """Start non-blocking scheduler background task."""
        if self._task is not None and not self._task.done():
            return
        self._stop_event.clear()

        async def _loop() -> None:
            while not self._stop_event.is_set():
                await self.run_once()
                wait_sec = self.config.cycle_interval_seconds
                if self.rate_limiter.state == CircuitBreakerState.OPEN:
                    wait_sec = max(wait_sec, self.rate_limiter.cooldown_seconds_remaining + 1.0)
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=wait_sec)
                except asyncio.TimeoutError:
                    continue

        self._task = asyncio.create_task(_loop(), name="sniper-scheduler-loop")

    async def stop(self) -> None:
        """Stop background task and cleanly close owned HTTP client."""
        self._stop_event.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=5.0)
            except Exception:
                self._task.cancel()
            self._task = None
        if self._owns_client:
            await self.http_client.aclose()
