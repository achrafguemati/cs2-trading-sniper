import { useEffect, useState } from "react";
import type { SettingsPayload } from "../types";
import { api } from "../api/client";

interface Props {
  settingsPayload: SettingsPayload | null;
  onRefresh: () => Promise<void>;
}

export function SettingsTab({ settingsPayload, onRefresh }: Props) {
  const [form, setForm] = useState<SettingsPayload["settings"] | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (settingsPayload) {
      setForm(settingsPayload.settings);
    }
  }, [settingsPayload]);

  if (!settingsPayload || !form) {
    return <div className="panel muted">Loading configuration...</div>;
  }

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setMessage(null);
    setBusy(true);
    try {
      await api.updateSettings(form);
      setMessage("Settings persisted locally. Rate ceiling enforced <= 12 RPM.");
      await onRefresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save settings.");
    } finally {
      setBusy(false);
    }
  };

  const handleTestChannel = async (ch: "discord" | "telegram" | "all") => {
    setError(null);
    setMessage(null);
    setBusy(true);
    try {
      const res = await api.testNotifications(ch);
      const summary = Object.entries(res.results)
        .map(([k, v]) => `${k}: ${v.status.toUpperCase()}${v.last_error ? ` (${v.last_error})` : ""}`)
        .join(" | ");
      setMessage(`Notification test complete → ${summary}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Test notification failed.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <div className="panel">
        <div className="section-header">
          <h3 className="section-title">Free Notification Channels (Zero Secret Echo-Back)</h3>
        </div>
        <div className="banner-info">
          Webhook URLs and Telegram Bot tokens live exclusively in your local <code>.env</code> file and are never transmitted back to the browser UI.
        </div>
        <div className="kpi-grid" style={{ marginBottom: 14 }}>
          <div className="kpi-card">
            <div className="kpi-label">Discord Incoming Webhook</div>
            <div className="kpi-value">
              <span className={`badge ${settingsPayload.channels.discord.configured ? "badge-green" : "badge-amber"}`}>
                {settingsPayload.channels.discord.configured ? "Configured & Ready" : "Not Configured in .env"}
              </span>
            </div>
            <div style={{ marginTop: 10 }}>
              <button className="btn" disabled={busy} onClick={() => handleTestChannel("discord")}>Test Discord</button>
            </div>
          </div>
          <div className="kpi-card">
            <div className="kpi-label">Telegram Bot API</div>
            <div className="kpi-value">
              <span className={`badge ${settingsPayload.channels.telegram.configured ? "badge-green" : "badge-amber"}`}>
                {settingsPayload.channels.telegram.configured ? "Configured & Ready" : "Not Configured in .env"}
              </span>
            </div>
            <div style={{ marginTop: 10 }}>
              <button className="btn" disabled={busy} onClick={() => handleTestChannel("telegram")}>Test Telegram</button>
            </div>
          </div>
        </div>
      </div>

      <div className="panel">
        <div className="section-header">
          <h3 className="section-title">Conservative Strategy &amp; Rate Pacing Settings</h3>
        </div>
        <form onSubmit={handleSave} className="form-grid">
          <label className="field">
            Requests / Minute (Max {settingsPayload.safety.max_rpm_allowed})
            <input
              type="number"
              min="1"
              max="12"
              step="1"
              value={form.requests_per_minute}
              onChange={(e) => setForm({ ...form, requests_per_minute: Math.min(12, Math.max(1, Number(e.target.value))) })}
            />
          </label>
          <label className="field">
            Poll Cycle Interval (sec)
            <input
              type="number"
              min="30"
              value={form.cycle_interval_seconds}
              onChange={(e) => setForm({ ...form, cycle_interval_seconds: Number(e.target.value) })}
            />
          </label>
          <label className="field">
            Min BUY Cons. ROI (%)
            <input
              type="number"
              step="0.5"
              min="0"
              value={form.min_roi_pct}
              onChange={(e) => setForm({ ...form, min_roi_pct: Number(e.target.value) })}
            />
          </label>
          <label className="field">
            Min SELL Bid Multiple (x)
            <input
              type="number"
              step="0.1"
              min="1"
              value={form.min_sell_multiplier}
              onChange={(e) => setForm({ ...form, min_sell_multiplier: Number(e.target.value) })}
            />
          </label>
          <label className="field">
            Alert Cooldown (sec)
            <input
              type="number"
              min="60"
              value={form.cooldown_seconds}
              onChange={(e) => setForm({ ...form, cooldown_seconds: Number(e.target.value) })}
            />
          </label>
          <button type="submit" className="btn btn-primary" disabled={busy}>
            Save Settings
          </button>
        </form>
        {message && <div className="banner-info" style={{ marginTop: 12, marginBottom: 0 }}>{message}</div>}
        {error && <div className="banner-warn" style={{ marginTop: 12, marginBottom: 0 }}>{error}</div>}
      </div>
    </div>
  );
}
