import { useEffect, useState } from "react";
import { get } from "./api";
import { opportunityLabel } from "./explain";

type Evaluation = {
  minutes: number;
  status: string;
  result: { entry_price_observed: boolean; target_price_observed_after_entry: boolean; price_change_percent: string | null; note: string } | null;
};
type Opportunity = {
  signal_id: string; pair: string; side: "BUY" | "SELL"; created_at: string; expires_at: string;
  parameters: {
    entry_limit: string; target_exit: string; quantity: string;
    quote_amount: string; estimated_fees_quote: string; estimated_profit_quote: string;
    fee_xlm_per_transaction: string; estimated_net_percent: string;
  };
  evaluations: Evaluation[];
};

const short = (value: string) => Number.isFinite(Number(value)) ? Number(value).toFixed(7).replace(/\.?0+$/, "") : value;

function Card({ item, expired }: { item: Opportunity; expired: boolean }) {
  const [base, quote] = item.pair.split("/");
  const label = (asset: string) => asset === "native" ? "XLM" : asset.split(":")[0];
  return <article className="assistant-section panel">
    <h3>{opportunityLabel(item.pair, item.side)}</h3>
    <p>Appeared: <time dateTime={item.created_at}>{new Date(item.created_at).toLocaleString()}</time> · {expired ? "Ended" : "Ends"}: <time dateTime={item.expires_at}>{new Date(item.expires_at).toLocaleString()}</time> (your local time).</p>
    <p>The price moved {item.side === "BUY" ? "up" : "down"}.</p>
    <p>Amount: {short(item.parameters.quantity)} {label(base)} · Suggested starting price: {short(item.parameters.entry_limit)} · Target price: {short(item.parameters.target_exit)} {label(quote)} per {label(base)}.</p>
    <p>Trade size: {short(item.parameters.quote_amount)} {label(quote)} · Estimated fee to start: {short(item.parameters.fee_xlm_per_transaction)} XLM · Estimated fee to exit: {short(item.parameters.fee_xlm_per_transaction)} XLM · Total estimated fees: {short(item.parameters.estimated_fees_quote)} {label(quote)}.</p>
    <p>Possible gain at target: {short(item.parameters.estimated_profit_quote)} {label(quote)} ({Number(item.parameters.estimated_net_percent).toFixed(2)}%).</p>
    <ul>{item.evaluations.map((evaluation) => <li key={evaluation.minutes} className={evaluation.status === "pending" || !evaluation.result ? "" : evaluation.result.target_price_observed_after_entry ? "followup-hit" : "followup-miss"} title={evaluation.result ? (evaluation.result.target_price_observed_after_entry ? "Target price was seen" : "Target price was not seen") : undefined}>{evaluation.minutes < 60 ? `${evaluation.minutes}m` : `${evaluation.minutes / 60}h`}: {evaluation.status === "pending" ? "We will check later" : !evaluation.result ? "Not enough data" : `Price change: ${evaluation.result.price_change_percent === null ? "unknown" : `${Number(evaluation.result.price_change_percent).toFixed(2)}%`}.`}</li>)}</ul>
  </article>;
}

export function Opportunities() {
  const [data, setData] = useState<{ items: Opportunity[]; screening: string; configured: boolean } | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    const refresh = () => {
      get<{ items: Opportunity[]; screening: string; configured: boolean }>("/api/assistant/opportunities", controller.signal)
        .then((response) => {
          if (!Array.isArray(response.items) || !["healthy", "unavailable"].includes(response.screening)) throw new Error("Invalid signal response");
          setData(response); setError("");
        })
        .catch((reason: unknown) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Could not load signals"); });
    };
    refresh();
    const timer = window.setInterval(refresh, 20000);
    return () => { controller.abort(); window.clearInterval(timer); };
  }, []);
  const now = Date.now();
  const current = data?.items.filter((item) => Date.parse(item.expires_at) > now) ?? [];
  const history = data?.items.filter((item) => Date.parse(item.expires_at) <= now) ?? [];
  return <>
    {error && <p role="alert" className="notice notice-error">Signals unavailable: {error}</p>}
    {data?.screening === "unavailable" && <p role="alert" className="notice notice-warning">Automatic screening or market collection is unavailable or has stopped updating. Existing signals may be out of date.</p>}
    {data && !data.configured && <p>Save portfolio settings to enable automatic screening.</p>}
    <section aria-labelledby="opportunities-title"><h2 id="opportunities-title">Current opportunities</h2>
      {data === null ? <p>{error ? "Signals could not be loaded." : "Loading signals…"}</p> : current.length ? current.map((item) => <Card key={item.signal_id} item={item} expired={false} />) : data.screening === "healthy" && data.configured && !error ? <p>No actionable opportunity currently meets the required criteria.</p> : <p>Current opportunities cannot be determined.</p>}
    </section>
    <section className="section-block" aria-labelledby="history-title"><h2 id="history-title">Signal history</h2>
      {data === null ? <p>{error ? "History could not be loaded." : "Loading history…"}</p> : history.length ? history.map((item) => <Card key={item.signal_id} item={item} expired />) : <p>No previous actionable signals.</p>}
    </section>
  </>;
}
