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
} from "../types";

export function formatMinor(
  minor: number | null | undefined,
  symbol = "$",
  precision = 2,
): string {
  if (minor === null || minor === undefined) return "N/A";
  const neg = minor < 0;
  const abs = Math.abs(Math.trunc(minor));
  if (precision <= 0) {
    return `${neg ? "-" : ""}${symbol}${abs}`;
  }
  const div = 10 ** precision;
  const whole = Math.floor(abs / div);
  const rem = String(abs % div).padStart(precision, "0");
  return `${neg ? "-" : ""}${symbol}${whole}.${rem}`;
}

export function formatSignedMinor(
  minor: number | null | undefined,
  symbol = "$",
  precision = 2,
): string {
  if (minor === null || minor === undefined) return "N/A";
  const prefix = minor > 0 ? "+" : "";
  return `${prefix}${formatMinor(minor, symbol, precision)}`;
}

export function formatDurationSeconds(totalSeconds: number): string {
  if (totalSeconds <= 0) return "Unlocked";
  const d = Math.floor(totalSeconds / 86400);
  const h = Math.floor((totalSeconds % 86400) / 3600);
  const m = Math.floor((totalSeconds % 3600) / 60);
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${m}m`;
  return `${Math.max(1, m)}m`;
}

export function formatLocalTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const dt = new Date(iso);
  if (Number.isNaN(dt.getTime())) return iso;
  return dt.toLocaleString();
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    ...init,
  });
  const text = await res.text();
  const data = text ? JSON.parse(text) : {};
  if (!res.ok) {
    const detail = typeof data?.detail === "string" ? data.detail : `HTTP ${res.status}`;
    throw new Error(detail);
  }
  return data as T;
}

export const api = {
  getHealth: () => request<HealthResponse>("/api/health"),
  getOverview: (currencyId = 1) => request<OverviewResponse>(`/api/overview?currency_id=${currencyId}`),

  getWatchlists: () => request<{ items: WatchlistItem[] }>("/api/watchlists"),
  addWatchlist: (payload: {
    url: string;
    strategy: string;
    currency_id?: number;
    max_buy_price_minor?: number | null;
    min_roi_pct?: number;
  }) => request<{ watchlist: WatchlistItem }>("/api/watchlists", { method: "POST", body: JSON.stringify(payload) }),
  toggleWatchlist: (id: number, isActive: boolean) =>
    request(`/api/watchlists/${id}`, { method: "PATCH", body: JSON.stringify({ is_active: isActive }) }),
  deleteWatchlist: (id: number) => request(`/api/watchlists/${id}`, { method: "DELETE" }),

  importHistory: (payload: {
    market_hash_name: string;
    currency_id: number;
    points: Array<{ bucket_date: string; median_price_minor: number; volume: number }>;
  }) => request<{ imported: number }>("/api/history/import", { method: "POST", body: JSON.stringify(payload) }),

  getBuyCandidates: () => request<{ candidates: BuyCandidate[] }>("/api/buy/candidates"),
  scanBuy: (dispatchNotifications = true) =>
    request<{ evaluated: number; actionable: number }>(`/api/buy/scan?dispatch_notifications=${dispatchNotifications}`, { method: "POST" }),

  getLatestInventory: () =>
    request<{ snapshot: { snapshot_id: number; total_count: number; observed_at: string; source: string } | null; items: InventoryItem[] }>(
      "/api/inventory/latest",
    ),
  importSteamInventory: (steamid64: string) =>
    request<{ imported_count: number; marketable_count: number }>("/api/inventory/import-steam", {
      method: "POST",
      body: JSON.stringify({ steamid64 }),
    }),
  importManualInventory: (format: "csv" | "json", content: string) =>
    request<{ imported_count: number; errors: string[] }>("/api/inventory/import-manual", {
      method: "POST",
      body: JSON.stringify({ format, content }),
    }),
  getSellCandidates: () => request<{ candidates: SellCandidate[] }>("/api/sell/candidates"),
  scanSell: (dispatchNotifications = true) =>
    request<{ evaluated: number; actionable: number }>(`/api/sell/scan?dispatch_notifications=${dispatchNotifications}`, { method: "POST" }),

  getPortfolio: (currencyId = 1) => request<PortfolioOverview>(`/api/portfolio/holdings?currency_id=${currencyId}`),
  addHolding: (payload: {
    market_hash_name: string;
    acquisition_cost_minor: number;
    restriction_days: number;
    currency_id?: number;
  }) => request("/api/portfolio/holdings", { method: "POST", body: JSON.stringify(payload) }),
  evaluatePortfolio: (dispatchNotifications = true) =>
    request(`/api/portfolio/evaluate?dispatch_notifications=${dispatchNotifications}`, { method: "POST" }),
  sellHolding: (holdingId: number, grossSalePriceMinor: number) =>
    request(`/api/portfolio/holdings/${holdingId}/sell`, {
      method: "POST",
      body: JSON.stringify({ gross_sale_price_minor: grossSalePriceMinor }),
    }),

  getAlerts: () => request<{ alerts: UnifiedAlert[] }>("/api/alerts"),
  testNotifications: (channel: "all" | "discord" | "telegram") =>
    request<{ results: Record<string, { status: string; last_error?: string }> }>("/api/notifications/test", {
      method: "POST",
      body: JSON.stringify({ channel }),
    }),
  getSourceHealth: () =>
    request<{ breaker: HealthResponse; events: SourceHealthEvent[] }>("/api/source-health"),
  resetBreaker: () =>
    request("/api/source-health/reset-breaker", {
      method: "POST",
      body: JSON.stringify({ confirm_human_review: true }),
    }),

  getSettings: () => request<SettingsPayload>("/api/settings"),
  updateSettings: (settings: SettingsPayload["settings"]) =>
    request<SettingsPayload>("/api/settings", { method: "PUT", body: JSON.stringify(settings) }),
};
