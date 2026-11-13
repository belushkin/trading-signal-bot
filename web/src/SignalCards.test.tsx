import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Signal, SignalEvaluation } from "./api";
import { SignalCard } from "./SignalCards";

const signal: Signal = {
  signal_id: "one", occurred_at: "2026-01-01T00:00:00Z", category: "large_trade",
  severity: "WATCH", confidence: "HIGH", interpretation: "Large trade",
  pair: "native/USDC:issuer", asset: null,
  observed_facts: { base_amount_xlm: "100", observed_quote_notional: "20" },
};

afterEach(cleanup);

describe("compact signal cards", () => {
  it.each(["WATCH", "HIGH", "CRITICAL", "INFO"])("shows essentials for %s", (severity) => {
    const open = vi.fn();
    render(<SignalCard signal={{ ...signal, severity }} onOpen={open} />);
    expect(screen.getByText("A large XLM trade in XLM/USDC")).toBeVisible();
    expect(screen.getByText("1 trade exchanged about 100 XLM for 20 USDC.")).toBeVisible();
    expect(screen.getByText("We have not checked the later price yet.")).toBeVisible();
    expect(screen.queryByText("This is not a suggestion to buy or sell.")).not.toBeInTheDocument();
    expect(screen.queryByText("Why should I care?")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "View details" }));
    expect(open).toHaveBeenCalledWith("one");
  });

  it.each(["WATCH", "HIGH", "CRITICAL", "INFO"])("keeps all checks concise for %s", (severity) => {
    const evaluations = [
      { horizon_minutes: 5, status: "measured", observed_change_percent: "1", price_basis: "snapshot_mid", reference_price: "0.5", measured_price: "0.505" },
      { horizon_minutes: 15, status: "measured", observed_change_percent: "-2", price_basis: "trade_price", reference_price: "0.48", measured_price: "0.4704" },
      { horizon_minutes: 60, status: "pending" },
    ] as SignalEvaluation[];
    render(<SignalCard signal={{ ...signal, severity, evaluations }} onOpen={vi.fn()} />);
    expect(screen.getByText("After 5 min: XLM went up +1% in USDC.")).toBeVisible();
    expect(screen.getByText("After 15 min: XLM went down −2% in USDC.")).toBeVisible();
    expect(screen.queryByText(/USDC went/)).not.toBeInTheDocument();
    expect(screen.queryByText(/1 XLM =/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Starting price:/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Later price:/)).not.toBeInTheDocument();
    expect(screen.getByText(/Some checks use trade prices/)).toBeVisible();
  });

  it("distinguishes missing data from a pending observation", () => {
    const evaluations = [{ horizon_minutes: 5, status: "missing" }] as SignalEvaluation[];
    render(<SignalCard signal={{ ...signal, evaluations }} onOpen={vi.fn()} />);
    expect(screen.getByText("We do not have enough data to check the later price.")).toBeVisible();
  });

  it.each([
    ["price_movement", { observed_percent: "-2", window_minutes: 5 }, "XLM price went down in USDC"],
    ["volume_anomaly", { observed_base_volume: "100" }, "More trading than usual in XLM/USDC"],
    ["spread_anomaly", { observed_percent: "3" }, "The buy–sell gap widened in XLM/USDC"],
    ["whale_transfer", { asset: "native", amount: "100" }, "A large XLM payment was sent"],
  ])("uses simple words for %s", (category, observed_facts, title) => {
    render(<SignalCard signal={{ ...signal, category, observed_facts }} onOpen={vi.fn()} />);
    expect(screen.getByText(title)).toBeVisible();
    expect(screen.queryByText("This is not a suggestion to buy or sell.")).not.toBeInTheDocument();
  });

  it.each([
    ["volume_anomaly", { observed_base_volume: "4552", window_start: "2026-01-01T20:20:00Z", window_end: "2026-01-01T20:25:00Z" }],
    ["unusual_volume", { current_five_minute_base_volume: "4552" }],
  ])("explains the assets exchanged without guessing buying or selling pressure for %s", (category, observed_facts) => {
    render(<SignalCard signal={{ ...signal, category, observed_facts }} onOpen={vi.fn()} />);
    expect(screen.getByText("4,552 XLM was exchanged for USDC.")).toBeVisible();
    expect(screen.queryByText(/Each trade has a buyer and a seller/)).not.toBeInTheDocument();
    expect(screen.queryByText(/20:20/)).not.toBeInTheDocument();
  });

  it("shows verified trade totals and the observed price", () => {
    render(<SignalCard signal={{
      ...signal, category: "volume_anomaly",
      trade_totals: { trade_count: 40, base_amount: "1051", quote_amount: "48253" },
      evaluations: [{
        horizon_minutes: 5, status: "measured", reference_price: "45.9",
        measured_price: "45.88", observed_change_percent: "-0.0411",
        price_basis: "snapshot_mid",
      } as SignalEvaluation],
    }} onOpen={vi.fn()} />);
    expect(screen.getByText("40 trades exchanged about 1,051 XLM for 48,253 USDC.")).toBeVisible();
    expect(screen.getByText("After 5 min: XLM went down −0.0411% in USDC.")).toBeVisible();
    expect(screen.queryByText(/based on buy and sell offers/)).not.toBeInTheDocument();
  });

  it("preserves recorded changes when individual prices are unavailable", () => {
    const evaluations = [
      { horizon_minutes: 5, status: "measured", observed_change_percent: "1", reference_price: null, measured_price: "1" },
      { horizon_minutes: 15, status: "measured", observed_change_percent: "2", reference_price: "1", measured_price: "0" },
      { horizon_minutes: 60, status: "measured", observed_change_percent: "2", reference_price: "-1", measured_price: "1" },
      { horizon_minutes: 240, status: "measured", observed_change_percent: "2", reference_price: "1", measured_price: "NaN" },
      { horizon_minutes: 1440, status: "measured", observed_change_percent: "2", reference_price: "1e308", measured_price: "1e-308" },
    ] as SignalEvaluation[];
    render(<SignalCard signal={{ ...signal, evaluations }} onOpen={vi.fn()} />);
    expect(screen.getByText("After 5 min: XLM went up +1% in USDC.")).toBeVisible();
    expect(screen.getByText("After 15 min: XLM went up +2% in USDC.")).toBeVisible();
    expect(screen.queryByText(/USDC change in XLM/)).not.toBeInTheDocument();
  });

  it("shows unchanged prices in both directions", () => {
    const evaluations = [{
      horizon_minutes: 5, status: "measured", observed_change_percent: "0",
      reference_price: "1", measured_price: "1",
    }] as SignalEvaluation[];
    render(<SignalCard signal={{ ...signal, evaluations }} onOpen={vi.fn()} />);
    expect(screen.getByText("After 5 min: XLM stayed at the same price in USDC.")).toBeVisible();
  });

  it("does not round small changes to zero", () => {
    const evaluations = [{
      horizon_minutes: 5, status: "measured", observed_change_percent: "0.000001",
      reference_price: "1", measured_price: "1.00000001",
    }] as SignalEvaluation[];
    render(<SignalCard signal={{ ...signal, evaluations }} onOpen={vi.fn()} />);
    expect(screen.getByText("After 5 min: XLM went up less than 0.0001% in USDC.")).toBeVisible();
  });

  it("mentions missing checks alongside measured results", () => {
    const evaluations = [
      { horizon_minutes: 5, status: "measured", observed_change_percent: "0", reference_price: "1", measured_price: "1" },
      { horizon_minutes: 15, status: "missing" },
    ] as SignalEvaluation[];
    render(<SignalCard signal={{ ...signal, evaluations }} onOpen={vi.fn()} />);
    expect(screen.getByText(/After 5 min:/)).toBeVisible();
    expect(screen.getByText("Some later price checks do not have enough data.")).toBeVisible();
  });
});
