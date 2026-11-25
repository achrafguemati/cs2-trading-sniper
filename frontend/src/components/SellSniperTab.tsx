import { useState } from "react";
import type { InventoryItem, SellCandidate } from "../types";
import { api, formatMinor } from "../api/client";

interface Props {
  inventoryItems: InventoryItem[];
  sellCandidates: SellCandidate[];
  onRefresh: () => Promise<void>;
}

export function SellSniperTab({ inventoryItems, sellCandidates, onRefresh }: Props) {
  const [steamId, setSteamId] = useState("");
  const [manualFormat, setManualFormat] = useState<"csv" | "json">("csv");
  const [manualText, setManualText] = useState(
    "market_hash_name,amount,marketable,tradable,market_restriction_days\nAK-47 | Redline (Field-Tested),1,true,true,0",
  );
  const [filterMode, setFilterMode] = useState<"marketable" | "locked" | "all">("marketable");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handleSteamImport = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setMessage(null);
    setBusy(true);
    try {
      const res = await api.importSteamInventory(steamId.trim());
      setMessage(`Imported ${res.imported_count} inventory items (${res.marketable_count} marketable).`);
      await onRefresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Steam inventory import failed.");
    } finally {
      setBusy(false);
    }
  };

  const handleManualImport = async () => {
    setError(null);
    setMessage(null);
    setBusy(true);
    try {
      const res = await api.importManualInventory(manualFormat, manualText);
      const errSuffix = res.errors.length > 0 ? ` (${res.errors.length} row errors skipped)` : "";
      setMessage(`Imported ${res.imported_count} manual item(s)${errSuffix}.`);
      await onRefresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Manual import failed.");
    } finally {
      setBusy(false);
    }
  };

  const handleFileUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setManualFormat(file.name.toLowerCase().endsWith(".json") ? "json" : "csv");
    const reader = new FileReader();
    reader.onload = () => setManualText(String(reader.result ?? ""));
    reader.readAsText(file);
  };

  const handleScanSell = async () => {
    setError(null);
    setMessage(null);
    setBusy(true);
    try {
      const res = await api.scanSell(true);
      setMessage(`SELL scan complete: ${res.evaluated} item(s) checked, ${res.actionable} actionable signal(s).`);
      await onRefresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "SELL scan failed.");
    } finally {
      setBusy(false);
    }
  };

  const filteredInv = inventoryItems.filter((item) => {
    if (filterMode === "marketable") return item.is_marketable;
    if (filterMode === "locked") return !item.is_marketable;
    return true;
  });

  return (
    <div>
      <div className="panel">
        <div className="section-header">
          <h3 className="section-title">Inventory Ingestion (Read-Only Public SteamID64 or Manual File)</h3>
          <button className="btn btn-primary" disabled={busy} onClick={handleScanSell}>
            {busy ? "Scanning..." : "Run Paced SELL Scan"}
          </button>
        </div>

        <form onSubmit={handleSteamImport} className="form-grid" style={{ marginBottom: 14 }}>
          <label className="field" style={{ gridColumn: "span 2" }}>
            Public SteamID64 (Zero Login / Zero Cookies)
            <input
              placeholder="76561198000000000"
              value={steamId}
              onChange={(e) => setSteamId(e.target.value)}
            />
          </label>
          <button type="submit" className="btn btn-emerald" disabled={busy || !steamId.trim()}>
            Import Public Inventory
          </button>
        </form>

        <div className="form-grid">
          <label className="field">
            Manual Format
            <select value={manualFormat} onChange={(e) => setManualFormat(e.target.value as "csv" | "json")}>
              <option value="csv">CSV</option>
              <option value="json">JSON</option>
            </select>
          </label>
          <label className="field">
            Load Local CSV / JSON File
            <input type="file" accept=".csv,.json" onChange={handleFileUpload} />
          </label>
          <button type="button" className="btn" disabled={busy} onClick={handleManualImport}>
            Import Manual Inventory
          </button>
        </div>
        <textarea
          rows={3}
          style={{ width: "100%", marginTop: 10, fontFamily: "var(--mono)", fontSize: "0.78rem" }}
          value={manualText}
          onChange={(e) => setManualText(e.target.value)}
        />

        {message && <div className="banner-info" style={{ marginTop: 12, marginBottom: 0 }}>{message}</div>}
        {error && <div className="banner-warn" style={{ marginTop: 12, marginBottom: 0 }}>{error}</div>}
      </div>

      <div className="panel">
        <div className="section-header">
          <h3 className="section-title">SELL Order-Book Candidates ({sellCandidates.length})</h3>
        </div>
        {sellCandidates.length === 0 ? (
          <div className="muted">No SELL evaluations recorded yet.</div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Item</th>
                  <th>Highest Bid (Gross)</th>
                  <th>Top Bid Qty</th>
                  <th>Cumulative Depth</th>
                  <th>Lowest Ask</th>
                  <th>Seller Receives Net ({sellCandidates[0]?.currency_code ?? "USD"} Wallet)</th>
                  <th>Multiple</th>
                  <th>Score / Conf</th>
                  <th>Status &amp; Risk Tags</th>
                  <th>Verify</th>
                </tr>
              </thead>
              <tbody>
                {sellCandidates.map((s) => (
                  <tr key={s.alert_id}>
                    <td><strong>{s.market_hash_name}</strong></td>
                    <td className="mono">{formatMinor(s.highest_buy_order_minor, s.currency_symbol)}</td>
                    <td className="mono"><strong>{s.top_level_buy_quantity}</strong></td>
                    <td className="mono muted">{s.cumulative_buy_quantity.toLocaleString()}</td>
                    <td className="mono">{formatMinor(s.lowest_sell_order_minor, s.currency_symbol)}</td>
                    <td className="mono positive">{formatMinor(s.modeled_seller_receives_minor, s.currency_symbol)}</td>
                    <td className="mono">{s.bid_multiple ? `${s.bid_multiple.toFixed(2)}x` : "N/A"}</td>
                    <td className="mono">{s.opportunity_score.toFixed(0)} / {s.confidence_score.toFixed(0)}</td>
                    <td>
                      <span className={`badge ${s.status.includes("Crossed") ? "badge-rose" : s.status === "Candidate" || s.status === "High-confidence signal" ? "badge-green" : "badge-amber"}`}>
                        {s.status}
                      </span>
                      <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginTop: 4 }}>
                        {s.warning_tags.map((tag) => (
                          <span key={tag} className={`badge ${tag.includes("CROSSED") ? "badge-rose" : "badge-amber"}`}>{tag}</span>
                        ))}
                      </div>
                    </td>
                    <td>
                      <a className="link" href={s.steam_listing_url} target="_blank" rel="noreferrer">
                        Open Steam ↗
                      </a>
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
          <h3 className="section-title">Latest Inventory Snapshot ({filteredInv.length} shown)</h3>
          <div className="row-actions">
            <button className={`btn ${filterMode === "marketable" ? "btn-primary" : ""}`} onClick={() => setFilterMode("marketable")}>Marketable Only</button>
            <button className={`btn ${filterMode === "locked" ? "btn-primary" : ""}`} onClick={() => setFilterMode("locked")}>Trade-Locked</button>
            <button className={`btn ${filterMode === "all" ? "btn-primary" : ""}`} onClick={() => setFilterMode("all")}>All ({inventoryItems.length})</button>
          </div>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Item Name</th>
                <th>Asset ID</th>
                <th>Qty</th>
                <th>Marketable</th>
                <th>Tradable</th>
                <th>Restriction Days</th>
              </tr>
            </thead>
            <tbody>
              {filteredInv.map((item) => (
                <tr key={item.inventory_item_id}>
                  <td>{item.market_hash_name}</td>
                  <td className="mono muted">{item.asset_id}</td>
                  <td className="mono">{item.amount}</td>
                  <td><span className={`badge ${item.is_marketable ? "badge-green" : "badge-amber"}`}>{item.is_marketable ? "Yes" : "Locked"}</span></td>
                  <td><span className={`badge ${item.is_tradable ? "badge-green" : "badge-neutral"}`}>{item.is_tradable ? "Yes" : "Locked"}</span></td>
                  <td className="mono">{item.market_restriction_days ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
