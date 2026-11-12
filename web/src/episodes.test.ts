import { describe, expect, it } from "vitest";
import type { Signal } from "./api";
import { buildEpisode, buildEpisodes, groupSignals, placeEpisodes } from "./episodes";

const NOW = Date.parse("2026-11-11T14:00:00Z");
const ctx = { now: NOW, collectorHealthy: true };
const minutesAgo = (m: number) => new Date(NOW - m * 60_000).toISOString();

function sig(id: string, category: string, ageMin: number, extra: Partial<Signal> = {}): Signal {
  return {
    signal_id: id, occurred_at: minutesAgo(ageMin), category, severity: "HIGH", confidence: "medium",
    pair: "native/VELO:GISSUER", asset: null, interpretation: "x",
    observed_facts: { observed_base_volume: 2100, baseline_mean_base_volume: 100 }, ...extra,
  };
}

describe("episode grouping", () => {
  it("groups repeated signals of one type in one market, keeping originals intact", () => {
    const input = [sig("a", "volume_anomaly", 50), sig("b", "volume_anomaly", 40), sig("c", "volume_anomaly", 20)];
    const groups = groupSignals(input);
    expect(groups).toHaveLength(1);
    expect(groups[0].map((s) => s.signal_id)).toEqual(["a", "b", "c"]);
    expect(input).toHaveLength(3);
  });
  it("does not merge different markets, even with different issuers of the same asset", () => {
    const groups = groupSignals([sig("a", "volume_anomaly", 10), sig("b", "volume_anomaly", 10, { pair: "native/VELO:GOTHER" })]);
    expect(groups).toHaveLength(2);
  });
  it("keeps signals separated by a long gap apart", () => {
    expect(groupSignals([sig("a", "volume_anomaly", 200), sig("b", "volume_anomaly", 10)])).toHaveLength(2);
  });
  it("only joins different signal types when they are close in time", () => {
    expect(groupSignals([sig("a", "volume_anomaly", 20), sig("b", "price_movement", 15)])).toHaveLength(1);
    expect(groupSignals([sig("a", "volume_anomaly", 60), sig("b", "price_movement", 15)])).toHaveLength(2);
  });
  it("uses measurement windows when present", () => {
    const windowed = sig("a", "volume_anomaly", 5, {
      observed_facts: { window_start: minutesAgo(10), window_end: minutesAgo(5), observed_base_volume: 5, baseline_mean_base_volume: 1 },
    });
    expect(groupSignals([windowed, sig("b", "price_movement", 12, { observed_facts: { window_minutes: 5, observed_percent: 1 } })])).toHaveLength(1);
  });
  it("never creates one giant episode from endless activity", () => {
    const chain = Array.from({ length: 30 }, (_, i) => sig(`s${i}`, "volume_anomaly", 900 - i * 25));
    expect(groupSignals(chain).length).toBeGreaterThan(1);
  });
});

describe("episode recommendations", () => {
  it("recommends investigating a fresh, well-supported high-severity episode without trade advice", () => {
    const e = buildEpisode([sig("a", "volume_anomaly", 20), sig("b", "volume_anomaly", 5)], ctx);
    expect(e.action).toBe("investigate");
    expect(e.status).toBe("ongoing");
    expect(e.next).toMatch(/elevated activity is continuing/);
    expect(e.keyObservation).toMatch(/21 times higher/);
    const text = JSON.stringify(e).toLowerCase();
    expect(text).not.toMatch(/\b(buy now|sell now|profit|bullish|bearish)\b/);
  });
  it("does not escalate a lone large trade on size alone", () => {
    const e = buildEpisode([sig("a", "large_trade", 5, { observed_facts: { observed_quote_notional: 10, baseline_median_quote_notional: 1 } })], ctx);
    expect(e.action).toBe("monitor");
    expect(e.next).toMatch(/only if the trade is part of a broader pattern/);
  });
  it("gives no recommendation when stored measurements are missing", () => {
    const e = buildEpisode([sig("a", "volume_anomaly", 5, { observed_facts: {} })], ctx);
    expect(e.evidence).toBe("insufficient");
    expect(e.action).toBe("insufficient");
    expect(e.next).toMatch(/insufficient/i);
  });
  it("marks status unknown when collection is unhealthy and flags limited evidence", () => {
    const e = buildEpisode([sig("a", "price_movement", 5, { observed_facts: { observed_percent: 3 } }), sig("b", "price_movement", 4)], { now: NOW, collectorHealthy: false });
    expect(e.status).toBe("unknown");
    expect(e.evidence).toBe("limited");
  });
  it("treats old and low-severity episodes as informational", () => {
    expect(buildEpisode([sig("a", "volume_anomaly", 3000)], ctx).action).toBe("informational");
    expect(buildEpisode([sig("a", "volume_anomaly", 5, { severity: "INFO" })], ctx).action).toBe("informational");
  });
  it("moves episodes with no activity for 6 hours to informational", () => {
    expect(buildEpisode([sig("a", "volume_anomaly", 500), sig("b", "volume_anomaly", 490)], ctx).action).toBe("informational");
    expect(buildEpisode([sig("a", "volume_anomaly", 300, { severity: "WATCH" })], ctx).action).toBe("monitor");
  });
  it("sorts by action, then severity", () => {
    const list = buildEpisodes([
      sig("a", "volume_anomaly", 5, { severity: "INFO", pair: "native/AAA:G1" }),
      sig("b", "volume_anomaly", 5, { severity: "WATCH", pair: "native/BBB:G1" }),
      sig("c", "volume_anomaly", 5, { severity: "HIGH", pair: "native/CCC:G1" }),
    ], ctx);
    expect(list.map((e) => e.primary.signal_id)).toEqual(["c", "b", "a"]);
  });
});

describe("review placement", () => {
  const ep = () => buildEpisode([sig("a", "volume_anomaly", 20), sig("b", "volume_anomaly", 5)], ctx);
  const review = (status: "reviewed" | "dismissed", sev = "HIGH", count = 2) => ({ episode_key: "a", status, reviewed_at: minutesAgo(1), signal_count: count, max_severity: sev, last_signal_at: minutesAgo(5) });
  it("keeps open episodes in the attention list", () => {
    expect(placeEpisodes([ep()], [])[0].placement).toBe("attention");
  });
  it("removes reviewed episodes and does not re-alert for extra signals of equal severity", () => {
    const placed = placeEpisodes([buildEpisode([sig("a", "volume_anomaly", 20), sig("b", "volume_anomaly", 5), sig("c", "volume_anomaly", 2)], ctx)], [review("reviewed")])[0];
    expect(placed.placement).toBe("closed");
    expect(placed.newSinceReview).toBe(1);
  });
  it("resurfaces a reviewed episode only when severity escalates", () => {
    const placed = placeEpisodes([ep()], [review("reviewed", "WATCH")])[0];
    expect(placed.placement).toBe("attention");
    expect(placed.resurfaced).toBe(true);
  });
  it("never resurfaces dismissed episodes", () => {
    expect(placeEpisodes([ep()], [review("dismissed", "WATCH")])[0].placement).toBe("closed");
  });
});
