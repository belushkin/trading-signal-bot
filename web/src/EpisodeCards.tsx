import { useState } from "react";
import type { Episode, Placed } from "./episodes";
import { ACTION_LABELS, EVIDENCE_LABELS, STATUS_LABELS } from "./episodes";
import { categoryTitle, severityInfo } from "./explain";
import { assessDirection } from "./direction";

function formatTime(ms: number): string {
  return new Date(ms).toISOString().replace("T", " ").slice(0, 16) + " UTC";
}

export function ActionTag({ action }: { action: Episode["action"] }) {
  return <span className={`action-tag action-${action}`} title={ACTION_LABELS[action].meaning}>{ACTION_LABELS[action].label}</span>;
}

type CardProps = {
  placed: Placed;
  onOpenSignal: (id: string) => void;
  onReview: (episode: Episode, status: "reviewed" | "dismissed") => Promise<void>;
  onReopen: (episode: Episode) => Promise<void>;
};

export function EpisodeCard({ placed, onOpenSignal, onReview, onReopen }: CardProps) {
  const { episode, review, resurfaced, newSinceReview, placement } = placed;
  const direction = assessDirection(episode.signals);
  const [busy, setBusy] = useState(false);
  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    try {
      await action();
    } finally {
      setBusy(false);
    }
  };
  return (
    <article className={`panel episode-card action-border-${episode.action}`} aria-label={episode.title}>
      <header>
        <ActionTag action={episode.action} />
        <span className="signal-kind">{episode.signals.length > 1 ? `${episode.signals.length} related signals` : "1 signal"}</span>
        <span className="episode-status">{STATUS_LABELS[episode.status].split(" — ")[0]}</span>
      </header>
      <h3>{episode.title}</h3>
      {resurfaced && <p className="story-note">New, more severe evidence arrived since you reviewed this event.</p>}
      {review && placement === "closed" && (
        <p className="story-note">You marked this {review.status === "dismissed" ? "dismissed" : "reviewed"}{newSinceReview > 0 ? `; ${newSinceReview} related signal${newSinceReview === 1 ? "" : "s"} arrived since, without higher severity` : ""}.</p>
      )}
      <dl className="episode-answers">
        <div><dt>What happened?</dt><dd>{episode.happened}</dd></div>
        <div><dt>Why does it matter?</dt><dd>{episode.why}</dd></div>
        <div><dt>What should I potentially do?</dt><dd><strong>{episode.next}</strong></dd></div>
        <div><dt>Is there enough evidence?</dt><dd>{EVIDENCE_LABELS[episode.evidence]}. {direction.summary}</dd></div>
      </dl>
      <p className="story-note">Monitoring information only — not trading advice. The data does not establish who was buying or selling, or why.</p>
      <div className="episode-actions">
        {placement === "closed" ? (
          <button className="button button-secondary" disabled={busy} onClick={() => run(() => onReopen(episode))}>Show in attention list again</button>
        ) : (
          <>
            <button className="button button-secondary" disabled={busy} onClick={() => run(() => onReview(episode, "reviewed"))}>Mark as reviewed</button>
            <button className="button button-secondary" disabled={busy} onClick={() => run(() => onReview(episode, "dismissed"))}>Dismiss</button>
          </>
        )}
      </div>
      <details className="technical-details">
        <summary>Evidence and technical details</summary>
        <ul className="episode-facts">
          <li>Status: {STATUS_LABELS[episode.status]}.</li>
          {episode.keyObservation && <li>{episode.keyObservation}</li>}
          <li>{direction.evidence} Aggressor direction and intent are not known.</li>
          {episode.evidenceReasons.map((reason) => <li key={reason}>{reason}</li>)}
          <li>Time range: {formatTime(episode.start)} to {formatTime(episode.end)}.</li>
          <li>Highest severity: {severityInfo(episode.maxSeverity).label}. Signal types: {[...new Set(episode.signals.map((item) => categoryTitle(item.category)))].join(", ")}.</li>
          <li>Grouping rule: same exact market; same signal type within 30 minutes, or different types within 10 minutes. The episode ID is the earliest signal ID: <code>{episode.key}</code>.</li>
          {episode.unknowns.map((item) => <li key={item}>Unknown: {item}</li>)}
        </ul>
        <table className="episode-signals">
          <thead><tr><th>Signal</th><th>Severity</th><th>Time</th><th /></tr></thead>
          <tbody>
            {episode.signals.map((item) => (
              <tr key={item.signal_id}>
                <td>{categoryTitle(item.category)}</td>
                <td>{severityInfo(item.severity).label}</td>
                <td>{formatTime(new Date(item.occurred_at).getTime())}</td>
                <td><button className="text-button" onClick={() => onOpenSignal(item.signal_id)}>Open full evidence</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </article>
  );
}
