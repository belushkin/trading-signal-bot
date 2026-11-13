import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Analytics } from "./api";
import { SignalAnalytics } from "./SignalAnalytics";

const row = (overrides: Partial<Analytics["outcomes_by_pair"][number]> = {}): Analytics["outcomes_by_pair"][number] => ({
  pair: "native/VELO:issuer", category: "volume_anomaly", severity: "HIGH", confidence: "MEDIUM",
  horizon_minutes: 5, price_basis: "snapshot_mid", signals: 10, measured: 10, missing: 0, pending: 0,
  mean_observed_change_percent: "1.5", median_observed_change_percent: "1",
  positive_percent: "60", negative_percent: "30", ...overrides,
});
const data: Analytics = {
  period_days: 30, excluded_legacy_signals: 2, market_quality: [],
  signals_by_category: [{ category: "volume_anomaly", count: 10 }, { category: "large_trade", count: 3 }],
  signals_by_severity: [], signal_frequency: [], price_evaluations: [],
  data_coverage: { events: 99, market_snapshots: 80, trades: 50, evaluations: 15,
    measured_evaluations: 10, pending_evaluations: 3, missing_evaluations: 2, missing_observation_rate_percent: null },
  outcomes_by_pair: [
    row(),
    row({ price_basis: "trade_price", measured: 2, signals: 2, mean_observed_change_percent: null, positive_percent: null, negative_percent: null }),
    row({ pair: "native/USDC:issuer", category: "large_trade", price_basis: "none", measured: 0, signals: 2, pending: 1, missing: 1, mean_observed_change_percent: null, positive_percent: null, negative_percent: null }),
    row({ horizon_minutes: 15, mean_observed_change_percent: "-2" }),
  ],
};

afterEach(cleanup);

describe("signal analytics", () => {
  it("does not show the removed signal types or price comparison sections", () => {
    render(<SignalAnalytics data={data} days={30} setDays={vi.fn()} />);
    expect(screen.getByText("Understand your signals")).toBeVisible();
    expect(screen.queryByText("Signals found")).not.toBeInTheDocument();
    expect(screen.queryByText("Later prices checked")).not.toBeInTheDocument();
    expect(screen.queryByText("Checks waiting")).not.toBeInTheDocument();
    expect(screen.queryByText(/These are market event signals/)).not.toBeInTheDocument();
    expect(screen.getByText(/2 older signals are not included/)).toBeVisible();
    expect(screen.queryByRole("heading", { name: "What did the signals notice?" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "See signals" })).not.toBeInTheDocument();
    expect(screen.queryByText("More trading than usual.")).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "What happened afterward?" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Check after")).not.toBeInTheDocument();
    expect(screen.queryByText("Market data quality")).not.toBeInTheDocument();
  });

  it("allows choosing the time period", () => {
    const setDays = vi.fn();
    render(<SignalAnalytics data={data} days={30} setDays={setDays} />);
    fireEvent.change(screen.getByLabelText("Time period"), { target: { value: "7" } });
    expect(setDays).toHaveBeenCalledWith(7);
  });

  it("shows honest empty states when no signals or checks exist", () => {
    render(<SignalAnalytics data={{ ...data, signals_by_category: [], outcomes_by_pair: [], excluded_legacy_signals: 0, data_coverage: { ...data.data_coverage, missing_evaluations: 1 } }} days={30} setDays={vi.fn()} />);
    expect(screen.queryByText("No signals found in this period.")).not.toBeInTheDocument();
    expect(screen.queryByText(/older signals/)).not.toBeInTheDocument();
  });

});
