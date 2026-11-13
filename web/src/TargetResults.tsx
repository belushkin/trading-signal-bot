import { useEffect, useState } from "react";
import { get } from "./api";
import { horizonLabel, opportunityLabel, pairNames } from "./explain";

type Row = {
  signal_id: string; pair: string; side: "BUY" | "SELL"; created_at: string; horizon_minutes: number;
  status: string; target_price_observed: boolean | null;
  quote_amount: string | null; quantity: string | null; estimated_profit_quote: string | null;
};

const short = (value: string | null) => value !== null && Number.isFinite(Number(value)) ? Number(value).toFixed(7).replace(/\.?0+$/, "") : "unknown";
const assetName = (asset: string) => asset === "native" ? "XLM" : asset.split(":")[0];

export function TargetResults({ days }: { days: number }) {
  const [rows, setRows] = useState<Row[] | null>(null);
  const [error, setError] = useState("");
  const [horizon, setHorizon] = useState(5);
  const [pair, setPair] = useState("");
  const [side, setSide] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    get<{ items: Row[] }>(`/api/assistant/target-results?days=${days}`, controller.signal)
      .then((response) => {
        if (!Array.isArray(response.items)) throw new Error("Invalid response");
        setRows(response.items); setError("");
      })
      .catch((reason: unknown) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Could not load"); });
    return () => controller.abort();
  }, [days]);
  if (error) return <p role="alert" className="notice notice-error">Target results unavailable: {error}</p>;
  if (rows === null) return <p>Loading target results…</p>;
  const filtered = rows.filter((row) => (!pair || row.pair === pair) && (!side || row.side === side));
  const pairs = [...new Set(rows.map((row) => row.pair))].sort();
  const horizons = [...new Set([5, 10, 60, 240, 1440, ...rows.map((row) => row.horizon_minutes)])].sort((a, b) => a - b);
  const summary = horizons.map((minutes) => {
    const items = filtered.filter((row) => row.horizon_minutes === minutes);
    const checked = items.filter((row) => row.target_price_observed !== null);
    const seen = checked.filter((row) => row.target_price_observed);
    return { minutes, checked: checked.length, seen: seen.length, waiting: items.length - checked.length };
  });
  const seenRows = filtered.filter((row) => row.horizon_minutes === horizon && row.target_price_observed);
  return <section className="section-block target-results" aria-labelledby="target-results-title">
    <div className="signal-analytics-section-heading"><div><h2 id="target-results-title">Was the target price seen?</h2>
      <p>For each idea we saved a target price. This shows how often the market price reached it.</p></div></div>
    <div className="signal-analytics-filters">
      <label>Market<select aria-label="Target market" value={pair} onChange={(event) => setPair(event.target.value)}>
        <option value="">All markets</option>{pairs.map((value) => <option value={value} key={value}>{pairNames(value).label}</option>)}
      </select></label>
      <label>Idea<select aria-label="Idea side" value={side} onChange={(event) => setSide(event.target.value)}>
        <option value="">Buy and sell</option><option value="BUY">Buy</option><option value="SELL">Sell</option>
      </select></label>
    </div>
    <div className="target-summary">
      {summary.map((item) => <div key={item.minutes} className={`panel target-summary-card ${item.checked && item.seen ? "is-hit" : ""}`}>
        <span>{horizonLabel(item.minutes)}</span>
        <strong>{item.checked ? `${item.seen} of ${item.checked}` : "–"}</strong>
        <small>{item.checked ? `${Math.round((item.seen / item.checked) * 100)}% reached the target` : "No checks yet"}{item.waiting ? ` · ${item.waiting} waiting` : ""}</small>
      </div>)}
    </div>
    <p className="signal-analytics-note">Seeing the target price does not mean a trade was possible at that amount.</p>
    <div className="signal-analytics-filters">
      <label>Show amounts for<select aria-label="Amounts after" value={horizon} onChange={(event) => setHorizon(Number(event.target.value))}>
        {horizons.map((value) => <option value={value} key={value}>{horizonLabel(value)}</option>)}
      </select></label>
    </div>
    {seenRows.length ? <div className="target-table-wrap"><table className="target-table">
      <thead><tr><th>Idea</th><th>Amount</th><th>Trade size</th><th>Possible gain</th><th>Date</th></tr></thead>
      <tbody>{seenRows.map((row) => {
        const [base, quote] = row.pair.split("/");
        return <tr key={`${row.signal_id}-${row.horizon_minutes}`}>
          <td>{opportunityLabel(row.pair, row.side)}</td>
          <td>{short(row.quantity)} {assetName(base)}</td>
          <td>{short(row.quote_amount)} {assetName(quote)}</td>
          <td>{short(row.estimated_profit_quote)} {assetName(quote)}</td>
          <td>{new Date(row.created_at).toLocaleString()}</td>
        </tr>;
      })}</tbody></table></div> : <div className="panel signal-analytics-empty">No target price was seen {horizonLabel(horizon).toLowerCase()} for these choices.</div>}
  </section>;
}
