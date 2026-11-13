import type { Signal, SignalEvaluation, VolumeEvidence } from "./api";

// Deterministic, template-based explanations. Every sentence is derived from persisted facts;
// nothing here infers trade direction, intent or causality.

export type Facts = Record<string, unknown>;

export type Outcome = { horizon: string; text: string; measured: boolean };

export type Explanation = {
  title: string;
  why: string;
  observed: string[];
  afterwards: Outcome[];
  afterwardsNote: string | null;
  reliability: string[];
  unknowns: string[];
};

export const HORIZONS = [5, 15, 60, 240, 1440];

export const SEVERITY_INFO: Record<string, { label: string; meaning: string; rank: number }> = {
  CRITICAL: { label: "Critical", meaning: "Needs immediate technical attention.", rank: 4 },
  HIGH: { label: "High", meaning: "Unusual market activity worth reviewing.", rank: 3 },
  WATCH: { label: "Watch", meaning: "Something unusual happened.", rank: 2 },
  INFO: { label: "Info", meaning: "A lower-priority event.", rank: 1 },
};

export function severityInfo(value: string) {
  return SEVERITY_INFO[value.toUpperCase()] ?? { label: value, meaning: "", rank: 0 };
}

export type PairNames = { base: string; counter: string; label: string };

// A pair key reads "BASE/COUNTER"; prices are always the amount of COUNTER per 1 BASE.
export function pairNames(pair: string | null | undefined): PairNames {
  if (!pair || !pair.includes("/")) {
    const single = assetName(pair ?? "");
    return { base: single || "the asset", counter: "the quote asset", label: single || "Unknown market" };
  }
  const [base, counter] = pair.split("/", 2);
  const b = assetName(base);
  const c = assetName(counter);
  return { base: b, counter: c, label: `${b}/${c}` };
}

export function opportunityLabel(pair: string, side: "BUY" | "SELL"): string {
  const { base, counter } = pairNames(pair);
  return `${side} ${base} / ${side === "BUY" ? "SELL" : "BUY"} ${counter}`;
}

function assetName(key: string): string {
  if (key === "native") return "XLM";
  return key.split(":")[0];
}

function num(value: unknown): number | null {
  if (value === null || value === undefined || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

export function fmt(value: unknown, maxDigits = 2): string {
  const parsed = num(value);
  if (parsed === null) return "N/A";
  const abs = Math.abs(parsed);
  const digits = abs !== 0 && abs < 1 ? Math.max(maxDigits, 4) : maxDigits;
  return parsed.toLocaleString("en-US", { maximumFractionDigits: digits });
}

export function timesHigher(observed: unknown, baseline: unknown): string | null {
  const o = num(observed);
  const b = num(baseline);
  if (o === null || b === null || b <= 0) return null;
  const ratio = o / b;
  if (ratio < 1.5) return "about the same as usual";
  return ratio < 10 ? `about ${ratio.toFixed(1)} times` : `about ${Math.round(ratio)} times`;
}

function signedPercent(value: unknown): string {
  const parsed = num(value);
  if (parsed === null) return "N/A";
  return `${parsed > 0 ? "+" : parsed < 0 ? "−" : ""}${fmt(Math.abs(parsed), 2)}%`;
}

function windowLabel(minutes: unknown): string {
  const m = num(minutes);
  if (m === null) return "the recent period";
  if (m < 60) return `${m} minutes`;
  return m === 60 ? "1 hour" : `${m / 60} hours`;
}

function utcTime(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return `${date.toISOString().slice(11, 16)} UTC`;
}

function venueSentence(tradeType: unknown): string {
  if (tradeType === "orderbook") return "It was matched on the order book.";
  if (tradeType === "liquidity_pool") return "It was executed against a liquidity pool.";
  return "Whether it was an order-book or liquidity-pool trade was not recorded.";
}

// Direction of a price change, always expressed for the base asset priced in the counter asset.
export function priceDirectionSentence(pair: PairNames, change: unknown): string {
  const parsed = num(change);
  if (parsed === null) return "We do not know how the price changed.";
  if (parsed === 0) return `${pair.base} stayed at the same price in ${pair.counter}.`;
  if (Math.abs(parsed) < 0.00005) return `${pair.base} went ${parsed > 0 ? "up" : "down"} less than 0.0001% in ${pair.counter}.`;
  return `${pair.base} ${parsed > 0 ? "went up" : "went down"} ${signedPercent(parsed)} in ${pair.counter}.`;
}

export const MISSING_REASONS: Record<string, string> = {
  no_snapshot_and_no_trade_in_grace:
    "No buy and sell offers or trades were recorded close enough to this time.",
  no_reference_trade_in_lookback:
    "There was no recent trade to compare with.",
  insufficient_trade_observations: "There were too few trades to check the price change.",
  no_snapshot_in_grace_window_legacy_no_trade_fallback:
    "There were no buy and sell offers recorded close enough to this time. This older record could not use trade prices instead.",
};

const GENERIC_MISSING = "We do not have enough market data to check the price at this time.";

export function missingReasonText(reason: string | null | undefined): string {
  return (reason && MISSING_REASONS[reason]) || GENERIC_MISSING;
}

export function priceBasisText(basis: string | null | undefined): string {
  if (basis === "trade_price")
    return "Price change based on actual trades. No suitable buy and sell offers were available.";
  if (basis === "snapshot_mid")
    return "Price change based on the middle of the best buy and sell offers.";
  return "The price source was not recorded.";
}

export function horizonLabel(minutes: number): string {
  return minutes < 60 ? `${minutes} min` : minutes < 1440 ? `${minutes / 60} h` : `${minutes / 1440} day`;
}

export function describeOutcomes(
  evaluations: SignalEvaluation[] | undefined,
  pair: PairNames,
): { outcomes: Outcome[]; note: string | null; reliability: string[] } {
  const byHorizon = new Map((evaluations ?? []).map((item) => [item.horizon_minutes, item]));
  const bases = new Set<string>();
  let measuredCount = 0;
  let missingCount = 0;
  const outcomes = HORIZONS.map((horizon): Outcome => {
    const item = byHorizon.get(horizon);
    const label = horizonLabel(horizon);
    if (!item) return { horizon: label, text: "No price check is recorded for this time.", measured: false };
    if (item.status === "measured") {
      measuredCount += 1;
      const basis = item.price_basis ?? "snapshot_mid";
      bases.add(basis);
      return {
        horizon: label,
        text: priceDirectionSentence(pair, item.observed_change_percent),
        measured: true,
      };
    }
    if (item.status === "pending") return { horizon: label, text: "Not yet. We will check later.", measured: false };
    missingCount += 1;
    return { horizon: label, text: missingReasonText(item.missing_reason), measured: false };
  });
  const reliability: string[] = [];
  if (byHorizon.size > 0) {
    reliability.push(`${measuredCount} of ${HORIZONS.length} later price checks could be measured${missingCount ? `; ${missingCount} could not` : ""}.`);
  }
  for (const basis of ["snapshot_mid", "trade_price"]) {
    if (bases.has(basis)) reliability.push(priceBasisText(basis));
  }
  const note = bases.size > 1
    ? "Some checks use trade prices; others use buy and sell offers. Compare them with care."
    : null;
  return { outcomes, note, reliability };
}

const COMMON_UNKNOWNS = [
  "Why this happened. A later price change does not show that this event caused it.",
];

export function explainSignal(
  signal: Pick<Signal, "category" | "severity" | "confidence" | "interpretation" | "pair" | "asset"> & {
    observed_facts?: Facts;
    evaluations?: SignalEvaluation[];
    volume_evidence?: VolumeEvidence | null;
  },
): Explanation {
  const facts: Facts = signal.observed_facts ?? {};
  const pair = pairNames(signal.pair ?? (typeof facts.pair === "string" ? facts.pair : null));
  const base: Explanation = {
    title: `${signal.category.replaceAll("_", " ")} in ${pair.label}`,
    why: signal.interpretation,
    observed: [],
    afterwards: [],
    afterwardsNote: null,
    reliability: [],
    unknowns: [...COMMON_UNKNOWNS],
  };
  const body = TEMPLATES[signal.category]?.(facts, pair, signal) ?? {};
  const outcome = describeOutcomes(signal.evaluations, pair);
  const result: Explanation = { ...base, ...body };
  result.afterwards = outcome.outcomes;
  result.afterwardsNote = outcome.note;
  result.reliability = [...(body.reliability ?? []), ...outcome.reliability];
  result.unknowns = [...(body.unknowns ?? []), ...COMMON_UNKNOWNS];
  const missing = outcome.outcomes.filter((item) => !item.measured && !item.text.startsWith("Not yet"));
  if (missing.length) result.unknowns.push(`${missing.length} later price check${missing.length > 1 ? "s" : ""} could not be measured.`);
  return result;
}

type Template = (
  facts: Facts,
  pair: PairNames,
  signal: { volume_evidence?: VolumeEvidence | null; confidence: string },
) => Partial<Explanation>;

function baselineMissing(what: string): string {
  return `No historical baseline is available for ${what}, so we cannot say how unusual this is.`;
}

const largeTrade: Template = (facts, pair) => {
  const ratio = timesHigher(facts.observed_quote_notional, facts.baseline_median_quote_notional);
  const unusual = ratio && !ratio.startsWith("about the same")
    ? `A single trade was ${ratio} larger (in ${pair.counter} value) than the typical trade in this market.`
    : facts.baseline_median_quote_notional ? "A single trade was larger than this market's configured large-trade threshold." : baselineMissing("trade sizes");
  const observed = [
    `${fmt(facts.base_amount_xlm, 0)} ${pair.base} was traded for about ${fmt(facts.observed_quote_notional)} ${pair.counter}.`,
    num(facts.baseline_median_quote_notional) !== null
      ? `The typical trade (median of the previous ${fmt(facts.baseline_trade_count, 0)} trades) was worth ${fmt(facts.baseline_median_quote_notional)} ${pair.counter}.`
      : "No typical trade size is available.",
    `The alert threshold for this market was ${fmt(facts.threshold_quote_notional)} ${pair.counter}.`,
    venueSentence(facts.trade_type),
  ];
  return {
    title: `A large ${pair.base} trade in ${pair.label}`,
    why: unusual,
    observed,
    reliability: [`Based on ${fmt(facts.baseline_trade_count, 0)} earlier trades${typeof facts.coverage === "string" ? ` (${facts.coverage})` : ""}.`],
    unknowns: [
      "Whether this was a buy or a sell: the alert does not record the direction of the trade.",
      "Who made the trade and why.",
    ],
  };
};

const volumeAnomaly: Template = (facts, pair, signal) => {
  const ratio = timesHigher(facts.observed_base_volume, facts.baseline_mean_base_volume);
  const from = utcTime(facts.window_start);
  const to = utcTime(facts.window_end);
  const period = from && to ? `the five-minute period ${from}–${to}` : "a five-minute period";
  const hasBaseline = num(facts.baseline_mean_base_volume) !== null && Number(facts.baseline_mean_base_volume) > 0;
  const observed = [
    `${fmt(facts.observed_base_volume, 0)} ${pair.base} was exchanged for ${pair.counter}.`,
    `Each trade had a buyer and a seller. We do not know whether people wanted to buy or sell more ${pair.base}.`,
    `The trades were recorded during ${period}.`,
    hasBaseline
      ? `The usual amount is ${fmt(facts.baseline_mean_base_volume, 0)} ${pair.base} (average of the previous ${fmt(facts.baseline_windows, 0)} five-minute periods).`
      : "No usual amount is available for comparison.",
    `The alert threshold was ${fmt(facts.threshold_base_volume, 0)} ${pair.base}.`,
  ];
  const evidence = signal.volume_evidence;
  if (evidence) observed.push(...volumeEvidenceLines(evidence, pair));
  const unknowns = ["Whether the activity was mostly buying or selling pressure, and who was behind it."];
  if (!evidence) unknowns.push("The split between order-book and liquidity-pool trades is shown in the signal details.");
  return {
    title: `More trading than usual in ${pair.label}`,
    why: hasBaseline && ratio
      ? `Trading activity was ${ratio} higher than usual during ${period}.`
      : baselineMissing("trading activity"),
    observed,
    reliability: [`Compared with ${fmt(facts.baseline_windows, 0)} earlier five-minute periods that contained trades. Periods without trades are not counted as zero.`],
    unknowns,
  };
};

export function volumeEvidenceLines(evidence: VolumeEvidence, pair: PairNames): string[] {
  const lines: string[] = [];
  const venues = evidence.venues;
  const book = venues.order_book;
  const pool = venues.liquidity_pool;
  const unknown = venues.unknown;
  if (evidence.trade_count === 0) return ["No individual trades are stored for this period."];
  lines.push(`This involved ${evidence.trade_count} trades.`);
  if (book && pool) {
    lines.push(`Order-book trades: ${book.count} (${fmt(book.base_volume, 0)} ${pair.base}). Liquidity-pool trades: ${pool.count} (${fmt(pool.base_volume, 0)} ${pair.base}).`);
  }
  if (unknown && unknown.count > 0) {
    lines.push(`For ${unknown.count} of these trades the venue was not recorded.`);
  }
  if (evidence.largest_trade_share_percent !== null) {
    const share = Number(evidence.largest_trade_share_percent);
    lines.push(
      share >= 50
        ? `One trade made up ${fmt(share, 0)}% of the volume.`
        : `The volume was spread across many trades; the largest was ${fmt(share, 0)}% of the total.`,
    );
  }
  const direction = evidence.direction;
  const sold = direction.base_is_seller_true as { count: number; base_volume: string } | undefined;
  const bought = direction.base_is_seller_false as { count: number; base_volume: string } | undefined;
  if (sold && bought && sold.count + bought.count > 0) {
    lines.push(
      `The ${pair.base} side was sold in ${sold.count} trades (${fmt(sold.base_volume, 0)} ${pair.base}) and bought in ${bought.count} trades (${fmt(bought.base_volume, 0)} ${pair.base}). This does not identify which side initiated the trade.`,
    );
  }
  return lines;
}

const movementTemplate = (kind: "price" | "spread"): Template => (facts, pair) => {
  const hasBaseline = num(facts.baseline_percent) !== null;
  const window = windowLabel(facts.window_minutes);
  if (kind === "price") {
    const observed = num(facts.observed_percent);
    const typical = hasBaseline ? `Typical movement over ${window} is about ${fmt(facts.baseline_percent)}%.` : baselineMissing("price movement");
    return {
      title: observed === null
        ? `${pair.base} price changed in ${pair.counter}`
        : `${pair.base} price ${observed < 0 ? "went down" : observed > 0 ? "went up" : "did not change"} in ${pair.counter}`,
      why: `The price of ${pair.base} in ${pair.counter} moved more over ${window} than it normally does.`,
      observed: [
        `${priceDirectionSentence(pair, facts.observed_percent)} Measured over ${window}.`,
        typical,
        `The alert threshold was ${fmt(facts.threshold_percent)}%.`,
        `The reference price was ${fmt(facts.reference_price, 4)} ${pair.counter} per ${pair.base}.`,
      ],
      reliability: [`Based on ${fmt(facts.sample_count, 0)} order-book mid-price observations${typeof facts.coverage === "string" ? ` (${facts.coverage})` : ""}.`],
      unknowns: ["What caused the price move. This alert only measures the change."],
    };
  }
  return {
    title: `The buy–sell gap widened in ${pair.label}`,
    why: hasBaseline
      ? "The gap between the best buy and best sell offers (the spread) was wider than usual, which can make trading more costly and prices less certain."
      : baselineMissing("the spread"),
    observed: [
      `The gap between buy and sell offers was ${fmt(facts.observed_percent)}% of the price.`,
      hasBaseline ? `It is usually about ${fmt(facts.baseline_percent)}%.` : "No usual spread is available.",
      `The alert threshold was ${fmt(facts.threshold_percent)}%.`,
    ],
    reliability: [`Based on ${fmt(facts.sample_count, 0)} two-sided order-book snapshots${typeof facts.coverage === "string" ? ` (${facts.coverage})` : ""}.`],
    unknowns: ["Whether the wider spread is temporary or why it happened."],
  };
};

const whaleTransfer: Template = (facts) => {
  const asset = typeof facts.asset === "string" ? (facts.asset === "native" ? "XLM" : facts.asset.split(":")[0]) : "an asset";
  return {
    title: `A large ${asset} payment was sent`,
    why: "A single payment was larger than the configured transfer-size threshold.",
    observed: [
      `${fmt(facts.amount, 0)} ${asset} was transferred.`,
      `The configured threshold was ${fmt(facts.configured_threshold, 0)} ${asset}.`,
    ],
    reliability: ["The amount comes directly from the recorded payment."],
    unknowns: ["The reason for the payment, and who owns the sending and receiving accounts."],
  };
};

const unusualVolume: Template = (facts, pair) => {
  const ratio = timesHigher(facts.current_five_minute_base_volume, facts.baseline_mean_base_volume);
  return {
    title: `Trading activity rose in ${pair.label}`,
    why: ratio && !ratio.startsWith("about the same") ? `Trading activity was ${ratio} higher than usual over five minutes.` : baselineMissing("trading activity"),
    observed: [
      `${fmt(facts.current_five_minute_base_volume, 0)} ${pair.base} was exchanged for ${pair.counter}.`,
      `Each trade had a buyer and a seller. We do not know whether people wanted to buy or sell more ${pair.base}.`,
      `The usual amount is ${fmt(facts.baseline_mean_base_volume, 0)} ${pair.base} (average of ${fmt(facts.baseline_windows, 0)} earlier periods).`,
    ],
    reliability: ["This is an earlier version of the volume rule; its history is kept as recorded."],
    unknowns: ["Whether the activity was buying or selling, and who was behind it."],
  };
};

const visibleLiquidity: Template = (facts, pair) => ({
  title: `A large order is visible in the ${pair.label} order book`,
  why: "An unusually large offer was waiting in the order book. Orders can be cancelled at any time.",
  observed: [
    `Best buy offer: ${fmt(facts.best_bid, 4)} ${pair.counter}. Best sell offer: ${fmt(facts.best_ask, 4)} ${pair.counter}.`,
    `The configured size threshold was ${fmt(facts.configured_base_amount_threshold, 0)} ${pair.base}.`,
  ],
  reliability: ["Based on a single order-book snapshot."],
  unknowns: ["Whether the order will stay, be cancelled, or ever be executed. This is not evidence of a trade."],
});

const TEMPLATES: Record<string, Template> = {
  large_trade: largeTrade,
  volume_anomaly: volumeAnomaly,
  price_movement: movementTemplate("price"),
  spread_anomaly: movementTemplate("spread"),
  wide_spread: (facts, pair) => ({
    title: `The buy–sell gap is wide in ${pair.label}`,
    why: "The gap between the best buy and sell offers was large. This can make trading more costly.",
    observed: [
      `The gap between buy and sell offers was ${fmt(facts.spread_percent)}% of the price.`,
      `The configured threshold was ${fmt(facts.configured_threshold_percent)}%. No historical comparison is available for this older rule.`,
    ],
    reliability: ["Based on a single order-book snapshot."],
    unknowns: ["Whether this spread is unusual for this market."],
  }),
  whale_transfer: whaleTransfer,
  unusual_volume: unusualVolume,
  visible_liquidity: visibleLiquidity,
};

export function categoryTitle(category: string): string {
  const names: Record<string, string> = {
    large_trade: "Large trade",
    volume_anomaly: "Unusual trading",
    price_movement: "Price change",
    spread_anomaly: "Buy and sell price gap",
    wide_spread: "Wide price gap (older rule)",
    whale_transfer: "Large payment",
    unusual_volume: "Unusual trading (older rule)",
    visible_liquidity: "Large visible order",
  };
  return names[category] ?? category.replaceAll("_", " ");
}

// Transparent ordering: events from the last 24 hours first, then by severity, then newest.
export function rankSignals<T extends Pick<Signal, "severity" | "occurred_at">>(items: T[], now: Date = new Date()): T[] {
  const cutoff = now.getTime() - 24 * 3600 * 1000;
  const recent = (item: T) => (new Date(item.occurred_at).getTime() >= cutoff ? 1 : 0);
  return [...items].sort(
    (a, b) =>
      recent(b) - recent(a) ||
      severityInfo(b.severity).rank - severityInfo(a.severity).rank ||
      new Date(b.occurred_at).getTime() - new Date(a.occurred_at).getTime(),
  );
}

export function marketSummary(notable24h: number, ranked: Pick<Signal, "severity" | "category" | "pair" | "occurred_at">[], marketCount: number): string {
  if (marketCount === 0) return "No markets are being monitored yet.";
  const monitored = `${marketCount} market${marketCount === 1 ? " is" : "s are"} being monitored.`;
  if (notable24h === 0) return `${monitored} No notable events were recorded in the last 24 hours.`;
  const top = ranked[0];
  const topIsRecent = top && Date.now() - new Date(top.occurred_at).getTime() <= 24 * 3600 * 1000;
  const topText = top && topIsRecent
    ? ` The most important recent event is a ${severityInfo(top.severity).label.toLowerCase()}-severity ${categoryTitle(top.category).toLowerCase()} in ${pairNames(top.pair).label}.`
    : "";
  return `${monitored} ${notable24h} notable event${notable24h === 1 ? " was" : "s were"} recorded in the last 24 hours.${topText}`;
}
