import { useState } from "react";
import type { BuyCandidate, WatchlistItem } from "../types";
import { api, formatMinor, formatSignedMinor } from "../api/client";

interface Props {
  watchlists: WatchlistItem[];
  candidates: BuyCandidate[];
  onRefresh: () => Promise<void>;
}

export function BuySniperTab({ watchlists, candidates, onRefresh }: Props) {
  const [url, setUrl] = useState("");
  const [strategy, setStrategy] = useState("BUY");
  const [maxPriceUsd, setMaxPriceUsd] = useState("");
  const [minRoi, setMinRoi] = useState("5.0");
  const [onlyActionable, setOnlyActionable] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handleAddWatchlist = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setMessage(null);
    setBusy(true);
    try {
      const parsedUsd = maxPriceUsd.trim() ? Math.round(Number(maxPriceUsd) * 100) : null;
      await api.addWatchlist({
        url,
        strategy,
        max_buy_price_minor: parsedUsd && parsedUsd > 0 ? parsedUsd : null,
        min_roi_pct: Number(minRoi) || 5.0,
      });
      setUrl("");
      setMaxPriceUsd("");
      setMessage("Watchlist item resolved and saved.");
      await onRefresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to add watchlist URL.");
    } finally {
      setBusy(false);
    }
  };

  const handleScanBuy = async () => {
    setError(null);
    setMessage(null);
    setBusy(true);
    try {
      const res = await api.scanBuy(true);
      setMessage(`BUY scan complete: ${res.evaluated} listing(s) evaluated, ${res.actionable} actionable signal(s).`);
      await onRefresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "BUY scan failed.");
    } finally {
      setBusy(false);
    }
  };

  const handleToggle = async (item: WatchlistItem) => {
    await api.toggleWatchlist(item.watchlist_id, !item.is_active);
    await onRefresh();
  };

  const handleDelete = async (id: number) => {
    await api.deleteWatchlist(id);
    await onRefresh();
  };

  const shownCandidates = onlyActionable ? candidates.filter((c) => c.is_actionable) : candidates;

  return (
    <div>
      <div className="panel">
        <div className="section-header">
          <h3 className="section-title">Add Steam URL to Watchlist (Standard or Grouped G...)</h3>
          <button className="btn btn-primary" disabled={busy} onClick={handleScanBuy}>
            {busy ? "Scanning..." : "Run Paced BUY Scan"}
          </button>
        </div>

        <form onSubmit={handleAddWatchlist} className="form-grid">
          <label className="field" style={{ gridColumn: "span 2" }}>
            Steam Community Market URL
            <input
              type="url"
              required
              placeholder="https://steamcommunity.com/market/listings/730/G18820420D5083003?category_Exterior=WearCategory2&appid=730"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
            />
          </label>
          <label className="field">
            Strategy
            <select value={strategy} onChange={(e) => setStrategy(e.target.value)}>
              <option value="BUY">BUY</option>
              <option value="SELL">SELL</option>
              <option value="BOTH">BOTH</option>
            </select>
          </label>
          <label className="field">
            Max Buyer-Paid (USD)
            <input
              type="number"
              step="0.01"
              min="0.03"
              placeholder="Optional"
              value={maxPriceUsd}
              onChange={(e) => setMaxPriceUsd(e.target.value)}
            />
          </label>
          <label className="field">
            Min Conservative ROI %
            <input
              type="number"
              step="0.5"
              min="0"
              value={minRoi}
              onChange={(e) => setMinRoi(e.target.value)}
            />
          </label>
          <button type="submit" className="btn btn-emerald" disabled={busy}>
            Add Watchlist
          </button>
        </form>

        {message && <div className="banner-info" style={{ marginTop: 12, marginBottom: 0 }}>{message}</div>}
        {error && <div className="banner-warn" style={{ marginTop: 12, marginBottom: 0 }}>{error}</div>}
      </div>

      <div className="panel">
        <div className="section-header">
          <h3 className="section-title">Monitored Watchlist ({watchlists.length})</h3>
        </div>
        {watchlists.length === 0 ? (
          <div className="muted">No items on watchlist yet.</div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Item</th>
                  <th>Strategy</th>
                  <th>item_nameid</th>
                  <th>Max Buy</th>
                  <th>Min ROI</th>
                  <th>State</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {watchlists.map((w) => (
                  <tr key={w.watchlist_id}>
                    <td><strong>{w.market_hash_name}</strong></td>
                    <td><span className="badge badge-blue">{w.strategy}</span></td>
                    <td className="mono">{w.item_nameid ?? "Unmapped"}</td>
                    <td className="mono">{w.max_buy_price_minor ? formatMinor(w.max_buy_price_minor) : "Auto Baseline"}</td>
                    <td className="mono">{w.min_roi_pct.toFixed(1)}%</td>
                    <td>
                      <span className={`badge ${w.is_active ? "badge-green" : "badge-neutral"}`}>
                        {w.is_active ? "Active" : "Paused"}
                      </span>
                    </td>
                    <td>
                      <div className="row-actions">
                        <button className="btn" onClick={() => handleToggle(w)}>{w.is_active ? "Pause" : "Resume"}</button>
                        <button className="btn btn-danger" onClick={() => handleDelete(w.watchlist_id)}>Remove</button>
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
          <h3 className="section-title">Individual Listing Evaluations ({shownCandidates.length})</h3>
          <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: "0.84rem" }}>
            <input type="checkbox" checked={onlyActionable} onChange={(e) => setOnlyActionable(e.target.checked)} />
            Actionable signals only
          </label>
        </div>
        {shownCandidates.length === 0 ? (
          <div className="muted">No individual listing evaluations match the current filter.</div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Item &amp; Variant</th>
                  <th>Listing / Asset ID</th>
                  <th>Buy Cost (Gross)</th>
                  <th>Ref / Discount</th>
                  <th>7d Cons. Profit ({shownCandidates[0]?.currency_code ?? "USD"} Wallet)</th>
                  <th>Float / Stickers</th>
                  <th>Score / Conf</th>
                  <th>Status &amp; Warnings</th>
                  <th>Verify</th>
                </tr>
              </thead>
              <tbody>
                {shownCandidates.map((c) => (
                  <tr key={c.evaluation_id}>
                    <td>
                      <div><strong>{c.market_hash_name}</strong></div>
                      {c.listing_status !== "active" && <span className="badge badge-neutral">{c.listing_status}</span>}
                    </td>
                    <td className="mono" style={{ fontSize: "0.76rem" }}>
                      <div>L: {c.steam_listing_id}</div>
                      {c.asset_id && <div className="muted">A: {c.asset_id}</div>}
                    </td>
                    <td className="mono">{formatMinor(c.purchase_buyer_pays_minor, c.currency_symbol)}</td>
                    <td className="mono">
                      <div>{formatMinor(c.reference_price_minor, c.currency_symbol)}</div>
                      {c.discount_pct !== null && <div className="muted">-{c.discount_pct.toFixed(1)}%</div>}
                    </td>
                    <td className={`mono ${(c.conservative_net_profit_minor ?? 0) > 0 ? "positive" : "negative"}`}>
                      <div>{formatSignedMinor(c.conservative_net_profit_minor, c.currency_symbol)}</div>
                      <div className="muted">{c.conservative_roi_pct !== null ? `${c.conservative_roi_pct.toFixed(1)}% ROI` : "—"}</div>
                    </td>
                    <td style={{ fontSize: "0.78rem" }}>
                      {c.float_value !== null && <div className="mono">Float: {c.float_value.toFixed(5)}</div>}
                      {c.paint_seed !== null && <div className="mono">Seed: {c.paint_seed}</div>}
                      {c.stickers.length > 0 && <div>{c.stickers.join(", ")}</div>}
                      {c.float_value === null && c.stickers.length === 0 && <span className="muted">—</span>}
                    </td>
                    <td className="mono">{c.opportunity_score.toFixed(0)} / {c.confidence_score.toFixed(0)}</td>
                    <td>
                      <span className={`badge ${c.is_actionable ? "badge-green" : c.status.includes("Data") ? "badge-amber" : "badge-neutral"}`}>
                        {c.status}
                      </span>
                      <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginTop: 4 }}>
                        {c.warning_tags.slice(0, 3).map((tag) => (
                          <span key={tag} className="badge badge-amber">{tag}</span>
                        ))}
                      </div>
                    </td>
                    <td>
                      <a className="link" href={c.steam_listing_url} target="_blank" rel="noreferrer">
                        Open on Steam ↗
                      </a>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
