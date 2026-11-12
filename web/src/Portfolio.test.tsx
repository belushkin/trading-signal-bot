import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PortfolioSettings } from "./Portfolio";

describe("portfolio settings", () => {
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
  const first = "USDC:GAAAA111111";
  const second = "USDC:GBBBB222222";

  it("validates reserve and saves distinct issuer balances", async () => {
    const fetchMock = vi.fn(async () => ({ ok: true, json: async () => ({ settings: null }) }));
    vi.stubGlobal("fetch", fetchMock);
    render(<PortfolioSettings pairs={[`native/${first}`, `native/${second}`]} />);
    const xlm = await screen.findByLabelText("Balance: XLM");
    await waitFor(() => expect(xlm).toBeEnabled());
    fireEvent.change(xlm, { target: { value: "10" } });
    fireEvent.change(screen.getByLabelText("Balance: USDC (GAAAA…1111)"), { target: { value: "1.25" } });
    fireEvent.change(screen.getByLabelText("Balance: USDC (GBBBB…2222)"), { target: { value: "2.5" } });
    fireEvent.change(screen.getByLabelText("XLM reserve"), { target: { value: "9" } });
    fireEvent.change(screen.getByLabelText("Assumed fee per transaction (XLM)"), { target: { value: "1" } });
    fireEvent.change(screen.getByLabelText("Maximum allocation (%)"), { target: { value: "10" } });
    fireEvent.click(screen.getByRole("button", { name: "Save settings" }));
    expect(screen.getByRole("alert")).toHaveTextContent("XLM must cover");
    fireEvent.change(screen.getByLabelText("Assumed fee per transaction (XLM)"), { target: { value: "0.5" } });
    fireEvent.click(screen.getByRole("button", { name: "Save settings" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Settings saved");
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("rejects amounts outside Stellar precision and allocation bounds", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => ({ settings: null }) })));
    render(<PortfolioSettings pairs={[]} />);
    const xlm = await screen.findByLabelText("Balance: XLM");
    await waitFor(() => expect(xlm).toBeEnabled());
    fireEvent.change(xlm, { target: { value: "1.00000001" } });
    fireEvent.change(screen.getByLabelText("XLM reserve"), { target: { value: "0" } });
    fireEvent.change(screen.getByLabelText("Assumed fee per transaction (XLM)"), { target: { value: "0" } });
    fireEvent.change(screen.getByLabelText("Maximum allocation (%)"), { target: { value: "101" } });
    fireEvent.click(screen.getByRole("button", { name: "Save settings" }));
    expect(screen.getByRole("alert")).toHaveTextContent("at most seven decimal places");
    fireEvent.change(xlm, { target: { value: "1" } });
    fireEvent.click(screen.getByRole("button", { name: "Save settings" }));
    expect(screen.getByRole("alert")).toHaveTextContent("no more than 100%");
  });
});
