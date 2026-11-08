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
    expect(screen.getByText("No signals yet")).toBeInTheDocument();
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
    expect(await screen.findByText("No signals yet")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Activity" }));
    expect(await screen.findByText("No recent activity")).toBeInTheDocument();
  });
});
