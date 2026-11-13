import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { Opportunities } from "./Opportunities";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it("does not present a stopped processor as a valid empty result", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => ({
    ok: true, json: async () => ({ items: [], configured: true, screening: "unavailable" }),
  })));
  render(<Opportunities />);
  expect(await screen.findByRole("alert")).toHaveTextContent("stopped updating");
  expect(screen.queryByText("No actionable opportunity currently meets the required criteria.")).not.toBeInTheDocument();
});

it("shows when current and past opportunities appeared in local time", async () => {
  const parameters = {
    reason: "Price moved", entry_limit: "1", target_exit: "1.02", quantity: "10",
    quote_amount: "10", estimated_fees_quote: "0.01", estimated_profit_quote: "0.1",
    fee_xlm_per_transaction: "0.00001", estimated_net_percent: "1",
    limitations: [], rule_version: "momentum-v1",
  };
  const created = "2025-01-01T12:34:56Z";
  const pastEnd = "2025-01-01T13:34:56Z";
  const currentEnd = "2099-01-01T00:00:00Z";
  vi.stubGlobal("fetch", vi.fn(async () => ({
    ok: true, json: async () => ({
      configured: true, screening: "healthy",
      items: [
        { signal_id: "current", pair: "native/USDC:issuer", side: "BUY", created_at: created, expires_at: currentEnd, parameters, evaluations: [] },
        { signal_id: "past", pair: "native/USDC:issuer", side: "SELL", created_at: created, expires_at: pastEnd, parameters, evaluations: [] },
      ],
    }),
  })));
  render(<Opportunities />);
  const appeared = new Date(created).toLocaleString();
  await screen.findAllByText(appeared);
  const current = screen.getByRole("region", { name: "Current opportunities" });
  const history = screen.getByRole("region", { name: "Signal history" });
  expect(within(current).getByRole("heading", { name: "BUY XLM / SELL USDC" })).toBeVisible();
  expect(within(history).getByRole("heading", { name: "SELL XLM / BUY USDC" })).toBeVisible();
  expect(within(current).queryByText("More details")).not.toBeInTheDocument();
  expect(within(history).queryByText("More details")).not.toBeInTheDocument();
  expect(within(current).getByText(appeared)).toHaveAttribute("datetime", created);
  expect(within(history).getByText(appeared)).toHaveAttribute("datetime", created);
  expect(within(current).getByText(/Ends:/)).toHaveTextContent(`Ends: ${new Date(currentEnd).toLocaleString()} (your local time)`);
  expect(within(history).getByText(/Ended:/)).toHaveTextContent(`Ended: ${new Date(pastEnd).toLocaleString()} (your local time)`);
});

it("displays an observed target without claiming execution", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => ({
    ok: true, json: async () => ({
      configured: true, screening: "healthy",
      items: [{
        signal_id: "one", pair: "native/USDC:issuer", side: "BUY",
        created_at: "2025-01-01T00:00:00Z", expires_at: "2099-01-01T00:00:00Z",
        parameters: {
          reason: "Price moved", entry_limit: "1", target_exit: "1.02", quantity: "10",
          quote_amount: "10", estimated_fees_quote: "0.01", estimated_profit_quote: "0.1",
          fee_xlm_per_transaction: "0.00001", estimated_net_percent: "1",
          limitations: ["Costs unknown"], rule_version: "momentum-v1",
        },
        evaluations: [{ minutes: 5, status: "observed", result: {
          entry_price_observed: true, target_price_observed_after_entry: true,
          price_change_percent: "2", note: "Execution unknown",
        } }],
      }],
    }),
  })));
  render(<Opportunities />);
  expect(await screen.findByText(/Price change: 2.00%/)).not.toHaveTextContent(/Starting price|missed changes|target price/);
  expect(screen.getByText(/Price change: 2.00%/)).toHaveClass("followup-hit");
  expect(screen.queryByText(/proven strategy|Not guaranteed|cannot estimate/)).not.toBeInTheDocument();
});
