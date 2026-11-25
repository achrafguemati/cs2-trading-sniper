import { useState } from "react";
import type { PortfolioOverview } from "../types";
import { api, formatDurationSeconds, formatLocalTime, formatMinor, formatSignedMinor } from "../api/client";

interface Props {
  portfolio: PortfolioOverview | null;
  onRefresh: () => Promise<void>;
}

export function PortfolioTab({ portfolio, onRefresh }: Props) {
  const [name, setName] = useState("");
  const [costUsd, setCostUsd] = useState("");
  const [days, setDays] = useState("7");
  const [saleInputs, setSaleInputs] = useState<Record<number, string>>({});
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handleAddHolding = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setMessage(null);
    const costCents = Math.round(Number(costUsd) * 100);
    if (!Number.isFinite(costCents) || costCents <= 0) {
      setError("Cost basis must be greater than $0.00.");
      return;
    }
    setBusy(true);
    try {
      await api.addHolding({
        market_hash_name: name.trim(),
        acquisition_cost_minor: costCents,
        restriction_days: Number(days) || 0,
      });
      setName("");
      setCostUsd("");
      setMessage("Holding added with exact integer break-even calculation.");
      await onRefresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to add holding.");
    } finally {
      setBusy(false);
    }
  };

  const handleEvaluate = async () => {
    setError(null);
    setBusy(true);
    try {
      await api.evaluatePortfolio(true);
      setMessage("Evaluated 7-day locks and market floors.");
      await onRefresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Evaluation failed.");
    } finally {
      setBusy(false);
    }
  };

  const handleMarkSold = async (holdingId: number) => {
    const grossCents = Math.round(Number(saleInputs[holdingId]) * 100);
    if (!Number.isFinite(grossCents) || grossCents <= 0) {
      setError("Enter a valid buyer-paid gross sale price in USD.");
      return;
    }
    setError(null);
    setBusy(true);
    try {
      await api.sellHolding(holdingId, grossCents);
      setMessage(`Holding #${holdingId} marked SOLD with realized Wallet P&L.`);
      await onRefresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sale update failed.");
    } finally {
      setBusy(false);
    }
  };

  const sym = portfolio?.currency_symbol ?? "$";

  return (
    <div>
      <div className="panel">
        <div className="section-header">
          <h3 className="section-title">Record Acquired Skin &amp; Track 7-Day Lock</h3>
          <button className="btn btn-primary" disabled={busy} onClick={handleEvaluate}>
            Check Unlock &amp; Break-Even
          </button>
        </div>
        <form onSubmit={handleAddHolding} className="form-grid">
          <label className="field" style={{ gridColumn: "span 2" }}>
            Exact Market Hash Name
            <input
              required
              placeholder="AK-47 | Redline (Field-Tested)"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </label>
          <label className="field">
            Cost Basis (Buyer Paid USD)
            <input
              type="number"
              required
              step="0.01"
              min="0.03"
              placeholder="20.12"
              value={costUsd}
              onChange={(e) => setCostUsd(e.target.value)}
            />
          </label>
          <label className="field">
            Restriction Days
            <input type="number" min="0" max="30" value={days} onChange={(e) => setDays(e.target.value)} />
          </label>
          <button type="submit" className="btn btn-emerald" disabled={busy}>
            Add Holding
          </button>
        </form>
        {message && <div className="banner-info" style={{ marginTop: 12, marginBottom: 0 }}>{message}</div>}
        {error && <div className="banner-warn" style={{ marginTop: 12, marginBottom: 0 }}>{error}</div>}
      </div>

      {portfolio && (
        <div className="kpi-grid">
          <div className="kpi-card">
            <div className="kpi-label">Capital Deployed ({portfolio.currency_code} Wallet)</div>
            <div className="kpi-value mono">{formatMinor(portfolio.capital_deployed_minor, sym)}</div>
          </div>
          <div className="kpi-card">
            <div className="kpi-label">Estimated Exit Value</div>
            <div className="kpi-value mono">{formatMinor(portfolio.estimated_wallet_value_minor, sym)}</div>
          </div>
          <div className="kpi-card">
            <div className="kpi-label">Unrealized Wallet P&amp;L</div>
            <div className={`kpi-value mono ${portfolio.unrealized_wallet_pnl_minor >= 0 ? "positive" : "negative"}`}>
              {formatSignedMinor(portfolio.unrealized_wallet_pnl_minor, sym)}
            </div>
          </div>
          <div className="kpi-card">
            <div className="kpi-label">Realized Wallet P&amp;L</div>
            <div className={`kpi-value mono ${portfolio.realized_wallet_pnl_minor >= 0 ? "positive" : "negative"}`}>
              {formatSignedMinor(portfolio.realized_wallet_pnl_minor, sym)}
            </div>
          </div>
        </div>
      )}

      <div className="panel">
        <div className="section-header">
          <h3 className="section-title">Holdings ({portfolio?.holdings.length ?? 0})</h3>
        </div>
        {!portfolio || portfolio.holdings.length === 0 ? (
          <div className="muted">No portfolio holdings recorded yet.</div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Item</th>
                  <th>Cost Basis</th>
                  <th>Exact Break-Even Gross</th>
                  <th>Current Floor / Est. Net</th>
                  <th>Wallet P&amp;L</th>
                  <th>7-Day Countdown</th>
                  <th>Status</th>
                  <th>Record Manual Sale</th>
                </tr>
              </thead>
              <tbody>
                {portfolio.holdings.map((h) => (
                  <tr key={h.holding_id}>
                    <td>
                      <div><strong>{h.market_hash_name}</strong></div>
                      <div className="muted" style={{ fontSize: "0.74rem" }}>Source: {h.restriction_source}</div>
                    </td>
                    <td className="mono">{formatMinor(h.acquisition_cost_minor, h.currency_symbol)}</td>
                    <td className="mono">{formatMinor(h.breakeven_buyer_pays_minor, h.currency_symbol)}</td>
                    <td className="mono">
                      <div>{formatMinor(h.current_floor_minor, h.currency_symbol)}</div>
                      <div className="muted">Net: {formatMinor(h.current_est_net_minor, h.currency_symbol)}</div>
                    </td>
                    <td className={`mono ${(h.status === "SOLD" ? h.realized_pnl_minor ?? 0 : h.unrealized_pnl_minor ?? 0) >= 0 ? "positive" : "negative"}`}>
                      {h.status === "SOLD"
                        ? formatSignedMinor(h.realized_pnl_minor, h.currency_symbol)
                        : formatSignedMinor(h.unrealized_pnl_minor, h.currency_symbol)}
                    </td>
                    <td>
                      <div>{h.status === "SOLD" ? "Completed" : formatDurationSeconds(h.seconds_until_marketable)}</div>
                      <div className="muted" style={{ fontSize: "0.74rem" }}>{formatLocalTime(h.marketable_at)}</div>
                    </td>
                    <td>
                      <span className={`badge ${h.status === "READY_TO_LIST" || h.status === "SOLD" ? "badge-green" : "badge-amber"}`}>
                        {h.status}
                      </span>
                      {h.is_under_water && (
                        <div style={{ marginTop: 4 }}>
                          <span className="badge badge-rose">
                            Under Water -{formatMinor(h.under_water_gap_minor, h.currency_symbol)}
                          </span>
                        </div>
                      )}
                    </td>
                    <td>
                      {h.status !== "SOLD" ? (
                        <div className="row-actions">
                          <input
                            type="number"
                            step="0.01"
                            min="0.03"
                            placeholder="Gross $"
                            style={{ width: 92 }}
                            value={saleInputs[h.holding_id] ?? ""}
                            onChange={(e) => setSaleInputs({ ...saleInputs, [h.holding_id]: e.target.value })}
                          />
                          <button className="btn" disabled={busy} onClick={() => handleMarkSold(h.holding_id)}>
                            Mark Sold
                          </button>
                        </div>
                      ) : (
                        <span className="mono muted">Net {formatMinor(h.realized_net_proceeds_minor, h.currency_symbol)}</span>
                      )}
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
