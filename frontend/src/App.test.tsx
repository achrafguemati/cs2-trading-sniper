import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";

function mockFetchRoutes() {
  const responses: Record<string, unknown> = {
    "/api/health": {
      status: "ok",
      read_only: true,
      schema_version: 6,
      breaker_state: "CLOSED",
      is_halted: false,
      halt_reason: "",
      is_cooling_down: false,
      cooldown_remaining_seconds: 0,
      consecutive_429_count: 0,
      available_tokens: 1,
      rpm_ceiling: 12,
      nameid_mappings_loaded: true,
      last_cycle: null,
    },
    "/api/overview?currency_id=1": {
      health: {
        breaker_state: "CLOSED",
        is_halted: false,
        halt_reason: "",
        is_cooling_down: false,
        cooldown_remaining_seconds: 0,
        rpm_ceiling: 12,
        last_cycle: null,
      },
      kpis: {
        active_watchlists: 2,
        active_buy_signals: 1,
        recent_sell_signals: 1,
        total_alerts: 2,
        inventory_items: 4,
        last_inventory_sync: "2026-11-24T10:00:00Z",
      },
      portfolio: {
        currency_id: 1,
        currency_code: "USD",
        currency_symbol: "$",
        total_holdings: 1,
        locked_count: 1,
        ready_to_list_count: 0,
        listed_count: 0,
        sold_count: 0,
        under_water_count: 0,
        capital_deployed_minor: 2012,
        estimated_wallet_value_minor: 2550,
        unrealized_wallet_pnl_minor: 538,
        realized_wallet_pnl_minor: 0,
        holdings: [],
      },
      wallet_notice: "Steam Wallet funds are closed-loop virtual balances and are never combined with withdrawable cash.",
    },
    "/api/watchlists": { items: [] },
    "/api/buy/candidates": {
      candidates: [
        {
          evaluation_id: 10,
          item_id: 1,
          market_hash_name: "AK-47 | Redline (Field-Tested)",
          exterior: "Field-Tested",
          steam_listing_id: "listing_9001",
          asset_id: "asset_9001",
          currency_code: "USD",
          currency_symbol: "$",
          purchase_buyer_pays_minor: 2012,
          reference_price_minor: 3000,
          baseline_vwm_minor: 3000,
          floor_median_minor: 3000,
          discount_pct: 32.9,
          conservative_exit_gross_minor: 2850,
          conservative_exit_net_minor: 2479,
          conservative_net_profit_minor: 467,
          conservative_roi_pct: 23.2,
          base_net_profit_minor: 600,
          optimistic_net_profit_minor: 750,
          opportunity_score: 88,
          confidence_score: 90,
          float_value: 0.165,
          paint_seed: 661,
          stickers: ["Crown (Foil)"],
          inspect_link: null,
          warning_tags: ["7-Day Re-Market Lock Applies", "Sticker Craft Detected"],
          status: "High-confidence signal",
          is_actionable: true,
          suppression_reason: null,
          listing_status: "active",
          evaluated_at: "2026-11-24T10:00:00Z",
          steam_listing_url: "https://steamcommunity.com/market/listings/730/AK-47%20%7C%20Redline%20%28Field-Tested%29",
        },
      ],
    },
    "/api/sell/candidates": {
      candidates: [
        {
          alert_id: 5,
          item_id: 1,
          market_hash_name: "AK-47 | Redline (Field-Tested)",
          exterior: "Field-Tested",
          currency_code: "USD",
          currency_symbol: "$",
          highest_buy_order_minor: 3500,
          lowest_sell_order_minor: 3800,
          top_level_buy_quantity: 1,
          cumulative_buy_quantity: 211731,
          modeled_steam_fee_minor: 152,
          modeled_publisher_fee_minor: 304,
          modeled_seller_receives_minor: 3044,
          historical_baseline_minor: 2500,
          bid_multiple: 1.4,
          opportunity_score: 72,
          confidence_score: 84,
          warning_tags: ["Single-Item Top Bid"],
          status: "Candidate",
          created_at: "2026-11-24T10:00:00Z",
          steam_listing_url: "https://steamcommunity.com/market/listings/730/AK-47%20%7C%20Redline%20%28Field-Tested%29",
        },
      ],
    },
    "/api/inventory/latest": { snapshot: null, items: [] },
    "/api/portfolio/holdings?currency_id=1": {
      currency_id: 1,
      currency_code: "USD",
      currency_symbol: "$",
      total_holdings: 0,
      locked_count: 0,
      ready_to_list_count: 0,
      listed_count: 0,
      sold_count: 0,
      under_water_count: 0,
      capital_deployed_minor: 0,
      estimated_wallet_value_minor: 0,
      unrealized_wallet_pnl_minor: 0,
      realized_wallet_pnl_minor: 0,
      holdings: [],
    },
    "/api/alerts": { alerts: [] },
    "/api/source-health": {
      breaker: { breaker_state: "CLOSED", available_tokens: 1, rpm_ceiling: 12 },
      events: [],
    },
    "/api/settings": {
      settings: {
        default_currency_id: 1,
        requests_per_minute: 12,
        cycle_interval_seconds: 300,
        min_roi_pct: 5,
        min_sell_multiplier: 1.5,
        min_bid_minor: 100,
        cooldown_seconds: 1800,
        scheduler_enabled: true,
      },
      channels: {
        discord: { enabled: true, configured: true },
        telegram: { enabled: true, configured: false },
      },
      safety: {
        read_only: true,
        max_rpm_allowed: 12,
        steam_credentials_stored: false,
      },
    },
    "/api/catalog/search?": {
      query: "",
      mappings_loaded: true,
      items: [
        {
          market_hash_name: "AK-47 | Redline (Field-Tested)",
          item_nameid: 175971494,
          exterior: "Field-Tested",
          is_stattrak: false,
          is_souvenir: false,
          steam_listing_url: "https://steamcommunity.com/market/listings/730/AK-47%20%7C%20Redline%20%28Field-Tested%29",
        },
      ],
      presets: [
        {
          id: "play_skins",
          label: "Liquid Play Skins",
          description: "High-turnover skins",
          items: [
            {
              market_hash_name: "AK-47 | Redline (Field-Tested)",
              item_nameid: 175971494,
              exterior: "Field-Tested",
              is_stattrak: false,
              is_souvenir: false,
              steam_listing_url: "https://steamcommunity.com/market/listings/730/AK-47%20%7C%20Redline%20%28Field-Tested%29",
            },
          ],
        },
      ],
    },
    "/api/discover/screen": {
      screened: 1,
      actionable: 1,
      candidates: [
        {
          market_hash_name: "AK-47 | Redline (Field-Tested)",
          exterior: "Field-Tested",
          item_nameid: 175971494,
          has_trusted_nameid: true,
          currency_id: 1,
          currency_code: "USD",
          currency_symbol: "$",
          steam_listing_url: "https://steamcommunity.com/market/listings/730/AK-47%20%7C%20Redline%20%28Field-Tested%29",
          lowest_ask_minor: 2000,
          floor_median_minor: 2050,
          highest_buy_order_minor: 1850,
          buy_order_count: 100,
          sell_order_count: 80,
          bid_ask_spread_pct: 7.5,
          vwm_7d_minor: 2600,
          vwm_14d_minor: 2700,
          vwm_30d_minor: 2700,
          sales_7d: 45,
          sales_30d: 180,
          dip_vs_30d_pct: 25.9,
          baseline_exit_gross_minor: 2700,
          net_at_baseline_exit_minor: 2349,
          profit_at_baseline_minor: 349,
          roi_at_baseline_pct: 17.45,
          cons_exit_gross_minor: 2565,
          cons_net_minor: 2231,
          cons_profit_minor: 231,
          cons_roi_pct: 11.55,
          breakeven_exit_for_ask_minor: 2300,
          breakeven_buy_minor: 2349,
          target_buy_minor: 2237,
          lowest_float: 0.1621,
          has_notable_craft: true,
          notable_stickers: ["Crown (Foil)"],
          best_inspect_link: null,
          active_listings_count: 10,
          verdict: "STRONG BUY NOW",
          verdict_tone: "good",
          screener_score: 89.5,
          watchlist_added: false,
          error: null,
        },
      ],
    },
  };

  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      const matchedKey = url.startsWith("/api/catalog/search") ? "/api/catalog/search?" : url;
      const body = responses[matchedKey] ?? {};
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
}

describe("CS2 Trading Sniper Dashboard", () => {
  beforeEach(() => {
    mockFetchRoutes();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders read-only overview, wallet disclaimer, BUY candidates, Discover Deals, and separate SELL depths", async () => {
    render(<App />);

    await waitFor(() => {
      expect(screen.getByText("STRICTLY READ-ONLY")).toBeInTheDocument();
      expect(screen.getByText(/closed-loop virtual balances/i)).toBeInTheDocument();
      expect(screen.getByText("listing_9001")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /^Discover Deals$/i }));
    await waitFor(() => {
      expect(screen.getByText("Liquid Play Skins")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /Analyze 1/i }));
    await waitFor(() => {
      expect(screen.getByText("STRONG BUY NOW")).toBeInTheDocument();
      expect(screen.getByText("+$3.49")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /^SELL Sniper$/i }));
    await waitFor(() => {
      expect(screen.getByText("211,731")).toBeInTheDocument();
      expect(screen.getByText("$30.44")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /^Settings$/i }));
    await waitFor(() => {
      expect(screen.getByText("Configured & Ready")).toBeInTheDocument();
      expect(screen.getByText("Not Configured in .env")).toBeInTheDocument();
    });
  });
});
