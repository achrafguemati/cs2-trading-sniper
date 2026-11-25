import { useEffect, useMemo, useState } from "react";
import {
  ArrowRight,
  Briefcase,
  Check,
  Compass,
  ExternalLink,
  Flame,
  Plus,
  Search,
  Sparkles,
  TrendingUp,
} from "lucide-react";
import type {
  CatalogItem,
  CatalogPreset,
  CrossMarketOpportunity,
  ScreenerCandidate,
  SteamDiscoverItem,
} from "../types";
import { api, formatMinor, formatSignedMinor } from "../api/client";

interface Props {
  defaultMinRoiPct: number;
  onRefresh: () => Promise<void>;
}

const CATEGORIES = [
  { id: "", label: "All Items" },
  { id: "rifles", label: "Rifles" },
  { id: "pistols", label: "Pistols" },
  { id: "knives", label: "Knives ★" },
  { id: "gloves", label: "Gloves" },
  { id: "cases", label: "Cases" },
  { id: "stickers", label: "Stickers" },
  { id: "agents", label: "Agents" },
];

const STRATEGIES = [
  { id: "all", label: "All Best Opportunities" },
  { id: "cross_market", label: "Skinport Cash → Steam Sell" },
  { id: "steam_dip", label: "Steam Ask Dips" },
  { id: "float_craft", label: "Low Float & Sticker Crafts" },
];

const EXTERIORS = ["", "Factory New", "Minimal Wear", "Field-Tested", "Well-Worn", "Battle-Scarred"];

type BrowseSource = "catalog" | "steam" | "cross_market";

export function DiscoverTab({ defaultMinRoiPct, onRefresh }: Props) {
  const [strategy, setStrategy] = useState("all");
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("");
  const [exterior, setExterior] = useState("");
  const [stattrakOnly, setStattrakOnly] = useState(false);
  const [minPriceUsd, setMinPriceUsd] = useState("5.00");
  const [maxPriceUsd, setMaxPriceUsd] = useState("");
  const minSupply = "5";
  const [targetRoiPct, setTargetRoiPct] = useState(String(defaultMinRoiPct || 5));
  const [huntCount, setHuntCount] = useState("5");
  const [autoWatchlist, setAutoWatchlist] = useState(false);

  const [browseSource, setBrowseSource] = useState<BrowseSource>("catalog");
  const [presets, setPresets] = useState<CatalogPreset[]>([]);
  const [activePresetId, setActivePresetId] = useState<string>("");
  const [catalogItems, setCatalogItems] = useState<CatalogItem[]>([]);
  const [steamItems, setSteamItems] = useState<SteamDiscoverItem[]>([]);
  const [crossItems, setCrossItems] = useState<CrossMarketOpportunity[]>([]);
  const [screenResults, setScreenResults] = useState<ScreenerCandidate[] | null>(null);
  const [selectedNames, setSelectedNames] = useState<string[]>([]);
  const [watchedSet, setWatchedSet] = useState<Set<string>>(new Set());
  const [trackedSet, setTrackedSet] = useState<Set<string>>(new Set());

  const [busyMode, setBusyMode] = useState<"catalog" | "steam" | "cross" | "hunt" | "screen" | "action" | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadCatalog = async (opts?: { q?: string; cat?: string; ext?: string; st?: boolean }) => {
    setError(null);
    setBusyMode("catalog");
    try {
      const res = await api.searchCatalog({
        q: opts?.q ?? query,
        category: opts?.cat ?? category,
        exterior: opts?.ext ?? exterior,
        stattrak_only: opts?.st ?? stattrakOnly,
        limit: 40,
      });
      setCatalogItems(res.items);
      if (res.presets?.length) {
        setPresets(res.presets);
      }
      setBrowseSource("catalog");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Catalog search failed.");
    } finally {
      setBusyMode(null);
    }
  };

  useEffect(() => {
    void loadCatalog({ q: "", cat: "", ext: "", st: false });
  }, []);

  const handleLoadCrossMarket = async () => {
    setError(null);
    setMessage(null);
    setBusyMode("cross");
    try {
      const minMinor = minPriceUsd ? Math.max(50, Math.round(Number(minPriceUsd) * 100)) : 300;
      const maxMinor = maxPriceUsd ? Math.max(100, Math.round(Number(maxPriceUsd) * 100)) : null;
      const res = await api.getCrossMarketSpreads({
        q: query.trim() || undefined,
        category: category || undefined,
        exterior: exterior || undefined,
        min_price_minor: minMinor,
        max_price_minor: maxMinor,
        min_supply: Math.max(1, Number(minSupply) || 5),
        min_roi_pct: Math.max(0, Number(targetRoiPct) || 5),
        limit: 35,
      });
      setCrossItems(res.items);
      setBrowseSource("cross_market");
      setActivePresetId("");
      setMessage(
        `Scanned ${res.feed_items.toLocaleString()} Skinport & CS.Market reference prices with 0 Steam requests — ${res.items.length} cross-market Buy Cash → Sell Steam Wallet spreads ranked below.`,
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Cross-market feed lookup failed.");
    } finally {
      setBusyMode(null);
    }
  };

  const handleAutoHunt = async () => {
    setError(null);
    setMessage(null);
    setBusyMode("hunt");
    try {
      const minMinor = minPriceUsd ? Math.max(50, Math.round(Number(minPriceUsd) * 100)) : 300;
      const maxMinor = maxPriceUsd ? Math.max(100, Math.round(Number(maxPriceUsd) * 100)) : null;
      const res = await api.autoHuntDeals({
        strategy,
        query: query.trim(),
        category: category || undefined,
        exterior: exterior || undefined,
        min_price_minor: minMinor,
        max_price_minor: maxMinor,
        min_roi_pct: Math.max(0, Number(targetRoiPct) || 5),
        max_items: Math.min(8, Math.max(1, Number(huntCount) || 5)),
        auto_watchlist: autoWatchlist,
      });
      if (res.cross_market_preview?.length) {
        setCrossItems(res.cross_market_preview);
      }
      setScreenResults(res.candidates);
      setMessage(
        `Auto-Hunt (${res.strategy}) scanned ${res.screened} items sequentially — ${res.actionable} high-value Buy→Sell opportunities found (cross-referenced with ${res.external_feed_items.toLocaleString()} Skinport/CS.Market items).`,
      );
      await onRefresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Auto-Hunt failed.");
    } finally {
      setBusyMode(null);
    }
  };

  const handleSteamSearch = async (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    setError(null);
    setMessage(null);
    setBusyMode("steam");
    try {
      const minMinor = minPriceUsd ? Math.max(1, Math.round(Number(minPriceUsd) * 100)) : null;
      const maxMinor = maxPriceUsd ? Math.max(1, Math.round(Number(maxPriceUsd) * 100)) : null;
      const res = await api.discoverSteamMarket({
        query: query.trim(),
        min_price_minor: minMinor,
        max_price_minor: maxMinor,
        min_supply: Math.max(0, Number(minSupply) || 0),
        currency_id: 1,
      });
      setSteamItems(res.items);
      setBrowseSource("steam");
      setActivePresetId("");
      setMessage(
        `Live Steam search returned ${res.items.length} filtered items (${res.total_raw} raw SSR results in 1 polite GET).`,
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Live Steam market search failed.");
    } finally {
      setBusyMode(null);
    }
  };

  const toggleSelect = (name: string) => {
    setSelectedNames((prev) => {
      if (prev.includes(name)) return prev.filter((x) => x !== name);
      if (prev.length >= 8) {
        setError("Auto-Hunt screener is bounded to 8 items per batch to respect Steam's 12 RPM rate limit.");
        return prev;
      }
      setError(null);
      return [...prev, name];
    });
  };

  const applyPreset = (preset: CatalogPreset) => {
    setActivePresetId(preset.id);
    setBrowseSource("catalog");
    setCatalogItems(preset.items);
    setSelectedNames(preset.items.slice(0, 8).map((i) => i.market_hash_name));
    setError(null);
    setMessage(`Loaded "${preset.label}" (${preset.items.length} liquid items). Click Analyze Selected to evaluate Buy→Sell deals.`);
  };

  const handleRunScreener = async (namesOverride?: string[]) => {
    const queue = (namesOverride ?? selectedNames).slice(0, 8);
    if (queue.length === 0) {
      setError("Select up to 8 items first, or click Auto-Hunt Best Opportunities.");
      return;
    }
    setError(null);
    setMessage(null);
    setBusyMode("screen");
    try {
      const budgetMinor = maxPriceUsd ? Math.max(1, Math.round(Number(maxPriceUsd) * 100)) : null;
      const res = await api.screenCandidates({
        market_hash_names: queue,
        currency_id: 1,
        min_roi_pct: Math.max(0, Number(targetRoiPct) || 5),
        auto_watchlist: autoWatchlist,
        max_budget_minor: budgetMinor,
        strategy,
      });
      setScreenResults(res.candidates);
      setMessage(
        `Screened ${res.screened} items sequentially — ${res.actionable} strong buy/arbitrage signals verified and 30d baselines warmed.`,
      );
      await onRefresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Screener failed.");
    } finally {
      setBusyMode(null);
    }
  };

  const handleAddToWatchlist = async (itemName: string, steamUrl: string, targetBuyMinor?: number | null) => {
    setError(null);
    setBusyMode("action");
    try {
      await api.addWatchlist({
        url: steamUrl,
        strategy: "BUY",
        max_buy_price_minor: targetBuyMinor && targetBuyMinor > 0 ? targetBuyMinor : null,
        min_roi_pct: Math.max(0, Number(targetRoiPct) || 5),
      });
      setWatchedSet((prev) => new Set(prev).add(itemName));
      setMessage(
        targetBuyMinor && targetBuyMinor > 0
          ? `Added ${itemName} to BUY Watchlist with Safe Max Buy Cap ${formatMinor(targetBuyMinor)}.`
          : `Added ${itemName} to BUY Watchlist.`,
      );
      await onRefresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to add item to watchlist.");
    } finally {
      setBusyMode(null);
    }
  };

  const handleTrackPortfolio = async (item: ScreenerCandidate) => {
    const buyCost = item.best_buy_price_minor ?? item.lowest_ask_minor;
    if (!buyCost || buyCost <= 0) {
      setError("No valid buy cost basis to record.");
      return;
    }
    setError(null);
    setBusyMode("action");
    try {
      await api.addHolding({
        market_hash_name: item.market_hash_name,
        acquisition_cost_minor: buyCost,
        restriction_days: 7,
        notes: `Tracked via ${item.best_buy_venue ?? "Discover"}`,
      });
      setTrackedSet((prev) => new Set(prev).add(item.market_hash_name));
      setMessage(`Tracked 1x ${item.market_hash_name} @ ${formatMinor(buyCost)} in 7-Day Portfolio.`);
      await onRefresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to track holding.");
    } finally {
      setBusyMode(null);
    }
  };

  const visibleBrowseRows = useMemo(() => {
    if (browseSource === "steam") {
      return steamItems.map((it) => ({
        key: it.market_hash_name,
        market_hash_name: it.market_hash_name,
        exterior: it.exterior,
        is_stattrak: it.is_stattrak,
        item_nameid: it.item_nameid,
        has_nameid: it.has_trusted_nameid,
        steam_listing_url: it.steam_listing_url,
        icon_url: it.icon_url,
        min_sell_price_minor: it.min_sell_price_minor,
        min_sell_display: it.min_sell_display,
        sell_listings: it.sell_listings,
        currency_symbol: it.currency_symbol,
      }));
    }
    return catalogItems.map((it) => ({
      key: it.market_hash_name,
      market_hash_name: it.market_hash_name,
      exterior: it.exterior,
      is_stattrak: it.is_stattrak,
      item_nameid: it.item_nameid,
      has_nameid: Boolean(it.item_nameid),
      steam_listing_url: it.steam_listing_url,
      icon_url: null as string | null,
      min_sell_price_minor: null as number | null,
      min_sell_display: null as string | null,
      sell_listings: null as number | null,
      currency_symbol: "$",
    }));
  }, [browseSource, steamItems, catalogItems]);

  return (
    <div>
      <div className="panel">
        <div className="section-header">
          <h3 className="section-title">
            <Compass size={18} color="#38bdf8" />
            Best Buy → Sell Opportunity Finder
          </h3>
          <span className="badge badge-blue">Skinport + CS.Market Reference + Live Steam SSR</span>
        </div>

        <div className="banner-info">
          <Sparkles size={16} style={{ flexShrink: 0, marginTop: 2 }} />
          <span>
            Built from audited open-source CS2 scanners: zero-request <strong>Cross-Market Spreads</strong> (External Cash &rarr; Steam Wallet),
            <strong> Wear-Bracket Float Percentiles</strong>, and live <strong>Sticker &amp; Charm Craft Valuation</strong> with exact dual-floor Valve fees.
            Cash costs and closed-loop Steam Wallet proceeds are always tracked separately.
          </span>
        </div>

        <div style={{ marginBottom: 12 }}>
          <div className="pill-bar">
            {STRATEGIES.map((s) => (
              <button
                key={s.id}
                type="button"
                className={`pill-btn ${strategy === s.id ? "active" : ""}`}
                onClick={() => setStrategy(s.id)}
              >
                {s.label}
              </button>
            ))}
          </div>
        </div>

        <form onSubmit={handleSteamSearch} style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <div className="form-grid">
            <label className="field" style={{ gridColumn: "span 2" }}>
              Keyword / Skin / Knife / Case (Optional for Auto-Hunt)
              <input
                placeholder="e.g. Redline, Printstream, Doppler, Asiimov..."
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
            </label>
            <label className="field">
              Wear Exterior
              <select
                value={exterior}
                onChange={(e) => {
                  const val = e.target.value;
                  setExterior(val);
                  if (browseSource === "catalog") void loadCatalog({ ext: val });
                }}
              >
                {EXTERIORS.map((ext) => (
                  <option key={ext || "all"} value={ext}>
                    {ext || "All Wears"}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              Budget Min / Max (USD)
              <div className="row-actions" style={{ flexWrap: "nowrap" }}>
                <input
                  type="number"
                  step="0.50"
                  min="0.50"
                  placeholder="Min $"
                  value={minPriceUsd}
                  onChange={(e) => setMinPriceUsd(e.target.value)}
                />
                <input
                  type="number"
                  step="1"
                  min="1"
                  placeholder="Max $"
                  value={maxPriceUsd}
                  onChange={(e) => setMaxPriceUsd(e.target.value)}
                />
              </div>
            </label>
            <label className="field">
              Min ROI (%) / Hunt Items
              <div className="row-actions" style={{ flexWrap: "nowrap" }}>
                <input
                  type="number"
                  step="1"
                  min="0"
                  placeholder="ROI %"
                  value={targetRoiPct}
                  onChange={(e) => setTargetRoiPct(e.target.value)}
                />
                <select value={huntCount} onChange={(e) => setHuntCount(e.target.value)}>
                  <option value="4">Top 4</option>
                  <option value="6">Top 6</option>
                  <option value="8">Top 8</option>
                </select>
              </div>
            </label>
          </div>

          <div
            style={{
              display: "flex",
              flexWrap: "wrap",
              alignItems: "center",
              justifyContent: "space-between",
              gap: 10,
            }}
          >
            <div className="pill-bar">
              {CATEGORIES.map((c) => (
                <button
                  key={c.id || "all"}
                  type="button"
                  className={`pill-btn ${category === c.id ? "active" : ""}`}
                  onClick={() => {
                    setCategory(c.id);
                    setActivePresetId("");
                    if (browseSource === "catalog") void loadCatalog({ cat: c.id });
                  }}
                >
                  {c.label}
                </button>
              ))}
              <label style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: "0.78rem", color: "#cbd5e1", marginLeft: 4 }}>
                <input
                  type="checkbox"
                  style={{ width: "auto" }}
                  checked={stattrakOnly}
                  onChange={(e) => {
                    setStattrakOnly(e.target.checked);
                    void loadCatalog({ st: e.target.checked });
                  }}
                />
                StatTrak™
              </label>
            </div>

            <div className="row-actions">
              <button
                type="button"
                className="btn"
                disabled={busyMode !== null}
                onClick={() => void loadCatalog()}
              >
                <Search size={14} /> Catalog (26.9k)
              </button>
              <button
                type="submit"
                className="btn"
                disabled={busyMode !== null}
              >
                <TrendingUp size={14} /> {busyMode === "steam" ? "Searching..." : "Search Steam Live"}
              </button>
              <button
                type="button"
                className="btn btn-primary"
                disabled={busyMode !== null}
                onClick={() => void handleLoadCrossMarket()}
              >
                <Flame size={14} /> {busyMode === "cross" ? "Scanning Feed..." : "Cross-Market Radar (0 Steam Req)"}
              </button>
              <button
                type="button"
                className="btn btn-emerald"
                disabled={busyMode !== null}
                onClick={() => void handleAutoHunt()}
              >
                <Sparkles size={14} /> {busyMode === "hunt" ? "Hunting Deals..." : "Auto-Hunt Best Deals"}
              </button>
            </div>
          </div>
        </form>

        {message && <div className="banner-info" style={{ marginTop: 12, marginBottom: 0 }}>{message}</div>}
        {error && <div className="banner-warn" style={{ marginTop: 12, marginBottom: 0 }}>{error}</div>}
      </div>

      {presets.length > 0 && (
        <div className="panel">
          <div className="section-header" style={{ marginBottom: 10 }}>
            <h3 className="section-title">1-Click Liquid Presets</h3>
            <span className="muted" style={{ fontSize: "0.78rem" }}>
              Select a liquid CS2 basket to pre-queue up to 8 items for live Steam verification
            </span>
          </div>
          <div className="preset-grid" style={{ marginBottom: 0 }}>
            {presets.map((p) => (
              <button
                key={p.id}
                type="button"
                className={`preset-card ${activePresetId === p.id ? "active" : ""}`}
                onClick={() => applyPreset(p)}
              >
                <div style={{ fontWeight: 700, fontSize: "0.9rem", marginBottom: 4 }}>{p.label}</div>
                <div className="muted" style={{ fontSize: "0.76rem", lineHeight: 1.35 }}>{p.description}</div>
                <div style={{ marginTop: 8 }}>
                  <span className="badge badge-neutral">{p.items.length} curated items</span>
                </div>
              </button>
            ))}
          </div>
        </div>
      )}

      {screenResults && (
        <div className="panel">
          <div className="section-header">
            <h3 className="section-title">
              Best Verified Buy → Sell Opportunities ({screenResults.length})
            </h3>
            <span className="muted" style={{ fontSize: "0.78rem" }}>
              Includes Recommended Trade Action, Exact Valve Fees, Float Tier &amp; Sticker Value
            </span>
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>CS2 Item &amp; Verdict</th>
                  <th>Recommended Buy &rarr; Sell Trade</th>
                  <th>Steam Live Book &amp; 30d Median</th>
                  <th>Max Safe Steam Buy / Bid Target</th>
                  <th>Float &amp; Craft Alpha</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {screenResults.map((row) => {
                  const sym = row.currency_symbol || "$";
                  const badgeCls =
                    row.verdict_tone === "good"
                      ? "badge-green"
                      : row.verdict_tone === "warn"
                        ? "badge-amber"
                        : "badge-neutral";
                  const isGoodTrade = (row.best_profit_minor ?? -1) > 0;
                  return (
                    <tr key={row.market_hash_name}>
                      <td>
                        <div style={{ fontWeight: 650 }}>{row.market_hash_name}</div>
                        <div className="row-actions" style={{ marginTop: 5 }}>
                          <span className={`badge ${badgeCls}`}>{row.verdict}</span>
                          <span className="badge badge-neutral mono">Score {row.screener_score.toFixed(0)}</span>
                        </div>
                        {row.exit_comparison && row.exit_comparison.length > 0 && (
                          <div className="venue-chips">
                            {row.exit_comparison.map((ex) => (
                              <span key={ex.venue} className="venue-chip mono">
                                {ex.venue}: {formatMinor(ex.net_minor, sym)}
                              </span>
                            ))}
                          </div>
                        )}
                        {row.error && <div className="negative" style={{ fontSize: "0.74rem", marginTop: 4 }}>{row.error}</div>}
                      </td>
                      <td>
                        <div className={`trade-ticket ${isGoodTrade ? "profitable" : ""}`}>
                          <div style={{ fontSize: "0.78rem", fontWeight: 700, display: "flex", alignItems: "center", gap: 4 }}>
                            <span>{row.best_buy_venue ?? "Steam"} {formatMinor(row.best_buy_price_minor, sym)}</span>
                            <ArrowRight size={12} />
                            <span>{row.best_sell_venue ?? "30d Median"} {formatMinor(row.best_sell_gross_minor, sym)}</span>
                          </div>
                          <div className={`mono ${isGoodTrade ? "positive" : "negative"}`} style={{ fontSize: "0.85rem", fontWeight: 700 }}>
                            {formatSignedMinor(row.best_profit_minor, sym)}
                            {row.best_roi_pct !== null && row.best_roi_pct !== undefined
                              ? ` (${row.best_roi_pct > 0 ? "+" : ""}${row.best_roi_pct}% ROI)`
                              : ""}
                          </div>
                          {row.skinport_min_minor && (
                            <div className="muted mono" style={{ fontSize: "0.71rem" }}>
                              Skinport Ask: {formatMinor(row.skinport_min_minor, sym)} ({row.skinport_quantity ?? 0} listed)
                              {row.cross_instant_profit_minor !== null && row.cross_instant_profit_minor !== undefined && row.cross_instant_profit_minor > 0
                                ? ` • Instant Bid Flip +${formatMinor(row.cross_instant_profit_minor, sym)}`
                                : ""}
                            </div>
                          )}
                        </div>
                      </td>
                      <td className="mono">
                        <div>Ask: <strong>{formatMinor(row.lowest_ask_minor, sym)}</strong> | Bid: {formatMinor(row.highest_buy_order_minor, sym)}</div>
                        <div style={{ fontSize: "0.76rem" }}>
                          30d VWM: <strong>{formatMinor(row.vwm_30d_minor, sym)}</strong> &bull; 7d Vol: {row.sales_7d}
                        </div>
                        {row.dip_vs_30d_pct !== null && (
                          <div style={{ fontSize: "0.74rem" }} className={row.dip_vs_30d_pct >= 8 ? "positive" : "muted"}>
                            {row.dip_vs_30d_pct > 0 ? `${row.dip_vs_30d_pct.toFixed(1)}% below 30d median` : `${Math.abs(row.dip_vs_30d_pct).toFixed(1)}% above median`}
                          </div>
                        )}
                      </td>
                      <td>
                        {row.target_buy_minor > 0 ? (
                          <div className="target-box mono">
                            <span style={{ fontSize: "0.84rem", fontWeight: 700, color: "#6ee7b7" }}>
                              &le; {formatMinor(row.target_buy_minor, sym)}
                            </span>
                            <span className="muted" style={{ fontSize: "0.71rem" }}>
                              Breakeven &le; {formatMinor(row.breakeven_buy_minor, sym)}
                            </span>
                          </div>
                        ) : (
                          <span className="muted">Need history</span>
                        )}
                      </td>
                      <td style={{ fontSize: "0.77rem" }}>
                        {row.lowest_float !== null ? (
                          <div className="mono">Float {row.lowest_float.toFixed(4)}</div>
                        ) : (
                          <div className="muted">Float N/A</div>
                        )}
                        {row.float_alpha_badge && (
                          <div className="badge badge-blue" style={{ marginTop: 4 }}>{row.float_alpha_badge}</div>
                        )}
                        {row.craft_badge ? (
                          <div className="badge badge-amber" style={{ marginTop: 4 }}>{row.craft_badge}</div>
                        ) : row.notable_stickers.length > 0 ? (
                          <div className="badge badge-amber" style={{ marginTop: 4 }}>{row.notable_stickers[0]}</div>
                        ) : null}
                      </td>
                      <td>
                        <div className="row-actions">
                          <button
                            type="button"
                            className="btn btn-xs btn-primary"
                            disabled={busyMode !== null || watchedSet.has(row.market_hash_name) || row.watchlist_added}
                            onClick={() =>
                              void handleAddToWatchlist(
                                row.market_hash_name,
                                row.steam_listing_url,
                                row.target_buy_minor,
                              )
                            }
                          >
                            {watchedSet.has(row.market_hash_name) || row.watchlist_added ? (
                              <>
                                <Check size={12} /> Watched
                              </>
                            ) : (
                              <>
                                <Plus size={12} /> Watch
                              </>
                            )}
                          </button>
                          {(row.best_buy_price_minor || row.lowest_ask_minor) && (
                            <button
                              type="button"
                              className="btn btn-xs"
                              disabled={busyMode !== null || trackedSet.has(row.market_hash_name)}
                              onClick={() => void handleTrackPortfolio(row)}
                            >
                              <Briefcase size={12} /> {trackedSet.has(row.market_hash_name) ? "Tracked" : "Track Buy"}
                            </button>
                          )}
                          <a href={row.steam_listing_url} target="_blank" rel="noreferrer" className="steam-link">
                            Steam <ExternalLink size={11} />
                          </a>
                          {row.skinport_url && (
                            <a href={row.skinport_url} target="_blank" rel="noreferrer" className="steam-link">
                              Skinport <ExternalLink size={11} />
                            </a>
                          )}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {browseSource === "cross_market" ? (
        <div className="panel">
          <div className="section-header">
            <h3 className="section-title">
              Instant Cross-Market Spreads ({crossItems.length} Ranked &bull; Buy Cash &rarr; Sell Steam)
            </h3>
            <button
              type="button"
              className="btn btn-xs btn-emerald"
              disabled={busyMode !== null || crossItems.length === 0}
              onClick={() => void handleRunScreener(crossItems.slice(0, 6).map((x) => x.market_hash_name))}
            >
              Verify Top 6 on Live Steam
            </button>
          </div>
          {crossItems.length === 0 ? (
            <div className="muted">No cross-market spreads matched filters (adjust Min ROI % or Budget filters).</div>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th style={{ width: 42 }}>Pick</th>
                    <th>CS2 Item &amp; Supply</th>
                    <th>Skinport Cash Ask</th>
                    <th>Ref Steam Sell &rarr; Est. Net</th>
                    <th>Est. Wallet Spread</th>
                    <th>Quick Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {crossItems.map((it) => (
                    <tr key={it.market_hash_name}>
                      <td>
                        <input
                          type="checkbox"
                          style={{ width: "auto", cursor: "pointer" }}
                          checked={selectedNames.includes(it.market_hash_name)}
                          onChange={() => toggleSelect(it.market_hash_name)}
                        />
                      </td>
                      <td>
                        <div style={{ fontWeight: 650 }}>{it.market_hash_name}</div>
                        <div className="muted" style={{ fontSize: "0.74rem" }}>
                          Skinport Stock: {it.skinport_quantity}
                          {it.csmarket_min_minor ? ` • CS.Market: ${formatMinor(it.csmarket_min_minor, it.currency_symbol)}` : ""}
                        </div>
                      </td>
                      <td className="mono">
                        <strong>{formatMinor(it.skinport_min_minor, it.currency_symbol)}</strong>
                      </td>
                      <td className="mono">
                        <div>Ref: {formatMinor(it.skinport_suggested_minor, it.currency_symbol)}</div>
                        <div className="muted" style={{ fontSize: "0.74rem" }}>
                          Steam Net: {formatMinor(it.est_steam_net_minor, it.currency_symbol)}
                        </div>
                      </td>
                      <td className="mono positive">
                        <strong>{formatSignedMinor(it.est_wallet_profit_minor, it.currency_symbol)}</strong>{" "}
                        <span>(+{it.est_wallet_roi_pct}%)</span>
                      </td>
                      <td>
                        <div className="row-actions">
                          <button
                            type="button"
                            className="btn btn-xs btn-primary"
                            disabled={busyMode !== null}
                            onClick={() => void handleRunScreener([it.market_hash_name])}
                          >
                            Verify Live Steam
                          </button>
                          {it.skinport_url && (
                            <a href={it.skinport_url} target="_blank" rel="noreferrer" className="steam-link">
                              Skinport <ExternalLink size={11} />
                            </a>
                          )}
                          <a href={it.steam_listing_url} target="_blank" rel="noreferrer" className="steam-link">
                            Steam <ExternalLink size={11} />
                          </a>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      ) : (
        <div className="panel">
          <div className="section-header">
            <h3 className="section-title">
              {browseSource === "steam"
                ? `Live Steam Market Results (${visibleBrowseRows.length})`
                : `Verified Offline Catalog Results (${visibleBrowseRows.length})`}
            </h3>
            <div className="row-actions">
              <button
                type="button"
                className="btn btn-xs"
                disabled={visibleBrowseRows.length === 0}
                onClick={() => setSelectedNames(visibleBrowseRows.slice(0, 8).map((r) => r.market_hash_name))}
              >
                Select Top 8
              </button>
              <button
                type="button"
                className="btn btn-xs"
                disabled={selectedNames.length === 0}
                onClick={() => setSelectedNames([])}
              >
                Clear ({selectedNames.length})
              </button>
            </div>
          </div>

          {visibleBrowseRows.length === 0 ? (
            <div className="muted">No matching items found. Try Cross-Market Radar or Search Steam Live.</div>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th style={{ width: 42 }}>Pick</th>
                    <th>CS2 Market Hash Name</th>
                    <th>Wear / Flags</th>
                    <th>Live Lowest Ask</th>
                    <th>Active Supply</th>
                    <th>Identity Provenance</th>
                    <th>Quick Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {visibleBrowseRows.map((row) => {
                    const isSelected = selectedNames.includes(row.market_hash_name);
                    const isWatched = watchedSet.has(row.market_hash_name);
                    return (
                      <tr key={row.key}>
                        <td>
                          <input
                            type="checkbox"
                            style={{ width: "auto", cursor: "pointer" }}
                            checked={isSelected}
                            onChange={() => toggleSelect(row.market_hash_name)}
                          />
                        </td>
                        <td>
                          <div className="item-cell">
                            {row.icon_url && <img src={row.icon_url} alt="" className="item-thumb" />}
                            <div>
                              <div style={{ fontWeight: 650 }}>{row.market_hash_name}</div>
                              <a href={row.steam_listing_url} target="_blank" rel="noreferrer" className="steam-link">
                                View on Steam Market <ExternalLink size={11} />
                              </a>
                            </div>
                          </div>
                        </td>
                        <td>
                          <div className="row-actions">
                            {row.exterior && <span className="badge badge-neutral">{row.exterior}</span>}
                            {row.is_stattrak && <span className="badge badge-amber">StatTrak™</span>}
                          </div>
                        </td>
                        <td className="mono">
                          {row.min_sell_price_minor !== null
                            ? formatMinor(row.min_sell_price_minor, row.currency_symbol)
                            : (row.min_sell_display ?? "Click Analyze")}
                        </td>
                        <td className="mono">
                          {row.sell_listings !== null ? row.sell_listings.toLocaleString() : "—"}
                        </td>
                        <td>
                          {row.has_nameid ? (
                            <span className="badge badge-green mono">ID {row.item_nameid}</span>
                          ) : (
                            <span className="badge badge-neutral">SSR Canonical</span>
                          )}
                        </td>
                        <td>
                          <div className="row-actions">
                            <button
                              type="button"
                              className="btn btn-xs"
                              disabled={busyMode !== null}
                              onClick={() => void handleRunScreener([row.market_hash_name])}
                            >
                              Analyze 1
                            </button>
                            <button
                              type="button"
                              className="btn btn-xs btn-primary"
                              disabled={busyMode !== null || isWatched}
                              onClick={() => void handleAddToWatchlist(row.market_hash_name, row.steam_listing_url)}
                            >
                              {isWatched ? "Watched" : "+ Watch"}
                            </button>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {selectedNames.length > 0 && (
        <div className="queue-bar">
          <div>
            <strong>{selectedNames.length} / 8 items queued</strong> for strictly read-only Auto-Hunt analysis
            <div className="muted" style={{ fontSize: "0.75rem" }}>
              Pulls live individual listings, 30d history, wear float percentile &amp; cross-market spreads (~{selectedNames.length * 5}s).
            </div>
          </div>
          <div className="row-actions">
            <label style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: "0.78rem", color: "#cbd5e1" }}>
              <input
                type="checkbox"
                style={{ width: "auto" }}
                checked={autoWatchlist}
                onChange={(e) => setAutoWatchlist(e.target.checked)}
              />
              Auto-Watch with Safe Target Cap
            </label>
            <button
              type="button"
              className="btn btn-emerald"
              disabled={busyMode !== null}
              onClick={() => void handleRunScreener()}
            >
              <Sparkles size={14} />
              {busyMode === "screen" ? "Analyzing Sequentially..." : `Analyze Selected (${selectedNames.length})`}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
