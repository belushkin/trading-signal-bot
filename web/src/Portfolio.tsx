import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { get, send } from "./api";

type Portfolio = { balances: Record<string, string>; reserve: string; allocation: string };
const emptyPortfolio: Portfolio = { balances: {}, reserve: "0", allocation: "10" };
const assetNames: Record<string, string> = {
  XLM: "Stellar Lumens", USDC: "USD Coin", SHX: "Stronghold Token",
  VELO: "Velo", AQUA: "Aquarius",
};

function symbol(asset: string): string {
  return asset === "native" ? "XLM" : asset.split(":")[0];
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
  const [loadError, setLoadError] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [valuation, setValuation] = useState<string | null>(null);
  const [valuationMessage, setValuationMessage] = useState("Enter your balances to see their value.");
  const [valuationRefresh, setValuationRefresh] = useState(0);
  const assets = [...new Set(["native", ...pairs.flatMap((pair) => pair.split("/")), ...Object.keys(draft.balances)])];

  useEffect(() => {
    const controller = new AbortController();
    get<{ settings: Portfolio | null }>("/api/assistant/settings", controller.signal)
      .then(({ settings }) => { if (settings) setDraft(settings); })
      .catch((reason: unknown) => { if (!controller.signal.aborted) setLoadError(reason instanceof Error ? reason.message : "Could not load settings"); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, []);
  useEffect(() => {
    const timer = window.setInterval(() => setValuationRefresh((value) => value + 1), 30000);
    return () => window.clearInterval(timer);
  }, []);

  const balances = Object.fromEntries(assets.map((asset) => [asset, draft.balances[asset] ?? "0"]));
  const payload: Portfolio = { balances, reserve: draft.reserve, allocation: draft.allocation };
  const valid = Object.values(balances).every(validAmount) && validAmount(draft.reserve)
    && units(draft.reserve) <= units(balances.native)
    && /^(?:\d{1,3})(?:\.\d{1,4})?$/.test(draft.allocation)
    && Number(draft.allocation) > 0 && Number(draft.allocation) <= 100;
  const payloadKey = JSON.stringify(payload);
  useEffect(() => {
    let active = true;
    setValuation(null);
    if (loading || loadError || !valid) {
      setValuationMessage("Enter valid balances and reserve to see your portfolio value.");
      return () => { active = false; };
    }
    setValuationMessage("Calculating portfolio value…");
    const timer = window.setTimeout(() => {
      send<{ value_xlm: string | null }>("/api/assistant/portfolio-value", "POST", JSON.parse(payloadKey))
        .then((result) => {
          if (!active) return;
          setValuation(result.value_xlm);
          setValuationMessage(result.value_xlm === null
            ? "Portfolio value is unavailable because current prices are missing for one or more assets."
            : "");
        })
        .catch(() => { if (active) setValuationMessage("Portfolio value is temporarily unavailable."); });
    }, 300);
    return () => { active = false; window.clearTimeout(timer); };
  }, [payloadKey, valid, loading, loadError, valuationRefresh]);

  const change = (next: Portfolio) => { setDraft(next); setMessage(""); setError(""); };
  const save = async (event: FormEvent) => {
    event.preventDefault();
    if (!Object.values(balances).every(validAmount) || !validAmount(draft.reserve)) {
      setError("Enter a non-negative amount with at most seven decimal places.");
      return;
    }
    if (units(draft.reserve) > units(balances.native)) {
      setError("XLM reserve cannot exceed your XLM balance.");
      return;
    }
    if (!valid) {
      setError("Maximum position size must be greater than 0 and no more than 100%.");
      return;
    }
    setBusy(true);
    try {
      await send("/api/assistant/settings", "PUT", payload);
      setMessage("Portfolio settings saved.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not save settings");
    } finally { setBusy(false); }
  };
  return <>
    <div className="page-heading"><div><h1>Portfolio settings</h1><p>Enter your balances manually. They are not retrieved from a wallet.</p></div></div>
    <section className="portfolio-panel panel">
      <form className="portfolio-form" onSubmit={save} noValidate>
        <h2>Your balances</h2>
        <p>Enter 0 for assets you do not hold.</p>
        <fieldset disabled={loading || busy || !!loadError}>
          <div className="portfolio-assets">
            {assets.map((asset) => {
              const code = symbol(asset);
              const issuer = asset.split(":")[1];
              const duplicate = assets.filter((other) => symbol(other) === code).length > 1;
              const shortIssuer = issuer ? `${issuer.slice(0, 5)}…${issuer.slice(-4)}` : "";
              const label = duplicate ? `${code} (${issuer})` : code;
              return <label className="portfolio-asset" key={asset}>
                <span><strong>{assetNames[code] ?? code}</strong><small>{code}{duplicate && ` · ${shortIssuer}`}</small></span>
                <input aria-label={label} type="text" inputMode="decimal" value={draft.balances[asset] ?? "0"} onChange={(event) => change({ ...draft, balances: { ...draft.balances, [asset]: event.target.value } })} />
              </label>;
            })}
          </div>
          <div className="portfolio-limits">
            <label>XLM reserve <input aria-label="XLM reserve" type="text" inputMode="decimal" value={draft.reserve} onChange={(event) => change({ ...draft, reserve: event.target.value })} /><small>Kept aside and excluded from available value.</small></label>
            <label>Maximum position size (%) <input aria-label="Maximum position size (%)" type="text" inputMode="decimal" value={draft.allocation} onChange={(event) => change({ ...draft, allocation: event.target.value })} /><small>Percentage of your total available portfolio value.</small></label>
          </div>
        </fieldset>
        <div className="portfolio-value" aria-live="polite">
          <span>Available portfolio value</span>
          {valuation !== null ? <strong>{Number(valuation).toLocaleString(undefined, { maximumFractionDigits: 7 })} XLM</strong> : <small>{valuationMessage}</small>}
          {valuation !== null && <small>Estimated using current market prices, after your XLM reserve.</small>}
        </div>
        <p className="portfolio-privacy">Your balances are saved on this server. Use the dashboard only on a trusted network.</p>
        <button className="button button-secondary" type="submit" disabled={loading || busy || !!loadError}>{busy ? "Saving…" : "Save settings"}</button>
        {(error || loadError) && <p className="notice notice-warning" role="alert">{error || `Could not load settings: ${loadError}`}</p>}
        {message && <p role="status">{message}</p>}
      </form>
    </section>
  </>;
}
