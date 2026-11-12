import type { Signal } from "./api";
import { categoryTitle, explainSignal, pairNames, severityInfo, timesHigher } from "./explain";

// Deterministic grouping of individual signals into market episodes, plus rule-based
// recommendations about what a human could check next. Nothing here changes stored signals,
// infers trade direction, or gives trading advice.

export const SAME_KIND_GAP_MINUTES = 30;
export const CROSS_KIND_GAP_MINUTES = 10;
export const MAX_EPISODE_HOURS = 6;
export const ONGOING_WITHIN_MINUTES = 30;
export const FRESH_HOURS = 6;

export type ActionLevel = "investigate" | "monitor" | "insufficient" | "informational";
export type EpisodeStatus = "ongoing" | "ended" | "unknown";
export type EvidenceLevel = "insufficient" | "limited" | "reasonable";
export type ReviewStatus = "reviewed" | "dismissed";

export type EpisodeReview = {
  episode_key: string;
  status: ReviewStatus;
  reviewed_at: string;
  signal_count: number;
  max_severity: string;
  last_signal_at: string;
};

export type Episode = {
  key: string;
  pair: string | null;
  signals: Signal[];
  start: number;
  end: number;
  maxSeverity: string;
  primary: Signal;
  status: EpisodeStatus;
  action: ActionLevel;
  evidence: EvidenceLevel;
  evidenceReasons: string[];
  title: string;
  happened: string;
  why: string;
  keyObservation: string | null;
  next: string;
  unknowns: string[];
};

export type Context = { now: number; collectorHealthy: boolean };

type Family = "volume" | "price" | "liquidity" | "trade" | "other";

const FAMILY: Record<string, Family> = {
  volume_anomaly: "volume",
  unusual_volume: "volume",
  price_movement: "price",
  spread_anomaly: "liquidity",
  wide_spread: "liquidity",
  visible_liquidity: "liquidity",
  large_trade: "trade",
  whale_transfer: "trade",
};

const PHRASE: Record<string, string> = {
  volume_anomaly: "Unusually high trading activity",
  unusual_volume: "Unusually high trading activity",
  price_movement: "Larger-than-usual price move",
  spread_anomaly: "Trading conditions changed",
  wide_spread: "Trading conditions changed",
  visible_liquidity: "Large visible order",
  large_trade: "Large trade",
  whale_transfer: "Large payment",
};

const MINUTE = 60_000;

export function familyOf(category: string): Family {
  return FAMILY[category] ?? "other";
}

function time(value: string): number {
  const parsed = new Date(value).getTime();
  return Number.isFinite(parsed) ? parsed : 0;
}

// The measurement window a signal describes; falls back to its own timestamp.
export function signalInterval(signal: Signal): [number, number] {
  const occurred = time(signal.occurred_at);
  const facts = signal.observed_facts ?? {};
  if (typeof facts.window_start === "string" && typeof facts.window_end === "string") {
    const start = time(facts.window_start);
    const end = time(facts.window_end);
    if (start > 0 && end >= start) return [start, end];
  }
  if (typeof facts.window_minutes === "number" && facts.window_minutes > 0) {
    return [occurred - facts.window_minutes * MINUTE, occurred];
  }
  return [occurred, occurred];
}

function gapMinutes(a: [number, number], b: [number, number]): number {
  return Math.max(0, Math.max(a[0], b[0]) - Math.min(a[1], b[1])) / MINUTE;
}

function related(a: Signal, b: Signal): boolean {
  const limit = a.category === b.category ? SAME_KIND_GAP_MINUTES : CROSS_KIND_GAP_MINUTES;
  return gapMinutes(signalInterval(a), signalInterval(b)) <= limit;
}

// Same exact pair (including issuers) and close in time. Different signal types are only
// joined when their windows overlap or nearly touch; uncertain cases stay separate.
export function groupSignals(signals: Signal[]): Signal[][] {
  const sorted = [...signals].sort(
    (a, b) => signalInterval(a)[0] - signalInterval(b)[0] || time(a.occurred_at) - time(b.occurred_at) || a.signal_id.localeCompare(b.signal_id),
  );
  const groups: Signal[][] = [];
  for (const signal of sorted) {
    let target: Signal[] | null = null;
    let targetEnd = -Infinity;
    if (signal.pair) {
      for (const group of groups) {
        if (group[0].pair !== signal.pair) continue;
        const groupStart = Math.min(...group.map((item) => signalInterval(item)[0]));
        const groupEnd = Math.max(...group.map((item) => signalInterval(item)[1]));
        if (signalInterval(signal)[1] - groupStart > MAX_EPISODE_HOURS * 60 * MINUTE) continue;
        if (!group.some((item) => related(item, signal))) continue;
        if (groupEnd >= targetEnd) {
          target = group;
          targetEnd = groupEnd;
        }
      }
    }
    if (target) target.push(signal);
    else groups.push([signal]);
  }
  return groups;
}

function severityRank(value: string): number {
  return severityInfo(value).rank;
}

function describeDuration(ms: number): string {
  const minutes = Math.max(0, Math.round(ms / MINUTE));
  if (minutes < 1) return "less than a minute";
  if (minutes < 60) return `${minutes} minute${minutes === 1 ? "" : "s"}`;
  const hours = Math.round(minutes / 6) / 10;
  return `${hours} hour${hours === 1 ? "" : "s"}`;
}

function ago(ms: number): string {
  return `${describeDuration(ms)} ago`;
}

function assessEvidence(signals: Signal[], ctx: Context): { level: EvidenceLevel; reasons: string[] } {
  const withFacts = signals.filter((item) => item.observed_facts && Object.keys(item.observed_facts).length > 0);
  if (!withFacts.length) {
    return { level: "insufficient", reasons: ["No measurements were stored with these signals, so we cannot judge how unusual they were."] };
  }
  const reasons: string[] = [];
  if (signals.length === 1) reasons.push("Only one signal supports this event.");
  if (!ctx.collectorHealthy) reasons.push("Data collection is not currently healthy, so newer activity may be missing.");
  const evaluations = signals.flatMap((item) => item.evaluations ?? []);
  const missing = evaluations.filter((item) => item.status === "missing").length;
  if (missing > 0 && !evaluations.some((item) => item.status === "measured")) {
    reasons.push("Later price checks could not be measured.");
  }
  return { level: reasons.length ? "limited" : "reasonable", reasons };
}

function chooseAction(signals: Signal[], maxSeverity: string, evidence: EvidenceLevel, end: number, ctx: Context): ActionLevel {
  const rank = severityRank(maxSeverity);
  const fresh = ctx.now - end <= FRESH_HOURS * 60 * MINUTE;
  if (evidence === "insufficient") return rank >= severityRank("WATCH") && fresh ? "insufficient" : "informational";
  if (!fresh || rank <= severityRank("INFO")) return "informational";
  // One large trade alone does not establish a pattern, so it is never escalated on size alone.
  const onlyTrades = signals.every((item) => familyOf(item.category) === "trade");
  const pattern = !onlyTrades || signals.length >= 3;
  if (rank >= severityRank("HIGH") && pattern) return "investigate";
  const families = new Set(signals.map((item) => familyOf(item.category)));
  if (rank >= severityRank("WATCH") && families.size >= 2 && pattern) return "investigate";
  return "monitor";
}

const FAMILY_ORDER: Family[] = ["price", "volume", "liquidity", "trade", "other"];

const WHY: Record<Family, string> = {
  volume: "Activity far above this market's usual level differs from its recent history. It does not by itself say why it happened.",
  price: "The price moved more than it usually does over a similar period. This does not say why it moved.",
  liquidity: "The gap between buy and sell offers changed, which can make trading in this market costlier or less predictable.",
  trade: "A single large trade is unusual for this market, but its size alone does not establish market direction.",
  other: "This differs from the market's recent history.",
};

function nextSentence(family: Family, status: EpisodeStatus): string | null {
  switch (family) {
    case "volume":
      return status === "ended" ? "Check whether activity has returned to its usual level." : "Check whether elevated activity is continuing.";
    case "price":
      return "Check whether the move is also visible in other markets and confirmed by actual trades.";
    case "liquidity":
      return "Check whether liquidity has deteriorated before drawing conclusions.";
    case "trade":
      return "Review surrounding activity only if the trade is part of a broader pattern.";
    default:
      return null;
  }
}

function recommend(action: ActionLevel, families: Family[], status: EpisodeStatus): string {
  if (action === "informational") return "No action needed — informational only.";
  if (action === "insufficient") return "Evidence is insufficient to recommend an action. Wait for more data.";
  const sentences = families.map((family) => nextSentence(family, status)).filter((item): item is string => item !== null).slice(0, 2);
  const lead = action === "investigate" ? "Worth a manual review: open the details and inspect recent activity." : "Keep monitoring this market.";
  return [lead, ...sentences].join(" ");
}

export function buildEpisode(signals: Signal[], ctx: Context): Episode {
  const sorted = [...signals].sort((a, b) => time(a.occurred_at) - time(b.occurred_at) || a.signal_id.localeCompare(b.signal_id));
  const intervals = sorted.map(signalInterval);
  const start = Math.min(...intervals.map((item) => item[0]));
  const end = Math.max(...sorted.map((item) => time(item.occurred_at)));
  const maxSeverity = sorted.reduce((best, item) => (severityRank(item.severity) > severityRank(best) ? item.severity : best), sorted[0].severity);
  const primary = [...sorted].sort((a, b) => severityRank(b.severity) - severityRank(a.severity) || time(b.occurred_at) - time(a.occurred_at))[0];
  const label = pairNames(primary.pair).label;

  const age = ctx.now - end;
  const status: EpisodeStatus = !ctx.collectorHealthy ? "unknown" : age <= ONGOING_WITHIN_MINUTES * MINUTE ? "ongoing" : "ended";
  const evidence = assessEvidence(sorted, ctx);
  const action = chooseAction(sorted, maxSeverity, evidence.level, end, ctx);

  const categories = [...new Set(sorted.map((item) => item.category))];
  const phrases = [...new Set(categories.map((item) => PHRASE[item] ?? categoryTitle(item)))];
  const families = FAMILY_ORDER.filter((family) => categories.some((item) => familyOf(item) === family));
  const headline = phrases.length > 2 ? `${phrases.slice(0, 2).join(" and ")} and more` : phrases.join(" and ");

  const span = end - start;
  const happened =
    sorted.length === 1
      ? `${phrases[0]} was recorded in ${label} ${ago(ctx.now - end)}.`
      : `${sorted.length} related signals about ${label} were recorded over ${describeDuration(span)}; the latest was ${ago(ctx.now - end)}.`;

  const facts = primary.observed_facts ?? {};
  const ratio = familyOf(primary.category) === "volume" ? timesHigher(facts.observed_base_volume ?? facts.current_five_minute_base_volume, facts.baseline_mean_base_volume) : null;
  const explanation = explainSignal(primary);
  const keyObservation = ratio ? `Trading activity was about ${ratio} higher than usual during the measured period.` : explanation.observed[0] ?? null;

  const unknowns = [...new Set(explanation.unknowns)].slice(0, 4);
  if (evidence.level === "limited") unknowns.push(...evidence.reasons);

  return {
    key: sorted[0].signal_id,
    pair: primary.pair,
    signals: sorted,
    start,
    end,
    maxSeverity,
    primary,
    status,
    action,
    evidence: evidence.level,
    evidenceReasons: evidence.reasons,
    title: `${headline} — ${label}`,
    happened,
    why: families.length ? WHY[families[0]] : WHY.other,
    keyObservation,
    next: recommend(action, families, status),
    unknowns,
  };
}

export function buildEpisodes(signals: Signal[], ctx: Context): Episode[] {
  const ACTION_RANK: Record<ActionLevel, number> = { investigate: 0, monitor: 1, insufficient: 2, informational: 3 };
  const EVIDENCE_RANK: Record<EvidenceLevel, number> = { reasonable: 0, limited: 1, insufficient: 2 };
  return groupSignals(signals)
    .map((group) => buildEpisode(group, ctx))
    .sort(
      (a, b) =>
        ACTION_RANK[a.action] - ACTION_RANK[b.action] ||
        severityRank(b.maxSeverity) - severityRank(a.maxSeverity) ||
        EVIDENCE_RANK[a.evidence] - EVIDENCE_RANK[b.evidence] ||
        b.end - a.end,
    );
}

export type Placement = "attention" | "quiet" | "closed";

export type Placed = { episode: Episode; placement: Placement; review: EpisodeReview | null; resurfaced: boolean; newSinceReview: number };

// Reviewed or dismissed episodes leave the attention list. A reviewed episode returns only when
// new related evidence is more severe than when it was reviewed; other new signals just update it.
export function placeEpisodes(episodes: Episode[], reviews: EpisodeReview[]): Placed[] {
  const byKey = new Map(reviews.map((item) => [item.episode_key, item]));
  return episodes.map((episode) => {
    const review = byKey.get(episode.key) ?? null;
    const newSinceReview = review ? Math.max(0, episode.signals.length - review.signal_count) : 0;
    let resurfaced = false;
    let placement: Placement = episode.action === "informational" ? "quiet" : "attention";
    if (review) {
      resurfaced = review.status === "reviewed" && severityRank(episode.maxSeverity) > severityRank(review.max_severity) && placement === "attention";
      placement = resurfaced ? "attention" : "closed";
    }
    return { episode, placement, review, resurfaced, newSinceReview };
  });
}

export const ACTION_LABELS: Record<ActionLevel, { label: string; meaning: string }> = {
  investigate: { label: "Investigate", meaning: "Worth a manual review." },
  monitor: { label: "Monitor", meaning: "Potentially interesting, but no immediate investigation is needed." },
  insufficient: { label: "Insufficient evidence", meaning: "Evidence is insufficient — wait for more data." },
  informational: { label: "No action", meaning: "Informational event; no action required." },
};

export const STATUS_LABELS: Record<EpisodeStatus, string> = {
  ongoing: "Ongoing — new related signals in the last 30 minutes",
  ended: "Appears to have ended — no new related signals for over 30 minutes",
  unknown: "Unknown — data collection is not healthy, so we cannot tell",
};

export const EVIDENCE_LABELS: Record<EvidenceLevel, string> = {
  reasonable: "Reasonable evidence",
  limited: "Limited evidence",
  insufficient: "Insufficient evidence",
};
