import { useMemo, useState } from "react";
import {
  ChevronDown,
  ChevronUp,
  ExternalLink,
  Layers,
  Play,
  RefreshCw,
  Upload,
} from "lucide-react";
import type { InventoryResponse, SellCandidate } from "../types";
import { api, formatLocalTime, formatMinor } from "../api/client";

interface Props {
  inventory: InventoryResponse | null;
  candidates: SellCandidate[];
  onRefresh: () => Promise<void>;
}

export function SellSniperTab({ inventory, candidates, onRefresh }: Props) {
  const [steamId, setSteamId] = useState(
    inventory?.snapshot?.steamid64 ??
      (inventory?.snapshot?.owner_id && /^\d{17}$/.test(inventory.snapshot.owner_id)
        ? inventory.snapshot.owner_id
        : ""),
  );
  const [csvText, setCsvText] = useState("");
  const [showImportDrawer, setShowImportDrawer] = useState(false);
  const [selectedNames, setSelectedNames] = useState<string[]>([]);
  const [invSearch, setInvSearch] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handleImportSteam = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setMessage(null);
    setBusy(true);
    try {
      const res = (await api.importSteamInventory(steamId.trim())) as {
        imported_count: number;
        marketable_count: number;
      };
      setShowImportDrawer(false);
      setMessage(
        `Imported ${res.imported_count} public CS2 items (${res.marketable_count} marketable).`,
      );
      await onRefresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Steam inventory import failed.");
    } finally {
      setBusy(false);
    }
  };

  const handleImportCsv = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!csvText.trim()) return;
    setError(null);
    setBusy(true);
    try {
      const res = (await api.importManualInventory("csv", csvText)) as { imported_count: number };
      setCsvText("");
      setShowImportDrawer(false);
      setMessage(`Imported ${res.imported_count} items via manual CSV.`);
      await onRefresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "CSV import failed.");
    } finally {
      setBusy(false);
    }
  };

  const handleSellScan = async (options?: { limit?: number; market_hash_names?: string[] }) => {
    setError(null);
    setMessage(null);
    setBusy(true);
    try {
      const res = (await api.triggerSellScan(true, options)) as {
        evaluated: number;
        actionable: number;
      };
      setMessage(
        `SELL orderbook scan finished: ${res.evaluated} items polled, ${res.actionable} actionable alerts.`,
      );
      await onRefresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "SELL scan failed.");
    } finally {
      setBusy(false);
    }
  };

  const groupedItems = useMemo(() => {
    const groups = inventory?.grouped_items ?? [];
    if (!invSearch.trim()) return groups;
    const q = invSearch.trim().toLowerCase();
    return groups.filter((g) => g.market_hash_name.toLowerCase().includes(q));
  }, [inventory, invSearch]);

  const toggleStack = (name: string) => {
    setSelectedNames((prev) =>
      prev.includes(name) ? prev.filter((x) => x !== name) : [...prev, name],
    );
  };

  return (
    <div>
      <div className="panel">
        <div className="section-header">
          <div>
            <h3 className="section-title">
              <Layers size={18} color="#10b981" />
              Public Inventory Stacks &amp; Orderbook Warmup
            </h3>
            <div className="muted" style={{ fontSize: "0.78rem", marginTop: 4 }}>
              {inventory?.snapshot
                ? `Snapshot #${inventory.snapshot.snapshot_id}: ${inventory.snapshot.total_count} raw items grouped into ${inventory.grouped_items?.length ?? 0} stacks (${formatLocalTime(inventory.snapshot.observed_at)})`
                : "No public inventory imported yet — click Sync / Import below."}
            </div>
          </div>
          <div className="row-actions">
            <button
              type="button"
              className="btn"
              onClick={() => setShowImportDrawer((prev) => !prev)}
            >
              <Upload size={14} /> Sync / Import
              {showImportDrawer ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
            </button>
            {selectedNames.length > 0 && (
              <button
                type="button"
                className="btn btn-emerald"
                disabled={busy}
                onClick={() => void handleSellScan({ market_hash_names: selectedNames })}
              >
                <Play size={14} /> Scan Selected ({selectedNames.length})
              </button>
            )}
            <button
              type="button"
              className="btn btn-primary"
              disabled={busy}
              onClick={() => void handleSellScan({ limit: 10 })}
            >
              <RefreshCw size={14} /> {busy ? "Scanning..." : "Warm Up Next 10"}
            </button>
            <button
              type="button"
              className="btn"
              disabled={busy}
              onClick={() => void handleSellScan()}
            >
              Full Scan All
            </button>
          </div>
        </div>

        {showImportDrawer && (
          <div
            style={{
              padding: 14,
              borderRadius: 10,
              border: "1px solid var(--border-subtle)",
              background: "rgba(8, 13, 24, 0.55)",
              marginBottom: 14,
            }}
          >
            <div className="split-grid">
              <form onSubmit={handleImportSteam} className="row-actions" style={{ alignItems: "flex-end" }}>
                <label className="field" style={{ flex: 1 }}>
                  Public SteamID64 (No Cookies)
                  <input
                    required
                    placeholder="76561197977396129"
                    value={steamId}
                    onChange={(e) => setSteamId(e.target.value)}
                  />
                </label>
                <button type="submit" className="btn btn-primary" disabled={busy}>
                  Sync Public Steam Inventory
                </button>
              </form>
              <form onSubmit={handleImportCsv} className="row-actions" style={{ alignItems: "flex-end" }}>
                <label className="field" style={{ flex: 1 }}>
                  Manual CSV Fallback (market_hash_name,is_marketable,is_tradable)
                  <textarea
                    rows={2}
                    placeholder="AK-47 | Redline (Field-Tested),true,true"
                    value={csvText}
                    onChange={(e) => setCsvText(e.target.value)}
                  />
                </label>
                <button type="submit" className="btn" disabled={busy}>
                  Import CSV
                </button>
              </form>
            </div>
          </div>
        )}

        {message && <div className="banner-info">{message}</div>}
        {error && <div className="banner-warn">{error}</div>}

        {inventory?.grouped_items && inventory.grouped_items.length > 0 && (
          <>
            <div className="section-header" style={{ marginTop: 4, marginBottom: 10 }}>
              <input
                style={{ maxWidth: 320 }}
                placeholder="Filter inventory stacks by skin or case..."
                value={invSearch}
                onChange={(e) => setInvSearch(e.target.value)}
              />
              <span className="muted" style={{ fontSize: "0.77rem" }}>
                Tip: SELL alerts need 3 orderbook polls per item to confirm spikes vs noise. Use &ldquo;Warm Up Next 10&rdquo; for fast batches.
              </span>
            </div>

            <div className="table-wrap" style={{ maxHeight: 380, overflowY: "auto" }}>
              <table>
                <thead>
                  <tr>
                    <th style={{ width: 38 }}>Pick</th>
                    <th>Item Stack</th>
                    <th>Qty / Marketable</th>
                    <th>Latest Top Bid</th>
                    <th>Est. Seller Net</th>
                    <th>Floor Ask</th>
                    <th>Baseline Warmup</th>
                    <th>Action</th>
                  </tr>
                </thead>
                <tbody>
                  {groupedItems.slice(0, 60).map((g) => {
                    const ratio = Math.min(
                      100,
                      Math.round((g.baseline_samples_count / Math.max(1, g.min_samples_needed)) * 100),
                    );
                    const ready = g.baseline_samples_count >= g.min_samples_needed;
                    return (
                      <tr key={g.market_hash_name}>
                        <td>
                          {g.marketable_count > 0 && (
                            <input
                              type="checkbox"
                              style={{ width: "auto", cursor: "pointer" }}
                              checked={selectedNames.includes(g.market_hash_name)}
                              onChange={() => toggleStack(g.market_hash_name)}
                            />
                          )}
                        </td>
                        <td>
                          <div className="item-cell">
                            {g.icon_url && <img src={g.icon_url} alt="" className="item-thumb" />}
                            <div>
                              <div style={{ fontWeight: 650 }}>{g.market_hash_name}</div>
                              <div className="muted" style={{ fontSize: "0.73rem" }}>
                                {g.has_trusted_nameid ? "Verified ID" : "No Trusted ID"}
                              </div>
                            </div>
                          </div>
                        </td>
                        <td className="mono">
                          <strong>&times;{g.quantity}</strong>{" "}
                          <span className="muted">({g.marketable_count} mkt)</span>
                        </td>
                        <td className="mono">{formatMinor(g.latest_highest_bid_minor, g.currency_symbol)}</td>
                        <td className="mono positive">
                          {formatMinor(g.modeled_seller_net_minor, g.currency_symbol)}
                        </td>
                        <td className="mono muted">
                          {formatMinor(g.latest_lowest_ask_minor, g.currency_symbol)}
                        </td>
                        <td>
                          <div className="warmup-wrap">
                            <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.72rem" }}>
                              <span className={ready ? "positive" : "muted"}>
                                {ready ? "Ready" : "Warming"}
                              </span>
                              <span className="mono">
                                {Math.min(g.baseline_samples_count, g.min_samples_needed)}/{g.min_samples_needed}
                              </span>
                            </div>
                            <div className="warmup-bar">
                              <div className="warmup-fill" style={{ width: `${ratio}%` }} />
                            </div>
                          </div>
                        </td>
                        <td>
                          {g.marketable_count > 0 ? (
                            <button
                              type="button"
                              className="btn btn-xs"
                              disabled={busy}
                              onClick={() => void handleSellScan({ market_hash_names: [g.market_hash_name] })}
                            >
                              Scan
                            </button>
                          ) : (
                            <span className="badge badge-neutral">Locked</span>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>

      <div className="panel">
        <div className="section-header">
          <h3 className="section-title">SELL Orderbook Spike &amp; Bid-Multiplier Signals ({candidates.length})</h3>
        </div>
        {candidates.length === 0 ? (
          <div className="muted">
            No SELL alert events logged yet. Polls accumulate baseline samples automatically; unusual buy-order spikes appear here.
          </div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>CS2 Item</th>
                  <th>Highest Buy Order</th>
                  <th>Seller Net (After Fees)</th>
                  <th>Lowest Ask</th>
                  <th>Buy Order Queue</th>
                  <th>Bid Multiple</th>
                  <th>Status &amp; Warnings</th>
                  <th>Market</th>
                </tr>
              </thead>
              <tbody>
                {candidates.map((c) => (
                  <tr key={c.alert_id}>
                    <td>
                      <div style={{ fontWeight: 650 }}>{c.market_hash_name}</div>
                    </td>
                    <td className="mono">{formatMinor(c.highest_buy_order_minor, c.currency_symbol)}</td>
                    <td className="mono positive">
                      <strong>{formatMinor(c.modeled_seller_receives_minor, c.currency_symbol)}</strong>
                      <div className="muted" style={{ fontSize: "0.74rem" }}>
                        Fees: {formatMinor(c.modeled_steam_fee_minor + c.modeled_publisher_fee_minor, c.currency_symbol)}
                      </div>
                    </td>
                    <td className="mono">{formatMinor(c.lowest_sell_order_minor, c.currency_symbol)}</td>
                    <td className="mono">
                      <div>Top: {c.top_level_buy_quantity}</div>
                      <div className="muted" style={{ fontSize: "0.74rem" }}>
                        Total: <strong>{c.cumulative_buy_quantity.toLocaleString()}</strong>
                      </div>
                    </td>
                    <td className="mono">{c.bid_multiple ? `${c.bid_multiple.toFixed(2)}x` : "—"}</td>
                    <td>
                      <span className={`badge ${c.status === "High-confidence signal" ? "badge-green" : c.status === "Candidate" ? "badge-blue" : "badge-amber"}`}>
                        {c.status}
                      </span>
                      <div className="muted" style={{ fontSize: "0.74rem", marginTop: 4 }}>
                        {c.warning_tags.join(" • ")}
                      </div>
                    </td>
                    <td>
                      <a href={c.steam_listing_url} target="_blank" rel="noreferrer" className="steam-link">
                        Steam <ExternalLink size={11} />
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
