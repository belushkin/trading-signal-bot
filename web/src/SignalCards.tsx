import type { ReactNode } from "react";
import type { Signal, SignalEvaluation, VolumeEvidence } from "./api";
import { categoryTitle, explainSignal, fmt, horizonLabel, pairNames, priceDirectionSentence, severityInfo } from "./explain";
import type { Explanation } from "./explain";

export function SeverityTag({ value }: { value: string }) {
  const info = severityInfo(value);
  return <span className={`severity-badge ${value.toLowerCase()}`} title={info.meaning}>{info.label}</span>;
}

function ago(value: string): string {
  const minutes = Math.max(0, Math.round((Date.now() - new Date(value).getTime()) / 60000));
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  if (minutes < 1440) return `${Math.round(minutes / 60)} h ago`;
  return `${Math.round(minutes / 1440)} d ago`;
}

type Explainable = Signal & {
  volume_evidence?: VolumeEvidence | null;
  evaluations?: SignalEvaluation[];
};

function Section({ question, children }: { question: string; children: ReactNode }) {
  return <section className="story-section"><h4>{question}</h4>{children}</section>;
}

export function ExplanationBody({ explanation }: { explanation: Explanation }) {
  const measured = explanation.afterwards.some((item) => item.measured);
  return (
    <div className="story">
      <Section question="Why does it matter?"><p>{explanation.why}</p></Section>
      <Section question="What happened?"><ul>{explanation.observed.map((line) => <li key={line}>{line}</li>)}</ul></Section>
      <Section question="What happened later?">
        <ul className="afterwards">
          {explanation.afterwards.map((item) => <li key={item.horizon} className={item.measured ? "" : "muted"}><strong>{item.horizon}</strong> {item.text}</li>)}
        </ul>
        {explanation.afterwardsNote && <p className="story-note">{explanation.afterwardsNote}</p>}
        <p className="story-note">{measured ? "These are price changes, not profit. We do not know if the event caused them." : "We do not have a later price yet. This does not mean the price stayed the same."}</p>
      </Section>
      <Section question="How much data do we have?"><ul>{explanation.reliability.map((line) => <li key={line}>{line}</li>)}</ul></Section>
      <Section question="What is unknown?"><ul>{explanation.unknowns.map((line) => <li key={line}>{line}</li>)}</ul></Section>
    </div>
  );
}

export function SignalCard({ signal, onOpen }: { signal: Explainable; onOpen: (id: string) => void }) {
  const explanation = explainSignal(signal);
  const pair = pairNames(signal.pair);
  const observation = explanation.observed[0];
  const missingOutcome = signal.evaluations?.some((item) => item.status !== "pending" && item.status !== "measured");
  const totals = signal.trade_totals;
  const singleTrade = signal.category === "large_trade"
    && signal.observed_facts?.base_amount_xlm != null
    && signal.observed_facts?.observed_quote_notional != null;
  const measured = [...(signal.evaluations ?? [])]
    .filter((item) => item.status === "measured")
    .sort((a, b) => a.horizon_minutes - b.horizon_minutes);
  return (
    <article className={`panel signal-card sev-${signal.severity.toLowerCase()}`}>
      <header>
        <SeverityTag value={signal.severity} />
        <span className="signal-kind">{categoryTitle(signal.category)}</span>
        <span className="signal-market">{pair.label}</span>
        <time className="muted" dateTime={signal.occurred_at}>{ago(signal.occurred_at)}</time>
      </header>
      <h3>{explanation.title}</h3>
      {totals ? <p className="signal-summary">{totals.trade_count} {totals.trade_count === 1 ? "trade exchanged" : "trades exchanged"} about {fmt(totals.base_amount)} {pair.base} for {fmt(totals.quote_amount)} {pair.counter}.</p>
        : singleTrade ? <p className="signal-summary">1 trade exchanged about {fmt(signal.observed_facts?.base_amount_xlm)} {pair.base} for {fmt(signal.observed_facts?.observed_quote_notional)} {pair.counter}.</p>
        : observation && !observation.includes("N/A") && <p className="signal-summary">{observation}</p>}
      {signal.category === "volume_anomaly" && !totals && <p className="signal-summary">Trade count and total received are unavailable.</p>}
      {measured.length ? measured.map((item) => (
        <p className="signal-outcome" key={item.horizon_minutes}>
          After {horizonLabel(item.horizon_minutes)}: {priceDirectionSentence(pair, item.observed_change_percent)}
        </p>
      )) : <p className="signal-outcome">{missingOutcome ? "We do not have enough data to check the later price." : "We have not checked the later price yet."}</p>}
      {measured.length > 0 && missingOutcome && <p className="signal-outcome">Some later price checks do not have enough data.</p>}
      {explanation.afterwardsNote && <p className="signal-outcome">{explanation.afterwardsNote}</p>}
      <button className="button button-secondary" onClick={() => onOpen(signal.signal_id)}>View details</button>
    </article>
  );
}
