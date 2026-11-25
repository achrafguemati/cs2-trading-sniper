import { useState } from "react";
import type { HealthResponse, SourceHealthEvent, UnifiedAlert } from "../types";
import { api, formatLocalTime, formatMinor, formatSignedMinor } from "../api/client";

interface Props {
  health: HealthResponse | null;
  events: SourceHealthEvent[];
  alerts: UnifiedAlert[];
  onRefresh: () => Promise<void>;
}

export function HealthAlertsTab({ health, events, alerts, onRefresh }: Props) {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const handleResetBreaker = async () => {
    setBusy(true);
    try {
      await api.resetBreaker();
      setMsg("Circuit breaker manually reset after human inspection.");
      await onRefresh();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <div className="panel">
        <div className="section-header">
          <h3 className="section-title">Steam Endpoint Circuit Breaker &amp; Rate Budget</h3>
          {health?.is_halted && (
            <button className="btn btn-danger" disabled={busy} onClick={handleResetBreaker}>
              Confirm Human Review &amp; Reset Breaker
            </button>
          )}
        </div>
        {health ? (
          <div className="kpi-grid" style={{ marginBottom: 0 }}>
            <div className="kpi-card">
              <div className="kpi-label">Circuit Breaker State</div>
              <div className="kpi-value">
                <span className={`badge ${health.breaker_state === "CLOSED" ? "badge-green" : health.breaker_state === "HALTED" ? "badge-rose" : "badge-amber"}`}>
                  {health.breaker_state}
                </span>
              </div>
              {health.halt_reason && <div className="kpi-sub negative">{health.halt_reason}</div>}
            </div>
            <div className="kpi-card">
              <div className="kpi-label">429 Cooldown Remaining</div>
              <div className="kpi-value mono">{health.cooldown_remaining_seconds}s</div>
              <div className="kpi-sub">Consecutive 429s: {health.consecutive_429_count}</div>
            </div>
            <div className="kpi-card">
              <div className="kpi-label">Token Bucket State</div>
              <div className="kpi-value mono">{health.available_tokens} / 1.0</div>
              <div className="kpi-sub">Hard ceiling: {health.rpm_ceiling} RPM sequential</div>
            </div>
            <div className="kpi-card">
              <div className="kpi-label">Identity Database</div>
              <div className="kpi-value">{health.nameid_mappings_loaded ? "Pinned Verified" : "Unloaded"}</div>
              <div className="kpi-sub">Schema v{health.schema_version}</div>
            </div>
          </div>
        ) : (
          <div className="muted">Loading health telemetry...</div>
        )}
        {msg && <div className="banner-info" style={{ marginTop: 12, marginBottom: 0 }}>{msg}</div>}
      </div>

      <div className="panel">
        <div className="section-header">
          <h3 className="section-title">Unified Alert &amp; Delivery Audit ({alerts.length})</h3>
        </div>
        {alerts.length === 0 ? (
          <div className="muted">No alerts dispatched or logged yet.</div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Time (Local)</th>
                  <th>Strategy</th>
                  <th>Item</th>
                  <th>Gross Trigger</th>
                  <th>Net / Impact</th>
                  <th>Score / Conf</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {alerts.map((a) => (
                  <tr key={a.id}>
                    <td className="mono" style={{ fontSize: "0.76rem" }}>{formatLocalTime(a.created_at)}</td>
                    <td><span className="badge badge-blue">{a.strategy}</span></td>
                    <td>{a.market_hash_name}</td>
                    <td className="mono">{formatMinor(a.price_minor)}</td>
                    <td className="mono positive">{formatSignedMinor(a.net_minor)}</td>
                    <td className="mono">{a.opportunity_score.toFixed(0)} / {a.confidence_score.toFixed(0)}</td>
                    <td><span className="badge badge-neutral">{a.status}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="panel">
        <div className="section-header">
          <h3 className="section-title">Recent Source Health Events ({events.length})</h3>
        </div>
        {events.length === 0 ? (
          <div className="muted">No scheduler requests logged yet.</div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Endpoint</th>
                  <th>HTTP</th>
                  <th>Breaker</th>
                  <th>Latency</th>
                  <th>Detail</th>
                </tr>
              </thead>
              <tbody>
                {events.map((ev) => (
                  <tr key={ev.event_id}>
                    <td className="mono" style={{ fontSize: "0.76rem" }}>{formatLocalTime(ev.timestamp)}</td>
                    <td className="mono">{ev.endpoint}</td>
                    <td className="mono">{ev.http_status ?? "—"}</td>
                    <td><span className="badge badge-neutral">{ev.breaker_state}</span></td>
                    <td className="mono">{ev.response_time_ms !== null ? `${ev.response_time_ms}ms` : "—"}</td>
                    <td style={{ fontSize: "0.8rem" }}>{ev.detail ?? "—"}</td>
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
