import type { Analytics } from "./api";
import { TargetResults } from "./TargetResults";

export function SignalAnalytics({ data, days, setDays }: {
  data: Analytics;
  days: number;
  setDays: (value: number) => void;
}) {
  return <>
    <div className="page-heading">
      <div><div className="eyebrow">YOUR SIGNALS</div><h1>Understand your signals</h1>
        <p>See what we noticed and what prices did later.</p></div>
      <select aria-label="Time period" value={days} onChange={(event) => setDays(Number(event.target.value))}>
        <option value={1}>24 hours</option><option value={7}>7 days</option><option value={30}>30 days</option><option value={90}>90 days</option><option value={365}>1 year</option>
      </select>
    </div>
    {data.excluded_legacy_signals > 0 && <p className="signal-analytics-note">{data.excluded_legacy_signals} older {data.excluded_legacy_signals === 1 ? "signal is" : "signals are"} not included because they used different rules.</p>}
    <TargetResults days={days} />
  </>;
}
