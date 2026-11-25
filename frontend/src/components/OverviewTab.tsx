import { Compass, Crosshair, ExternalLink, Radar, ShieldCheck } from "lucide-react";
import type { BuyCandidate, OverviewResponse, SellCandidate } from "../types";
import { formatLocalTime, formatMinor, formatSignedMinor } from "../api/client";

interface Props {
  overview: OverviewResponse | null;
  buyCandidates: BuyCandidate[];
  sellCandidates: SellCandidate[];
  onNavigate?: (tab: "discover" | "buy" | "sell") => void;
}

export function OverviewTab({ overview, buyCandidates, sellCandidates, onNavigate }: Props) {
  if (!overview) {
    return <div className="panel muted">Loading telemetry overview...</div>;
  }

  const { portfolio, kpis, health } = overview;
  const sym = portfolio.currency_symbol || "$";

  return (
    <div>
      <div className="banner-info">
        <ShieldCheck size={16} style={{ flexShrink: 0, marginTop: 2 }} />
        <span>
          <strong>Wallet Accounting Guarantee:</strong> {overview.wallet_notice}
        </span>
      </div>

      <div className="kpi-grid">
        <div className="kpi-card">
          <div className="kpi-label">Active BUY Watchlists</div>
          <div className="kpi-value mono">{kpis.active_watchlists}</div>
          <div className="kpi-sub">Actionable signals: {kpis.active_buy_signals}</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-label">Tracked Inventory Items</div>
          <div className="kpi-value mono">{kpis.inventory_items}</div>
          <div className="kpi-sub">Last sync: {formatLocalTime(kpis.last_inventory_sync)}</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-label">Portfolio Capital (Wallet)</div>
          <div className="kpi-value mono">{formatMinor(portfolio.capital_deployed_minor, sym)}</div>
          <div className="kpi-sub">
            {portfolio.locked_count} locked &bull; {portfolio.ready_to_list_count} ready to list
          </div>
        </div>
        <div className="kpi-card">
          <div className="kpi-label">Unrealized Wallet P&amp;L</div>
          <div
            className={`kpi-value mono ${
              portfolio.unrealized_wallet_pnl_minor >= 0 ? "positive" : "negative"
            }`}
          >
            {formatSignedMinor(portfolio.unrealized_wallet_pnl_minor, sym)}
          </div>
          <div className="kpi-sub">
            Realized: {formatSignedMinor(portfolio.realized_wallet_pnl_minor, sym)}
          </div>
        </div>
        <div className="kpi-card">
          <div className="kpi-label">Steam Source Breaker</div>
          <div className="kpi-value">
            <span className={`badge ${health.breaker_state === "CLOSED" ? "badge-green" : "badge-rose"}`}>
              {health.breaker_state}
            </span>
          </div>
          <div className="kpi-sub">Pacing ceiling: &le; {health.rpm_ceiling} RPM sequential</div>
        </div>
      </div>

      {onNavigate && (
        <div className="panel" style={{ display: "flex", flexWrap: "wrap", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
          <div>
            <strong>Looking for profitable flips?</strong>
            <div className="muted" style={{ fontSize: "0.82rem", marginTop: 2 }}>
              Use the built-in CS2 item searcher to browse 26,935 verified items or scan live Steam dips before setting watchlists.
            </div>
          </div>
          <div className="row-actions">
            <button type="button" className="btn btn-primary" onClick={() => onNavigate("discover")}>
              <Compass size={14} /> Open Discover &amp; Auto-Hunt
            </button>
            <button type="button" className="btn" onClick={() => onNavigate("buy")}>
              <Crosshair size={14} /> Review BUY Sniper
            </button>
            <button type="button" className="btn" onClick={() => onNavigate("sell")}>
              <Radar size={14} /> Manage SELL Stacks
            </button>
          </div>
        </div>
      )}

      <div className="split-grid">
        <div className="panel">
          <div className="section-header">
            <h3 className="section-title">Latest BUY Underpriced Candidates</h3>
          </div>
          {buyCandidates.length === 0 ? (
            <div className="muted">No BUY candidates evaluated yet.</div>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>CS2 Item</th>
                    <th>Ask / Listing</th>
                    <th>7d Cons. ROI</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {buyCandidates.slice(0, 6).map((c) => (
                    <tr key={c.evaluation_id}>
                      <td>
                        <div style={{ fontWeight: 600 }}>{c.market_hash_name}</div>
                        <a href={c.steam_listing_url} target="_blank" rel="noreferrer" className="steam-link">
                          Steam Market <ExternalLink size={11} />
                        </a>
                      </td>
                      <td className="mono">
                        <div>{formatMinor(c.purchase_buyer_pays_minor, c.currency_symbol)}</div>
                        <div className="muted" style={{ fontSize: "0.73rem" }}>{c.steam_listing_id}</div>
                      </td>
                      <td className={`mono ${c.conservative_net_profit_minor >= 0 ? "positive" : "negative"}`}>
                        {formatSignedMinor(c.conservative_net_profit_minor, c.currency_symbol)} ({c.conservative_roi_pct}%)
                      </td>
                      <td>
                        <span className={`badge ${c.is_actionable ? "badge-green" : "badge-amber"}`}>
                          {c.status}
                        </span>
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
            <h3 className="section-title">Latest SELL Orderbook Spike Alerts</h3>
          </div>
          {sellCandidates.length === 0 ? (
            <div className="muted">No SELL signals detected yet.</div>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>CS2 Item</th>
                    <th>High Bid / Seller Net</th>
                    <th>Mult</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {sellCandidates.slice(0, 6).map((c) => (
                    <tr key={c.alert_id}>
                      <td>
                        <div style={{ fontWeight: 600 }}>{c.market_hash_name}</div>
                        <a href={c.steam_listing_url} target="_blank" rel="noreferrer" className="steam-link">
                          Steam Market <ExternalLink size={11} />
                        </a>
                      </td>
                      <td className="mono">
                        <div>{formatMinor(c.highest_buy_order_minor, c.currency_symbol)}</div>
                        <div className="positive" style={{ fontSize: "0.76rem" }}>
                          Net: {formatMinor(c.modeled_seller_receives_minor, c.currency_symbol)}
                        </div>
                      </td>
                      <td className="mono">{c.bid_multiple ? `${c.bid_multiple.toFixed(2)}x` : "—"}</td>
                      <td>
                        <span className={`badge ${c.status === "High-confidence signal" ? "badge-green" : "badge-blue"}`}>
                          {c.status}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
