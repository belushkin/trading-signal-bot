import { useCallback, useEffect, useMemo, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import {
  Activity as ActivityIcon,
  AlertTriangle,
  ArrowUpRight,
  BarChart3,
  BellRing,
  ChevronDown,
  Clock3,
  Database,
  ExternalLink,
  LayoutDashboard,
  LoaderCircle,
  Moon,
  RefreshCw,
  Search,
  Sun,
  Waves,
  X,
} from "lucide-react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  Activity,
  Analytics,
  get,
  Market,
  MarketDetail,
  Overview,
  PageResult,
  queryString,
  Signal,
  SignalDetail,
  VolumeEvidence,
} from "./api";

type View = "Overview" | "Markets" | "Signals" | "Activity" | "Analytics";
type Data = Overview | Market[] | PageResult<Signal> | PageResult<Activity> | Analytics;

const navigation: { name: View; icon: typeof LayoutDashboard }[] = [
  { name: "Overview", icon: LayoutDashboard },
  { name: "Markets", icon: Waves },
  { name: "Signals", icon: BellRing },
  { name: "Activity", icon: ActivityIcon },
  { name: "Analytics", icon: BarChart3 },
];

export default function App() {
  const [view, setView] = useState<View>("Overview");
  const [data, setData] = useState<Data | null>(null);
  const [loadedRoute, setLoadedRoute] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshSeconds, setRefreshSeconds] = useState(20);
  const [lastRefresh, setLastRefresh] = useState<string | null>(null);
  const [dark, setDark] = useState(true);
  const [days, setDays] = useState(30);
  const [searchText, setSearchText] = useState("");
  const [search, setSearch] = useState("");
  const [pairFilter, setPairFilter] = useState("");
  const [pairDraft, setPairDraft] = useState("");
  const [categoryFilter, setCategoryFilter] = useState("");
  const [severityFilter, setSeverityFilter] = useState("");
  const [eventTypeFilter, setEventTypeFilter] = useState("");
  const [signalOffset, setSignalOffset] = useState(0);
  const [activityOffset, setActivityOffset] = useState(0);
  const [selectedSignal, setSelectedSignal] = useState<string | null>(null);
  const [selectedPair, setSelectedPair] = useState<string | null>(null);

  const route = useMemo(() => {
    if (view === "Overview") return "/api/overview";
    if (view === "Markets") return "/api/markets";
    if (view === "Signals")
      return `/api/signals${queryString({
        days,
        pair: pairFilter,
        category: categoryFilter,
        severity: severityFilter,
        q: search,
        limit: 50,
        offset: signalOffset,
      })}`;
    if (view === "Activity")
      return `/api/activity${queryString({ days, event_type: eventTypeFilter, q: search, limit: 50, offset: activityOffset })}`;
    return `/api/analytics${queryString({ days })}`;
  }, [view, days, pairFilter, categoryFilter, severityFilter, eventTypeFilter, search, signalOffset, activityOffset]);
  const currentData = loadedRoute === route ? data : null;

  useEffect(() => setSignalOffset(0), [days, pairFilter, categoryFilter, severityFilter, search]);
  useEffect(() => setActivityOffset(0), [days, eventTypeFilter, search]);

  const refresh = useCallback(
    async (signal?: AbortSignal) => {
      try {
        const [result, health] = await Promise.all([
          route === "/api/markets"
            ? get<{ items: Market[] }>(route, signal).then((response) => response.items)
            : get<Data>(route, signal),
          get<{ refresh_seconds: number }>("/api/health", signal),
        ]);
        if (signal?.aborted) return;
        setData(result);
        setLoadedRoute(route);
        setRefreshSeconds(Math.min(300, Math.max(5, health.refresh_seconds)));
        setLastRefresh(new Date().toISOString());
        setError(null);
      } catch (reason) {
        if (signal?.aborted) return;
        setError(reason instanceof Error ? reason.message : "Could not load dashboard data");
      } finally {
        if (!signal?.aborted) setLoading(false);
      }
    },
    [route],
  );

  useEffect(() => {
    const controller = new AbortController();
    setError(null);
    setLoading(true);
    void refresh(controller.signal);
    const timer = window.setInterval(() => void refresh(controller.signal), refreshSeconds * 1000);
    return () => {
      controller.abort();
      window.clearInterval(timer);
    };
  }, [refresh, refreshSeconds]);

  const submitSearch = (event: FormEvent) => {
    event.preventDefault();
    setSearch(searchText.trim());
    if (view === "Signals") setPairFilter(pairDraft.trim());
  };

  const openSignal = (id: string) => setSelectedSignal(id);
  const openPair = (pair: string) => setSelectedPair(pair);

  return (
    <div className={`app-shell ${dark ? "theme-dark" : "theme-light"}`}>
      <aside className="sidebar">
        <a className="brand" href="#" onClick={() => setView("Overview")}>
          <span className="brand-mark"><Waves size={19} /></span>
          <span><strong>Stellar</strong><small>INTELLIGENCE</small></span>
        </a>
        <div className="nav-label">WORKSPACE</div>
        <nav aria-label="Main navigation">
          {navigation.map(({ name, icon: Icon }) => (
            <button
              key={name}
              className={`nav-item ${view === name ? "active" : ""}`}
              onClick={() => {
                setView(name);
                setSelectedPair(null);
              }}
              aria-current={view === name ? "page" : undefined}
            >
              <Icon size={17} strokeWidth={1.8} />
              <span>{name}</span>
            </button>
          ))}
        </nav>
        <div className="sidebar-foot">
          <span className="read-only-dot" />
          <span>Read-only intelligence</span>
        </div>
      </aside>

      <main className="main-area">
        <header className="topbar">
          <div className="breadcrumb"><span>Dashboard</span><span className="slash">/</span><strong>{view}</strong></div>
          <div className="top-actions">
            <span className="refresh-label">
              <RefreshCw size={13} />
              {lastRefresh ? `Updated ${relativeTime(lastRefresh)}` : "Waiting for data"}
            </span>
            <button className="icon-button" aria-label="Toggle color theme" onClick={() => setDark((value) => !value)}>
              {dark ? <Sun size={17} /> : <Moon size={17} />}
            </button>
          </div>
        </header>

        <div className="page-content">
          {error && (
            <div className="notice notice-error" role="alert">
              <AlertTriangle size={17} />
              <span><strong>Dashboard data unavailable</strong><small>{error}</small></span>
              <button className="button button-subtle" onClick={() => void refresh()}>Retry</button>
            </div>
          )}
          {(loading || loadedRoute !== route) && !currentData ? (
            <LoadingState />
          ) : !currentData ? (
            error ? null : <EmptyState title="No dashboard response" detail="The API has not returned data yet." />
          ) : (
            <>
              {view === "Overview" && <OverviewPage data={currentData as Overview} onSignal={openSignal} onPair={openPair} onSignals={() => setView("Signals")} />}
              {view === "Markets" && <MarketsPage data={currentData as Market[]} onPair={openPair} />}
              {view === "Signals" && (
                <SignalsPage
                  data={currentData as PageResult<Signal>}
                  days={days}
                  setDays={setDays}
                  searchText={searchText}
                  setSearchText={setSearchText}
                  onSearch={submitSearch}
                  pair={pairFilter}
                  pairDraft={pairDraft}
                  setPairDraft={setPairDraft}
                  category={categoryFilter}
                  setCategory={setCategoryFilter}
                  severity={severityFilter}
                  setSeverity={setSeverityFilter}
                  offset={signalOffset}
                  setOffset={setSignalOffset}
                  onSignal={openSignal}
                />
              )}
              {view === "Activity" && (
                <ActivityPage
                  data={currentData as PageResult<Activity>}
                  days={days}
                  setDays={setDays}
                  searchText={searchText}
                  setSearchText={setSearchText}
                  onSearch={submitSearch}
                  eventType={eventTypeFilter}
                  setEventType={setEventTypeFilter}
                  offset={activityOffset}
                  setOffset={setActivityOffset}
                />
              )}
              {view === "Analytics" && <AnalyticsPage data={currentData as Analytics} days={days} setDays={setDays} />}
            </>
          )}
        </div>
      </main>
      {selectedSignal && <SignalDialog id={selectedSignal} onClose={() => setSelectedSignal(null)} />}
      {selectedPair && (
        <MarketDialog
          pair={selectedPair}
          onClose={() => setSelectedPair(null)}
          onSignal={() => {
            setPairFilter(selectedPair);
            setPairDraft(selectedPair);
            setView("Signals");
            setSelectedPair(null);
          }}
        />
      )}
    </div>
  );
}

function PageHeading({ eyebrow, title, detail, action }: { eyebrow: string; title: string; detail: string; action?: ReactNode }) {
  return (
    <div className="page-heading">
      <div><div className="eyebrow">{eyebrow}</div><h1>{title}</h1><p>{detail}</p></div>
      {action}
    </div>
  );
}

function OverviewPage({ data, onSignal, onPair, onSignals }: { data: Overview; onSignal: (id: string) => void; onPair: (pair: string) => void; onSignals: () => void }) {
  return (
    <>
      <PageHeading eyebrow="SYSTEM MONITOR" title="Overview" detail="A clear view of collection health, monitored markets, and recent signals." />
      <div className="status-strip">
        <StatusItem label="Collector" status={data.collector.status} detail={data.collector.last_success_at ? `Last success ${formatDateTime(data.collector.last_success_at)}` : "No successful cycle recorded"} />
        <StatusItem label="Database" status={data.database.status} detail="PostgreSQL connection verified" />
        <StatusItem label="Data freshness" status={data.data_freshness_seconds === null ? "unknown" : data.data_freshness_seconds > 300 ? "stale" : "healthy"} detail={data.data_freshness_seconds === null ? "No event observations" : `${formatDuration(data.data_freshness_seconds)} since latest event`} />
      </div>
      {data.warnings.length > 0 && (
        <div className="warning-list">
          {data.warnings.map((warning) => <div className="notice notice-warning" key={warning.code}><AlertTriangle size={16} /><span>{warning.message}</span></div>)}
        </div>
      )}
      <div className="metric-grid">
        <MetricCard title="Collected events" value={formatInteger(data.counts.events)} detail="Persisted from Stellar Horizon" icon={<Database size={17} />} />
        <MetricCard title="Generated signals" value={formatInteger(data.counts.signals)} detail="Rule-based observations" icon={<BellRing size={17} />} />
        <MetricCard title="Monitored markets" value={formatInteger(data.monitored_pairs.length)} detail="From current configuration" icon={<Waves size={17} />} />
        <MetricCard title="Collector interruptions" value={formatInteger(data.collector.interruption_count ?? 0)} detail={`${formatInteger(data.collector.skipped_trade_count ?? 0)} skipped trades${data.collector.last_skipped_reason ? ` · last: ${data.collector.last_skipped_reason}` : ""}`} icon={<AlertTriangle size={17} />} />
        <MetricCard title="Last collection" value={data.collector.last_success_at ? relativeTime(data.collector.last_success_at) : "N/A"} detail={data.collector.last_error ? "Latest cycle reported an error" : "Latest successful collector cycle"} icon={<Clock3 size={17} />} />
      </div>
      <section className="section-block">
        <SectionHeading title="Monitored markets" detail="Latest persisted order-book observations" />
        {data.markets.length ? <MarketTable markets={data.markets} onPair={onPair} /> : <EmptyState title="No markets configured" detail="Set BOT_WATCHLIST_ENABLED or BOT_MONITORED_PAIRS to begin collecting market observations." compact />}
      </section>
      <section className="section-block">
        <SectionHeading title="Recent signal activity" detail="Latest persisted rule matches" linkText="All signals" onLink={onSignals} />
        {data.recent_signals.length ? <SignalTable items={data.recent_signals} onSignal={onSignal} /> : <EmptyState title="No signals yet" detail="Signals will appear here when persisted observations meet configured rules." compact />}
      </section>
    </>
  );
}

function MarketsPage({ data, onPair }: { data: Market[]; onPair: (pair: string) => void }) {
  return (
    <>
      <PageHeading eyebrow="MARKET MONITOR" title="Markets" detail="Observed prices, liquidity, and trade volume from configured Stellar pairs." />
      {data.length ? (
        <div className="market-grid">
          {data.map((market) => (
            <button className="market-card" key={market.pair} onClick={() => onPair(market.pair)}>
              <div className="market-card-head"><span className="pair-name">{shortPair(market.pair)} {market.tier ? `· Tier ${market.tier}` : ""}</span>{market.enabled === false ? <span>Disabled</span> : <Freshness status={market.status} timestamp={market.last_observed_at} />}</div>
              <div className="market-price">{formatNumber(market.last_trade_price)} <small>last trade · quote / base</small></div>
              <div className={`market-change ${changeTone(market.price_change_24h_percent)}`}>
                {market.price_change_24h_percent === null ? "N/A" : `${Number(market.price_change_24h_percent) > 0 ? "+" : ""}${formatNumber(market.price_change_24h_percent)}%`}
                <small>24h observed change</small>
              </div>
              <div className="market-card-foot"><span>Observed 24h volume <strong>{formatNumber(market.volume_24h_base)}</strong> · {market.trade_count_24h ?? "N/A"} trades</span><ChevronDown size={16} /></div>
            </button>
          ))}
        </div>
      ) : <EmptyState title="No markets are being monitored" detail="Enable watchlist entries or set BOT_MONITORED_PAIRS and restart the app." />}
      <section className="section-block">
        <SectionHeading title="Market observations" detail="No interpolation; metrics reflect actual persisted snapshots." />
        {data.length ? <MarketTable markets={data} onPair={onPair} /> : null}
      </section>
    </>
  );
}

function SignalsPage(props: {
  data: PageResult<Signal>;
  days: number;
  setDays: (value: number) => void;
  searchText: string;
  setSearchText: (value: string) => void;
  onSearch: (event: FormEvent) => void;
  pair: string;
  pairDraft: string;
  setPairDraft: (value: string) => void;
  category: string;
  setCategory: (value: string) => void;
  severity: string;
  setSeverity: (value: string) => void;
  offset: number;
  setOffset: (value: number) => void;
  onSignal: (id: string) => void;
}) {
  return (
    <>
      <PageHeading eyebrow="RULE-BASED OBSERVATIONS" title="Signals" detail="Search generated signals and inspect their evidence, interpretation, and subsequent observations." />
      <form className="filter-bar" onSubmit={props.onSearch}>
        <label className="search-field"><Search size={16} /><input placeholder="Search category, interpretation, transaction, or ID" value={props.searchText} onChange={(event) => props.setSearchText(event.target.value)} /><button type="submit" className="sr-only">Search</button></label>
        <input aria-label="Asset or trading pair" className="filter-input" placeholder="Asset / pair" value={props.pairDraft} onChange={(event) => props.setPairDraft(event.target.value)} />
        <select aria-label="Signal category" value={props.category} onChange={(event) => props.setCategory(event.target.value)}>
          <option value="">All categories</option><option value="price_movement">Price movement</option><option value="volume_anomaly">Volume anomaly</option><option value="spread_anomaly">Spread anomaly</option><option value="large_trade">Large trade</option><option value="whale_transfer">Large transfer</option><option value="unusual_volume">Legacy unusual volume</option><option value="visible_liquidity">Legacy visible liquidity</option><option value="wide_spread">Legacy wide spread</option>
        </select>
        <select aria-label="Severity" value={props.severity} onChange={(event) => props.setSeverity(event.target.value)}>
          <option value="">All severities</option><option>INFO</option><option>WATCH</option><option>HIGH</option><option>CRITICAL</option>
        </select>
        <PeriodSelect days={props.days} setDays={props.setDays} />
      </form>
      <Pagination label="signals" data={props.data} offset={props.offset} setOffset={props.setOffset} />
      {props.data.items.length ? <SignalTable items={props.data.items} onSignal={props.onSignal} /> : <EmptyState title="No signals match these filters" detail="Try a wider time period or clear one or more filters." />}
    </>
  );
}

function ActivityPage(props: {
  data: PageResult<Activity>;
  days: number;
  setDays: (value: number) => void;
  searchText: string;
  setSearchText: (value: string) => void;
  onSearch: (event: FormEvent) => void;
  eventType: string;
  setEventType: (value: string) => void;
  offset: number;
  setOffset: (value: number) => void;
}) {
  return (
    <>
      <PageHeading eyebrow="STELLAR NETWORK" title="Activity" detail="Recent persisted payments, trades, and ledger observations. Account ownership and intent are not inferred." />
      <form className="filter-bar" onSubmit={props.onSearch}>
        <label className="search-field"><Search size={16} /><input placeholder="Search event, type, or transaction" value={props.searchText} onChange={(event) => props.setSearchText(event.target.value)} /><button type="submit" className="sr-only">Search</button></label>
        <select aria-label="Activity type" value={props.eventType} onChange={(event) => props.setEventType(event.target.value)}>
          <option value="">All activity</option><option value="payment">Payments</option><option value="trade">Trades</option><option value="ledger">Ledgers</option>
        </select>
        <PeriodSelect days={props.days} setDays={props.setDays} />
      </form>
      {props.data.items.length ? (
        <div className="panel activity-list">
          {props.data.items.map((item) => <ActivityRow key={item.event_id} item={item} />)}
        </div>
      ) : <EmptyState title="No recent activity" detail="No matching persisted payment, trade, or ledger events were found for this time period." />}
      <Pagination label="activity events" data={props.data} offset={props.offset} setOffset={props.setOffset} />
    </>
  );
}

function AnalyticsPage({ data, days, setDays }: { data: Analytics; days: number; setDays: (value: number) => void }) {
  return (
    <>
      <PageHeading eyebrow="OBSERVATION QUALITY" title="Analytics" detail="Current engine signals only. Historical signals are excluded; observed changes are not profitability or directional accuracy." action={<PeriodSelect days={days} setDays={setDays} />} />
      <div className="metric-grid analytics-metrics">
        <MetricCard title="Events in period" value={formatInteger(data.data_coverage.events)} detail="Persisted chain events" icon={<Database size={17} />} />
        <MetricCard title="Order-book snapshots" value={formatInteger(data.data_coverage.market_snapshots)} detail="Actual recorded observations" icon={<Waves size={17} />} />
        <MetricCard title="Measured evaluations" value={formatInteger(data.data_coverage.measured_evaluations)} detail={`${formatInteger(data.data_coverage.missing_evaluations)} missing · ${formatInteger(data.data_coverage.pending_evaluations)} pending`} icon={<ActivityIcon size={17} />} />
        <MetricCard title="Trade provenance" value={`${formatInteger(data.data_coverage.trades_with_provenance ?? 0)} / ${formatInteger(data.data_coverage.trades)}`} detail={`Measured: ${Object.entries(data.data_coverage.measured_by_price_basis ?? {}).map(([k, v]) => `${priceBasisLabel(k)} ${v}`).join(", ") || "none"}`} icon={<ActivityIcon size={17} />} />
        <MetricCard title="Missing rate" value={data.data_coverage.missing_observation_rate_percent === null ? "N/A" : `${formatNumber(data.data_coverage.missing_observation_rate_percent)}%`} detail="Of persisted signal evaluation horizons" icon={<AlertTriangle size={17} />} />
      </div>
      <div className="chart-grid">
        <ChartCard title="Signals over time" detail={`Daily count · last ${days} days`}>
          {data.signal_frequency.length ? <ResponsiveContainer width="100%" height={250}><AreaChart data={data.signal_frequency}><CartesianGrid strokeDasharray="3 3" vertical={false} /><XAxis dataKey="date" tickFormatter={shortDate} /><YAxis allowDecimals={false} width={34} /><Tooltip /><Area type="linear" dataKey="count" stroke="var(--accent)" fill="var(--accent-soft)" strokeWidth={2} /></AreaChart></ResponsiveContainer> : <ChartEmpty />}
        </ChartCard>
        <ChartCard title="Signals by category" detail="Persisted signal categories">
          {data.signals_by_category.length ? <ResponsiveContainer width="100%" height={250}><BarChart data={data.signals_by_category}><CartesianGrid strokeDasharray="3 3" vertical={false} /><XAxis dataKey="category" tickFormatter={categoryLabel} /><YAxis allowDecimals={false} width={34} /><Tooltip /><Bar dataKey="count" fill="var(--accent)" radius={[4, 4, 0, 0]} /></BarChart></ResponsiveContainer> : <ChartEmpty />}
        </ChartCard>
      </div>
      <section className="section-block">
        <SectionHeading title="Observed price changes after signals" detail="Descriptive change by horizon; sampled observations only." />
        <div className="panel evaluation-summary">
          {data.price_evaluations.length ? <div className="horizon-stats">{data.price_evaluations.map((evaluation) => <div key={`${evaluation.horizon_minutes}-${evaluation.price_basis ?? ""}`}><small>{formatHorizon(evaluation.horizon_minutes)} · {priceBasisLabel(evaluation.price_basis)}</small><strong>{evaluation.mean_observed_change_percent === null ? "Insufficient sample" : `${formatNumber(evaluation.mean_observed_change_percent)}%`}</strong><span>{evaluation.sample_count} / {evaluation.minimum_sample_count} observations</span></div>)}</div> : <EmptyState title="No completed price evaluations" detail="Subsequent observations will appear here when signal evaluation horizons are reached." compact />}
          <p>Observed changes are not profitability or directional accuracy. A mean is shown only with at least 10 measured observations for that horizon.</p>
        </div>
      </section>
      <section className="section-block">
        <SectionHeading title="Market data quality" detail="Trade rules need continuous watermarked scans; older observations do not establish ingestion coverage." />
        {data.market_quality.length ? <div className="panel table-wrap"><table><thead><tr><th>Market</th><th>Snapshot freshness</th><th>Last observed</th><th>Trade scan watermark</th><th>Baseline readiness</th></tr></thead><tbody>
          {data.market_quality.map((market) => <tr key={market.pair}><td>{shortPair(market.pair)}</td><td><Freshness status={market.status} timestamp={market.last_observed_at} /></td><td>{market.last_observed_at ? relativeTime(market.last_observed_at) : "N/A"}</td><td>{market.trade_ingestion_status ?? "unverified"}{market.last_trade_scan_through ? ` · ${relativeTime(market.last_trade_scan_through)}` : ""}</td><td>{Object.entries(market.readiness ?? {}).filter(([name]) => name === "spread_anomaly" || name === "unusual_volume" || name === "large_trade" || name.startsWith("price_")).map(([name, value]) => `${name.replaceAll("_", " ")}: ${value.ready ? "ready" : "insufficient"}`).join(" · ") || "N/A"}</td></tr>)}
        </tbody></table></div> : <EmptyState title="No monitored markets" detail="Enable a configured watchlist market to inspect data quality." compact />}
      </section>
      <section className="section-block">
        <SectionHeading title="Outcomes by market and signal" detail={`${data.excluded_legacy_signals} legacy signals excluded, including signals generated before depth-unit corrections. Statistics require 10 measured observations.`} />
        {data.outcomes_by_pair.length ? <div className="panel table-wrap"><table><thead><tr><th>Market</th><th>Signal</th><th>Horizon</th><th>Price basis</th><th>Signals</th><th>Measured / missing / pending</th><th>Mean / median change</th><th>Positive / negative</th></tr></thead><tbody>
          {data.outcomes_by_pair.map((item) => <tr key={`${item.pair}-${item.category}-${item.severity}-${item.horizon_minutes}-${item.price_basis ?? "none"}`}><td>{shortPair(item.pair)}</td><td>{categoryLabel(item.category)} · {item.severity}</td><td>{formatHorizon(item.horizon_minutes)}</td><td>{priceBasisLabel(item.price_basis)}</td><td>{item.signals}</td><td>{item.measured} / {item.missing} / {item.pending}</td><td>{item.mean_observed_change_percent === null ? "Insufficient sample" : `${formatNumber(item.mean_observed_change_percent)}% / ${formatNumber(item.median_observed_change_percent)}%`}</td><td>{item.positive_percent === null ? "N/A" : `${formatNumber(item.positive_percent)}% / ${formatNumber(item.negative_percent)}%`}</td></tr>)}
        </tbody></table></div> : <EmptyState title="No current-engine signal outcomes" detail="Legacy signals are retained but are not compared with the current engine." compact />}
      </section>
      <section className="section-block">
        <SectionHeading title="Signals by severity" detail="No sample-size adjustment" />
        <div className="severity-grid">{data.signals_by_severity.length ? data.signals_by_severity.map(({ severity, count }) => <div className="panel severity-count" key={severity}><SeverityBadge value={severity} /><strong>{count}</strong></div>) : <EmptyState title="No signal counts in this period" detail="Signals appear after persisted observations meet configured rules." compact />}</div>
      </section>
    </>
  );
}

function MarketTable({ markets, onPair }: { markets: Market[]; onPair: (pair: string) => void }) {
  return (
    <div className="panel table-wrap"><table><thead><tr><th>Market</th><th>Tier</th><th>Status</th><th>Last trade price</th><th>24h change</th><th>Observed 24h volume</th><th>Trades</th><th>Best bid / ask</th><th>Spread</th><th>Bid / ask depth</th><th>Last observation</th></tr></thead>
    <tbody>{markets.map((item) => <tr key={item.pair} className="clickable-row" onClick={() => onPair(item.pair)}><td className="strong-cell">{shortPair(item.pair)}</td><td>{item.tier ?? "N/A"}</td><td>{item.enabled === false ? "Disabled" : <Freshness status={item.status} timestamp={item.last_observed_at} />}</td><td>{formatNumber(item.last_trade_price)}</td><td className={changeTone(item.price_change_24h_percent)}>{item.price_change_24h_percent === null ? "N/A" : `${formatNumber(item.price_change_24h_percent)}%`}</td><td>{formatNumber(item.volume_24h_base)}</td><td>{item.trade_count_24h ?? "N/A"}</td><td>{formatNumber(item.best_bid)} <span className="muted">/</span> {formatNumber(item.best_ask)}</td><td>{item.spread_percent === null ? "N/A" : `${formatNumber(item.spread_percent)}%`}</td><td>{formatNumber(item.bid_depth)} / {formatNumber(item.ask_depth)}</td><td>{item.last_observed_at ? relativeTime(item.last_observed_at) : "N/A"}</td></tr>)}</tbody>
    </table></div>
  );
}

function SignalTable({ items, onSignal }: { items: Signal[]; onSignal: (id: string) => void }) {
  return (
    <div className="panel table-wrap"><table><thead><tr><th>Time</th><th>Category</th><th>Market / asset</th><th>Severity</th><th>Confidence</th><th>Interpretation</th><th /></tr></thead>
      <tbody>{items.map((item) => <tr key={item.signal_id} className="clickable-row" onClick={() => onSignal(item.signal_id)}><td className="nowrap">{relativeTime(item.occurred_at)}</td><td>{categoryLabel(item.category)}</td><td>{shortPair(item.pair ?? item.asset ?? "N/A")}</td><td><SeverityBadge value={item.severity} /></td><td>{item.confidence}</td><td className="interpretation-cell">{item.interpretation}</td><td><ExternalLink size={14} className="muted" /></td></tr>)}</tbody>
    </table></div>
  );
}

function ActivityRow({ item }: { item: Activity }) {
  const attrs = item.attributes;
  const summary = item.event_type === "payment"
    ? `${formatNumber(valueString(attrs.amount))} ${valueString(attrs.asset) ?? ""}`.trim()
    : item.event_type === "trade"
      ? `${formatNumber(valueString(attrs.base_amount))} ${shortPair(valueString(attrs.pair) ?? "")}`
      : `Ledger ${valueString(attrs.sequence) ?? ""}`;
  return (
    <div className="activity-row">
      <span className={`activity-icon ${item.event_type}`}><ActivityIcon size={16} /></span>
      <div className="activity-body"><div className="activity-title"><strong>{eventLabel(item.event_type)}</strong><span>{summary || "Observed event"}</span></div><div className="activity-meta"><time>{formatDateTime(item.occurred_at)}</time><code>{item.event_id}</code>{item.signal_categories.map((category) => <span className="mini-tag" key={category}>{categoryLabel(category)}</span>)}</div></div>
      {item.event_type === "payment" && Boolean(attrs.source_account || attrs.destination_account) && <span className="activity-accounts">Accounts observed: {shortAccount(valueString(attrs.source_account))} → {shortAccount(valueString(attrs.destination_account))}</span>}
      {item.explorer_url && <a className="explorer-link" href={item.explorer_url} target="_blank" rel="noreferrer" onClick={(event) => event.stopPropagation()}>StellarExpert <ExternalLink size={13} /></a>}
    </div>
  );
}

function SignalDialog({ id, onClose }: { id: string; onClose: () => void }) {
  const [detail, setDetail] = useState<SignalDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    const controller = new AbortController();
    get<SignalDetail>(`/api/signals/${encodeURIComponent(id)}`, controller.signal)
      .then(setDetail)
      .catch((reason: unknown) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Could not load signal"); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [id]);
  return (
    <Dialog title="Signal detail" onClose={onClose}>
      {loading ? <LoadingState compact /> : error ? <ErrorState message={error} /> : detail ? (
        <>
          <div className="detail-title"><div><div className="eyebrow">{categoryLabel(detail.category)}</div><h2>{shortPair(detail.pair ?? detail.asset ?? "Signal")}</h2></div><SeverityBadge value={detail.severity} /></div>
          <div className="detail-facts"><DetailField label="Timestamp" value={formatDateTime(detail.occurred_at)} /><DetailField label="Confidence" value={detail.confidence} /><DetailField label="Expires" value={formatDateTime(detail.expires_at)} /><DetailField label="Source event" value={detail.source?.event_id ?? "N/A"} /><DetailField label="Deduplication key" value={detail.deduplication_key} /></div>
          <div className="fact-block"><div className="fact-heading"><span className="fact-dot" />Observed facts <small>Persisted measurements</small></div><pre>{JSON.stringify(detail.observed_facts, null, 2)}</pre></div>
          <div className="fact-block interpretation-block"><div className="fact-heading"><span className="interpret-dot" />Interpretation <small>Rule-generated explanation</small></div><p>{detail.interpretation}</p></div>
          {detail.source?.explorer_url ? <a className="button button-secondary explorer-button" href={detail.source.explorer_url} target="_blank" rel="noreferrer">View Stellar transaction <ExternalLink size={14} /></a> : detail.source?.source_ref && <a className="button button-secondary explorer-button" href={detail.source.source_ref} target="_blank" rel="noreferrer">View source event <ExternalLink size={14} /></a>}
          {detail.volume_evidence && <VolumeEvidencePanel evidence={detail.volume_evidence} />}
          <div className="section-block"><SectionHeading title="Subsequent price evaluation" detail="Observed price changes are not trading performance." />
            {detail.evaluations.length ? <div className="evaluation-list">{detail.evaluations.map((evaluation) => <div className="evaluation-row" key={evaluation.horizon_minutes}><strong>{formatHorizon(evaluation.horizon_minutes)}</strong><span>{evaluation.status === "measured" ? `${formatNumber(evaluation.observed_change_percent)}%` : evaluation.status.replaceAll("_", " ")}</span><small>{evaluation.measured_at ? formatDateTime(evaluation.measured_at) : `Expected ${formatDateTime(evaluation.target_at)}`}</small><small>{evaluation.data_quality} · {evaluation.observation_count} observations</small><small>Price basis: {evaluation.status === "measured" ? priceBasisLabel(evaluation.price_basis) : "none"}{evaluation.observation_ref ? ` · ${evaluation.observation_ref}` : ""}</small>{evaluation.missing_reason && <small>Missing: {evaluation.missing_reason.replaceAll("_", " ")}</small>}</div>)}</div> : <EmptyState title="No evaluation horizons" detail="This signal has no associated price-evaluation record." compact />}
          </div>
        </>
      ) : null}
    </Dialog>
  );
}

function MarketDialog({ pair, onClose, onSignal }: { pair: string; onClose: () => void; onSignal: () => void }) {
  const [detail, setDetail] = useState<MarketDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [hours, setHours] = useState(24);
  useEffect(() => {
    const controller = new AbortController();
    get<MarketDetail>(`/api/markets/${encodeURIComponent(pair)}?history_hours=${hours}`, controller.signal)
      .then(setDetail)
      .catch((reason: unknown) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Could not load market"); });
    return () => controller.abort();
  }, [pair, hours]);
  return (
    <Dialog title="Market detail" onClose={onClose} wide>
      <div className="detail-title"><div><div className="eyebrow">MONITORED MARKET</div><h2>{shortPair(pair)}</h2></div><button className="button button-secondary" onClick={onSignal}>View signals</button></div>
      {error ? <ErrorState message={error} /> : !detail ? <LoadingState compact /> : <>
        <p>{detail.data_source}. Trade volume is observed only and may include order-book and AMM trades; it may be incomplete.</p>
        <p><code>{detail.pair}</code> · {detail.summary.enabled === false ? "Disabled" : `Tier ${detail.summary.tier ?? "N/A"}`} · {detail.summary.snapshot_count ?? 0} persisted snapshots</p>
        <div className="market-detail-stats">
          <DetailField label="Last trade price" value={formatNumber(detail.summary.last_trade_price)} />
          <DetailField label="Last trade time" value={detail.summary.last_trade_at ? formatDateTime(detail.summary.last_trade_at) : "N/A"} />
          <DetailField label="24h change" value={detail.summary.price_change_24h_percent === null ? "N/A" : `${formatNumber(detail.summary.price_change_24h_percent)}%`} />
          <DetailField label="24h base volume" value={formatNumber(detail.summary.volume_24h_base)} />
          <DetailField label="Best bid / ask" value={`${formatNumber(detail.summary.best_bid)} / ${formatNumber(detail.summary.best_ask)}`} />
          <DetailField label="Spread" value={detail.summary.spread_percent === null ? "N/A" : `${formatNumber(detail.summary.spread_percent)}%`} />
          <DetailField label="Bid / ask depth" value={`${formatNumber(detail.summary.bid_depth)} / ${formatNumber(detail.summary.ask_depth)}`} />
        </div>
        <div className="chart-toolbar"><div><h3>Observed price</h3><span>{detail.history.length} actual snapshots{detail.truncated ? " · results capped" : ""}</span></div><select aria-label="Chart period" value={hours} onChange={(event) => setHours(Number(event.target.value))}><option value={24}>24 hours</option><option value={168}>7 days</option><option value={720}>30 days</option></select></div>
        {detail.history.some((item) => item.price !== null) ? <div className="market-chart"><ResponsiveContainer width="100%" height={270}><ScatterChart><CartesianGrid strokeDasharray="3 3" vertical={false} /><XAxis dataKey="observed_at" type="category" tickFormatter={formatAxisTime} minTickGap={45} /><YAxis dataKey="price" type="number" domain={["auto", "auto"]} width={62} /><Tooltip labelFormatter={(value) => formatDateTime(String(value))} /><Scatter name="Observed midpoint" data={detail.history.filter((item) => item.price !== null).map((item) => ({ ...item, price: Number(item.price) }))} dataKey="price" fill="var(--accent)" shape="circle" /></ScatterChart></ResponsiveContainer></div> : <ChartEmpty message="No valid two-sided order-book observations in this period." />}
        <div className="chart-toolbar"><div><h3>Observed base volume</h3><span>15-minute buckets from persisted trades only; empty intervals are omitted, not filled with zero.</span></div></div>
        {detail.volume_history.length ? <ResponsiveContainer width="100%" height={210}><BarChart data={detail.volume_history.map((item) => ({ ...item, base_volume: Number(item.base_volume) }))}><CartesianGrid strokeDasharray="3 3" vertical={false} /><XAxis dataKey="observed_at" tickFormatter={formatAxisTime} minTickGap={45} /><YAxis width={62} /><Tooltip labelFormatter={(value) => formatDateTime(String(value))} /><Bar dataKey="base_volume" fill="var(--blue)" /></BarChart></ResponsiveContainer> : <ChartEmpty message="No trade-volume observations in this period." />}
        <div className="section-block"><SectionHeading title="Signal readiness" detail="Data sufficiency is not signal quality or profitability." />
          {Object.entries(detail.summary.readiness ?? {}).map(([name, value]) => <p key={name}><strong>{name.replaceAll("_", " ")}: {detail.summary.enabled === false ? "disabled" : value.ready ? "data available" : "not ready"}</strong> — {value.requirement}</p>)}
        </div>
        <div className="section-block"><SectionHeading title="Recent trades" detail="Persisted Horizon trades; source type may be order book or AMM." />
          {detail.recent_trades?.length ? detail.recent_trades.map((trade, index) => <p key={`${trade.occurred_at}-${index}`}>{formatDateTime(trade.occurred_at)} · {formatNumber(trade.base_amount)} base · {formatNumber(trade.price)} quote/base · {trade.trade_type ?? "type unknown"}</p>) : <p>No persisted trades.</p>}
        </div>
        <div className="section-block"><SectionHeading title="Recent order-book snapshots" detail="Observed best prices, spread and depth; no reconstruction." />
          {detail.history.length ? detail.history.slice(-10).reverse().map((item, index) => <p key={`${item.observed_at}-${index}`}>{formatDateTime(item.observed_at)} · bid {formatNumber(item.best_bid)} · ask {formatNumber(item.best_ask)} · spread {formatNumber(item.spread_percent)}% · depth {formatNumber(item.bid_depth)} / {formatNumber(item.ask_depth)}</p>) : <p>No persisted snapshots.</p>}
        </div>
      </>}
    </Dialog>
  );
}

function Dialog({ title, onClose, wide, children }: { title: string; onClose: () => void; wide?: boolean; children: ReactNode }) {
  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [onClose]);
  return <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}><section className={`dialog ${wide ? "dialog-wide" : ""}`} role="dialog" aria-modal="true" aria-label={title}><div className="dialog-header"><span>{title}</span><button className="icon-button" aria-label="Close dialog" onClick={onClose}><X size={18} /></button></div><div className="dialog-content">{children}</div></section></div>;
}

function StatusItem({ label, status, detail }: { label: string; status: string; detail: string }) {
  const good = status === "healthy";
  const warning = status === "stale" || status === "starting";
  return <div className="status-item"><span className={`status-dot ${good ? "good" : warning ? "warn" : "bad"}`} /><div><div className="status-name">{label}<span className={`status-text ${good ? "positive" : warning ? "warning-text" : "negative"}`}>{status.replaceAll("_", " ")}</span></div><small>{detail}</small></div></div>;
}

function MetricCard({ title, value, detail, icon }: { title: string; value: string; detail: string; icon: ReactNode }) {
  return <div className="panel metric-card"><div className="metric-top"><span>{title}</span><span className="metric-icon">{icon}</span></div><strong>{value}</strong><small>{detail}</small></div>;
}

function Freshness({ status, timestamp }: { status: string; timestamp: string | null }) {
  const label = status === "fresh" ? "Fresh" : status === "stale" ? "Stale" : "No data";
  return <span className={`freshness ${status}`} title={timestamp ? formatDateTime(timestamp) : "No observation recorded"}><span />{label}</span>;
}

function SeverityBadge({ value }: { value: string }) {
  return <span className={`severity-badge ${value.toLowerCase()}`}>{value}</span>;
}

function DetailField({ label, value }: { label: string; value: string }) {
  return <div className="detail-field"><small>{label}</small><strong>{value}</strong></div>;
}

function SectionHeading({ title, detail, linkText, onLink }: { title: string; detail: string; linkText?: string; onLink?: () => void }) {
  return <div className="section-heading"><div><h2>{title}</h2><p>{detail}</p></div>{linkText && <button className="text-button" onClick={onLink}>{linkText} <ArrowUpRight size={14} /></button>}</div>;
}

function ChartCard({ title, detail, children }: { title: string; detail: string; children: ReactNode }) {
  return <div className="panel chart-card"><div className="chart-card-heading"><div><h3>{title}</h3><p>{detail}</p></div></div>{children}</div>;
}

function PeriodSelect({ days, setDays }: { days: number; setDays: (value: number) => void }) {
  return <select aria-label="Time period" value={days} onChange={(event) => setDays(Number(event.target.value))}><option value={1}>24 hours</option><option value={7}>7 days</option><option value={30}>30 days</option><option value={90}>90 days</option><option value={365}>1 year</option></select>;
}

function Pagination<T>({
  label,
  data,
  offset,
  setOffset,
}: {
  label: string;
  data: PageResult<T>;
  offset: number;
  setOffset: (value: number) => void;
}) {
  const first = data.total === 0 ? 0 : offset + 1;
  const last = Math.min(offset + data.items.length, data.total);
  return (
    <div className="pagination">
      <span>{first}-{last} of {data.total.toLocaleString()} {label}</span>
      <div>
        <button className="button button-secondary" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - data.limit))}>Previous</button>
        <button className="button button-secondary" disabled={offset + data.items.length >= data.total} onClick={() => setOffset(offset + data.limit)}>Next</button>
      </div>
    </div>
  );
}

function LoadingState({ compact = false }: { compact?: boolean }) {
  return <div className={`state-card ${compact ? "compact" : ""}`} role="status"><LoaderCircle className="spin" size={20} /><strong>Loading persisted dashboard data</strong><span>Reading observations from the configured database…</span></div>;
}

function ErrorState({ message }: { message: string }) {
  return <div className="state-card error-state" role="alert"><AlertTriangle size={20} /><strong>Could not load this detail</strong><span>{message}</span></div>;
}

function EmptyState({ title, detail, compact = false }: { title: string; detail: string; compact?: boolean }) {
  return <div className={`state-card empty-state ${compact ? "compact" : ""}`}><div className="empty-icon"><Database size={18} /></div><strong>{title}</strong><span>{detail}</span></div>;
}

function ChartEmpty({ message = "No persisted observations are available for this period." }: { message?: string }) {
  return <div className="chart-empty">{message}</div>;
}

function formatNumber(value: string | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return "N/A";
  return new Intl.NumberFormat(undefined, { maximumSignificantDigits: 8 }).format(Number(value));
}

function formatInteger(value: number): string {
  return new Intl.NumberFormat().format(value);
}

function formatDateTime(value: string): string {
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "medium", timeZone: "UTC" }).format(new Date(value)) + " UTC";
}

function relativeTime(value: string): string {
  const seconds = Math.max(0, Math.floor((Date.now() - new Date(value).getTime()) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  return `${Math.floor(seconds / 3600)}h ${Math.floor(seconds % 3600 / 60)}m`;
}

function shortPair(value: string): string {
  return value.replace(/:G[A-Z2-7]{20,}/g, (issuer) => `:${issuer.slice(1, 5)}…${issuer.slice(-4)}`);
}

function categoryLabel(value: string): string {
  return value.split("_").map((part) => part.charAt(0).toUpperCase() + part.slice(1)).join(" ");
}

function eventLabel(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1);
}

function formatHorizon(minutes: number): string {
  return minutes < 60 ? `${minutes}m` : minutes < 1440 ? `${minutes / 60}h` : `${minutes / 1440}d`;
}

function formatAxisTime(value: string): string {
  return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "UTC" }).format(new Date(value));
}

function shortDate(value: string): string {
  return value.slice(5);
}

function changeTone(value: string | null): string {
  if (value === null) return "muted";
  return Number(value) > 0 ? "positive" : Number(value) < 0 ? "negative" : "muted";
}

function valueString(value: unknown): string | null {
  return typeof value === "string" || typeof value === "number" ? String(value) : null;
}

function shortAccount(value: string | null): string {
  if (!value) return "N/A";
  return value.length > 18 ? `${value.slice(0, 7)}…${value.slice(-5)}` : value;
}

function priceBasisLabel(basis?: string | null): string {
  if (basis === "trade_price") return "Trade price (fallback)";
  if (basis === "snapshot_mid") return "Order-book mid";
  return basis ? basis.replaceAll("_", " ") : "N/A";
}

function VolumeEvidencePanel({ evidence }: { evidence: VolumeEvidence }) {
  const venueNames: Record<string, string> = { order_book: "Order book", liquidity_pool: "Liquidity pool", unknown: "Venue not recorded" };
  const coverage = evidence.coverage;
  return (
    <div className="section-block volume-evidence">
      <SectionHeading title="Volume evidence" detail="Computed from persisted trades in the signal window." />
      <div className="detail-facts">
        <DetailField label="Trades" value={formatInteger(evidence.trade_count)} />
        <DetailField label="Base volume" value={formatNumber(evidence.base_volume)} />
        <DetailField label="Largest trade share" value={evidence.largest_trade_share_percent === null ? "N/A" : `${formatNumber(evidence.largest_trade_share_percent)}%`} />
        <DetailField label="Venue coverage" value={`${coverage.venue_trades} / ${coverage.total_trades} trades`} />
        {evidence.matches_signal_volume === false && <DetailField label="Warning" value="Stored trades do not sum to the signal volume" />}
      </div>
      <div className="evaluation-list">
        {Object.entries(evidence.venues).filter(([, bucket]) => bucket.count > 0).map(([name, bucket]) => (
          <div className="evaluation-row" key={name}><strong>{venueNames[name] ?? name}</strong><span>{formatNumber(bucket.base_volume)} base</span><small>{bucket.count} trades</small></div>
        ))}
      </div>
      {evidence.largest_trades_by_base.length > 0 && <table><thead><tr><th>Largest trades</th><th>Time</th><th>Base</th><th>Quote</th><th>Venue</th></tr></thead><tbody>
        {evidence.largest_trades_by_base.map((trade) => <tr key={trade.trade_id}><td>{trade.trade_id.split(":").pop()}</td><td>{formatDateTime(trade.occurred_at)}</td><td>{formatNumber(trade.base_amount)}</td><td>{formatNumber(trade.quote_amount)}</td><td>{venueNames[trade.venue ?? "unknown"] ?? trade.venue}</td></tr>)}
      </tbody></table>}
      {evidence.top_operations.length > 0 && <p><small>Top operations: {evidence.top_operations.map((op) => `${op.operation_id} (${formatNumber(op.base_volume)})`).join(", ")}</small></p>}
      <p><small>{evidence.direction.note} Orientation coverage: {coverage.orientation_trades} / {coverage.total_trades} trades. {evidence.distinct_accounts !== null ? `${evidence.distinct_accounts} distinct accounts (identities hidden).` : "Account data not recorded."}</small></p>
    </div>
  );
}
