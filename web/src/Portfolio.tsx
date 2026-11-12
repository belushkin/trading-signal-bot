import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { get, send } from "./api";

export type Portfolio = {
  balances: Record<string, string>;
  reserve: string;
  fee: string;
  allocation: string;
  min_movement_percent: string;
  minimum_severity: "WATCH" | "HIGH";
};

export const emptyPortfolio: Portfolio = { balances: {}, reserve: "", fee: "", allocation: "", min_movement_percent: "1", minimum_severity: "WATCH" };

function assetLabel(asset: string): string {
  if (asset === "native") return "XLM";
  const [code, issuer] = asset.split(":");
  return issuer ? `${code} (${issuer.slice(0, 5)}…${issuer.slice(-4)})` : asset;
}

function validAmount(value: string): boolean {
  return /^(?:0|[1-9]\d{0,11})(?:\.\d{1,7})?$/.test(value)
    && units(value) <= 9223372036854775807n;
}

function units(value: string): bigint {
  const [whole, fraction = ""] = value.split(".");
  return BigInt(whole) * 10000000n + BigInt(fraction.padEnd(7, "0"));
}

export function PortfolioSettings({ pairs }: { pairs: string[] }) {
  const [draft, setDraft] = useState<Portfolio>(emptyPortfolio);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    get<{ settings: Portfolio | null }>("/api/assistant/settings", controller.signal)
      .then(({ settings }) => { if (settings) setDraft(settings); })
      .catch((reason: unknown) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Could not load settings"); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, []);
  const assets = [...new Set(["native", ...pairs.flatMap((pair) => pair.split("/"))])];
  const labelFor = (asset: string) => {
    const label = assetLabel(asset);
    return assets.some((other) => other !== asset && assetLabel(other) === label)
      ? asset : label;
  };
  const change = (next: Portfolio) => { setDraft(next); setMessage(""); setError(""); };
  const save = async (event: FormEvent) => {
    event.preventDefault();
    const values = [draft.reserve, draft.fee, ...assets.map((asset) => draft.balances[asset] ?? "")];
    if (values.some((value) => !validAmount(value))) {
      setError("Enter a non-negative Stellar amount with at most seven decimal places for each field.");
      return;
    }
    if (!/^(?:\d{1,3})(?:\.\d{1,4})?$/.test(draft.allocation) || Number(draft.allocation) <= 0 || Number(draft.allocation) > 100) {
      setError("Maximum allocation must be greater than 0 and no more than 100%.");
      return;
    }
    if (units(draft.reserve) + 2n * units(draft.fee) > units(draft.balances.native)) {
      setError("XLM must cover the reserve and two assumed transaction fees.");
      return;
    }
    if (!/^(?:\d{1,3})(?:\.\d{1,4})?$/.test(draft.min_movement_percent) || Number(draft.min_movement_percent) <= 0 || Number(draft.min_movement_percent) > 100) {
      setError("Minimum price movement must be greater than 0 and no more than 100%.");
      return;
    }
    setBusy(true);
    try {
      await send("/api/assistant/settings", "PUT", draft);
      setError("");
      setMessage("Settings saved. New signals will use these settings.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not save settings");
    } finally { setBusy(false); }
  };
  return (
    <>
      <div className="page-heading"><div><div className="eyebrow">SIGNAL SETTINGS</div><h1>Portfolio settings</h1><p>Enter balances for future signal sizing. No wallet access or private keys are required.</p></div></div>
      <section className="assistant-section panel">
        <p>Balances and assumptions are saved in the database. This dashboard has no authentication; use it only on a trusted network.</p>
        <p>New signals use a preliminary momentum rule, not a validated strategy. Fee assumptions may differ from actual costs.</p>
        <form className="portfolio-form" onSubmit={save} noValidate>
          <fieldset disabled={loading || busy}>
            {assets.map((asset) => <label key={asset}>Balance: {labelFor(asset)}
              <input aria-label={`Balance: ${labelFor(asset)}`} type="text" inputMode="decimal" value={draft.balances[asset] ?? ""} onChange={(event) => change({ ...draft, balances: { ...draft.balances, [asset]: event.target.value } })} />
            </label>)}
            <label>XLM reserve <input type="text" inputMode="decimal" value={draft.reserve} onChange={(event) => change({ ...draft, reserve: event.target.value })} /></label>
            <label>Assumed fee per transaction (XLM) <input type="text" inputMode="decimal" value={draft.fee} onChange={(event) => change({ ...draft, fee: event.target.value })} /></label>
            <label>Maximum allocation (%) <input type="text" inputMode="decimal" value={draft.allocation} onChange={(event) => change({ ...draft, allocation: event.target.value })} /></label>
            <label>Minimum observed price movement (%) <input type="text" inputMode="decimal" value={draft.min_movement_percent} onChange={(event) => change({ ...draft, min_movement_percent: event.target.value })} /></label>
            <label>Minimum anomaly severity <select value={draft.minimum_severity} onChange={(event) => change({ ...draft, minimum_severity: event.target.value === "WATCH" ? "WATCH" : "HIGH" })}><option value="HIGH">High</option><option value="WATCH">Watch</option></select></label>
          </fieldset>
          <p>Reserve and fees are assumptions, not an account check. Allocation limits the available asset, not the value of the whole portfolio.</p>
          <button className="button button-secondary" type="submit" disabled={loading || busy}>Save settings</button>
          {error && <p className="notice notice-warning" role="alert">{error}</p>}
          {message && <p role="status">{message}</p>}
        </form>
        <details className="technical-details"><summary>Exact asset identities</summary><ul>{assets.map((asset) => <li key={asset}><code>{asset}</code></li>)}</ul></details>
      </section>
    </>
  );
}
