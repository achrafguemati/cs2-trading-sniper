import { useCallback, useEffect, useState } from "react";
import {
  Activity,
  Briefcase,
  Compass,
  Crosshair,
  LayoutDashboard,
  Radar,
  RefreshCw,
  Settings,
  ShieldCheck,
} from "lucide-react";
import { api } from "./api/client";
import type {
  BuyCandidate,
  HealthStatus,
  InventoryResponse,
  OverviewResponse,
  PortfolioOverview,
  SellCandidate,
  SettingsPayload,
  SourceHealthResponse,
  UnifiedAlert,
  WatchlistItem,
} from "./types";
import { OverviewTab } from "./components/OverviewTab";
import { DiscoverTab } from "./components/DiscoverTab";
import { BuySniperTab } from "./components/BuySniperTab";
import { SellSniperTab } from "./components/SellSniperTab";
import { PortfolioTab } from "./components/PortfolioTab";
import { HealthAlertsTab } from "./components/HealthAlertsTab";
import { SettingsTab } from "./components/SettingsTab";

type TabId = "overview" | "discover" | "buy" | "sell" | "portfolio" | "health" | "settings";

const TABS: Array<{ id: TabId; label: string; icon: typeof LayoutDashboard }> = [
  { id: "overview", label: "Overview", icon: LayoutDashboard },
  { id: "discover", label: "Discover Deals", icon: Compass },
  { id: "buy", label: "BUY Sniper", icon: Crosshair },
  { id: "sell", label: "SELL Sniper", icon: Radar },
  { id: "portfolio", label: "7-Day Portfolio", icon: Briefcase },
  { id: "health", label: "Health & Alerts", icon: Activity },
  { id: "settings", label: "Settings", icon: Settings },
];

export default function App() {
  const [activeTab, setActiveTab] = useState<TabId>("overview");
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [overview, setOverview] = useState<OverviewResponse | null>(null);
  const [watchlists, setWatchlists] = useState<WatchlistItem[]>([]);
  const [buyCandidates, setBuyCandidates] = useState<BuyCandidate[]>([]);
  const [sellCandidates, setSellCandidates] = useState<SellCandidate[]>([]);
  const [inventory, setInventory] = useState<InventoryResponse | null>(null);
  const [portfolio, setPortfolio] = useState<PortfolioOverview | null>(null);
  const [alerts, setAlerts] = useState<UnifiedAlert[]>([]);
  const [sourceHealth, setSourceHealth] = useState<SourceHealthResponse | null>(null);
  const [settingsPayload, setSettingsPayload] = useState<SettingsPayload | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const refreshAll = useCallback(async () => {
    setRefreshing(true);
    try {
      const [h, ov, wl, buy, sell, inv, port, al, sh, st] = await Promise.all([
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
      setBuyCandidates(buy.candidates);
      setSellCandidates(sell.candidates);
      setInventory(inv);
      setPortfolio(port);
      setAlerts(al.alerts);
      setSourceHealth(sh);
      setSettingsPayload(st);
      setLoadError(null);
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "Failed to reach local FastAPI server.");
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void refreshAll();
    const timer = window.setInterval(() => {
      void refreshAll();
    }, 30000);
    return () => window.clearInterval(timer);
  }, [refreshAll]);

  const activeTitle = TABS.find((t) => t.id === activeTab)?.label ?? "Dashboard";

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand-block">
          <div className="brand-title">
            <span className="brand-logo">
              <Radar size={17} />
            </span>
            CS2 Trading Sniper
          </div>
          <div className="brand-sub">
            <ShieldCheck size={12} /> STRICTLY READ-ONLY
          </div>
        </div>

        <nav className="nav-list" aria-label="Main Navigation">
          {TABS.map((t) => {
            const Icon = t.icon;
            const badgeCount =
              t.id === "buy" && overview?.kpis.active_buy_signals
                ? overview.kpis.active_buy_signals
                : t.id === "sell" && overview?.kpis.recent_sell_signals
                  ? overview.kpis.recent_sell_signals
                  : null;
            return (
              <button
                key={t.id}
                type="button"
                aria-label={t.label}
                className={`nav-btn ${activeTab === t.id ? "active" : ""}`}
                onClick={() => setActiveTab(t.id)}
              >
                <Icon size={16} aria-hidden="true" />
                <span>{t.label}</span>
                {badgeCount !== null && (
                  <span className="nav-badge-count" aria-hidden="true">
                    {badgeCount}
                  </span>
                )}
              </button>
            );
          })}
        </nav>

        <div className="sidebar-footer">
          <div style={{ fontSize: "0.75rem", color: "#94a3b8" }}>
            Steam Wallet Mode &bull; Zero Auth
          </div>
          <div className="muted mono" style={{ fontSize: "0.72rem" }}>
            Hard Ceiling &le; {health?.rpm_ceiling ?? 12} RPM
          </div>
        </div>
      </aside>

      <main className="main-area">
        <header className="top-bar">
          <div>
            <h1 className="top-bar-title">{activeTitle}</h1>
            <p className="top-bar-sub">
              Read-only quantitative CS2 market screener, exact integer fee math &amp; 7-day lock portfolio
            </p>
          </div>

          <div className="top-bar-actions">
            <span className="status-pill">
              <span
                className={`status-dot ${
                  health?.is_halted ? "danger" : health?.is_cooling_down ? "warn" : ""
                }`}
              />
              Breaker: {health?.breaker_state ?? "..."}
            </span>
            <span className="status-pill">
              DB: {health?.nameid_mappings_loaded ? "26,935 Verified" : "Booting"}
            </span>
            <button type="button" className="btn" disabled={refreshing} onClick={() => void refreshAll()}>
              <RefreshCw size={14} /> {refreshing ? "Syncing..." : "Refresh"}
            </button>
          </div>
        </header>

        {loadError && (
          <div className="banner-warn">
            <strong>API Connection Issue:</strong>&nbsp;{loadError} — verify local <code>sniper serve</code> backend is running.
          </div>
        )}

        {health?.is_halted && (
          <div className="banner-warn">
            <strong>CIRCUIT BREAKER HALTED:</strong>&nbsp;{health.halt_reason || "Unexpected response"} Inspect Health &amp; Alerts before resetting.
          </div>
        )}

        {activeTab === "overview" && (
          <OverviewTab
            overview={overview}
            buyCandidates={buyCandidates}
            sellCandidates={sellCandidates}
            onNavigate={setActiveTab}
          />
        )}
        {activeTab === "discover" && (
          <DiscoverTab
            defaultMinRoiPct={settingsPayload?.settings.min_roi_pct ?? 5}
            onRefresh={refreshAll}
          />
        )}
        {activeTab === "buy" && (
          <BuySniperTab
            watchlists={watchlists}
            candidates={buyCandidates}
            onRefresh={refreshAll}
            onNavigate={setActiveTab}
          />
        )}
        {activeTab === "sell" && (
          <SellSniperTab
            inventory={inventory}
            candidates={sellCandidates}
            onRefresh={refreshAll}
          />
        )}
        {activeTab === "portfolio" && <PortfolioTab portfolio={portfolio} onRefresh={refreshAll} />}
        {activeTab === "health" && (
          <HealthAlertsTab
            health={health}
            events={sourceHealth?.events ?? []}
            alerts={alerts}
            onRefresh={refreshAll}
          />
        )}
        {activeTab === "settings" && (
          <SettingsTab settingsPayload={settingsPayload} onRefresh={refreshAll} />
        )}
      </main>
    </div>
  );
}
