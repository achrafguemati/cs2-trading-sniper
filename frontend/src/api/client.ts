import type {
  BuyCandidate,
  CatalogSearchResponse,
  DiscoverScreenResponse,
  HealthStatus,
  InventoryResponse,
  OverviewResponse,
  PortfolioOverview,
  SellCandidate,
  SettingsPayload,
  SourceHealthResponse,
  SteamDiscoverResponse,
  UnifiedAlert,
  WatchlistItem,
} from "../types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
    ...init,
  });

  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const payload = await res.json();
      if (payload?.detail) detail = String(payload.detail);
    } catch {
      // Ignore unparseable body
    }
    throw new Error(detail);
  }
  return (await res.json()) as T;
}

export function formatMinor(minor: number | null | undefined, symbol = "$"): string {
  if (minor === null || minor === undefined) return "—";
  const neg = minor < 0;
  const abs = Math.abs(minor);
  const dollars = Math.floor(abs / 100);
  const cents = String(abs % 100).padStart(2, "0");
  return `${neg ? "-" : ""}${symbol}${dollars}.${cents}`;
}

export function formatSignedMinor(minor: number | null | undefined, symbol = "$"): string {
  if (minor === null || minor === undefined) return "—";
  const prefix = minor > 0 ? "+" : "";
  return `${prefix}${formatMinor(minor, symbol)}`;
}

export function formatLocalTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString();
}

export function formatDurationSeconds(seconds: number): string {
  if (seconds <= 0) return "Ready now";
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

export const api = {
  getHealth: () => request<HealthStatus>("/api/health"),
  getOverview: (currencyId = 1) => request<OverviewResponse>(`/api/overview?currency_id=${currencyId}`),

  getWatchlists: () => request<{ items: WatchlistItem[] }>("/api/watchlists"),
  addWatchlist: (body: {
    url: string;
    strategy?: string;
    max_buy_price_minor?: number | null;
    min_roi_pct?: number;
    poll_interval_seconds?: number;
  }) => request("/api/watchlists", { method: "POST", body: JSON.stringify(body) }),
  toggleWatchlist: (id: number, isActive: boolean) =>
    request(`/api/watchlists/${id}`, { method: "PATCH", body: JSON.stringify({ is_active: isActive }) }),
  deleteWatchlist: (id: number) => request(`/api/watchlists/${id}`, { method: "DELETE" }),

  getBuyCandidates: () => request<{ candidates: BuyCandidate[] }>("/api/buy/candidates"),
  triggerBuyScan: (dispatchNotifications = true) =>
    request(`/api/buy/scan?dispatch_notifications=${dispatchNotifications}`, { method: "POST" }),

  searchCatalog: (params: {
    q?: string;
    category?: string;
    exterior?: string;
    stattrak_only?: boolean;
    limit?: number;
  }) => {
    const qs = new URLSearchParams();
    if (params.q) qs.set("q", params.q);
    if (params.category) qs.set("category", params.category);
    if (params.exterior) qs.set("exterior", params.exterior);
    if (params.stattrak_only) qs.set("stattrak_only", "true");
    if (params.limit) qs.set("limit", String(params.limit));
    return request<CatalogSearchResponse>(`/api/catalog/search?${qs.toString()}`);
  },
  discoverSteamMarket: (body: {
    query: string;
    start?: number;
    min_price_minor?: number | null;
    max_price_minor?: number | null;
    min_supply?: number;
    currency_id?: number;
    only_verified_nameid?: boolean;
  }) => request<SteamDiscoverResponse>("/api/discover/steam", { method: "POST", body: JSON.stringify(body) }),
  screenCandidates: (body: {
    market_hash_names: string[];
    currency_id?: number;
    min_roi_pct?: number;
    auto_watchlist?: boolean;
    max_budget_minor?: number | null;
  }) => request<DiscoverScreenResponse>("/api/discover/screen", { method: "POST", body: JSON.stringify(body) }),

  getLatestInventory: () => request<InventoryResponse>("/api/inventory/latest"),
  importSteamInventory: (steamid64: string) =>
    request("/api/inventory/import-steam", { method: "POST", body: JSON.stringify({ steamid64 }) }),
  importManualInventory: (format: "csv" | "json", content: string) =>
    request("/api/inventory/import-manual", { method: "POST", body: JSON.stringify({ format, content }) }),

  getSellCandidates: () => request<{ candidates: SellCandidate[] }>("/api/sell/candidates"),
  triggerSellScan: (
    dispatchNotifications = true,
    options?: { limit?: number; market_hash_names?: string[] },
  ) =>
    request(`/api/sell/scan?dispatch_notifications=${dispatchNotifications}`, {
      method: "POST",
      ...(options ? { body: JSON.stringify({ dispatch_notifications: dispatchNotifications, ...options }) } : {}),
    }),

  getPortfolio: (currencyId = 1) =>
    request<PortfolioOverview>(`/api/portfolio/holdings?currency_id=${currencyId}`),
  addHolding: (body: {
    market_hash_name: string;
    acquisition_cost_minor: number;
    restriction_days: number;
    notes?: string;
  }) => request("/api/portfolio/holdings", { method: "POST", body: JSON.stringify(body) }),
  sellHolding: (holdingId: number, grossSalePriceMinor: number) =>
    request(`/api/portfolio/holdings/${holdingId}/sell`, {
      method: "POST",
      body: JSON.stringify({ gross_sale_price_minor: grossSalePriceMinor }),
    }),
  evaluatePortfolio: (dispatchNotifications = true) =>
    request(`/api/portfolio/evaluate?dispatch_notifications=${dispatchNotifications}`, { method: "POST" }),

  getAlerts: () => request<{ alerts: UnifiedAlert[] }>("/api/alerts"),
  getSourceHealth: () => request<SourceHealthResponse>("/api/source-health"),
  resetBreaker: () =>
    request("/api/source-health/reset-breaker", {
      method: "POST",
      body: JSON.stringify({ confirm_human_review: true }),
    }),

  getSettings: () => request<SettingsPayload>("/api/settings"),
  updateSettings: (settings: SettingsPayload["settings"]) =>
    request<SettingsPayload>("/api/settings", { method: "PUT", body: JSON.stringify(settings) }),
  testNotifications: (channel: "all" | "discord" | "telegram" = "all") =>
    request<{ results: Record<string, { status: string; last_error?: string }> }>("/api/notifications/test", {
      method: "POST",
      body: JSON.stringify({ channel }),
    }),
};
