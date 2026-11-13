import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PortfolioSettings } from "./Portfolio";

describe("portfolio settings", () => {
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
  const first = "USDC:GAAAA111111";
  const second = "USDC:GBBBB222222";

  it("saves exact issuer balances and validates the reserve", async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => ({
      ok: true, json: async () => init?.method === "POST" ? { value_xlm: null } : { settings: null },
    }));
    vi.stubGlobal("fetch", fetchMock);
    render(<PortfolioSettings pairs={[`native/${first}`, `native/${second}`]} />);
    const xlm = await screen.findByLabelText("XLM");
    await waitFor(() => expect(xlm).toBeEnabled());
    fireEvent.change(xlm, { target: { value: "10" } });
    fireEvent.change(screen.getByLabelText(`USDC (${first.split(":")[1]})`), { target: { value: "1.25" } });
    fireEvent.change(screen.getByLabelText(`USDC (${second.split(":")[1]})`), { target: { value: "2.5" } });
    fireEvent.change(screen.getByLabelText("XLM reserve"), { target: { value: "11" } });
    fireEvent.click(screen.getByRole("button", { name: "Save settings" }));
    expect(screen.getByRole("alert")).toHaveTextContent("cannot exceed");
    fireEvent.change(screen.getByLabelText("XLM reserve"), { target: { value: "9" } });
    fireEvent.click(screen.getByRole("button", { name: "Save settings" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Portfolio settings saved");
    const request = fetchMock.mock.calls.find((call) => call[1]?.method === "PUT");
    expect(JSON.parse(String(request?.[1]?.body))).toEqual({
      balances: { native: "10", [first]: "1.25", [second]: "2.5" }, reserve: "9", allocation: "10",
    });
    expect(screen.queryByText(/fee per transaction|anomaly|momentum/i)).not.toBeInTheDocument();
  });

  it("shows unavailable valuation and rejects invalid amounts", async () => {
    vi.stubGlobal("fetch", vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => ({
      ok: true, json: async () => init?.method === "POST" ? { value_xlm: null } : { settings: null },
    })));
    render(<PortfolioSettings pairs={[`native/${first}`]} />);
    expect(await screen.findByText(/current prices are missing/)).toBeVisible();
    const xlm = screen.getByLabelText("XLM");
    fireEvent.change(xlm, { target: { value: "-1" } });
    fireEvent.click(screen.getByRole("button", { name: "Save settings" }));
    expect(screen.getByRole("alert")).toHaveTextContent("non-negative");
    fireEvent.change(xlm, { target: { value: "1" } });
    fireEvent.change(screen.getByLabelText("Maximum position size (%)"), { target: { value: "101" } });
    fireEvent.click(screen.getByRole("button", { name: "Save settings" }));
    expect(screen.getByRole("alert")).toHaveTextContent("no more than 100%");
  });
});
