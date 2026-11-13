import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { TargetResults } from "./TargetResults";

afterEach(() => vi.unstubAllGlobals());

const row = (id: string, horizon: number, seen: boolean | null) => ({
  signal_id: id, pair: "native/VELO:ISSUER", side: "BUY", created_at: "2025-01-01T00:00:00Z",
  horizon_minutes: horizon, status: seen === null ? "pending" : "observed", target_price_observed: seen,
  quote_amount: "10.1234567000000", quantity: "2", estimated_profit_quote: "0.5",
});

test("counts reached targets per time and lists amounts", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => ({
    ok: true, status: 200,
    json: async () => ({ items: [row("a", 5, true), row("b", 5, false), row("a", 60, null)] }),
  })));
  render(<TargetResults days={30} />);
  expect(await screen.findByText("1 of 2")).toBeInTheDocument();
  expect(screen.getByText("50% reached the target")).toBeInTheDocument();
  expect(screen.getByText("BUY XLM / SELL VELO")).toBeInTheDocument();
  expect(screen.getByText("10.1234567 VELO")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Amounts after"), { target: { value: "60" } });
  expect(screen.getByText(/No target price was seen/)).toBeInTheDocument();
});
