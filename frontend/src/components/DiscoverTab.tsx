import { useEffect, useMemo, useState } from "react";
import {
  Briefcase,
  Check,
  Compass,
  ExternalLink,
  Plus,
  Search,
  Sparkles,
  TrendingUp,
} from "lucide-react";
import type {
  CatalogItem,
  CatalogPreset,
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

const EXTERIORS = ["", "Factory New", "Minimal Wear", "Field-Tested", "Well-Worn", "Battle-Scarred"];

export function DiscoverTab({ defaultMinRoiPct, onRefresh }: Props) {
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("");
  const [exterior, setExterior] = useState("");
  const [stattrakOnly, setStattrakOnly] = useState(false);
  const [minPriceUsd, setMinPriceUsd] = useState("1.00");
  const [maxPriceUsd, setMaxPriceUsd] = useState("");
  const [minSupply, setMinSupply] = useState("10");
  const [targetRoiPct, setTargetRoiPct] = useState(String(defaultMinRoiPct || 5));
  const [autoWatchlist, setAutoWatchlist] = useState(false);

  const [presets, setPresets] = useState<CatalogPreset[]>([]);
  const [activePresetId, setActivePresetId] = useState<string>("");
  const [catalogItems, setCatalogItems] = useState<CatalogItem[]>([]);
  const [steamItems, setSteamItems] = useState<SteamDiscoverItem[] | null>(null);
  const [screenResults, setScreenResults] = useState<ScreenerCandidate[] | null>(null);
  const [selectedNames, setSelectedNames] = useState<string[]>([]);
  const [watchedSet, setWatchedSet] = useState<Set<string>>(new Set());
  const [trackedSet, setTrackedSet] = useState<Set<string>>(new Set());

  const [busyMode, setBusyMode] = useState<"catalog" | "steam" | "screen" | "action" | null>(null);
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
      setSteamItems(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Catalog search failed.");
    } finally {
      setBusyMode(null);
    }
  };

  useEffect(() => {
    void loadCatalog({ q: "", cat: "", ext: "", st: false });
  }, []);

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
    setSteamItems(null);
    setCatalogItems(preset.items);
    setSelectedNames(preset.items.slice(0, 8).map((i) => i.market_hash_name));
    setError(null);
    setMessage(`Loaded "${preset.label}" (${preset.items.length} liquid items). Click Analyze Deals to pull live asks, bids & 30d baselines.`);
  };

  const handleRunScreener = async (namesOverride?: string[]) => {
    const queue = (namesOverride ?? selectedNames).slice(0, 8);
    if (queue.length === 0) {
      setError("Select up to 8 items first, or click a Liquid Preset card.");
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
      });
      setScreenResults(res.candidates);
      setMessage(
        `Screened ${res.screened} items sequentially at safe pacing — ${res.actionable} strong buy/dip signals detected and 30d baselines warmed.`,
      );
      await onRefresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Auto-Hunt screener failed.");
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
    if (!item.lowest_ask_minor || item.lowest_ask_minor <= 0) {
      setError("No valid ask price to record cost basis.");
      return;
    }
    setError(null);
    setBusyMode("action");
    try {
      await api.addHolding({
        market_hash_name: item.market_hash_name,
        acquisition_cost_minor: item.lowest_ask_minor,
        restriction_days: 7,
        notes: "Tracked from Discover Auto-Hunt",
      });
      setTrackedSet((prev) => new Set(prev).add(item.market_hash_name));
      setMessage(`Tracked 1x ${item.market_hash_name} @ ${formatMinor(item.lowest_ask_minor)} in 7-Day Portfolio.`);
      await onRefresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to track holding.");
    } finally {
      setBusyMode(null);
    }
  };

  const visibleBrowseRows = useMemo(() => {
    if (steamItems !== null) {
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
  }, [steamItems, catalogItems]);

  return (
    <div>
      <div className="panel">
        <div className="section-header">
          <h3 className="section-title">
            <Compass size={18} color="#38bdf8" />
            Discover CS2 Items &amp; Auto-Hunt Buy-to-Sell Opportunities
          </h3>
          <span className="badge badge-blue">26,935 Offline Mappings + Live Steam SSR Search</span>
        </div>
        <div className="banner-info">
          <Sparkles size={16} style={{ flexShrink: 0, marginTop: 2 }} />
          <span>
            Stop hunting Steam URLs manually. Search locally in <strong>0ms</strong> or run a <strong>Live Steam Market Search</strong>,
            then screen up to 8 items at safe sequential pacing (&le;12 RPM) to see exact <strong>Max Safe Buy Targets</strong> after 15% modeled Valve fees, 30d median dips, and sticker/float alpha.
          </span>
        </div>

        <form
          onSubmit={handleSteamSearch}
          style={{ display: "flex", flexDirection: "column", gap: 12 }}
        >
          <div className="form-grid">
            <label className="field" style={{ gridColumn: "span 2" }}>
              Keyword / Skin / Knife / Case
              <input
                placeholder="e.g. Redline, Doppler, Printstream, Asiimov, Kilowatt..."
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
            </label>
            <label className="field">
              Wear Exterior (Catalog)
              <select
                value={exterior}
                onChange={(e) => {
                  const val = e.target.value;
                  setExterior(val);
                  void loadCatalog({ ext: val });
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
              Min / Max Budget (USD)
              <div className="row-actions" style={{ flexWrap: "nowrap" }}>
                <input
                  type="number"
                  step="0.50"
                  min="0"
                  placeholder="Min $"
                  value={minPriceUsd}
                  onChange={(e) => setMinPriceUsd(e.target.value)}
                />
                <input
                  type="number"
                  step="1"
                  min="0"
                  placeholder="Max $"
                  value={maxPriceUsd}
                  onChange={(e) => setMaxPriceUsd(e.target.value)}
                />
              </div>
            </label>
            <label className="field">
              Target Exit ROI (%) / Min Supply
              <div className="row-actions" style={{ flexWrap: "nowrap" }}>
                <input
                  type="number"
                  step="1"
                  min="0"
                  placeholder="ROI %"
                  value={targetRoiPct}
                  onChange={(e) => setTargetRoiPct(e.target.value)}
                />
                <input
                  type="number"
                  min="0"
                  placeholder="Supply"
                  value={minSupply}
                  onChange={(e) => setMinSupply(e.target.value)}
                />
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
                    void loadCatalog({ cat: c.id });
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
                StatTrak™ only
              </label>
            </div>

            <div className="row-actions">
              <button
                type="button"
                className="btn"
                disabled={busyMode !== null}
                onClick={() => void loadCatalog()}
              >
                <Search size={14} />
                Filter Local Catalog
              </button>
              <button
                type="submit"
                className="btn btn-primary"
                disabled={busyMode !== null}
              >
                <TrendingUp size={14} />
                {busyMode === "steam" ? "Searching Steam..." : "Search Live Steam Market"}
              </button>
            </div>
          </div>
        </form>

        {message && (
          <div className="banner-info" style={{ marginTop: 12, marginBottom: 0 }}>
            {message}
          </div>
        )}
        {error && (
          <div className="banner-warn" style={{ marginTop: 12, marginBottom: 0 }}>
            {error}
          </div>
        )}
      </div>

      {presets.length > 0 && (
        <div className="panel">
          <div className="section-header" style={{ marginBottom: 10 }}>
            <h3 className="section-title">1-Click Liquid Presets</h3>
            <span className="muted" style={{ fontSize: "0.78rem" }}>
              Select a market basket to auto-queue up to 8 liquid items for deep Buy→Sell screening
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
              Auto-Hunt Opportunity Matrix ({screenResults.length} Screened)
            </h3>
            <span className="muted" style={{ fontSize: "0.78rem" }}>
              Sorted by Spread + Dip % + Liquidity &bull; Exact Integer Valve Fees Included
            </span>
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>CS2 Item &amp; Verdict</th>
                  <th>Live Ask / Top Bid</th>
                  <th>30d Median &amp; Volume</th>
                  <th>If Bought @ Ask → 30d Exit</th>
                  <th>Max Safe Buy / Bid Target</th>
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
                  return (
                    <tr key={row.market_hash_name}>
                      <td>
                        <div style={{ fontWeight: 650 }}>{row.market_hash_name}</div>
                        <div className="row-actions" style={{ marginTop: 5 }}>
                          <span className={`badge ${badgeCls}`}>{row.verdict}</span>
                          <span className="badge badge-neutral mono">Score {row.screener_score.toFixed(0)}</span>
                          {row.has_trusted_nameid && <span className="badge badge-blue">Verified ID</span>}
                        </div>
                        {row.error && <div className="negative" style={{ fontSize: "0.74rem", marginTop: 4 }}>{row.error}</div>}
                      </td>
                      <td className="mono">
                        <div>Ask: <strong>{formatMinor(row.lowest_ask_minor, sym)}</strong></div>
                        <div className="muted" style={{ fontSize: "0.76rem" }}>
                          Bid: {formatMinor(row.highest_buy_order_minor, sym)}
                          {row.bid_ask_spread_pct !== null ? ` (${row.bid_ask_spread_pct.toFixed(1)}% spread)` : ""}
                        </div>
                      </td>
                      <td className="mono">
                        <div>30d VWM: <strong>{formatMinor(row.vwm_30d_minor, sym)}</strong></div>
                        <div style={{ fontSize: "0.76rem" }}>
                          7d Vol: {row.sales_7d} &bull;{" "}
                          <span className={(row.dip_vs_30d_pct ?? 0) >= 8 ? "positive" : "muted"}>
                            {row.dip_vs_30d_pct !== null
                              ? `${row.dip_vs_30d_pct > 0 ? "-" : "+"}${Math.abs(row.dip_vs_30d_pct).toFixed(1)}% vs 30d`
                              : "No baseline"}
                          </span>
                        </div>
                      </td>
                      <td className="mono">
                        {row.profit_at_baseline_minor !== null ? (
                          <>
                            <div className={row.profit_at_baseline_minor >= 0 ? "positive" : "negative"}>
                              <strong>{formatSignedMinor(row.profit_at_baseline_minor, sym)}</strong>{" "}
                              ({row.roi_at_baseline_pct !== null ? `${row.roi_at_baseline_pct > 0 ? "+" : ""}${row.roi_at_baseline_pct}%` : "—"})
                            </div>
                            <div className="muted" style={{ fontSize: "0.74rem" }}>
                              Breakeven Exit &ge; {formatMinor(row.breakeven_exit_for_ask_minor, sym)}
                            </div>
                          </>
                        ) : (
                          <span className="muted">—</span>
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
                      <td style={{ fontSize: "0.78rem" }}>
                        {row.lowest_float !== null ? (
                          <div className="mono">Float {row.lowest_float.toFixed(4)}</div>
                        ) : (
                          <div className="muted">Float N/A</div>
                        )}
                        {row.notable_stickers.length > 0 && (
                          <div className="badge badge-amber" style={{ marginTop: 4 }}>
                            {row.notable_stickers[0]}
                          </div>
                        )}
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
                          {row.lowest_ask_minor && (
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

      <div className="panel">
        <div className="section-header">
          <h3 className="section-title">
            {steamItems !== null
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
          <div className="muted">No matching items found. Try clearing filters or running a Live Steam Market Search.</div>
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

      {selectedNames.length > 0 && (
        <div className="queue-bar">
          <div>
            <strong>{selectedNames.length} / 8 items queued</strong> for strictly read-only Auto-Hunt analysis
            <div className="muted" style={{ fontSize: "0.75rem" }}>
              Pulls live individual listings, orderbook top bid &amp; 30d history in 1 sequential GET per item (~{selectedNames.length * 5}s).
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
