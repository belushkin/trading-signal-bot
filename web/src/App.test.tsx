import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App";

vi.mock("./Opportunities", () => ({
  Opportunities: () => <div>No actionable opportunity currently meets the required criteria.</div>,
}));

async function openDiagnostics() {
  fireEvent.click(await screen.findByText("Technical diagnostics"));
}

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
    expect(await screen.findByText("No actionable opportunity currently meets the required criteria.")).toBeInTheDocument();
    expect(screen.queryByText("No markets configured")).not.toBeInTheDocument();
    await openDiagnostics();
    expect(await screen.findByText("No markets configured")).toBeInTheDocument();
    expect(screen.getByText("No action needed")).toBeInTheDocument();
    expect(screen.getByText("No persisted Stellar events yet.")).toBeInTheDocument();
    expect(screen.queryByText(/hypothetical trade calculator/i)).not.toBeInTheDocument();
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
    expect(await screen.findByText("No actionable opportunity currently meets the required criteria.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Activity" }));
    expect(await screen.findByText("No recent activity")).toBeInTheDocument();
  });

  it("persists portfolio settings through the API", async () => {
    const overview = {
      collector: { status: "healthy", last_success_at: null, last_error_at: null, last_error: null },
      database: { status: "healthy" }, counts: { events: 0, signals: 0 },
      last_event_at: null, last_signal_at: null, data_freshness_seconds: null,
      monitored_pairs: [], markets: [], recent_signals: [], warnings: [],
    };
    let settings: unknown = null;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).includes("/api/assistant/settings")) {
        if (init?.method === "PUT") settings = JSON.parse(String(init.body));
        return { ok: true, json: async () => ({ settings }) };
      }
      return { ok: true, json: async () => String(input).includes("/api/health") ? { refresh_seconds: 20 } : overview };
    });
    vi.stubGlobal("fetch", fetchMock);
    const app = render(<App />);
    await screen.findByText("No actionable opportunity currently meets the required criteria.");
    fireEvent.click(screen.getByRole("button", { name: "Portfolio" }));
    const balance = await screen.findByLabelText("XLM");
    await vi.waitFor(() => expect(balance).toBeEnabled());
    fireEvent.change(balance, { target: { value: "100" } });
    fireEvent.change(screen.getByLabelText("XLM reserve"), { target: { value: "10" } });
    fireEvent.change(screen.getByLabelText("Maximum position size (%)"), { target: { value: "5" } });
    fireEvent.click(screen.getByRole("button", { name: "Save settings" }));
    expect(await screen.findByText("Portfolio settings saved.")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Overview" }));
    fireEvent.click(screen.getByRole("button", { name: "Portfolio" }));
    await vi.waitFor(() => expect(screen.getByLabelText("XLM")).toHaveValue("100"));
    expect(fetchMock.mock.calls.some((call) => call[1]?.method === "PUT")).toBe(true);
    app.unmount();
    render(<App />);
    await screen.findByText("No actionable opportunity currently meets the required criteria.");
    fireEvent.click(screen.getByRole("button", { name: "Portfolio" }));
    await vi.waitFor(() => expect(screen.getByLabelText("XLM")).toHaveValue("100"));
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
    expect(await screen.findByText("No actionable opportunity currently meets the required criteria.")).toBeInTheDocument();
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
        severity: "WATCH", confidence: "MEDIUM", horizon_minutes: 5, signals: 1, measured: 0, missing: 0,
        pending: 1, mean_observed_change_percent: null, median_observed_change_percent: null,
        positive_percent: null, negative_percent: null }],
    };
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => ({
      ok: true,
      json: async () => String(input).includes("/api/health") ? { refresh_seconds: 20 }
        : String(input).includes("/api/analytics") ? analytics : overview,
    })));
    render(<App />);
    expect(await screen.findByText("No actionable opportunity currently meets the required criteria.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Analytics" }));
    expect(await screen.findByText(/5 older signals are not included/)).toBeInTheDocument();
    expect(screen.queryByText("Checks waiting")).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "What happened afterward?" })).not.toBeInTheDocument();
  });

  it("keeps target results available on the analytics page", async () => {
    const overview = {
      collector: { status: "healthy", last_success_at: null, last_error_at: null, last_error: null,
        interruption_count: 2, skipped_trade_count: 3, last_skipped_reason: "dust_zero_base_amount" },
      database: { status: "healthy" }, counts: { events: 0, signals: 0 },
      last_event_at: null, last_signal_at: null, data_freshness_seconds: null,
      monitored_pairs: [], markets: [], recent_signals: [], warnings: [],
      generated_at: "2026-01-01T00:00:00Z",
    };
    const row = (price_basis: string) => ({ pair: "native/USDC:issuer", category: "volume_anomaly",
      severity: "HIGH", confidence: "MEDIUM", horizon_minutes: 15, price_basis, signals: 1, measured: 1, missing: 0,
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
        : String(input).includes("/api/assistant/target-results") ? { items: [] }
        : String(input).includes("/api/analytics") ? analytics : overview,
    })));
    render(<App />);
    await openDiagnostics();
    expect(await screen.findByText(/3 skipped trades/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Analytics" }));
    expect(await screen.findByRole("heading", { name: "Was the target price seen?" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "What happened afterward?" })).not.toBeInTheDocument();
    expect(screen.queryByText("Trade provenance")).not.toBeInTheDocument();
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
    expect(await screen.findByText("No actionable opportunity currently meets the required criteria.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Signals" }));
    await screen.findByText("No signals match these filters");
    const filter = screen.getByLabelText("Signal category");
    for (const value of ["price_movement", "volume_anomaly", "spread_anomaly", "large_trade"]) {
      expect(filter.querySelector(`option[value="${value}"]`)).not.toBeNull();
    }
  });

  it("groups signals into one episode and lets the operator mark it reviewed", async () => {
    const at = (m: number) => new Date(Date.now() - m * 60000).toISOString();
    const signal = (id: string, m: number) => ({
      signal_id: id, occurred_at: at(m), category: "volume_anomaly", severity: "HIGH", confidence: "medium",
      pair: "native/VELO:GISSUER", asset: null, interpretation: "x",
      observed_facts: { observed_base_volume: 2100, baseline_mean_base_volume: 100 }, evaluations: [],
    });
    const signals = [signal("s1", 20), signal("s2", 10), signal("s3", 2)];
    const overview = {
      collector: { status: "healthy", last_success_at: at(1), last_error_at: null, last_error: null },
      database: { status: "healthy" }, counts: { events: 1, signals: 3 },
      last_event_at: at(1), last_signal_at: at(2), data_freshness_seconds: 5,
      monitored_pairs: ["native/VELO:GISSUER"], markets: [], recent_signals: signals, warnings: [],
      generated_at: at(0),
    };
    const calls: { url: string; method?: string }[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push({ url, method: init?.method });
      const json = url.includes("/api/health") ? { refresh_seconds: 20 }
        : url.includes("/api/episode-reviews") ? { items: [] }
        : url.includes("/api/signals") ? { items: signals, total: 3, limit: 100, offset: 0 }
        : overview;
      return { ok: true, json: async () => json };
    }));
    render(<App />);
    expect(await screen.findByText("No actionable opportunity currently meets the required criteria.")).toBeInTheDocument();
    expect(screen.queryByText("3 related signals", { exact: false })).not.toBeInTheDocument();
    expect(screen.queryByText("Generated signals")).not.toBeInTheDocument();
    await openDiagnostics();
    expect((await screen.findAllByText("3 related signals", { exact: false })).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("article")).toHaveLength(1);
    expect(screen.getAllByText(/not trading advice/i).length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole("button", { name: "Mark as reviewed" }));
    await vi.waitFor(() => expect(calls.some((c) => c.method === "PUT" && c.url.endsWith("/api/episode-reviews/s1"))).toBe(true));
  });

  it("retains market details without the removed calculator", async () => {
    const pair = "native/VELO:GISSUER";
    const market = {
      pair, enabled: true, status: "fresh", latest_price: "2", price_change_24h_percent: null,
      volume_24h_base: null, last_trade_price: null, last_trade_at: null, best_bid: "1.9",
      best_ask: "2.1", spread_percent: "9", bid_depth: "10", ask_depth: "20",
      last_observed_at: new Date().toISOString(),
    };
    const overview = {
      collector: { status: "healthy", last_success_at: new Date().toISOString(), last_error_at: null, last_error: null },
      database: { status: "healthy" }, counts: { events: 0, signals: 0 },
      last_event_at: null, last_signal_at: null, data_freshness_seconds: 0,
      monitored_pairs: [pair], markets: [market], recent_signals: [], warnings: [],
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      const body = path.includes("/api/health") ? { refresh_seconds: 20 }
        : path.includes("/api/episode-reviews") ? { items: [] }
        : path.includes("/api/signals") ? { items: [], total: 0 }
        : path.includes("/api/markets/") ? {
          pair, summary: market, history: [], volume_history: [], recent_trades: [],
          latest_order_book_levels: { observed_at: "2026-01-01T00:00:00Z", levels: { bids: [], bid_amount_units: "quote" } },
          data_source: "Persisted data", truncated: false,
        } : overview;
      return { ok: true, json: async () => body };
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);
    await openDiagnostics();
    fireEvent.click(await screen.findByRole("row", { name: /native\/VELO.*fresh/i }));
    const dialog = await screen.findByRole("dialog", { name: "Market detail" });
    expect(await within(dialog).findByText(/Persisted data/)).toBeVisible();
    fireEvent.click(within(dialog).getByText("Latest recorded order-book levels"));
    expect(within(dialog).getByText(/not proof of executable liquidity/)).toBeVisible();
    expect(within(dialog).getByText(/"bid_amount_units": "quote"/)).toBeVisible();
    expect(within(dialog).queryByText(/calculator/i)).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      `/api/markets/${encodeURIComponent(pair)}?history_hours=24`, expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );
  });
});
