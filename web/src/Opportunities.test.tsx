import { cleanup, render, screen } from "@testing-library/react";
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
  expect(await screen.findByText(/target price observed afterwards/)).toHaveTextContent("execution unknown");
  expect(screen.getByText(/This preliminary rule is not validated/)).toBeVisible();
});
