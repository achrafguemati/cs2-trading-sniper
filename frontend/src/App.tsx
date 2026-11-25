import { useCallback, useEffect, useState } from "react";
import {
  Activity,
  Briefcase,
  LayoutDashboard,
  RefreshCw,
  Settings,
  ShieldCheck,
  TrendingDown,
  TrendingUp,
} from "lucide-react";
import { api } from "./api/client";
import type {
  BuyCandidate,
  HealthResponse,
  InventoryItem,
  OverviewResponse,
  PortfolioOverview,
  SellCandidate,
  SettingsPayload,
  SourceHealthEvent,
  UnifiedAlert,
  WatchlistItem,
} from "./types";
import { OverviewTab } from "./components/OverviewTab";
import { BuySniperTab } from "./components/BuySniperTab";
import { SellSniperTab } from "./components/SellSniperTab";
import { PortfolioTab } from "./components/PortfolioTab";
import { HealthAlertsTab } from "./components/HealthAlertsTab";
import { SettingsTab } from "./components/SettingsTab";

type TabId = "overview" | "buy" | "sell" | "portfolio" | "health" | "settings";

export function App() {
  const [tab, setTab] = useState<TabId>("overview");
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [overview, setOverview] = useState<OverviewResponse | null>(null);
  const [watchlists, setWatchlists] = useState<WatchlistItem[]>([]);
  const [buyCandidates, setBuyCandidates] = useState<BuyCandidate[]>([]);
  const [sellCandidates, setSellCandidates] = useState<SellCandidate[]>([]);
  const [inventoryItems, setInventoryItems] = useState<InventoryItem[]>([]);
  const [portfolio, setPortfolio] = useState<PortfolioOverview | null>(null);
  const [alerts, setAlerts] = useState<UnifiedAlert[]>([]);
  const [healthEvents, setHealthEvents] = useState<SourceHealthEvent[]>([]);
  const [settingsPayload, setSettingsPayload] = useState<SettingsPayload | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const refreshAll = useCallback(async () => {
    setRefreshing(true);
    try {
      const [h, ov, wl, bc, sc, inv, port, al, sh, st] = await Promise.all([
        api.getHealth(),
        api.getOverview(1),
        api.getWatchlists(),
        api.getBuyCandidates(),
        api.getSellCandidates(),
        api.getLatestInventory(),
        api.getPortfolio(1),
        api.getAlerts(),
        api.getSourceHealth(),
        api.getSettings(),
      ]);
      setHealth(h);
      setOverview(ov);
      setWatchlists(wl.items);
      setBuyCandidates(bc.candidates);
      setSellCandidates(sc.candidates);
      setInventoryItems(inv.items);
      setPortfolio(port);
      setAlerts(al.alerts);
      setHealthEvents(sh.events);
      setSettingsPayload(st);
      setLoadError(null);
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "Unable to reach CS2 Trading Sniper backend.");
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void refreshAll();
    const timer = window.setInterval(() => {
      void refreshAll();
    }, 15000);
    return () => window.clearInterval(timer);
  }, [refreshAll]);

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-title">
            <ShieldCheck size={20} color="#38bdf8" />
            CS2 Trading Sniper
          </div>
          <div className="brand-sub">Read-Only Quantitative Terminal</div>
        </div>

        <nav className="nav-list" aria-label="Primary navigation">
          <button className={`nav-btn ${tab === "overview" ? "active" : ""}`} onClick={() => setTab("overview")}>
            <LayoutDashboard size={17} /> Overview
          </button>
          <button className={`nav-btn ${tab === "buy" ? "active" : ""}`} onClick={() => setTab("buy")}>
            <TrendingDown size={17} /> BUY Sniper
          </button>
          <button className={`nav-btn ${tab === "sell" ? "active" : ""}`} onClick={() => setTab("sell")}>
            <TrendingUp size={17} /> SELL Sniper
          </button>
          <button className={`nav-btn ${tab === "portfolio" ? "active" : ""}`} onClick={() => setTab("portfolio")}>
            <Briefcase size={17} /> Portfolio &amp; 7d Lock
          </button>
          <button className={`nav-btn ${tab === "health" ? "active" : ""}`} onClick={() => setTab("health")}>
            <Activity size={17} /> Alerts &amp; Health
          </button>
          <button className={`nav-btn ${tab === "settings" ? "active" : ""}`} onClick={() => setTab("settings")}>
            <Settings size={17} /> Settings
          </button>
        </nav>

        <div className="safety-card">
          <div><strong>STRICTLY READ-ONLY</strong></div>
          <div style={{ marginTop: 4 }}>Zero auto-buy / auto-sell</div>
          <div>Zero Steam passwords or cookies</div>
          <div>Single-IP budget ≤ 12 RPM</div>
        </div>
      </aside>

      <main className="main-area">
        <header className="topbar">
          <div>
            <h1 style={{ margin: 0, fontSize: "1.35rem" }}>
              {tab === "overview" && "Market Terminal Overview"}
              {tab === "buy" && "Mode A — BUY SNIPER (Listing Evaluator)"}
              {tab === "sell" && "Mode B — SELL SNIPER (High Buy-Order Monitor)"}
              {tab === "portfolio" && "7-Day Restriction & Portfolio Tracker"}
              {tab === "health" && "Source Health & Alert Audit Log"}
              {tab === "settings" && "Settings & Free Alert Channels"}
            </h1>
          </div>
          <div className="row-actions">
            {health && (
              <span className={`badge ${health.breaker_state === "CLOSED" ? "badge-green" : health.breaker_state === "HALTED" ? "badge-rose" : "badge-amber"}`}>
                Steam Source: {health.breaker_state}
              </span>
            )}
            <button className="btn" onClick={() => void refreshAll()} disabled={refreshing}>
              <RefreshCw size={15} /> {refreshing ? "Refreshing..." : "Refresh"}
            </button>
          </div>
        </header>

        {health && (health.is_halted || health.is_cooling_down) && (
          <div className="banner-warn" role="alert">
            <div>
              <strong>Steam Source Degraded ({health.breaker_state}):</strong>{" "}
              {health.is_halted
                ? health.halt_reason || "Polling halted due to HTTP 403 or schema failure."
                : `HTTP 429 cooldown active (${health.cooldown_remaining_seconds}s remaining).`}
            </div>
            <button className="btn" onClick={() => setTab("health")}>Inspect Telemetry →</button>
          </div>
        )}

        {loadError && (
          <div className="banner-warn" role="alert">
            <span>Backend error: {loadError}</span>
          </div>
        )}

        {tab === "overview" && (
          <OverviewTab
            overview={overview}
            buyCandidates={buyCandidates}
            sellCandidates={sellCandidates}
            onNavigate={(next) => setTab(next)}
          />
        )}
        {tab === "buy" && (
          <BuySniperTab watchlists={watchlists} candidates={buyCandidates} onRefresh={refreshAll} />
        )}
        {tab === "sell" && (
          <SellSniperTab inventoryItems={inventoryItems} sellCandidates={sellCandidates} onRefresh={refreshAll} />
        )}
        {tab === "portfolio" && (
          <PortfolioTab portfolio={portfolio} onRefresh={refreshAll} />
        )}
        {tab === "health" && (
          <HealthAlertsTab
            health={health}
            events={healthEvents}
            alerts={alerts}
            onRefresh={refreshAll}
          />
        )}
        {tab === "settings" && (
          <SettingsTab settingsPayload={settingsPayload} onRefresh={refreshAll} />
        )}
      </main>
    </div>
  );
}

export default App;
