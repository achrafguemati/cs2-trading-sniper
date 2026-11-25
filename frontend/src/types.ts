export interface HealthStatus {
  status: string;
  read_only: boolean;
  schema_version: number;
  breaker_state: string;
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

export type HealthResponse = HealthStatus;

export interface OverviewResponse {
  health: {
    breaker_state: string;
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
  market_hash_name: string;
  exterior: string | null;
  input_url: string;
  strategy: string;
  currency_id: number;
  max_buy_price_minor: number | null;
  min_discount_pct: number | null;
  min_net_profit_minor: number | null;
  min_roi_pct: number;
  min_sell_multiplier: number;
  min_bid_minor: number;
  poll_interval_seconds: number;
  is_active: boolean;
  notes: string | null;
  created_at: string;
  updated_at: string;
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
  discount_pct: number;
  conservative_exit_gross_minor: number;
  conservative_exit_net_minor: number;
  conservative_net_profit_minor: number;
  conservative_roi_pct: number;
  base_net_profit_minor: number;
  optimistic_net_profit_minor: number;
  breakeven_buy_price_minor?: number | null;
  target_buy_price_minor?: number | null;
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

export interface InventorySnapshot {
  snapshot_id: number;
  steamid64: string | null;
  owner_id: string | null;
  source: string;
  total_count: number;
  observed_at: string;
  is_complete: boolean;
}

export interface InventoryItem {
  inventory_item_id: number;
  asset_id: string;
  market_hash_name: string;
  item_type: string | null;
  is_marketable: boolean;
  is_tradable: boolean;
}

export interface GroupedInventoryItem {
  market_hash_name: string;
  item_type: string | null;
  quantity: number;
  marketable_count: number;
  tradable_count: number;
  icon_url: string | null;
  sample_asset_id: string;
  steam_listing_url: string;
  currency_code: string;
  currency_symbol: string;
  has_trusted_nameid: boolean;
  latest_highest_bid_minor: number | null;
  latest_lowest_ask_minor: number | null;
  modeled_seller_net_minor: number | null;
  baseline_samples_count: number;
  min_samples_needed: number;
  latest_observed_at: string | null;
}

export interface InventoryResponse {
  snapshot: InventorySnapshot | null;
  items: InventoryItem[];
  grouped_items?: GroupedInventoryItem[];
  min_samples_needed?: number;
}

export interface PortfolioHolding {
  holding_id: number;
  item_id: number | null;
  market_hash_name: string;
  currency_id: number;
  currency_code: string;
  currency_symbol: string;
  acquisition_cost_minor: number;
  quantity: number;
  breakeven_buyer_pays_minor: number;
  current_floor_minor: number | null;
  current_est_net_minor: number | null;
  unrealized_pnl_minor: number | null;
  realized_sale_gross_minor: number | null;
  realized_net_proceeds_minor: number | null;
  realized_pnl_minor: number | null;
  status: string;
  is_under_water: boolean;
  under_water_gap_minor: number;
  marketable_at: string;
  seconds_until_marketable: number;
  restriction_source: string;
  acquired_at: string;
  sold_at: string | null;
  notes: string | null;
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
  holdings: PortfolioHolding[];
}

export interface UnifiedAlert {
  id: string;
  strategy: string;
  item_id: number;
  market_hash_name: string;
  alert_type: string;
  price_minor: number | null;
  net_minor: number | null;
  roi_pct?: number | null;
  opportunity_score: number;
  confidence_score: number;
  status: string;
  warning_tags: string[];
  deliveries: Array<{ channel_name: string; status: string; attempt_count: number; last_error: string | null }>;
  created_at: string;
}

export interface SourceHealthEvent {
  event_id: number;
  endpoint: string;
  http_status: number | null;
  breaker_state: string;
  response_time_ms: number | null;
  detail: string | null;
  timestamp: string;
}

export interface SourceHealthResponse {
  breaker: {
    state?: string;
    breaker_state?: string;
    is_halted?: boolean;
    halt_reason?: string;
    is_cooling_down?: boolean;
    cooldown_remaining_seconds?: number;
    consecutive_429_count?: number;
    available_tokens: number;
    rpm_ceiling: number;
  };
  events: SourceHealthEvent[];
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

export interface CatalogItem {
  market_hash_name: string;
  item_nameid: number | null;
  exterior: string | null;
  is_stattrak: boolean;
  is_souvenir: boolean;
  steam_listing_url: string;
}

export interface CatalogPreset {
  id: string;
  label: string;
  description: string;
  items: CatalogItem[];
}

export interface CatalogSearchResponse {
  query: string;
  mappings_loaded: boolean;
  items: CatalogItem[];
  presets: CatalogPreset[];
}

export interface SteamDiscoverItem {
  market_hash_name: string;
  sell_listings: number;
  buy_orders: number | null;
  min_sell_display: string | null;
  min_sell_price_minor: number | null;
  currency_id: number | null;
  item_type: string | null;
  name_color: string | null;
  icon_url: string | null;
  exterior: string | null;
  is_stattrak: boolean;
  is_souvenir: boolean;
  item_nameid: number | null;
  has_trusted_nameid: boolean;
  currency_code: string;
  currency_symbol: string;
  steam_listing_url: string;
}

export interface SteamDiscoverResponse {
  query: string;
  total_raw: number;
  currency: { currency_id: number; code: string; symbol: string };
  items: SteamDiscoverItem[];
}

export interface ScreenerCandidate {
  market_hash_name: string;
  exterior: string | null;
  item_nameid: number | null;
  has_trusted_nameid: boolean;
  currency_id: number;
  currency_code: string;
  currency_symbol: string;
  steam_listing_url: string;
  lowest_ask_minor: number | null;
  floor_median_minor: number | null;
  highest_buy_order_minor: number | null;
  buy_order_count: number | null;
  sell_order_count: number | null;
  bid_ask_spread_pct: number | null;
  vwm_7d_minor: number | null;
  vwm_14d_minor: number | null;
  vwm_30d_minor: number | null;
  sales_7d: number;
  sales_30d: number;
  dip_vs_30d_pct: number | null;
  baseline_exit_gross_minor: number | null;
  net_at_baseline_exit_minor: number | null;
  profit_at_baseline_minor: number | null;
  roi_at_baseline_pct: number | null;
  cons_exit_gross_minor: number | null;
  cons_net_minor: number | null;
  cons_profit_minor: number | null;
  cons_roi_pct: number | null;
  breakeven_exit_for_ask_minor: number | null;
  breakeven_buy_minor: number;
  target_buy_minor: number;
  lowest_float: number | null;
  wear_percentile?: number | null;
  distance_to_better_wear?: number | null;
  float_alpha_badge?: string | null;
  has_notable_craft: boolean;
  notable_stickers: string[];
  sticker_total_value_minor?: number;
  retained_craft_alpha_minor?: number;
  craft_sp_pct?: number | null;
  craft_badge?: string | null;
  best_inspect_link: string | null;
  active_listings_count: number;
  skinport_min_minor?: number | null;
  skinport_quantity?: number;
  skinport_url?: string | null;
  csmarket_min_minor?: number | null;
  cross_instant_profit_minor?: number | null;
  cross_target_profit_minor?: number | null;
  cross_target_roi_pct?: number | null;
  best_buy_venue?: string | null;
  best_buy_price_minor?: number | null;
  best_sell_venue?: string | null;
  best_sell_gross_minor?: number | null;
  best_sell_net_minor?: number | null;
  best_profit_minor?: number | null;
  best_roi_pct?: number | null;
  trade_summary?: string | null;
  exit_comparison?: Array<{
    venue: string;
    wallet_type: string;
    fee_label: string;
    fee_minor: number;
    net_minor: number;
  }>;
  verdict: string;
  verdict_tone: "good" | "warn" | "neutral";
  screener_score: number;
  watchlist_added: boolean;
  error: string | null;
}

export interface DiscoverScreenResponse {
  screened: number;
  actionable: number;
  candidates: ScreenerCandidate[];
}

export interface CrossMarketOpportunity {
  market_hash_name: string;
  exterior: string | null;
  is_stattrak: boolean;
  is_souvenir: boolean;
  item_nameid: number | null;
  has_trusted_nameid: boolean;
  currency_code: string;
  currency_symbol: string;
  skinport_min_minor: number;
  skinport_suggested_minor: number;
  skinport_quantity: number;
  skinport_url: string | null;
  csmarket_min_minor: number | null;
  est_steam_net_minor: number;
  est_wallet_profit_minor: number;
  est_wallet_roi_pct: number;
  rank_score: number;
  steam_listing_url: string;
}

export interface CrossMarketResponse {
  feed_items: number;
  currency: { currency_id: number; code: string; symbol: string };
  items: CrossMarketOpportunity[];
}

export interface AutoHuntResponse {
  strategy: string;
  external_feed_items: number;
  cross_market_preview: CrossMarketOpportunity[];
  screened: number;
  actionable: number;
  candidates: ScreenerCandidate[];
}
