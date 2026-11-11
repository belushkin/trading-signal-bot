import type { ReactNode } from "react";
import type { Signal, SignalEvaluation, VolumeEvidence } from "./api";
import { categoryTitle, explainSignal, pairNames, severityInfo } from "./explain";
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
      <Section question="Why should I care?"><p>{explanation.why}</p></Section>
      <Section question="What did we observe?"><ul>{explanation.observed.map((line) => <li key={line}>{line}</li>)}</ul></Section>
      <Section question="What happened afterwards?">
        <ul className="afterwards">
          {explanation.afterwards.map((item) => <li key={item.horizon} className={item.measured ? "" : "muted"}><strong>{item.horizon}</strong> {item.text}</li>)}
        </ul>
        {explanation.afterwardsNote && <p className="story-note">{explanation.afterwardsNote}</p>}
        <p className="story-note">{measured ? "These are observed price changes only. They are not profit or trading performance, and they do not show that the event caused them." : "No later price has been measured yet. An unmeasured price is not the same as no price change."}</p>
      </Section>
      <Section question="How reliable is this assessment?"><ul>{explanation.reliability.map((line) => <li key={line}>{line}</li>)}</ul></Section>
      <Section question="What don't we know?"><ul>{explanation.unknowns.map((line) => <li key={line}>{line}</li>)}</ul></Section>
    </div>
  );
}

export function SignalCard({ signal, onOpen }: { signal: Explainable; onOpen: (id: string) => void }) {
  const explanation = explainSignal(signal);
  const pair = pairNames(signal.pair);
  return (
    <article className={`panel signal-card sev-${signal.severity.toLowerCase()}`}>
      <header>
        <SeverityTag value={signal.severity} />
        <span className="signal-kind">{categoryTitle(signal.category)}</span>
        <span className="signal-market">{pair.label}</span>
        <time className="muted" dateTime={signal.occurred_at}>{ago(signal.occurred_at)}</time>
      </header>
      <h3>What happened? {explanation.title}</h3>
      <ExplanationBody explanation={explanation} />
      <button className="button button-secondary" onClick={() => onOpen(signal.signal_id)}>See evidence and technical details</button>
    </article>
  );
}
