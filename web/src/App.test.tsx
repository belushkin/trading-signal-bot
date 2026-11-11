import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App";

describe("dashboard request states", () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("shows a loading state while the API request is pending", () => {
    vi.stubGlobal("fetch", vi.fn(() => new Promise(() => undefined)));
    render(<App />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading persisted dashboard data");
  });

  it("shows an explicit error when the API is unavailable", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 503,
        json: async () => ({
          error: { code: "service_unavailable", message: "Dashboard database is unavailable" },
        }),
      }),
    );
    render(<App />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Dashboard data unavailable");
    expect(screen.getByText("Dashboard database is unavailable")).toBeInTheDocument();
  });

  it("explains an empty persisted database and empty market configuration", async () => {
    const overview = {
      collector: {
        status: "unhealthy",
        last_success_at: null,
        last_error_at: null,
        last_error: null,
      },
      database: { status: "healthy" },
      counts: { events: 0, signals: 0 },
      last_event_at: null,
      last_signal_at: null,
      data_freshness_seconds: null,
      monitored_pairs: [],
      markets: [],
      recent_signals: [],
      warnings: [
        { code: "no_markets_configured", message: "No markets are configured." },
        { code: "no_events", message: "No persisted Stellar events yet." },
      ],
      generated_at: "2026-01-01T00:00:00Z",
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => ({
        ok: true,
        json: async () =>
          String(input).includes("/api/health") ? { refresh_seconds: 20 } : overview,
      })),
    );
    render(<App />);
    expect(await screen.findByText("No markets configured")).toBeInTheDocument();
    expect(screen.getByText("No notable events yet")).toBeInTheDocument();
    expect(screen.getByText("No persisted Stellar events yet.")).toBeInTheDocument();
  });

  it("loads a different response shape after navigating between dashboard pages", async () => {
    const overview = {
      collector: { status: "unhealthy", last_success_at: null, last_error_at: null, last_error: null },
      database: { status: "healthy" },
      counts: { events: 0, signals: 0 },
      last_event_at: null,
      last_signal_at: null,
      data_freshness_seconds: null,
      monitored_pairs: [],
      markets: [],
      recent_signals: [],
      warnings: [],
      generated_at: "2026-01-01T00:00:00Z",
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const path = String(input);
        const body = path.includes("/api/health")
          ? { refresh_seconds: 20 }
          : path.includes("/api/activity")
            ? { items: [], total: 0, limit: 50, offset: 0 }
            : overview;
        return { ok: true, json: async () => body };
      }),
    );
    render(<App />);
    expect(await screen.findByText("No notable events yet")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Activity" }));
    expect(await screen.findByText("No recent activity")).toBeInTheDocument();
  });

  it("shows watchlist tier, disabled state, and missing market metrics", async () => {
    const overview = {
      collector: { status: "starting", last_success_at: null, last_error_at: null, last_error: null },
      database: { status: "healthy" },
      counts: { events: 0, signals: 0 },
      last_event_at: null,
      last_signal_at: null,
      data_freshness_seconds: null,
      monitored_pairs: [],
      markets: [],
      recent_signals: [],
      warnings: [],
      generated_at: "2026-01-01T00:00:00Z",
    };
    const market = {
      pair: "native/BTC:ISSUER",
      tier: 2,
      enabled: false,
      status: "no_data",
      latest_price: null,
      price_change_24h_percent: null,
      volume_24h_base: null,
      trade_count_24h: null,
      last_trade_price: null,
      last_observed_at: null,
      best_bid: null,
      best_ask: null,
      spread_percent: null,
      bid_depth: null,
      ask_depth: null,
    };
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      return {
        ok: true,
        json: async () => path.includes("/api/health")
          ? { refresh_seconds: 20 }
          : path.includes("/api/markets") ? { items: [market] } : overview,
      };
    }));
    render(<App />);
    expect(await screen.findByText("No notable events yet")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Markets" }));
    expect(await screen.findByText(/Tier 2/)).toBeInTheDocument();
    expect(screen.getAllByText("Disabled").length).toBeGreaterThan(0);
    expect(screen.getAllByText("N/A").length).toBeGreaterThan(0);
  });

  it("distinguishes pending outcomes and excluded legacy signals in analytics", async () => {
    const overview = {
      collector: { status: "starting", last_success_at: null, last_error_at: null, last_error: null },
      database: { status: "healthy" }, counts: { events: 0, signals: 0 },
      last_event_at: null, last_signal_at: null, data_freshness_seconds: null,
      monitored_pairs: [], markets: [], recent_signals: [], warnings: [],
      generated_at: "2026-01-01T00:00:00Z",
    };
    const analytics = {
      excluded_legacy_signals: 5, market_quality: [], signals_by_category: [], signals_by_severity: [],
      signal_frequency: [], price_evaluations: [],
      data_coverage: { events: 0, market_snapshots: 0, trades: 0, evaluations: 1,
        measured_evaluations: 0, missing_evaluations: 0, pending_evaluations: 1,
        missing_observation_rate_percent: null },
      outcomes_by_pair: [{ pair: "native/USDC:issuer", category: "spread_anomaly",
        severity: "WATCH", horizon_minutes: 5, signals: 1, measured: 0, missing: 0,
        pending: 1, mean_observed_change_percent: null, median_observed_change_percent: null,
        positive_percent: null, negative_percent: null }],
    };
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => ({
      ok: true,
      json: async () => String(input).includes("/api/health") ? { refresh_seconds: 20 }
        : String(input).includes("/api/analytics") ? analytics : overview,
    })));
    render(<App />);
    expect(await screen.findByText("No notable events yet")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Analytics" }));
    expect(await screen.findByText(/5 legacy signals excluded/)).toBeInTheDocument();
    expect(screen.getByText("Insufficient sample")).toBeInTheDocument();
    expect(screen.getByText("0 / 0 / 1")).toBeInTheDocument();
  });

  it("keeps snapshot-based and trade-based evaluations in separate analytics rows", async () => {
    const overview = {
      collector: { status: "healthy", last_success_at: null, last_error_at: null, last_error: null,
        interruption_count: 2, skipped_trade_count: 3, last_skipped_reason: "dust_zero_base_amount" },
      database: { status: "healthy" }, counts: { events: 0, signals: 0 },
      last_event_at: null, last_signal_at: null, data_freshness_seconds: null,
      monitored_pairs: [], markets: [], recent_signals: [], warnings: [],
      generated_at: "2026-01-01T00:00:00Z",
    };
    const row = (price_basis: string) => ({ pair: "native/USDC:issuer", category: "volume_anomaly",
      severity: "HIGH", horizon_minutes: 15, price_basis, signals: 1, measured: 1, missing: 0,
      pending: 0, mean_observed_change_percent: null, median_observed_change_percent: null,
      positive_percent: null, negative_percent: null });
    const analytics = {
      excluded_legacy_signals: 0, market_quality: [], signals_by_category: [], signals_by_severity: [],
      signal_frequency: [], price_evaluations: [],
      data_coverage: { events: 0, market_snapshots: 0, trades: 4, trades_with_provenance: 3,
        measured_by_price_basis: { snapshot_mid: 1, trade_price: 1 }, evaluations: 2,
        measured_evaluations: 2, missing_evaluations: 0, pending_evaluations: 0,
        missing_observation_rate_percent: "0" },
      outcomes_by_pair: [row("snapshot_mid"), row("trade_price")],
    };
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => ({
      ok: true,
      json: async () => String(input).includes("/api/health") ? { refresh_seconds: 20 }
        : String(input).includes("/api/analytics") ? analytics : overview,
    })));
    render(<App />);
    expect(await screen.findByText(/3 skipped trades/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Analytics" }));
    expect(await screen.findByText("Order-book mid")).toBeInTheDocument();
    expect(screen.getByText("Trade price (fallback)")).toBeInTheDocument();
    expect(screen.getByText("3 / 4")).toBeInTheDocument();
  });

  it("offers filters for all current market signal categories", async () => {
    const overview = {
      collector: { status: "starting", last_success_at: null, last_error_at: null, last_error: null },
      database: { status: "healthy" }, counts: { events: 0, signals: 0 },
      last_event_at: null, last_signal_at: null, data_freshness_seconds: null,
      monitored_pairs: [], markets: [], recent_signals: [], warnings: [],
      generated_at: "2026-01-01T00:00:00Z",
    };
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => ({
      ok: true,
      json: async () => String(input).includes("/api/health") ? { refresh_seconds: 20 }
        : String(input).includes("/api/signals") ? { items: [], total: 0, limit: 50, offset: 0 }
          : overview,
    })));
    render(<App />);
    expect(await screen.findByText("No notable events yet")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Signals" }));
    await screen.findByText("No signals match these filters");
    const filter = screen.getByLabelText("Signal category");
    for (const value of ["price_movement", "volume_anomaly", "spread_anomaly", "large_trade"]) {
      expect(filter.querySelector(`option[value="${value}"]`)).not.toBeNull();
    }
  });
});
