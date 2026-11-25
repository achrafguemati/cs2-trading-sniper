import type {
  BuyCandidate,
  HoldingView,
  OverviewResponse,
  SellCandidate,
} from "../types";
import { formatDurationSeconds, formatMinor, formatSignedMinor } from "../api/client";

interface Props {
  overview: OverviewResponse | null;
  buyCandidates: BuyCandidate[];
  sellCandidates: SellCandidate[];
  onNavigate: (tab: "buy" | "sell" | "portfolio" | "health") => void;
}

export function OverviewTab({ overview, buyCandidates, sellCandidates, onNavigate }: Props) {
  if (!overview) {
    return <div className="panel muted">Loading system metrics...</div>;
  }

  const { portfolio, kpis, health } = overview;
  const sym = portfolio.currency_symbol;
  const actionableBuys = buyCandidates.filter((c) => c.is_actionable).slice(0, 5);
  const topSells = sellCandidates.filter((s) => s.status === "Candidate" || s.status === "High-confidence signal").slice(0, 5);
  const activeHoldings: HoldingView[] = portfolio.holdings.filter((h) => h.status !== "SOLD").slice(0, 5);

  return (
    <div>
      <div className="banner-info">
        <strong>Accounting Boundary:</strong> {overview.wallet_notice}
      </div>

      <div className="kpi-grid">
        <div className="kpi-card">
          <div className="kpi-label">Active Watchlists</div>
          <div className="kpi-value mono">{kpis.active_watchlists}</div>
          <div className="kpi-sub">Inventory synced: {kpis.inventory_items} items</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-label">Actionable BUY Signals</div>
          <div className="kpi-value mono positive">{kpis.active_buy_signals}</div>
          <div className="kpi-sub">Conservative 7d buffer passed</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-label">Actionable SELL Signals</div>
          <div className="kpi-value mono">{kpis.recent_sell_signals}</div>
          <div className="kpi-sub">Total alerts logged: {kpis.total_alerts}</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-label">Capital Deployed ({portfolio.currency_code} Wallet)</div>
          <div className="kpi-value mono">{formatMinor(portfolio.capital_deployed_minor, sym)}</div>
          <div className="kpi-sub">
            {portfolio.locked_count} locked · {portfolio.ready_to_list_count} ready · {portfolio.under_water_count} under water
          </div>
        </div>
        <div className="kpi-card">
          <div className="kpi-label">Unrealized Wallet P&amp;L</div>
          <div className={`kpi-value mono ${portfolio.unrealized_wallet_pnl_minor >= 0 ? "positive" : "negative"}`}>
            {formatSignedMinor(portfolio.unrealized_wallet_pnl_minor, sym)}
          </div>
          <div className="kpi-sub">Realized Wallet P&amp;L: {formatSignedMinor(portfolio.realized_wallet_pnl_minor, sym)}</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-label">Source Health &amp; Pacing</div>
          <div className="kpi-value">
            <span className={`badge ${health.breaker_state === "CLOSED" ? "badge-green" : health.breaker_state === "HALTED" ? "badge-rose" : "badge-amber"}`}>
              {health.breaker_state}
            </span>
          </div>
          <div className="kpi-sub mono">Max {health.rpm_ceiling} RPM sequential budget</div>
        </div>
      </div>

      <div className="panel">
        <div className="section-header">
          <h3 className="section-title">Top BUY Candidates (7-Day Risk Adjusted)</h3>
          <button className="btn" onClick={() => onNavigate("buy")}>Open BUY Terminal →</button>
        </div>
        {actionableBuys.length === 0 ? (
          <div className="muted">No active BUY candidates yet. Add watchlists and run a paced scan.</div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Item</th>
                  <th>Listing ID</th>
                  <th>Cost (Buyer Pays)</th>
                  <th>7d Cons. Profit ({portfolio.currency_code} Wallet)</th>
                  <th>Score / Conf</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {actionableBuys.map((b) => (
                  <tr key={b.evaluation_id}>
                    <td>{b.market_hash_name}</td>
                    <td className="mono">{b.steam_listing_id}</td>
                    <td className="mono">{formatMinor(b.purchase_buyer_pays_minor, b.currency_symbol)}</td>
                    <td className="mono positive">
                      {formatSignedMinor(b.conservative_net_profit_minor, b.currency_symbol)} ({b.conservative_roi_pct?.toFixed(1)}%)
                    </td>
                    <td className="mono">{b.opportunity_score.toFixed(0)} / {b.confidence_score.toFixed(0)}</td>
                    <td><span className="badge badge-green">{b.status}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="panel">
        <div className="section-header">
          <h3 className="section-title">Recent SELL Signals &amp; Active 7-Day Holdings</h3>
          <div className="row-actions">
            <button className="btn" onClick={() => onNavigate("sell")}>SELL Sniper →</button>
            <button className="btn" onClick={() => onNavigate("portfolio")}>Portfolio →</button>
          </div>
        </div>
        <div className="form-grid" style={{ alignItems: "start" }}>
          <div>
            <div className="kpi-label" style={{ marginBottom: 8 }}>High Buy-Order Signals ({topSells.length})</div>
            {topSells.length === 0 ? (
              <div className="muted">No actionable SELL alerts currently active.</div>
            ) : (
              topSells.map((s) => (
                <div key={s.alert_id} style={{ padding: "8px 0", borderBottom: "1px solid var(--border-subtle)" }}>
                  <div><strong>{s.market_hash_name}</strong></div>
                  <div className="mono" style={{ fontSize: "0.8rem" }}>
                    Bid: {formatMinor(s.highest_buy_order_minor, s.currency_symbol)} → Net: {formatMinor(s.modeled_seller_receives_minor, s.currency_symbol)} · Top Qty: {s.top_level_buy_quantity} (Cum: {s.cumulative_buy_quantity})
                  </div>
                </div>
              ))
            )}
          </div>
          <div>
            <div className="kpi-label" style={{ marginBottom: 8 }}>7-Day Marketability Queue ({activeHoldings.length})</div>
            {activeHoldings.length === 0 ? (
              <div className="muted">No active holdings tracked yet.</div>
            ) : (
              activeHoldings.map((h) => (
                <div key={h.holding_id} style={{ padding: "8px 0", borderBottom: "1px solid var(--border-subtle)" }}>
                  <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
                    <strong>{h.market_hash_name}</strong>
                    <span className={`badge ${h.is_locked ? "badge-amber" : "badge-green"}`}>
                      {h.is_locked ? `Locked ${formatDurationSeconds(h.seconds_until_marketable)}` : h.status}
                    </span>
                  </div>
                  <div className="mono" style={{ fontSize: "0.8rem" }}>
                    Cost: {formatMinor(h.acquisition_cost_minor, h.currency_symbol)} · Break-even: {formatMinor(h.breakeven_buyer_pays_minor, h.currency_symbol)}
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
