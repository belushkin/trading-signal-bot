export type Market = {
  pair: string;
  tier?: number | null;
  enabled?: boolean;
  status: "fresh" | "stale" | "no_data";
  latest_price: string | null;
  price_change_24h_percent: string | null;
  volume_24h_base: string | null;
  trade_count_24h?: number | null;
  snapshot_count?: number | null;
  trade_ingestion_status?: "current" | "stale" | "unverified";
  last_trade_scan_through?: string | null;
  readiness?: Record<string, { ready: boolean; requirement: string }>;
  last_trade_price: string | null;
  last_trade_at: string | null;
  best_bid: string | null;
  best_ask: string | null;
  spread_percent: string | null;
  bid_depth: string | null;
  ask_depth: string | null;
  last_observed_at: string | null;
};

export type Signal = {
  signal_id: string;
  occurred_at: string;
  category: string;
  severity: string;
  confidence: string;
  pair: string | null;
  asset: string | null;
  interpretation: string;
  expires_at?: string;
  observed_facts?: Record<string, unknown>;
  evaluations?: SignalEvaluation[];
};

export type SignalEvaluation = {
  horizon_minutes: number;
  target_at: string;
  reference_price: string | null;
  measured_price: string | null;
  measured_at: string | null;
  observed_change_percent: string | null;
  status: string;
  data_quality: string;
  observation_count: number;
  largest_gap_seconds: number | null;
  price_basis?: string | null;
  observation_ref?: string | null;
  reference_observed_at?: string | null;
  missing_reason?: string | null;
};

export type PageResult<T> = { items: T[]; total: number; limit: number; offset: number };

export type Overview = {
  collector: {
    status: string;
    last_success_at: string | null;
    last_error_at: string | null;
    last_error: string | null;
    consecutive_failures?: number;
    interruption_count?: number;
    last_interruption_at?: string | null;
    skipped_trade_count?: number;
    last_skipped_at?: string | null;
    last_skipped_reason?: string | null;
  };
  database: { status: string };
  counts: { events: number; signals: number; notable_events_24h?: number };
  last_event_at: string | null;
  last_signal_at: string | null;
  data_freshness_seconds: number | null;
  monitored_pairs: string[];
  markets: Market[];
  recent_signals: Signal[];
  warnings: { code: string; message: string }[];
  generated_at: string;
};

export type SignalDetail = Signal & {
  observed_facts: Record<string, unknown>;
  interpretation: string;
  expires_at: string;
  deduplication_key: string;
  source: {
    event_id: string;
    occurred_at: string;
    source_ref: string | null;
    explorer_url: string | null;
  } | null;
  evaluations: SignalEvaluation[];
  volume_evidence?: VolumeEvidence | null;
  account_diagnostics_available?: boolean;
};

export type EvidenceTrade = {
  trade_id: string;
  occurred_at: string;
  base_amount: string;
  quote_amount: string;
  price: string;
  venue: string | null;
  source_ref: string | null;
};

type VolumeBucket = { count: number; base_volume: string; quote_volume: string };

export type VolumeEvidence = {
  trade_count: number;
  base_volume: string;
  quote_volume: string;
  venues: Record<string, VolumeBucket>;
  coverage: { venue_trades: number; orientation_trades: number; operation_trades: number; total_trades: number };
  largest_trade_share_percent: string | null;
  largest_trades_by_base: EvidenceTrade[];
  mean_trade_base_size: string | null;
  median_trade_base_size: string | null;
  distinct_operations: number | null;
  top_operations: { operation_id: string; trade_count: number; base_volume: string; share_percent: string | null }[];
  direction: { note: string } & Record<string, VolumeBucket | string>;
  distinct_accounts: number | null;
  matches_signal_volume?: boolean;
  signal_observed_base_volume?: string | null;
};

export type Activity = {
  event_id: string;
  occurred_at: string;
  source_ref: string;
  explorer_url: string | null;
  event_type: string;
  attributes: Record<string, unknown>;
  signal_categories: string[];
};

export type MarketDetail = {
  pair: string;
  summary: Market;
  history: { observed_at: string; price: string | null; bid_depth: string | null; ask_depth: string; best_bid: string | null; best_ask: string | null; spread_percent: string | null }[];
  volume_history: { observed_at: string; base_volume: string }[];
  recent_trades: { occurred_at: string; price: string | null; base_amount: string; quote_amount: string; trade_type: string | null }[];
  data_source: string;
  history_hours: number;
  truncated: boolean;
};

export type Analytics = {
  period_days: number;
  market_quality: Market[];
  excluded_legacy_signals: number;
  outcomes_by_pair: {
    pair: string;
    category: string;
    severity: string;
    horizon_minutes: number;
    price_basis?: string | null;
    signals: number;
    measured: number;
    missing: number;
    pending: number;
    mean_observed_change_percent: string | null;
    median_observed_change_percent: string | null;
    positive_percent: string | null;
    negative_percent: string | null;
  }[];
  signals_by_category: { category: string; count: number }[];
  signals_by_severity: { severity: string; count: number }[];
  signal_frequency: { date: string; count: number }[];
  data_coverage: {
    events: number;
    market_snapshots: number;
    trades: number;
    trades_with_provenance?: number;
    measured_by_price_basis?: Record<string, number>;
    evaluations: number;
    measured_evaluations: number;
    missing_evaluations: number;
    pending_evaluations: number;
    missing_observation_rate_percent: string | null;
  };
  price_evaluations: {
    horizon_minutes: number;
    price_basis?: string;
    sample_count: number;
    minimum_sample_count: number;
    mean_observed_change_percent: string | null;
  }[];
};

export async function get<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(path, { signal, headers: { Accept: "application/json" } });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(body?.error?.message ?? `Request failed (${response.status})`);
  }
  return response.json() as Promise<T>;
}

export function queryString(values: Record<string, string | number | undefined>): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(values)) {
    if (value !== undefined && value !== "") params.set(key, String(value));
  }
  const query = params.toString();
  return query ? `?${query}` : "";
}
