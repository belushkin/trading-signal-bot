export type Market = {
  pair: string;
  status: "fresh" | "stale" | "no_data";
  latest_price: string | null;
  price_change_24h_percent: string | null;
  volume_24h_base: string | null;
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
};

export type PageResult<T> = { items: T[]; total: number; limit: number; offset: number };

export type Overview = {
  collector: {
    status: string;
    last_success_at: string | null;
    last_error_at: string | null;
    last_error: string | null;
  };
  database: { status: string };
  counts: { events: number; signals: number };
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
  source: {
    event_id: string;
    occurred_at: string;
    source_ref: string;
    explorer_url: string | null;
  } | null;
  evaluations: {
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
  }[];
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
  history: { observed_at: string; price: string | null; bid_depth: string; ask_depth: string }[];
  volume_history: { observed_at: string; base_volume: string }[];
  history_hours: number;
  truncated: boolean;
};

export type Analytics = {
  period_days: number;
  signals_by_category: { category: string; count: number }[];
  signals_by_severity: { severity: string; count: number }[];
  signal_frequency: { date: string; count: number }[];
  data_coverage: {
    events: number;
    market_snapshots: number;
    trades: number;
    evaluations: number;
    measured_evaluations: number;
    missing_evaluations: number;
    missing_observation_rate_percent: string | null;
  };
  price_evaluations: {
    horizon_minutes: number;
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
