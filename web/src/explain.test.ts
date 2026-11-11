import { describe, expect, it } from "vitest";
import { describeOutcomes, explainSignal, missingReasonText, pairNames, priceBasisText, rankSignals, severityInfo } from "./explain";

const base: any = { signal_id: "1", market_id: "m", pair: "native/VELO", severity: "HIGH", category: "large_trade", title: "t", interpretation: "i", created_at: new Date().toISOString(), observed_facts: { trade_type: "orderbook", observed_quote_notional: 100, baseline_median_quote_notional: 10 } };

describe("explanations", () => {
  it("maps native to XLM in pair orientation", () => {
    expect(pairNames("native/VELO").base).toBe("XLM");
  });
  it("never claims buying or selling for large trades", () => {
    const text = JSON.stringify(explainSignal(base)).toLowerCase();
    expect(text).not.toMatch(/\b(bought|sold|accumulat|distribut|profit)\b/);
  });
  it("handles missing facts without throwing", () => {
    expect(() => explainSignal({ ...base, category: "volume_anomaly", observed_facts: undefined })).not.toThrow();
  });
  it("explains missing observations in plain language", () => {
    expect(missingReasonText("anything_unknown")).toMatch(/insufficient market data/);
  });
  it("labels price bases", () => {
    expect(priceBasisText("trade_price")).toMatch(/actual trades/);
    expect(priceBasisText("snapshot_mid")).toMatch(/order-book/);
  });
  it("describes mixed bases", () => {
    const out = describeOutcomes([
      { horizon_minutes: 5, status: "measured", price_basis: "snapshot_mid", price_change_percent: 1 },
      { horizon_minutes: 15, status: "measured", price_basis: "trade_price", price_change_percent: -1 },
    ] as any, pairNames("native/VELO"));
    expect(JSON.stringify(out).toLowerCase()).toMatch(/mix|different|both/);
  });
  it("labels severities and ranks by severity", () => {
    expect(severityInfo("HIGH").label).toBe("High");
    const r = rankSignals([{ ...base, signal_id: "a", severity: "WATCH" }, { ...base, signal_id: "b", severity: "CRITICAL" }]);
    expect(r[0].signal_id).toBe("b");
  });
});
