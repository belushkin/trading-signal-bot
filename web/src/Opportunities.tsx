import { useEffect, useState } from "react";
import { get } from "./api";

type Evaluation = {
  minutes: number;
  status: string;
  result: { entry_price_observed: boolean; target_price_observed_after_entry: boolean; price_change_percent: string | null; note: string } | null;
};
type Opportunity = {
  signal_id: string; pair: string; side: "BUY" | "SELL"; created_at: string; expires_at: string;
  parameters: {
    reason: string; entry_limit: string; target_exit: string; quantity: string;
    quote_amount: string; estimated_fees_quote: string; estimated_profit_quote: string;
    fee_xlm_per_transaction: string; estimated_net_percent: string;
    limitations: string[]; rule_version: string;
  };
  evaluations: Evaluation[];
};

function Card({ item }: { item: Opportunity }) {
  const [base, quote] = item.pair.split("/");
  const label = (asset: string) => asset === "native" ? "XLM" : asset.split(":")[0];
  return <article className="assistant-section panel">
    <h3>{item.side} {label(base)} / {label(quote)}</h3>
    <p>{item.parameters.reason}</p>
    <p>Quantity: {item.parameters.quantity} {label(base)} · Entry limit: {item.parameters.entry_limit} · Exit target: {item.parameters.target_exit} {label(quote)} per {label(base)}.</p>
    <p>Position: {item.parameters.quote_amount} {label(quote)} · Assumed entry fee: {item.parameters.fee_xlm_per_transaction} XLM · Assumed exit fee: {item.parameters.fee_xlm_per_transaction} XLM · Combined estimate: {item.parameters.estimated_fees_quote} {label(quote)}.</p>
    <p>Estimated target: {item.parameters.estimated_profit_quote} {label(quote)} ({Number(item.parameters.estimated_net_percent).toFixed(2)}%) before unknown costs.</p>
    <p><strong>Estimated only:</strong> slippage, actual fees and execution are unknown. This preliminary rule is not validated. No order is placed.</p>
    <p>Created {new Date(item.created_at).toLocaleString()} · Expires {new Date(item.expires_at).toLocaleString()}.</p>
    <ul>{item.evaluations.map((evaluation) => <li key={evaluation.minutes}>{evaluation.minutes < 60 ? `${evaluation.minutes}m` : `${evaluation.minutes / 60}h`}: {evaluation.status === "pending" ? "Waiting for observation" : !evaluation.result ? "Not enough data" : `${evaluation.result.entry_price_observed ? "Entry price observed" : "Entry price not observed"}; ${evaluation.result.target_price_observed_after_entry ? "target price observed afterwards" : "target not observed after entry"}. Price change: ${evaluation.result.price_change_percent === null ? "unknown" : `${Number(evaluation.result.price_change_percent).toFixed(2)}%`}. ${evaluation.status === "unknown" ? "Insufficient endpoint data. " : ""}Sample gaps may hide movement; execution unknown.`}</li>)}</ul>
    <details className="technical-details"><summary>Evidence and limitations</summary><p>Exact market: {item.pair}. Rule: {item.parameters.rule_version}.</p><ul>{item.parameters.limitations.map((line) => <li key={line}>{line}</li>)}</ul></details>
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
      {data === null ? <p>{error ? "Signals could not be loaded." : "Loading signals…"}</p> : current.length ? current.map((item) => <Card key={item.signal_id} item={item} />) : data.screening === "healthy" && data.configured && !error ? <p>No actionable opportunity currently meets the required criteria.</p> : <p>Current opportunities cannot be determined.</p>}
    </section>
    <section className="section-block" aria-labelledby="history-title"><h2 id="history-title">Signal history</h2>
      {data === null ? <p>{error ? "History could not be loaded." : "Loading history…"}</p> : history.length ? history.map((item) => <Card key={item.signal_id} item={item} />) : <p>No previous actionable signals.</p>}
    </section>
  </>;
}
