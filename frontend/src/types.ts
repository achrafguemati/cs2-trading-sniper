export type BreakerState = "CLOSED" | "OPEN" | "HALF_OPEN" | "HALTED";

export interface HealthResponse {
  status: string;
  read_only: boolean;
  schema_version: number;
  breaker_state: BreakerState;
  is_halted: boolean;
  halt_reason: string;
  is_cooling_down: boolean;
  cooldown_remaining_seconds: number;
  consecutive_429_count: number;
  available_tokens: number;
  rpm_ceiling: number;
  nameid_mappings_loaded: boolean;
  last_cycle: Record<string, unknown> | null;
}

export interface HoldingView {
  holding_id: number;
  item_id: number;
  asset_id: string | null;
  market_hash_name: string;
  currency_id: number;
  currency_code: string;
  currency_symbol: string;
  quantity: number;
  acquisition_cost_minor: number;
  breakeven_buyer_pays_minor: number;
  acquired_at: string;
  marketable_at: string | null;
  restriction_source: string;
  status: "HOLDING" | "READY_TO_LIST" | "LISTED" | "SOLD";
  seconds_until_marketable: number;
  is_locked: boolean;
  days_held: number;
  current_floor_minor: number | null;
  current_est_net_minor: number | null;
  unrealized_pnl_minor: number | null;
  unrealized_roi_pct: number | null;
  realized_net_proceeds_minor: number | null;
  realized_pnl_minor: number | null;
  is_under_water: boolean;
  under_water_gap_minor: number;
}

export interface PortfolioOverview {
  currency_id: number;
  currency_code: string;
  currency_symbol: string;
  total_holdings: number;
  locked_count: number;
  ready_to_list_count: number;
  listed_count: number;
  sold_count: number;
  under_water_count: number;
  capital_deployed_minor: number;
  estimated_wallet_value_minor: number;
  unrealized_wallet_pnl_minor: number;
  realized_wallet_pnl_minor: number;
  holdings: HoldingView[];
}

export interface OverviewResponse {
  health: {
    breaker_state: BreakerState;
    is_halted: boolean;
    halt_reason: string;
    is_cooling_down: boolean;
    cooldown_remaining_seconds: number;
    rpm_ceiling: number;
    last_cycle: Record<string, unknown> | null;
  };
  kpis: {
    active_watchlists: number;
    active_buy_signals: number;
    recent_sell_signals: number;
    total_alerts: number;
    inventory_items: number;
    last_inventory_sync: string | null;
  };
  portfolio: PortfolioOverview;
  wallet_notice: string;
}

export interface WatchlistItem {
  watchlist_id: number;
  item_id: number;
  input_url: string;
  strategy: "BUY" | "SELL" | "BOTH";
  currency_id: number;
  max_buy_price_minor: number | null;
  min_discount_pct: number | null;
  min_net_profit_minor: number | null;
  min_roi_pct: number;
  poll_interval_seconds: number;
  is_active: boolean;
  last_scanned_at: string | null;
  market_hash_name: string;
  exterior: string | null;
  item_nameid: number | null;
  has_trusted_nameid: boolean;
}

export interface BuyCandidate {
  evaluation_id: number;
  item_id: number;
  market_hash_name: string;
  exterior: string | null;
  steam_listing_id: string;
  asset_id: string | null;
  currency_code: string;
  currency_symbol: string;
  purchase_buyer_pays_minor: number;
  reference_price_minor: number | null;
  baseline_vwm_minor: number | null;
  floor_median_minor: number | null;
  discount_pct: number | null;
  conservative_exit_gross_minor: number | null;
  conservative_exit_net_minor: number | null;
  conservative_net_profit_minor: number | null;
  conservative_roi_pct: number | null;
  base_net_profit_minor: number | null;
  optimistic_net_profit_minor: number | null;
  opportunity_score: number;
  confidence_score: number;
  float_value: number | null;
  paint_seed: number | null;
  stickers: string[];
  inspect_link: string | null;
  warning_tags: string[];
  status: string;
  is_actionable: boolean;
  suppression_reason: string | null;
  listing_status: string;
  evaluated_at: string;
  steam_listing_url: string;
}

export interface SellCandidate {
  alert_id: number;
  item_id: number;
  market_hash_name: string;
  exterior: string | null;
  currency_code: string;
  currency_symbol: string;
  highest_buy_order_minor: number;
  lowest_sell_order_minor: number | null;
  top_level_buy_quantity: number;
  cumulative_buy_quantity: number;
  modeled_steam_fee_minor: number;
  modeled_publisher_fee_minor: number;
  modeled_seller_receives_minor: number;
  historical_baseline_minor: number | null;
  bid_multiple: number | null;
  opportunity_score: number;
  confidence_score: number;
  warning_tags: string[];
  status: string;
  created_at: string;
  steam_listing_url: string;
}

export interface InventoryItem {
  inventory_item_id: number;
  asset_id: string;
  class_id: string;
  instance_id: string;
  market_hash_name: string;
  amount: number;
  is_marketable: boolean;
  is_tradable: boolean;
  market_restriction_days: number | null;
}

export interface UnifiedAlert {
  id: string;
  strategy: string;
  item_id: number;
  market_hash_name: string;
  alert_type: string;
  price_minor: number;
  net_minor: number | null;
  roi_pct?: number | null;
  opportunity_score: number;
  confidence_score: number;
  status: string;
  warning_tags: string[];
  deliveries: Array<{
    channel: string;
    status: string;
    attempts: number;
    last_error: string | null;
    delivered_at: string | null;
  }>;
  created_at: string;
}

export interface SourceHealthEvent {
  event_id: number;
  timestamp: string;
  endpoint: string;
  http_status: number | null;
  response_time_ms: number | null;
  breaker_state: BreakerState;
  retry_after_seconds: number | null;
  detail: string | null;
}

export interface SettingsPayload {
  settings: {
    default_currency_id: number;
    requests_per_minute: number;
    cycle_interval_seconds: number;
    min_roi_pct: number;
    min_sell_multiplier: number;
    min_bid_minor: number;
    cooldown_seconds: number;
    scheduler_enabled: boolean;
  };
  channels: {
    discord: { enabled: boolean; configured: boolean };
    telegram: { enabled: boolean; configured: boolean };
  };
  safety: {
    read_only: boolean;
    max_rpm_allowed: number;
    steam_credentials_stored: boolean;
  };
}
