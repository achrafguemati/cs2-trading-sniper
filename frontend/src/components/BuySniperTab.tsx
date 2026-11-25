import { useMemo, useState } from "react";
import {
  Briefcase,
  ChevronDown,
  ChevronUp,
  Compass,
  Crosshair,
  ExternalLink,
  Plus,
  RefreshCw,
  Trash2,
} from "lucide-react";
import type { BuyCandidate, WatchlistItem } from "../types";
import { api, formatMinor, formatSignedMinor } from "../api/client";

interface Props {
  watchlists: WatchlistItem[];
  candidates: BuyCandidate[];
  onRefresh: () => Promise<void>;
  onNavigate?: (tab: "discover") => void;
}

type CandidateFilter = "all" | "active" | "actionable" | "best_per_item";

export function BuySniperTab({ watchlists, candidates, onRefresh, onNavigate }: Props) {
  const [url, setUrl] = useState("");
  const [maxPriceUsd, setMaxPriceUsd] = useState("");
  const [minRoiPct, setMinRoiPct] = useState("5");
  const [showManualForm, setShowManualForm] = useState(watchlists.length === 0);
  const [filterMode, setFilterMode] = useState<CandidateFilter>("best_per_item");
  const [busy, setBusy] = useState(false);
  const [trackedIds, setTrackedIds] = useState<Set<number>>(new Set());
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handleAdd = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setMessage(null);
    setBusy(true);
    try {
      const maxPriceMinor = maxPriceUsd ? Math.round(Number(maxPriceUsd) * 100) : null;
      await api.addWatchlist({
        url: url.trim(),
        strategy: "BUY",
        max_buy_price_minor: maxPriceMinor && maxPriceMinor > 0 ? maxPriceMinor : null,
        min_roi_pct: Number(minRoiPct) || 5,
      });
      setUrl("");
      setMaxPriceUsd("");
      setShowManualForm(false);
      setMessage("Watchlist item added safely (offline identity resolved).");
      await onRefresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to add watchlist URL.");
    } finally {
      setBusy(false);
    }
  };

  const handleScanNow = async () => {
    setError(null);
    setMessage(null);
    setBusy(true);
    try {
      const res = (await api.triggerBuyScan(true)) as { evaluated: number; actionable: number };
      setMessage(`BUY scan completed: ${res.evaluated} listings evaluated, ${res.actionable} actionable alerts.`);
      await onRefresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Scan failed.");
    } finally {
      setBusy(false);
    }
  };

  const handleToggle = async (w: WatchlistItem) => {
    await api.toggleWatchlist(w.watchlist_id, !w.is_active);
    await onRefresh();
  };

  const handleDelete = async (id: number) => {
    await api.deleteWatchlist(id);
    await onRefresh();
  };

  const handleTrackBuy = async (c: BuyCandidate) => {
    setError(null);
    setBusy(true);
    try {
      await api.addHolding({
        market_hash_name: c.market_hash_name,
        acquisition_cost_minor: c.purchase_buyer_pays_minor,
        restriction_days: 7,
        notes: `Tracked from BUY Sniper listing #${c.steam_listing_id}`,
      });
      setTrackedIds((prev) => new Set(prev).add(c.evaluation_id));
      setMessage(`Tracked ${c.market_hash_name} @ ${formatMinor(c.purchase_buyer_pays_minor, c.currency_symbol)} in 7-Day Portfolio.`);
      await onRefresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to add holding.");
    } finally {
      setBusy(false);
    }
  };

  const filteredCandidates = useMemo(() => {
    if (filterMode === "actionable") {
      return candidates.filter((c) => c.is_actionable);
    }
    if (filterMode === "active") {
      return candidates.filter((c) => c.listing_status === "active");
    }
    if (filterMode === "best_per_item") {
      const seen = new Set<string>();
      const best: BuyCandidate[] = [];
      const sorted = [...candidates].sort((a, b) => {
        if (a.listing_status !== b.listing_status) return a.listing_status === "active" ? -1 : 1;
        if (a.is_actionable !== b.is_actionable) return a.is_actionable ? -1 : 1;
        return b.conservative_roi_pct - a.conservative_roi_pct;
      });
      for (const c of sorted) {
        const k = `${c.market_hash_name}:${c.steam_listing_id}`;
        if (!seen.has(k)) {
          seen.add(k);
          best.push(c);
        }
      }
      return best;
    }
    return candidates;
  }, [candidates, filterMode]);

  return (
    <div>
      <div className="panel">
        <div className="section-header">
          <h3 className="section-title">
            <Crosshair size={18} color="#38bdf8" />
            BUY Watchlist ({watchlists.length})
          </h3>
          <div className="row-actions">
            {onNavigate && (
              <button type="button" className="btn" onClick={() => onNavigate("discover")}>
                <Compass size={14} /> Browse &amp; Find Deals
              </button>
            )}
            <button
              type="button"
              className="btn"
              onClick={() => setShowManualForm((prev) => !prev)}
            >
              <Plus size={14} /> Add Steam URL
              {showManualForm ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
            </button>
            <button className="btn btn-primary" disabled={busy} onClick={() => void handleScanNow()}>
              <RefreshCw size={14} /> {busy ? "Scanning..." : "Run BUY Scan"}
            </button>
          </div>
        </div>

        {showManualForm && (
          <form onSubmit={handleAdd} className="form-grid" style={{ marginBottom: 14, paddingTop: 4 }}>
            <label className="field" style={{ gridColumn: "span 2" }}>
              Steam CS2 Listing URL
              <input
                type="url"
                required
                placeholder="https://steamcommunity.com/market/listings/730/AK-47%20%7C%20Redline%20%28Field-Tested%29"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
              />
            </label>
            <label className="field">
              Max Buy Price (USD optional)
              <input
                type="number"
                step="0.01"
                min="0.03"
                placeholder="e.g. 24.50"
                value={maxPriceUsd}
                onChange={(e) => setMaxPriceUsd(e.target.value)}
              />
            </label>
            <label className="field">
              Min Cons. ROI (%)
              <input
                type="number"
                step="0.5"
                min="0"
                value={minRoiPct}
                onChange={(e) => setMinRoiPct(e.target.value)}
              />
            </label>
            <button type="submit" className="btn btn-emerald" disabled={busy}>
              Save Watchlist Item
            </button>
          </form>
        )}

        {message && <div className="banner-info">{message}</div>}
        {error && <div className="banner-warn">{error}</div>}

        {watchlists.length === 0 ? (
          <div className="muted" style={{ fontSize: "0.86rem" }}>
            No BUY watchlist items yet. Use <strong>Discover Deals</strong> to auto-find liquid skins or paste a Steam URL above.
          </div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>CS2 Item</th>
                  <th>Status</th>
                  <th>Max Buy Cap</th>
                  <th>Min Cons. ROI</th>
                  <th>Interval</th>
                  <th>Controls</th>
                </tr>
              </thead>
              <tbody>
                {watchlists.map((w) => (
                  <tr key={w.watchlist_id}>
                    <td>
                      <div style={{ fontWeight: 650 }}>{w.market_hash_name}</div>
                      <a href={w.input_url} target="_blank" rel="noreferrer" className="steam-link">
                        Open Steam Listing <ExternalLink size={11} />
                      </a>
                    </td>
                    <td>
                      <span className={`badge ${w.is_active ? "badge-green" : "badge-neutral"}`}>
                        {w.is_active ? "Active" : "Paused"}
                      </span>
                    </td>
                    <td className="mono">{w.max_buy_price_minor ? formatMinor(w.max_buy_price_minor) : "Any (ROI gated)"}</td>
                    <td className="mono">{w.min_roi_pct}%</td>
                    <td className="mono">{w.poll_interval_seconds}s</td>
                    <td>
                      <div className="row-actions">
                        <button className="btn btn-xs" onClick={() => void handleToggle(w)}>
                          {w.is_active ? "Pause" : "Resume"}
                        </button>
                        <button className="btn btn-xs btn-danger" onClick={() => void handleDelete(w.watchlist_id)}>
                          <Trash2 size={12} /> Remove
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="panel">
        <div className="section-header">
          <h3 className="section-title">BUY Evaluations &amp; Target Entry Prices ({filteredCandidates.length})</h3>
          <div className="pill-bar">
            {([
              ["best_per_item", "Clean View"],
              ["active", "Active Only"],
              ["actionable", "Actionable Only"],
              ["all", `All (${candidates.length})`],
            ] as const).map(([key, label]) => (
              <button
                key={key}
                type="button"
                className={`pill-btn ${filterMode === key ? "active" : ""}`}
                onClick={() => setFilterMode(key)}
              >
                {label}
              </button>
            ))}
          </div>
        </div>

        {filteredCandidates.length === 0 ? (
          <div className="muted">No matching BUY evaluations found for this filter.</div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>CS2 Item &amp; Listing</th>
                  <th>Ask Price</th>
                  <th>30d VWM Exit</th>
                  <th>Max Safe Buy Target</th>
                  <th>7d Cons. Profit &amp; ROI</th>
                  <th>Float / Seed / Craft</th>
                  <th>Verdict &amp; Action</th>
                </tr>
              </thead>
              <tbody>
                {filteredCandidates.map((c) => {
                  const isProfitableAsk =
                    c.target_buy_price_minor !== null &&
                    c.target_buy_price_minor !== undefined &&
                    c.purchase_buyer_pays_minor <= c.target_buy_price_minor;
                  return (
                    <tr key={c.evaluation_id}>
                      <td>
                        <div style={{ fontWeight: 650 }}>{c.market_hash_name}</div>
                        <div className="muted mono" style={{ fontSize: "0.75rem" }}>
                          #{c.steam_listing_id} &bull; {c.listing_status}
                        </div>
                      </td>
                      <td className="mono">
                        <strong>{formatMinor(c.purchase_buyer_pays_minor, c.currency_symbol)}</strong>
                      </td>
                      <td className="mono">
                        <div>{formatMinor(c.reference_price_minor, c.currency_symbol)}</div>
                        <div className="muted" style={{ fontSize: "0.74rem" }}>
                          30d Exit Net: {formatSignedMinor(c.base_net_profit_minor, c.currency_symbol)}
                        </div>
                      </td>
                      <td>
                        {c.target_buy_price_minor ? (
                          <div className="target-box mono">
                            <span style={{ fontSize: "0.82rem", fontWeight: 700, color: isProfitableAsk ? "#6ee7b7" : "#7dd3fc" }}>
                              &le; {formatMinor(c.target_buy_price_minor, c.currency_symbol)}
                            </span>
                            <span className="muted" style={{ fontSize: "0.7rem" }}>
                              Breakeven &le; {formatMinor(c.breakeven_buy_price_minor, c.currency_symbol)}
                            </span>
                          </div>
                        ) : (
                          <span className="muted">—</span>
                        )}
                      </td>
                      <td className={`mono ${c.conservative_net_profit_minor >= 0 ? "positive" : "negative"}`}>
                        <strong>{formatSignedMinor(c.conservative_net_profit_minor, c.currency_symbol)}</strong>{" "}
                        <span>({c.conservative_roi_pct}%)</span>
                      </td>
                      <td style={{ fontSize: "0.78rem" }}>
                        <div className="mono">
                          {c.float_value !== null ? `Float ${c.float_value.toFixed(4)}` : "Float N/A"}
                          {c.paint_seed !== null ? ` • Seed ${c.paint_seed}` : ""}
                        </div>
                        {c.stickers.length > 0 && (
                          <div className="badge badge-amber" style={{ marginTop: 4 }}>
                            {c.stickers.join(", ")}
                          </div>
                        )}
                      </td>
                      <td>
                        <div style={{ display: "flex", flexDirection: "column", gap: 6, alignItems: "flex-start" }}>
                          <span className={`badge ${c.is_actionable ? "badge-green" : "badge-amber"}`}>
                            {c.status}
                          </span>
                          <div className="row-actions">
                            <button
                              type="button"
                              className="btn btn-xs"
                              disabled={busy || trackedIds.has(c.evaluation_id)}
                              onClick={() => void handleTrackBuy(c)}
                            >
                              <Briefcase size={11} /> {trackedIds.has(c.evaluation_id) ? "Tracked" : "Track Buy"}
                            </button>
                            <a href={c.steam_listing_url} target="_blank" rel="noreferrer" className="steam-link">
                              Steam <ExternalLink size={11} />
                            </a>
                          </div>
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
    </div>
  );
}
